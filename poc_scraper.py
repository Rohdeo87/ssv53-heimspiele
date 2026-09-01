#!/usr/bin/env python3
"""SSV53 PoC: FUSSBALL.DE/DFBnet-Spiele anhand der Spielstätte auslesen.

Der PoC schreibt bewusst noch nicht in Appack. Er erzeugt stattdessen eine
prüfbare Vorschau als JSON/CSV sowie getrennte ICS-Dateien für Rasen und
Kunstrasen.
"""
from __future__ import annotations

import argparse
import calendar
import csv
import hashlib
import html
import json
import logging
import random
import re
import sys
import time
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Any, Callable, Iterable
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup, Tag

LOG = logging.getLogger("ssv53-dfbnet-poc")
BASE_URL = "https://www.fussball.de"
DATE_TIME_PATTERNS = (
    # FUSSBALL.DE verwendet je nach Ansicht zwischen Datum und Uhrzeit
    # einen Bindestrich, ein Komma oder einen senkrechten Strich.
    re.compile(r"(?P<date>\d{2}\.\d{2}\.\d{4})\s*(?:-|,|\|)?\s*(?P<time>\d{2}:\d{2})"),
    re.compile(r"(?P<date>\d{2}\.\d{2}\.\d{2})\s*(?:-|,|\|)?\s*(?P<time>\d{2}:\d{2})"),
)
STATUS_TERMS = (
    "Absetzung",
    "Spielabsetzung",
    "Ausfall",
    "Spielausfall",
    "Nichtantritt HEIM",
    "Nichtantritt GAST",
    "Nichtantritt BEIDE",
    "Annullierung",
    "Annuliert",
    "Abbruch",
    "vorläufiges Spiel",
)
MATCH_ROW_CLASSES = {"row-competition", "row-festival", "row-tournament"}


class ScrapeError(RuntimeError):
    pass


class GlobalAbortError(ScrapeError):
    """Fehler, nach dem im aktuellen Lauf keine weiteren Requests erfolgen."""


class RateLimitError(GlobalAbortError):
    pass


class SecurityLockError(GlobalAbortError):
    """Permanente Sperre nach 403/406 oder erkannter Sicherheitsseite."""


class RequestBudgetExceeded(GlobalAbortError):
    pass


@dataclass
class VenueRule:
    name: str
    pattern: str
    decision: str
    calendar: str = ""

    def compiled(self) -> re.Pattern[str]:
        return re.compile(self.pattern, re.IGNORECASE)


@dataclass
class Match:
    external_id: str
    match_number: str
    team_id: str
    team_name: str
    team_category: str
    team_role: str
    kickoff: str
    home_team: str
    away_team: str
    competition: str
    match_type: str
    status: str
    venue_raw: str
    detail_url: str
    source_url: str
    decision: str = "review"
    calendar: str = ""
    venue_rule: str = ""
    event_start: str = ""
    event_end: str = ""
    checksum: str = ""
    warnings: list[str] = field(default_factory=list)
    home_team_id: str = ""
    away_team_id: str = ""
    postponed_to: str = ""


class Client:
    """Zurückhaltender HTTP-Client mit fest eingebauten Schutzgrenzen."""

    ABSOLUTE_MAX_REQUESTS = 10
    ABSOLUTE_MAX_RETRIES = 1
    MIN_DELAY_SECONDS = 3.0
    DEFAULT_RATE_LIMIT_BLOCK_SECONDS = 6 * 60 * 60
    RETRYABLE_STATUS_CODES = {502, 503, 504}
    SECURITY_STATUS_CODES = {403, 406}
    TECHNICAL_CHALLENGE_MARKERS = (
        "cf-chl-",
        "/cdn-cgi/challenge-platform/",
        "challenge-platform",
    )
    VISIBLE_CHALLENGE_MARKERS = (
        "checking your browser",
        "verify you are human",
        "verification required",
        "security check",
        "attention required",
        "access denied",
        "captcha",
        "just a moment",
        "bot detection",
        "unusual traffic",
    )

    def __init__(self, config: dict[str, Any], state_path: Path | None = None) -> None:
        request_cfg = config.get("request", {})
        self.timeout = max(float(request_cfg.get("timeout_seconds", 25)), 1.0)
        self.max_retries = min(
            max(int(request_cfg.get("max_retries", 1)), 0),
            self.ABSOLUTE_MAX_RETRIES,
        )
        self.delay = max(
            float(request_cfg.get("delay_seconds", self.MIN_DELAY_SECONDS)),
            self.MIN_DELAY_SECONDS,
        )
        self.jitter = max(float(request_cfg.get("jitter_seconds", 1.0)), 0.0)
        configured_limit = int(
            request_cfg.get("max_requests_per_run", self.ABSOLUTE_MAX_REQUESTS)
        )
        self.max_requests = min(
            max(configured_limit, 1), self.ABSOLUTE_MAX_REQUESTS
        )
        self.state_path = state_path
        self.state = self._load_state()
        self.request_count = 0
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": request_cfg.get(
                "user_agent",
                "SSV53-Belegungsplan-PoC/12.3 "
                "(+https://www.ssv53.de; mailto:thomas.rohde@ssv53.de)",
            ),
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "de-DE,de;q=0.9,en;q=0.5",
        })
        self._last_request = 0.0

    def _load_state(self) -> dict[str, Any]:
        defaults: dict[str, Any] = {
            "blocked_until": "",
            "last_run_at": "",
            "last_status": "not_started",
            "last_request_count": 0,
            "last_http_status": None,
            "last_retry_after_seconds": None,
            "security_lock": False,
            "security_lock_reason": "",
            "security_lock_at": "",
            "security_lock_url": "",
            "security_lock_http_status": None,
            "manual_unlock_required": False,
        }
        if not self.state_path or not self.state_path.exists():
            return defaults
        try:
            loaded = json.loads(self.state_path.read_text(encoding="utf-8"))
            return {**defaults, **loaded} if isinstance(loaded, dict) else defaults
        except (OSError, json.JSONDecodeError):
            return defaults

    def _save_state(self) -> None:
        if not self.state_path:
            return
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.state_path.with_suffix(self.state_path.suffix + ".tmp")
        temporary.write_text(
            json.dumps(self.state, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        temporary.replace(self.state_path)

    @staticmethod
    def _utc_now() -> datetime:
        return datetime.now(timezone.utc)

    @staticmethod
    def _parse_retry_after(value: str | None) -> int | None:
        raw = str(value or "").strip()
        if not raw:
            return None
        if raw.isdigit():
            return max(int(raw), 0)
        try:
            parsed = parsedate_to_datetime(raw)
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=timezone.utc)
            seconds = int((parsed - datetime.now(timezone.utc)).total_seconds())
            return max(seconds, 0)
        except (TypeError, ValueError, OverflowError):
            return None

    def assert_not_blocked(self) -> None:
        if bool(self.state.get("security_lock")):
            reason = str(self.state.get("security_lock_reason") or "Sicherheitsreaktion")
            locked_at = str(self.state.get("security_lock_at") or "unbekannt")
            raise SecurityLockError(
                "Permanente Sicherheitssperre aktiv. Es werden keine Requests "
                f"gesendet. Grund: {reason}; gesetzt: {locked_at}. "
                "Die Sperre muss nach manueller Prüfung ausdrücklich aufgehoben werden."
            )

        raw = str(self.state.get("blocked_until") or "").strip()
        if not raw:
            return
        try:
            blocked_until = datetime.fromisoformat(raw.replace("Z", "+00:00"))
            if blocked_until.tzinfo is None:
                blocked_until = blocked_until.replace(tzinfo=timezone.utc)
        except ValueError:
            self.state["blocked_until"] = ""
            self._save_state()
            return
        if self._utc_now() < blocked_until:
            raise RateLimitError(
                "Abrufe sind wegen eines vorherigen 429-Hinweises bis "
                f"{blocked_until.isoformat()} gesperrt"
            )
        self.state["blocked_until"] = ""
        self._save_state()

    def _throttle(self) -> None:
        wait = self.delay + random.uniform(0, self.jitter)
        elapsed = time.monotonic() - self._last_request
        if elapsed < wait:
            time.sleep(wait - elapsed)

    def _reserve_request(self) -> None:
        self.assert_not_blocked()
        if self.request_count >= self.max_requests:
            raise RequestBudgetExceeded(
                f"Harte Request-Obergrenze von {self.max_requests} erreicht"
            )
        self.request_count += 1

    def _record_http_status(self, status_code: int) -> None:
        self.state["last_http_status"] = status_code
        self.state["last_request_count"] = self.request_count
        self._save_state()

    def _handle_rate_limit(self, response: requests.Response, url: str) -> None:
        retry_after = self._parse_retry_after(response.headers.get("Retry-After"))
        block_seconds = (
            retry_after
            if retry_after is not None
            else self.DEFAULT_RATE_LIMIT_BLOCK_SECONDS
        )
        blocked_until = self._utc_now() + timedelta(seconds=block_seconds)
        self.state.update({
            "blocked_until": blocked_until.isoformat(),
            "last_status": "rate_limited",
            "last_http_status": 429,
            "last_retry_after_seconds": block_seconds,
            "last_request_count": self.request_count,
        })
        self._save_state()
        raise RateLimitError(
            "FUSSBALL.DE meldet 429. Der gesamte Lauf wird beendet; "
            f"keine weiteren Mannschaften werden abgerufen. URL: {url}"
        )

    @classmethod
    def _detect_challenge(cls, response: requests.Response) -> str:
        """Erkennt typische Sicherheits-/Bot-Challenges, ohne sie zu umgehen."""
        content_type = str(response.headers.get("Content-Type") or "").casefold()
        server = str(response.headers.get("Server") or "").casefold()
        text = str(response.text or "")
        sample = text[:250000].casefold()

        # Technische Challenge-Signaturen sind eindeutig und sperren sofort.
        # Reine Begriffe wie ``captcha`` duerfen dagegen nicht im gesamten
        # Quelltext gesucht werden: regulaere FUSSBALL.DE-Spielseiten enthalten
        # ein CAPTCHA im Kontaktformular "Falsches Ergebnis melden".
        for marker in cls.TECHNICAL_CHALLENGE_MARKERS:
            if marker in sample:
                return f"Challenge-Marker erkannt: {marker}"

        title = ""
        heading_text = ""
        visible_text = ""
        if "text/html" in content_type or "<html" in sample:
            soup = BeautifulSoup(text, "html.parser")
            title = normalize_space(soup.title.get_text(" ", strip=True)).casefold() if soup.title else ""
            heading_text = normalize_space(
                " ".join(node.get_text(" ", strip=True) for node in soup.select("h1, h2, [role='heading']"))
            ).casefold()
            for node in soup.select("script, style, noscript, template"):
                node.decompose()
            visible_text = normalize_space(soup.get_text(" ", strip=True)).casefold()

        for marker in cls.VISIBLE_CHALLENGE_MARKERS:
            if marker in title or marker in heading_text:
                return f"Sicherheitsseite anhand sichtbarer Ueberschrift erkannt: {marker}"
            # Kleine Zwischen-/Sperrseiten haben oft keine semantische
            # Ueberschrift. Dort bleibt ein sichtbarer Challenge-Hinweis
            # dennoch sperrend; umfangreiche regulaere Seiten werden nicht
            # wegen eingebetteter Kontaktformulare blockiert.
            if len(visible_text) < 5000 and marker in visible_text:
                return f"Sicherheitsseite anhand sichtbarem Hinweis erkannt: {marker}"

        # Zusätzliche strukturelle Signale typischer Challenge-Seiten. Ein
        # einzelner Cloudflare-Header reicht bewusst nicht aus, da reguläre
        # Seiten ebenfalls über ein CDN ausgeliefert werden können.
        if "cloudflare" in server and (
            "<title>just a moment" in sample
            or "cf-ray" in str(response.headers).casefold() and "challenge" in sample
        ):
            return "Cloudflare-Sicherheitsseite erkannt"

        return ""

    def _activate_security_lock(
        self,
        *,
        reason: str,
        url: str,
        status_code: int | None,
    ) -> None:
        now = self._utc_now().isoformat()
        self.state.update({
            "security_lock": True,
            "security_lock_reason": reason,
            "security_lock_at": now,
            "security_lock_url": url,
            "security_lock_http_status": status_code,
            "manual_unlock_required": True,
            "last_status": "security_locked",
            "last_http_status": status_code,
            "last_request_count": self.request_count,
        })
        self._save_state()
        raise SecurityLockError(
            "FUSSBALL.DE hat eine mögliche Sicherheitsreaktion geliefert. "
            "Der gesamte Lauf wurde sofort beendet und alle zukünftigen "
            "Abrufe sind bis zur manuellen Prüfung gesperrt. "
            f"Grund: {reason}; URL: {url}"
        )

    def _wait_before_retry(self, response: requests.Response | None = None) -> None:
        retry_after = self._parse_retry_after(
            response.headers.get("Retry-After") if response is not None else None
        )
        if retry_after is not None:
            # Lange serverseitige Wartezeiten werden nicht ausgesessen; der Lauf
            # endet stattdessen und versucht es erst beim nächsten Zeitplanlauf.
            if retry_after > 300:
                raise ScrapeError(
                    f"Server verlangt eine Wartezeit von {retry_after} Sekunden"
                )
            time.sleep(max(float(retry_after), self.MIN_DELAY_SECONDS))
            return
        time.sleep(5.0)

    def get_text(self, url: str) -> str:
        """Maximal ein Retry, ausschließlich bei Timeout oder 502/503/504."""
        last_error: Exception | None = None
        attempts = self.max_retries + 1

        for attempt in range(attempts):
            response: requests.Response | None = None
            try:
                self._reserve_request()
                self._throttle()
                response = self.session.get(url, timeout=self.timeout)
                self._last_request = time.monotonic()
                self._record_http_status(response.status_code)

                if response.status_code == 429:
                    self._handle_rate_limit(response, url)

                if response.status_code in self.SECURITY_STATUS_CODES:
                    self._activate_security_lock(
                        reason=f"HTTP {response.status_code}",
                        url=url,
                        status_code=response.status_code,
                    )

                challenge_reason = self._detect_challenge(response)
                if challenge_reason:
                    self._activate_security_lock(
                        reason=challenge_reason,
                        url=url,
                        status_code=response.status_code,
                    )

                if response.status_code in self.RETRYABLE_STATUS_CODES:
                    last_error = ScrapeError(
                        f"Vorübergehender HTTP-Fehler {response.status_code} bei {url}"
                    )
                    if attempt + 1 < attempts:
                        self._wait_before_retry(response)
                        continue
                    raise last_error

                if response.status_code >= 400:
                    # 403/406 wurden bereits global gesperrt; andere 4xx
                    # werden ebenfalls niemals wiederholt.
                    raise ScrapeError(
                        f"Nicht wiederholbarer HTTP-Fehler {response.status_code} bei {url}"
                    )

                if not response.text.strip():
                    raise ScrapeError(f"Leere Antwort von {url}")
                return response.text

            except requests.Timeout as exc:
                last_error = exc
                if attempt + 1 < attempts:
                    self._wait_before_retry()
                    continue
                break
            except (RateLimitError, SecurityLockError, RequestBudgetExceeded):
                raise
            except requests.RequestException as exc:
                # Andere Netzwerkfehler werden bewusst nicht wiederholt.
                last_error = exc
                break
            except ScrapeError:
                raise

        raise ScrapeError(f"Abruf fehlgeschlagen: {url}: {last_error}")

    def finish_run(self, status: str) -> None:
        self.state.update({
            "last_run_at": self._utc_now().isoformat(),
            "last_status": status,
            "last_request_count": self.request_count,
        })
        if status == "success":
            self.state["last_retry_after_seconds"] = None
        self._save_state()



def normalize_space(value: str) -> str:
    return re.sub(r"\s+", " ", html.unescape(value or "")).strip()


def normalize_match_text(value: str) -> str:
    text = normalize_space(value).casefold()
    text = text.replace("ä", "ae").replace("ö", "oe").replace("ü", "ue").replace("ß", "ss")
    return re.sub(r"[^a-z0-9]+", " ", text).strip()


def parse_datetime(text: str) -> datetime | None:
    text = normalize_space(text)
    for pattern in DATE_TIME_PATTERNS:
        match = pattern.search(text)
        if not match:
            continue
        raw_date = match.group("date")
        fmt = "%d.%m.%Y %H:%M" if len(raw_date.split(".")[-1]) == 4 else "%d.%m.%y %H:%M"
        try:
            return datetime.strptime(
                f"{raw_date} {match.group('time')}", fmt
            ).replace(tzinfo=ZoneInfo("Europe/Berlin"))
        except ValueError:
            continue
    return None


def extract_club_id(config: dict[str, Any]) -> str:
    explicit = str(config.get("club_id") or "").strip()
    if explicit:
        return explicit
    club_url = str(config.get("club_url") or "")
    match = re.search(r"/-/id/([A-Z0-9]+)", club_url, re.IGNORECASE)
    return match.group(1) if match else ""


def extract_team_id(url: str) -> str:
    match = re.search(r"/team-id/([A-Z0-9]+)", url, re.IGNORECASE)
    return match.group(1) if match else ""


def club_matchplan_url(
    config: dict[str, Any], date_from: str, date_to: str
) -> str:
    club_id = extract_club_id(config)
    if not club_id:
        raise SystemExit("club_id oder eine club_url mit Vereins-ID ist erforderlich")
    request_cfg = config.get("request", {})
    max_rows = max(int(request_cfg.get("max_rows_per_response", 50)), 10)
    template = str(
        request_cfg.get(
            "club_matchplan_endpoint_template",
            BASE_URL
            + "/ajax.club.matchplan/-/id/{club_id}/mode/PAGE/"
            + "show-filter/false/show-venues/true/mime-type/HTML/"
            + "datum-von/{date_from}/datum-bis/{date_to}/max/{max_rows}",
        )
    )
    return template.format(
        base_url=BASE_URL,
        club_id=club_id,
        date_from=date_from,
        date_to=date_to,
        max_rows=max_rows,
    )


def parse_iso_date(value: str, field_name: str) -> date:
    try:
        return datetime.strptime(value, "%Y-%m-%d").date()
    except ValueError as exc:
        raise SystemExit(f"{field_name} muss YYYY-MM-DD verwenden") from exc


def add_months(value: date, months: int) -> date:
    month_index = value.year * 12 + (value.month - 1) + months
    year, month_zero = divmod(month_index, 12)
    month = month_zero + 1
    day = min(value.day, calendar.monthrange(year, month)[1])
    return date(year, month, day)


def build_initial_windows(
    date_from: str, date_to: str, months_per_window: int = 3
) -> list[tuple[str, str]]:
    start = parse_iso_date(date_from, "date_from")
    end = parse_iso_date(date_to, "date_to")
    if start > end:
        raise SystemExit("date_from darf nicht nach date_to liegen")
    if months_per_window < 1:
        raise SystemExit("initial_window_months muss mindestens 1 sein")

    windows: list[tuple[str, str]] = []
    current = start
    while current <= end:
        next_start = add_months(current, months_per_window)
        window_end = min(end, next_start - timedelta(days=1))
        windows.append((current.isoformat(), window_end.isoformat()))
        current = window_end + timedelta(days=1)
    return windows


def split_window(date_from: str, date_to: str) -> list[tuple[str, str]]:
    start = parse_iso_date(date_from, "date_from")
    end = parse_iso_date(date_to, "date_to")
    if start >= end:
        return []
    midpoint = start + timedelta(days=(end - start).days // 2)
    return [
        (start.isoformat(), midpoint.isoformat()),
        ((midpoint + timedelta(days=1)).isoformat(), end.isoformat()),
    ]


def fetch_club_matchplan(
    client: Client,
    config: dict[str, Any],
    date_from: str,
    date_to: str,
) -> tuple[str, str]:
    url = club_matchplan_url(config, date_from, date_to)
    body = client.get_text(url)
    if "row-competition" not in body and "club-matchplan-table" not in body:
        raise ScrapeError(
            f"Vereinsspielplan {date_from}–{date_to}: erwartete Struktur fehlt"
        )
    return body, url


def row_classes(row: Tag) -> set[str]:
    value = row.get("class") or []
    return {str(item) for item in value}


def row_text(row: Tag) -> str:
    return normalize_space(row.get_text(" ", strip=True))


def is_next_match_header(row: Tag) -> bool:
    classes = row_classes(row)
    if MATCH_ROW_CLASSES.intersection(classes) or "row-headline" in classes:
        return True
    text = row_text(row)
    if parse_datetime(text) is None:
        return False
    if row.select_one('.club-name, a[href*="/spiel/"], [class*="venue"], [class*="location"]'):
        return False
    return True


def block_rows(start_row: Tag) -> list[Tag]:
    rows = [start_row]
    sibling = start_row.find_next_sibling("tr")
    while sibling is not None:
        if is_next_match_header(sibling):
            break
        rows.append(sibling)
        sibling = sibling.find_next_sibling("tr")
    return rows


def kickoff_for_competition_row(
    competition_row: Tag, rows: list[Tag]
) -> datetime | None:
    """Ermittelt den Anstoß ausschließlich aus dem zugehörigen Spielblock.

    FUSSBALL.DE rendert Vereinsspielpläne teilweise doppelt (Desktop/Mobil)
    und kann zwischen diesen Darstellungen zusätzliche Tabellenzeilen
    einschieben. Die frühere breite Rückwärtssuche konnte dadurch das Datum
    des benachbarten Spiels übernehmen. Diese Fassung akzeptiert nur:

    1. ein Datum direkt in der Wettbewerbszeile,
    2. die unmittelbar zugehörige vorherige Überschriftszeile oder
    3. genau einen eindeutigen Datumswert innerhalb des aktuellen Blocks.

    Bei Mehrdeutigkeit wird bewusst kein Anstoß geraten. Eine zweite
    Darstellung desselben Spiels darf die fehlende Angabe später ergänzen.
    """
    kickoff = parse_datetime(row_text(competition_row))
    if kickoff is not None:
        return kickoff

    sibling = competition_row.find_previous_sibling("tr")
    while sibling is not None:
        classes = row_classes(sibling)
        if MATCH_ROW_CLASSES.intersection(classes):
            break
        if "row-headline" in classes or is_next_match_header(sibling):
            return parse_datetime(row_text(sibling))
        sibling = sibling.find_previous_sibling("tr")

    candidates: dict[str, datetime] = {}
    for row in rows[1:]:
        elements = row.select(
            '.column-date, [class*="date"], [class*="kickoff"], time[datetime]'
        )
        texts = [row_text(element) for element in elements]
        if row.has_attr("datetime"):
            texts.append(str(row.get("datetime") or ""))
        for text in texts:
            parsed = parse_datetime(text)
            if parsed is not None:
                candidates[parsed.isoformat(timespec="minutes")] = parsed

    if len(candidates) == 1:
        return next(iter(candidates.values()))
    return None


def extract_detail_id(detail_url: str) -> str:
    id_match = re.search(r"/-/spiel/([A-Z0-9]+)(?:[/?#]|$)", detail_url, re.IGNORECASE)
    if id_match:
        return id_match.group(1)
    candidates = re.findall(r"/spiel/([A-Z0-9]+)(?:[/?#]|$)", detail_url, re.IGNORECASE)
    return candidates[-1] if candidates else ""


def source_detail_ids(soup: BeautifulSoup) -> set[str]:
    result: set[str] = set()
    for link in soup.select('a[href*="/spiel/"]'):
        detail_id = extract_detail_id(urljoin(BASE_URL, str(link.get("href") or "")))
        if detail_id:
            result.add(detail_id)
    return result


def extract_festival_group_id(url: str) -> str:
    match = re.search(r"/-/staffel/([A-Z0-9]+-G)(?:[/?#]|$)", url, re.IGNORECASE)
    return match.group(1) if match else ""


def postponed_kickoff(rows: list[Tag], detail_id: str) -> str:
    """A dated score link is a relocation pointer, not a second kickoff."""
    targets = set()
    for element in select_from_rows(rows, '.column-score .info-text'):
        target = parse_datetime(row_text(element))
        if target is None:
            continue
        link = element.find_parent('a')
        if not detail_id or not isinstance(link, Tag) or extract_detail_id(
            urljoin(BASE_URL, str(link.get('href') or ''))
        ) != detail_id:
            raise ScrapeError("Verlegungshinweis ohne eindeutig zugehörige Spiel-ID")
        targets.add(target.isoformat(timespec="minutes"))
    if len(targets) > 1:
        raise ScrapeError(f"Widersprüchliche Verlegungsziele für Spiel {detail_id}")
    return next(iter(targets), "")


def select_from_rows(rows: Iterable[Tag], selector: str) -> list[Tag]:
    result: list[Tag] = []
    for row in rows:
        result.extend(row.select(selector))
    return result


def unique_texts(elements: Iterable[Tag]) -> list[str]:
    values: list[str] = []
    seen: set[str] = set()
    for element in elements:
        text = normalize_space(element.get_text(" ", strip=True))
        key = normalize_match_text(text)
        if text and key not in seen:
            values.append(text)
            seen.add(key)
    return values


def extract_venue(rows: list[Tag]) -> str:
    candidates: list[str] = []
    selectors = (
        '[class*="venue"]',
        '[class*="location"]',
        '[class*="address"]',
        '.column-venue',
        '.venue',
        '.location',
    )
    for selector in selectors:
        candidates.extend(unique_texts(select_from_rows(rows, selector)))
    for cell in select_from_rows(rows, "td, div"):
        text = normalize_space(cell.get_text(" ", strip=True))
        normalized = normalize_match_text(text)
        if any(token in normalized for token in ("sportplatz", "rasenplatz", "kunstrasen", "stadion")):
            if 5 <= len(text) <= 300:
                candidates.append(text)
    if not candidates:
        return ""
    candidates = sorted(set(candidates), key=lambda item: (len(item), item))
    for candidate in candidates:
        normalized = normalize_match_text(candidate)
        if "sportplatz" in normalized and ("rasen" in normalized or "platz" in normalized):
            return candidate
    return candidates[0]


def extract_competition(row: Tag) -> str:
    explicit = row.select_one('[class*="competition"], .column-competition')
    text = normalize_space(explicit.get_text(" ", strip=True)) if explicit else row_text(row)
    text = re.sub(r"\b\d{2}\.\d{2}\.\d{2,4}\b", " ", text)
    text = re.sub(r"\b\d{2}:\d{2}\b", " ", text)
    text = re.sub(r"\b\d{9}\b", " ", text)
    text = re.sub(r"\b(?:ME|PO|FS|FR|TU|PR)\b", " ", text)
    return normalize_space(text.strip(" -|"))


def extract_team_category(competition_row: Tag) -> str:
    selectors = (
        ".column-team",
        '[class*="team-type"]',
        '[class*="team-category"]',
        '[class*="team"]:not(.club-name)',
    )
    for selector in selectors:
        element = competition_row.select_one(selector)
        if element:
            value = normalize_space(element.get_text(" ", strip=True))
            if value:
                return value

    cells = [normalize_space(cell.get_text(" ", strip=True)) for cell in competition_row.find_all("td", recursive=False)]
    for value in cells:
        normalized = normalize_match_text(value)
        if not value or parse_datetime(value):
            continue
        if re.search(r"\b\d{9}\b", value) or re.search(r"\b(?:ME|PO|FS|FR|TU|PR)\b", value):
            continue
        if any(token in normalized for token in ("liga", "pokal", "freundschaft", "turnier")):
            continue
        return value
    return ""


def extract_status(block_text: str) -> str:
    lower = block_text.casefold()
    for term in STATUS_TERMS:
        if term.casefold() in lower:
            return term
    return ""


def club_name_match(value: str, pattern: str) -> bool:
    if not pattern:
        return False
    return bool(re.search(pattern, normalize_space(value), re.IGNORECASE))


def team_id_for_club_element(element: Tag | None) -> str:
    if element is None:
        return ""
    link = element if element.name == "a" else element.find_parent("a") or element.find("a")
    if not isinstance(link, Tag):
        return ""
    return extract_team_id(urljoin(BASE_URL, str(link.get("href") or "")))


def determine_club_team(
    club_elements: list[Tag],
    home_team: str,
    away_team: str,
    team_category: str,
    club_team_pattern: str,
    *,
    is_festival: bool = False,
) -> tuple[str, str, str]:
    if is_festival:
        participants = {}
        for index, element in enumerate(club_elements):
            name = normalize_space(element.get_text(" ", strip=True))
            team_id = team_id_for_club_element(element)
            if team_id and club_name_match(name, club_team_pattern):
                participants[team_id] = (
                    name,
                    team_id,
                    "home" if index == 0 else "away",
                )
        if len(participants) == 1:
            return next(iter(participants.values()))
    home_match = club_name_match(home_team, club_team_pattern)
    away_match = club_name_match(away_team, club_team_pattern)
    if home_match and not away_match:
        element = club_elements[0] if club_elements else None
        team_id = team_id_for_club_element(element)
        return home_team, team_id, "home"
    if away_match and not home_match:
        element = club_elements[1] if len(club_elements) > 1 else None
        team_id = team_id_for_club_element(element)
        return away_team, team_id, "away"

    fallback = team_category or "Vereinsmannschaft"
    stable = hashlib.sha256(normalize_match_text(fallback).encode("utf-8")).hexdigest()[:16]
    return fallback, f"auto-{stable}", "unknown"


def has_more_results(html_text: str) -> bool:
    soup = BeautifulSoup(html_text, "lxml")
    for element in soup.select(
        '.load-more, [class*="load-more"], [data-action*="load"], a, button'
    ):
        text = normalize_match_text(element.get_text(" ", strip=True))
        if text in {"mehr laden", "weitere laden", "mehr anzeigen"}:
            return True
    return bool(re.search(r">\s*Mehr\s+laden\s*<", html_text, re.IGNORECASE))


def match_duration_minutes(
    team_name: str, team_category: str, config: dict[str, Any]
) -> int:
    timing = config.get("event_timing", {})
    combined = normalize_space(f"{team_category} {team_name}")
    for rule in timing.get("duration_rules", []) or []:
        pattern = str(rule.get("pattern") or "")
        if pattern and re.search(pattern, combined, re.IGNORECASE):
            return max(int(rule.get("minutes", 90)), 1)
    return max(int(timing.get("default_match_duration_minutes", 90)), 1)



def parse_detail_page_reference(html_text: str) -> dict[str, Any]:
    """Liest kanonische Datums-/Zeitangaben aus einer Spiel-Detailseite.

    Die Spielseite ist bei widersprüchlichen Mehrfachdarstellungen die
    maßgebliche Referenz. Exakte Zeitangaben werden bevorzugt; ist nur das
    Datum im Seitentitel vorhanden, reicht dieses zur Auswahl zwischen
    unterschiedlichen Tagen.
    """
    soup = BeautifulSoup(html_text, "lxml")
    exact: dict[str, str] = {}
    dates: set[str] = set()

    def add_datetime_text(value: str, source: str) -> None:
        raw = normalize_space(value)
        if not raw:
            return
        parsed = parse_datetime(raw)
        if parsed is not None:
            key = parsed.isoformat(timespec="minutes")
            exact[key] = source
            dates.add(key[:10])
            return

        iso_value = raw.replace("Z", "+00:00")
        try:
            parsed_iso = datetime.fromisoformat(iso_value)
        except ValueError:
            parsed_iso = None
        if parsed_iso is not None:
            if parsed_iso.tzinfo is None:
                parsed_iso = parsed_iso.replace(tzinfo=ZoneInfo("Europe/Berlin"))
            else:
                parsed_iso = parsed_iso.astimezone(ZoneInfo("Europe/Berlin"))
            key = parsed_iso.isoformat(timespec="minutes")
            exact[key] = source
            dates.add(key[:10])
            return

        for match in re.finditer(r"\b(\d{2})\.(\d{2})\.(\d{4})\b", raw):
            day, month, year = match.groups()
            try:
                dates.add(date(int(year), int(month), int(day)).isoformat())
            except ValueError:
                continue

    for element in soup.select(
        'meta[itemprop="startDate"][content], '
        'meta[property*="start"][content], '
        'meta[name*="start"][content], '
        'time[datetime], [itemprop="startDate"], '
        '[data-kickoff], [data-start-date], [data-start]'
    ):
        value = (
            element.get("content")
            or element.get("datetime")
            or element.get("data-start")
            or element.get("data-date")
            or element.get_text(" ", strip=True)
        )
        add_datetime_text(str(value or ""), "structured")

    for script in soup.select('script[type="application/ld+json"]'):
        try:
            payload = json.loads(script.string or script.get_text() or "null")
        except json.JSONDecodeError:
            continue

        def walk(value: Any) -> None:
            if isinstance(value, dict):
                for key, child in value.items():
                    if str(key).casefold() in {"startdate", "start_date", "kickoff"}:
                        add_datetime_text(str(child or ""), "json-ld")
                    walk(child)
            elif isinstance(value, list):
                for child in value:
                    walk(child)

        walk(payload)

    for pattern in (
        r'"startDate"\s*:\s*"([^"]+)"',
        r'"start_date"\s*:\s*"([^"]+)"',
        r'"kickoff"\s*:\s*"([^"]+)"',
    ):
        for match in re.finditer(pattern, html_text, re.IGNORECASE):
            add_datetime_text(match.group(1), "embedded-json")

    title = soup.title.get_text(" ", strip=True) if soup.title else ""
    add_datetime_text(title, "title")
    for heading in soup.select("h1, h2, .headline, .match-date, .date, .kickoff"):
        add_datetime_text(heading.get_text(" ", strip=True), "visible")

    return {
        "exact_kickoffs": sorted(exact),
        "dates": sorted(dates),
        "sources": exact,
        "title": title,
    }


def recalculate_event_times(match: Match, config: dict[str, Any]) -> None:
    if not match.kickoff:
        match.event_start = ""
        match.event_end = ""
        return
    try:
        kickoff = datetime.fromisoformat(match.kickoff)
    except ValueError:
        match.event_start = ""
        match.event_end = ""
        return
    timing = config.get("event_timing", {})
    before = int(timing.get("before_minutes", 60))
    after = int(timing.get("after_minutes", 60))
    duration = match_duration_minutes(match.team_name, match.team_category, config)
    match.event_start = (kickoff - timedelta(minutes=before)).isoformat(timespec="minutes")
    match.event_end = (kickoff + timedelta(minutes=duration + after)).isoformat(timespec="minutes")


def _festival_birth_year(match: Match) -> int | None:
    for text in (match.team_name, match.detail_url, match.home_team, match.away_team):
        years = {
            int(value)
            for value in re.findall(r"(?<!\d)(20\d{2})(?!\d)", str(text or ""))
        }
        if len(years) == 1:
            return next(iter(years))
        if len(years) > 1:
            return None
    return None


def _is_festival_match(match: Match) -> bool:
    combined = normalize_match_text(
        " ".join((match.team_name, match.team_category, match.competition,
                  match.home_team, match.away_team))
    )
    return bool(
        re.search(r"\b(?:kinderfussball|festival|spielfest|spielenachmittag)\b", combined)
    )


def apply_festival_round_assignment_rules(
    matches: list[Match], config: dict[str, Any]
) -> list[dict[str, Any]]:
    settings = config.get("festival_round_assignment")
    if not isinstance(settings, dict):
        return []
    if str(settings.get("strategy") or "").strip() != "younger_birth_year_first":
        return []
    try:
        max_gap_minutes = int(settings.get("max_round_gap_minutes", 180))
    except (TypeError, ValueError) as exc:
        raise ScrapeError("festival_round_assignment.max_round_gap_minutes ist ungültig") from exc
    if max_gap_minutes < 1 or max_gap_minutes > 360:
        raise ScrapeError(
            "festival_round_assignment.max_round_gap_minutes muss zwischen 1 und 360 liegen"
        )
    raw_overrides = settings.get("explicit_kickoffs") or {}
    if not isinstance(raw_overrides, dict):
        raise ScrapeError("festival_round_assignment.explicit_kickoffs muss ein Objekt sein")
    overrides = {str(key): str(value) for key, value in raw_overrides.items()}

    grouped: dict[tuple[str, str, str, str], list[tuple[datetime, Match, int]]] = {}
    for match in matches:
        if not _is_festival_match(match) or not match.kickoff:
            continue
        year = _festival_birth_year(match)
        if year is None:
            continue
        try:
            kickoff = datetime.fromisoformat(match.kickoff)
        except ValueError:
            continue
        key = (
            kickoff.date().isoformat(),
            normalize_match_text(match.venue_raw),
            normalize_match_text(match.team_category),
            normalize_match_text(match.competition),
        )
        grouped.setdefault(key, []).append((kickoff, match, year))

    audit: list[dict[str, Any]] = []
    max_gap = timedelta(minutes=max_gap_minutes)
    for key, candidates in grouped.items():
        ordered = sorted(candidates, key=lambda item: (item[0], item[2], item[1].external_id))
        clusters: list[list[tuple[datetime, Match, int]]] = []
        for candidate in ordered:
            if not clusters or candidate[0] - clusters[-1][-1][0] > max_gap:
                clusters.append([candidate])
            else:
                clusters[-1].append(candidate)
        for cluster in clusters:
            years = [item[2] for item in cluster]
            slots = sorted({item[0] for item in cluster})
            if (
                len(cluster) < 2
                or len(set(years)) != len(cluster)
                or len(slots) != len(cluster)
            ):
                continue
            override_members = [item for item in cluster if item[1].external_id in overrides]
            method = "younger_birth_year_first"
            assigned: dict[str, datetime] = {}
            if override_members:
                if len(override_members) != len(cluster):
                    raise ScrapeError(
                        "Eine Festival-Sonderzuordnung muss alle Runden des Blocks enthalten"
                    )
                method = "explicit_kickoffs"
                for _, match, _ in cluster:
                    try:
                        value = datetime.fromisoformat(overrides[match.external_id])
                    except ValueError as exc:
                        raise ScrapeError(
                            f"Ungültige Festival-Sonderzeit für {match.external_id}"
                        ) from exc
                    if value.tzinfo is None or value.utcoffset() is None:
                        raise ScrapeError(
                            f"Festival-Sonderzeit für {match.external_id} benötigt eine Zeitzone"
                        )
                    assigned[match.external_id] = value
                if set(assigned.values()) != set(slots):
                    raise ScrapeError(
                        "Festival-Sonderzeiten müssen den offiziellen Rundenzeiten entsprechen"
                    )
            else:
                for (_, match, _), slot in zip(
                    sorted(cluster, key=lambda item: item[2], reverse=True), slots
                ):
                    assigned[match.external_id] = slot

            before = {item[1].external_id: item[1].kickoff for item in cluster}
            for _, match, _ in cluster:
                target = assigned[match.external_id].isoformat(timespec="minutes")
                if match.kickoff != target:
                    match.kickoff = target
                    recalculate_event_times(match, config)
            after = {item[1].external_id: item[1].kickoff for item in cluster}
            audit.append({
                "method": method,
                "group": {"date": key[0], "venue": key[1]},
                "assignments": [
                    {
                        "external_id": match.external_id,
                        "team_id": match.team_id,
                        "birth_year": year,
                        "before": before[match.external_id],
                        "after": after[match.external_id],
                    }
                    for _, match, year in sorted(
                        cluster, key=lambda item: item[2], reverse=True
                    )
                ],
                "changed": before != after,
            })
    return audit


def build_duplicate_detail_resolver(
    client: Client,
    config: dict[str, Any],
    raw_output_dir: Path | None = None,
) -> Callable[[str, list[Match], dict[str, Any]], Match | None]:
    """Erzeugt einen konservativen Resolver für Terminverschiebungen.

    Nur ein reiner Anstoß-Konflikt wird über die offizielle Spielseite
    aufgelöst. Widersprüche bei Mannschaften, Spielnummer oder Spielstätte
    bleiben harte Fehler.
    """
    def resolve(
        detail_id: str, group: list[Match], detail: dict[str, Any]
    ) -> Match | None:
        if set((detail.get("conflicts") or {}).keys()) != {"kickoff"}:
            return None
        detail_urls = [item.detail_url for item in group if item.detail_url]
        if not detail_urls:
            return None
        detail_url = detail_urls[0]
        body = client.get_text(detail_url)
        if raw_output_dir is not None:
            raw_output_dir.mkdir(parents=True, exist_ok=True)
            (raw_output_dir / f"detail_{detail_id}.html").write_text(
                body, encoding="utf-8"
            )
        reference = parse_detail_page_reference(body)
        exact = set(reference.get("exact_kickoffs") or [])
        dates = set(reference.get("dates") or [])

        exact_matches = [item for item in group if item.kickoff in exact]
        candidates = exact_matches
        resolution = "detail_exact_datetime"
        if len(candidates) != 1:
            candidates = [
                item for item in group
                if item.kickoff and item.kickoff[:10] in dates
            ]
            resolution = "detail_date"
        if len(candidates) != 1:
            detail["resolution_attempt"] = {
                "method": resolution,
                "reference": reference,
                "candidate_kickoffs": sorted({item.kickoff for item in group}),
                "resolved": False,
            }
            return None

        chosen = candidates[0]
        note = "Terminverschiebung anhand der offiziellen Spielseite aufgelöst"
        if note not in chosen.warnings:
            chosen.warnings.append(note)
        recalculate_event_times(chosen, config)
        detail["resolution_attempt"] = {
            "method": resolution,
            "reference": reference,
            "candidate_kickoffs": sorted({item.kickoff for item in group}),
            "selected_kickoff": chosen.kickoff,
            "resolved": True,
        }
        return chosen

    return resolve


def _normalized_duplicate_value(field_name: str, value: str) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    if field_name in {"home_team", "away_team", "venue_raw", "status"}:
        return normalize_match_text(text)
    return text


def resolve_postponement_chain(
    detail_id: str, group: list[Match]
) -> tuple[list[Match], list[dict[str, str]]]:
    """Resolve only a complete identity-checked relocation chain."""
    if not any(item.postponed_to for item in group):
        return group, []
    numbers = {item.match_number for item in group}
    identities = {tuple(sorted((item.home_team_id, item.away_team_id))) for item in group}
    if len(numbers) != 1 or "" in numbers or len(identities) != 1:
        raise ScrapeError(f"Verlegung {detail_id}: Spielnummer/Mannschafts-IDs widersprüchlich")
    identity = next(iter(identities))
    if not all(identity) or identity[0] == identity[1]:
        raise ScrapeError(f"Verlegung {detail_id}: Mannschafts-IDs nicht eindeutig")

    by_kickoff: dict[str, list[Match]] = {}
    for item in group:
        by_kickoff.setdefault(item.kickoff, []).append(item)
    terminal_times = set()
    for item in group:
        current = item.kickoff
        visited = set()
        while True:
            if not current or current in visited or current not in by_kickoff:
                raise ScrapeError(f"Verlegung {detail_id}: Ziel fehlt oder zyklische Verlegung")
            visited.add(current)
            edges = {row.postponed_to for row in by_kickoff[current]}
            if len(edges) != 1:
                raise ScrapeError(f"Verlegung {detail_id}: widersprüchliche Zielangaben")
            target = next(iter(edges))
            if not target:
                terminal_times.add(current)
                break
            current = target
    if len(terminal_times) != 1:
        raise ScrapeError(f"Verlegung {detail_id}: mehrere gültige Zieltermine")
    final_time = next(iter(terminal_times))
    final_rows = by_kickoff[final_time]
    if not any(item.venue_raw for item in final_rows):
        raise ScrapeError(f"Verlegung {detail_id}: Spielstätte am Zieltermin fehlt")
    provenance = [
        {"from": item.kickoff, "to": item.postponed_to}
        for item in group if item.postponed_to
    ]
    return final_rows, provenance


def _merge_duplicate_match_group(
    detail_id: str,
    group: list[Match],
    resolver: Callable[[str, list[Match], dict[str, Any]], Match | None] | None = None,
) -> tuple[Match | None, dict[str, Any]]:
    """Führt harmlose Mehrfachdarstellungen desselben Spiels zusammen.

    FUSSBALL.DE kann denselben Spiel-Link innerhalb eines Vereinsspielplans
    mehrfach ausgeben. Das ist nur dann unkritisch, wenn sich die für Termin
    und Platz entscheidenden Angaben nicht widersprechen. Fehlende Angaben
    dürfen sich ergänzen; widersprüchliche Angaben führen weiterhin zu einem
    harten Abbruch.
    """
    occurrences = len(group)
    group, relocations = resolve_postponement_chain(detail_id, group)
    critical_fields = (
        "match_number",
        "kickoff",
        "home_team",
        "away_team",
        "venue_raw",
        "status",
        "home_team_id",
        "away_team_id",
    )
    conflicts: dict[str, list[str]] = {}
    for field_name in critical_fields:
        values: dict[str, str] = {}
        for item in group:
            raw = str(getattr(item, field_name) or "").strip()
            normalized = _normalized_duplicate_value(field_name, raw)
            if normalized and normalized not in values:
                values[normalized] = raw
        if len(values) > 1:
            conflicts[field_name] = sorted(values.values())

    detail = {
        "detail_id": detail_id,
        "occurrences": occurrences,
        "conflicts": conflicts,
    }
    if relocations:
        detail["resolved"] = True
        detail["resolution_attempt"] = {
            "method": "explicit_postponement_chain",
            "relocations": relocations,
            "selected_kickoff": group[0].kickoff,
            "resolved": True,
        }
    if conflicts:
        resolved = resolver(detail_id, group, detail) if resolver else None
        if resolved is None:
            return None, detail
        detail["resolved"] = True
        detail["merged_external_id"] = resolved.external_id
        return resolved, detail

    def score(item: Match) -> tuple[int, int]:
        populated = sum(
            bool(str(getattr(item, field_name) or "").strip())
            for field_name in (
                "match_number", "team_id", "team_name", "team_category",
                "team_role", "kickoff", "home_team", "away_team",
                "competition", "match_type", "status", "venue_raw",
                "detail_url", "event_start", "event_end",
            )
        )
        warning_penalty = -len(item.warnings)
        return populated, warning_penalty

    merged = max(group, key=score)
    fields_to_fill = (
        "match_number", "team_id", "team_name", "team_category",
        "team_role", "kickoff", "home_team", "away_team",
        "competition", "match_type", "status", "venue_raw",
        "detail_url", "source_url", "event_start", "event_end",
    )
    for item in group:
        if item is merged:
            continue
        for field_name in fields_to_fill:
            if not str(getattr(merged, field_name) or "").strip():
                value = getattr(item, field_name)
                if str(value or "").strip():
                    setattr(merged, field_name, value)
        for warning in item.warnings:
            if warning not in merged.warnings:
                merged.warnings.append(warning)

    note = "Mehrfachdarstellung im Vereinsspielplan automatisch zusammengeführt"
    if note not in merged.warnings:
        merged.warnings.append(note)
    detail["merged_external_id"] = merged.external_id
    return merged, detail


def collapse_duplicate_detail_ids(
    matches: list[Match],
    resolver: Callable[[str, list[Match], dict[str, Any]], Match | None] | None = None,
) -> tuple[list[Match], list[str], list[dict[str, Any]], list[dict[str, Any]]]:
    grouped: dict[str, list[Match]] = {}
    without_detail_id: list[Match] = []
    for item in matches:
        detail_id = extract_detail_id(item.detail_url)
        if not detail_id and extract_festival_group_id(item.detail_url):
            detail_id = "festival:" + item.external_id
        if detail_id:
            grouped.setdefault(detail_id, []).append(item)
        else:
            without_detail_id.append(item)

    collapsed_ids: list[str] = []
    conflict_details: list[dict[str, Any]] = []
    resolution_details: list[dict[str, Any]] = []
    result = list(without_detail_id)
    for detail_id, group in grouped.items():
        if len(group) == 1 and not group[0].postponed_to:
            result.append(group[0])
            continue
        merged, detail = _merge_duplicate_match_group(detail_id, group, resolver)
        if merged is None:
            conflict_details.append(detail)
            continue
        collapsed_ids.append(detail_id)
        if detail.get("resolved"):
            resolution_details.append(detail)
        result.append(merged)

    return (
        result,
        sorted(collapsed_ids),
        sorted(conflict_details, key=lambda item: str(item.get("detail_id") or "")),
        sorted(resolution_details, key=lambda item: str(item.get("detail_id") or "")),
    )

def parse_club_matchplan(
    html_text: str,
    source_url: str,
    config: dict[str, Any],
    audit: dict[str, Any] | None = None,
    duplicate_resolver: Callable[[str, list[Match], dict[str, Any]], Match | None] | None = None,
) -> list[Match]:
    soup = BeautifulSoup(html_text, "lxml")
    competition_rows = soup.select(
        "tr.row-competition, tr.row-festival, tr.row-tournament"
    )
    if not competition_rows:
        # Leere Zeitfenster sind zulässig, solange eine Spielplantabelle vorhanden ist.
        if "club-matchplan-table" in html_text or soup.select_one(".club-matchplan-table"):
            if audit is not None:
                audit.update({
                    "source_url": source_url,
                    "competition_rows": 0,
                    "source_detail_ids": 0,
                    "parsed_matches": 0,
                    "parsed_detail_ids": 0,
                    "missing_detail_ids": [],
                    "duplicate_detail_ids": [],
                    "has_more": has_more_results(html_text),
                })
            return []
        raise ScrapeError("Keine Vereinsspielplan-Tabelle gefunden")

    club_team_pattern = str(config.get("club_team_pattern") or "")

    matches: list[Match] = []
    for competition_row in competition_rows:
        rows = block_rows(competition_row)
        block_text = normalize_space(" ".join(row_text(row) for row in rows))
        kickoff = kickoff_for_competition_row(competition_row, rows)
        warnings: list[str] = []
        if kickoff is None:
            warnings.append("Datum oder Anstoßzeit nicht eindeutig lesbar")

        club_elements = select_from_rows(rows, ".club-name")
        clubs = unique_texts(club_elements)
        normalized_block = normalize_match_text(block_text)
        is_festival = bool(
            re.search(
                r"\b(?:kinderfussball|festival|spielfest|spielenachmittag)\b",
                normalized_block,
            )
        )
        if len(clubs) < 2:
            fallback_elements = select_from_rows(rows, '[class*="club"]')
            clubs = [value for value in unique_texts(fallback_elements) if len(value) <= 120][:2]
            club_elements = fallback_elements[:2]
        home_team = clubs[0] if clubs else ""
        away_team = clubs[1] if len(clubs) > 1 else ""
        if home_team and not away_team and is_festival:
            away_team = "Kinderfußball-Festival"
        if len(clubs) == 1 and "spielfrei" in normalize_match_text(block_text):
            away_team = "spielfrei"
        if not home_team or not away_team:
            warnings.append("Heim- oder Gastmannschaft fehlt")

        links = select_from_rows(rows, 'a[href*="/spiel/"]')
        detail_url = ""
        for link in links:
            href = str(link.get("href") or "")
            if href:
                detail_url = urljoin(BASE_URL, href)
                break

        if not detail_url and is_festival:
            for link in select_from_rows(rows, 'a[href*="/staffel/"]'):
                candidate = urljoin(BASE_URL, str(link.get("href") or ""))
                if extract_festival_group_id(candidate):
                    detail_url = candidate
                    break

        detail_id = extract_detail_id(detail_url)
        number_match = re.search(r"\b(\d{9})\b", block_text)
        match_number = number_match.group(1) if number_match else ""
        external_id = detail_id or match_number or extract_festival_group_id(detail_url)
        if not external_id:
            seed = f"{kickoff}|{home_team}|{away_team}"
            external_id = hashlib.sha256(seed.encode("utf-8")).hexdigest()[:24]
            warnings.append("Keine offizielle Spiel-ID gefunden; Ersatz-ID gebildet")

        team_category = extract_team_category(competition_row)
        team_name, team_id, team_role = determine_club_team(
            club_elements,
            home_team,
            away_team,
            team_category,
            club_team_pattern,
            is_festival=is_festival,
        )
        if team_role == "unknown":
            warnings.append("Vereinsmannschaft formal nicht eindeutig zugeordnet")

        code_match = re.search(r"\b(ME|PO|FS|FR|TU|PR)\b", block_text)
        match_type = code_match.group(1) if code_match else ""
        venue = extract_venue(rows)
        if not venue:
            warnings.append("Spielstätte fehlt")

        match = Match(
            external_id=external_id,
            match_number=match_number,
            team_id=team_id,
            team_name=team_name,
            team_category=team_category,
            team_role=team_role,
            kickoff=kickoff.isoformat(timespec="minutes") if kickoff else "",
            home_team=home_team,
            away_team=away_team,
            competition=extract_competition(competition_row),
            match_type=match_type,
            status=extract_status(block_text),
            venue_raw=venue,
            detail_url=detail_url,
            source_url=source_url,
            warnings=warnings,
            home_team_id=team_id_for_club_element(club_elements[0]) if club_elements else "",
            away_team_id=(
                team_id_for_club_element(club_elements[1])
                if len(club_elements) > 1 else ""
            ),
            postponed_to=postponed_kickoff(rows, detail_id),
        )
        if kickoff:
            recalculate_event_times(match, config)
        matches.append(match)

    festival_round_assignments = apply_festival_round_assignment_rules(matches, config)

    source_ids = source_detail_ids(soup)
    parsed_ids_before_merge = {
        extract_detail_id(match.detail_url)
        for match in matches
        if extract_detail_id(match.detail_url)
    }
    missing_ids = sorted(source_ids - parsed_ids_before_merge)
    source_festivals = {
        extract_festival_group_id(str(link.get("href") or ""))
        for link in soup.select('a[href*="/staffel/"]')
    } - {""}
    parsed_festivals = {
        extract_festival_group_id(item.detail_url) for item in matches
    } - {""}
    missing_festivals = sorted(source_festivals - parsed_festivals)
    (
        merged_matches,
        collapsed_duplicate_ids,
        duplicate_conflicts,
        duplicate_resolutions,
    ) = collapse_duplicate_detail_ids(matches, duplicate_resolver)
    conflicting_duplicate_ids = sorted(
        str(item.get("detail_id") or "")
        for item in duplicate_conflicts
        if item.get("detail_id")
    )
    parsed_ids_after_merge = {
        extract_detail_id(match.detail_url)
        for match in merged_matches
        if extract_detail_id(match.detail_url)
    }
    if audit is not None:
        audit.update({
            "source_url": source_url,
            "source_scope": "all club teams; inclusion decided only by venue",
            "competition_rows": len(competition_rows),
            "source_detail_ids": len(source_ids),
            "parsed_matches_before_duplicate_merge": len(matches),
            "parsed_matches": len(merged_matches),
            "parsed_detail_ids": len(parsed_ids_after_merge),
            "missing_detail_ids": missing_ids,
            "source_festival_groups": len(source_festivals),
            "missing_festival_groups": missing_festivals,
            "festival_round_assignments": festival_round_assignments,
            "duplicate_detail_ids": conflicting_duplicate_ids,
            "collapsed_duplicate_detail_ids": collapsed_duplicate_ids,
            "duplicate_conflicts": duplicate_conflicts,
            "duplicate_resolutions": duplicate_resolutions,
            "has_more": has_more_results(html_text),
        })
    if missing_ids:
        raise ScrapeError(
            f"Unvollständiger Vereinsspielplan-Parser: {len(missing_ids)} Spiel-Link(s) nicht verarbeitet: "
            + ", ".join(missing_ids)
        )
    if missing_festivals:
        raise ScrapeError("Nicht verarbeitete Festival-Links: " + ", ".join(missing_festivals))
    if duplicate_conflicts:
        descriptions = []
        for item in duplicate_conflicts:
            fields = ", ".join(sorted((item.get("conflicts") or {}).keys()))
            descriptions.append(f"{item.get('detail_id')} ({fields})")
        raise ScrapeError(
            "Widersprüchliche Mehrfachdarstellungen derselben Spiel-ID: "
            + "; ".join(descriptions)
        )
    return merged_matches


def apply_venue_rules(
    match: Match,
    rules: list[VenueRule],
    default_decision: str,
    local_venue_pattern: str = "",
) -> None:
    if "spielfrei" in normalize_match_text(match.home_team + " " + match.away_team):
        match.decision = "exclude"
        match.calendar = ""
        match.venue_rule = "Spielfrei"
        match.warnings = [warning for warning in match.warnings if warning != "Spielstätte fehlt"]
    else:
        normalized_venue = normalize_space(match.venue_raw)
        for rule in rules:
            if rule.compiled().search(normalized_venue):
                match.decision = rule.decision
                match.calendar = rule.calendar
                match.venue_rule = rule.name
                break
        else:
            if not match.venue_raw and match.team_role == "away":
                # Ein formal als Auswärtsspiel geführter Termin ohne bereits
                # veröffentlichte Spielstätte ist keine Platzbelegung in
                # Schönwalde. Sobald der Verband später doch einen lokalen
                # Platz einträgt, greifen bei der nächsten Abfrage weiterhin
                # die normalen, strengeren Spielstättenregeln.
                match.decision = "exclude"
                match.calendar = ""
                match.venue_rule = "Auswärtsspiel ohne Spielstätte"
                match.warnings = [
                    warning
                    for warning in match.warnings
                    if warning != "Spielstätte fehlt"
                ]
            elif not match.venue_raw:
                match.decision = "review"
                match.calendar = ""
                match.venue_rule = "Spielstätte fehlt"
                match.warnings.append("Keine automatische Platzzuordnung möglich")
            elif local_venue_pattern and re.search(local_venue_pattern, normalized_venue, re.IGNORECASE):
                match.decision = "review"
                match.calendar = ""
                match.venue_rule = "Lokale Spielstätte unklar"
                match.warnings.append("Schönwalder Spielstätte erkannt, Platz 1/2 aber nicht eindeutig")
            else:
                match.decision = "exclude"
                match.calendar = ""
                match.venue_rule = "Auswärtige Spielstätte"

    checksum_payload = {
        "kickoff": match.kickoff,
        "home_team": match.home_team,
        "away_team": match.away_team,
        "status": match.status,
        "venue": match.venue_raw,
        "calendar": match.calendar,
        "decision": match.decision,
    }
    match.checksum = hashlib.sha256(
        json.dumps(checksum_payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
    ).hexdigest()


def deduplicate(matches: list[Match]) -> list[Match]:
    result: dict[str, Match] = {}
    for match in matches:
        existing = result.get(match.external_id)
        if existing is None:
            result[match.external_id] = match
            continue
        current_score = bool(match.venue_raw) + bool(match.detail_url) + bool(match.kickoff)
        existing_score = bool(existing.venue_raw) + bool(existing.detail_url) + bool(existing.kickoff)
        if current_score > existing_score:
            result[match.external_id] = match
    return sorted(result.values(), key=lambda item: (item.kickoff or "9999", item.external_id))


def iso_to_ics(value: str) -> str:
    dt = datetime.fromisoformat(value)
    return dt.strftime("%Y%m%dT%H%M%S")


def ics_escape(value: str) -> str:
    return str(value or "").replace("\\", "\\\\").replace(";", "\\;").replace(",", "\\,").replace("\n", "\\n")


def write_ics(path: Path, matches: list[Match], calendar_name: str) -> None:
    lines = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "PRODID:-//SSV53//DFBnet PoC//DE",
        "CALSCALE:GREGORIAN",
        "METHOD:PUBLISH",
        f"X-WR-CALNAME:{ics_escape(calendar_name)}",
        "X-WR-TIMEZONE:Europe/Berlin",
    ]
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    for match in matches:
        if not match.event_start or not match.event_end:
            continue
        lines.extend([
            "BEGIN:VEVENT",
            f"UID:dfb-{ics_escape(match.external_id)}@ssv53.de",
            f"DTSTAMP:{stamp}",
            f"DTSTART;TZID=Europe/Berlin:{iso_to_ics(match.event_start)}",
            f"DTEND;TZID=Europe/Berlin:{iso_to_ics(match.event_end)}",
            f"SUMMARY:{ics_escape(match.team_name + ': ' + match.home_team + ' – ' + match.away_team)}",
            f"LOCATION:{ics_escape(match.venue_raw)}",
            f"DESCRIPTION:{ics_escape(match.competition + ' | ' + match.detail_url)}",
            "END:VEVENT",
        ])
    lines.append("END:VCALENDAR")
    path.write_text("\r\n".join(lines) + "\r\n", encoding="utf-8")


def load_previous_registry(path: Path | None) -> dict[str, Any]:
    if path is None or not path.exists():
        return {"teams": []}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {"teams": []}
    except (OSError, json.JSONDecodeError):
        return {"teams": []}


def registry_key(team_id: str, team_name: str, team_category: str) -> str:
    if team_id and not team_id.startswith("auto-"):
        return team_id
    basis = normalize_match_text(team_name or team_category or team_id)
    return "auto-" + hashlib.sha256(basis.encode("utf-8")).hexdigest()[:16]


def build_team_registry(
    matches: list[Match],
    previous: dict[str, Any],
    club_id: str,
) -> dict[str, Any]:
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    previous_items = {
        str(item.get("key") or registry_key(str(item.get("team_id") or ""), str(item.get("name") or ""), str(item.get("category") or ""))): item
        for item in previous.get("teams", [])
        if isinstance(item, dict)
    }
    current: dict[str, dict[str, str]] = {}
    for match in matches:
        key = registry_key(match.team_id, match.team_name, match.team_category)
        current[key] = {
            "key": key,
            "team_id": match.team_id,
            "name": match.team_name,
            "category": match.team_category,
        }

    merged: dict[str, dict[str, Any]] = {}
    for key, old in previous_items.items():
        merged[key] = dict(old)
    for key, item in current.items():
        old = previous_items.get(key, {})
        merged[key] = {
            **item,
            "first_seen_at": str(old.get("first_seen_at") or now),
            "last_seen_at": now,
        }

    new_keys = sorted(set(current) - set(previous_items))
    known_keys = sorted(set(current) & set(previous_items))
    not_seen_keys = sorted(set(previous_items) - set(current))
    return {
        "updated_at": now,
        "club_id": club_id,
        "teams": sorted(merged.values(), key=lambda item: (normalize_match_text(str(item.get("name") or "")), str(item.get("key") or ""))),
        "changes": {
            "new": [current[key]["name"] for key in new_keys],
            "known": [current[key]["name"] for key in known_keys],
            "not_seen_in_current_run": [str(previous_items[key].get("name") or key) for key in not_seen_keys],
        },
    }


def write_outputs(
    output_dir: Path,
    matches: list[Match],
    quality_report: dict[str, Any],
    registry: dict[str, Any],
) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    groups = {
        "all_matches": matches,
        "included_matches": [m for m in matches if m.decision == "include"],
        "review_matches": [m for m in matches if m.decision == "review"],
        "excluded_matches": [m for m in matches if m.decision == "exclude"],
    }
    for name, items in groups.items():
        (output_dir / f"{name}.json").write_text(
            json.dumps([asdict(item) for item in items], ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    csv_fields = list(asdict(matches[0]).keys()) if matches else list(Match.__dataclass_fields__.keys())
    with (output_dir / "appack_preview.csv").open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(handle, fieldnames=csv_fields)
        writer.writeheader()
        for match in matches:
            row = asdict(match)
            row["warnings"] = " | ".join(match.warnings)
            writer.writerow(row)

    for calendar_name in ("Rasen", "Kunstrasen"):
        items = [m for m in matches if m.decision == "include" and m.calendar == calendar_name]
        write_ics(output_dir / f"{calendar_name.lower()}.ics", items, f"SSV53 {calendar_name} – Spiele")

    by_team = {
        team: {
            "total": sum(1 for m in matches if m.team_name == team),
            "included": sum(1 for m in matches if m.team_name == team and m.decision == "include"),
            "excluded": sum(1 for m in matches if m.team_name == team and m.decision == "exclude"),
            "review": sum(1 for m in matches if m.team_name == team and m.decision == "review"),
        }
        for team in sorted({m.team_name for m in matches})
    }
    summary = {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "selection_mode": "club-wide venue",
        "event_timing": quality_report.get("event_timing", {}),
        "total": len(matches),
        "included": len(groups["included_matches"]),
        "review": len(groups["review_matches"]),
        "excluded": len(groups["excluded_matches"]),
        "by_calendar": {
            "Rasen": sum(1 for m in matches if m.calendar == "Rasen" and m.decision == "include"),
            "Kunstrasen": sum(1 for m in matches if m.calendar == "Kunstrasen" and m.decision == "include"),
        },
        "by_team": by_team,
        "teams_discovered": sorted({m.team_name for m in matches}),
        "team_registry_changes": registry.get("changes", {}),
        "publishable": bool(quality_report.get("publishable")),
        "quality_errors": list(quality_report.get("errors", [])),
        "request_count": quality_report.get("request_count", 0),
        "accepted_windows": quality_report.get("accepted_windows", []),
    }
    (output_dir / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    (output_dir / "quality_report.json").write_text(json.dumps(quality_report, ensure_ascii=False, indent=2), encoding="utf-8")
    (output_dir / "team_registry.json").write_text(json.dumps(registry, ensure_ascii=False, indent=2), encoding="utf-8")


def windows_cover_range(
    windows: list[dict[str, Any]], date_from: str, date_to: str
) -> bool:
    accepted = sorted(
        (parse_iso_date(str(item["date_from"]), "date_from"), parse_iso_date(str(item["date_to"]), "date_to"))
        for item in windows
        if item.get("accepted")
    )
    if not accepted:
        return False
    expected = parse_iso_date(date_from, "date_from")
    final = parse_iso_date(date_to, "date_to")
    for start, end in accepted:
        if start != expected or end < start:
            return False
        expected = end + timedelta(days=1)
    return expected == final + timedelta(days=1)


def evaluate_quality(
    matches: list[Match],
    window_audits: list[dict[str, Any]],
    config: dict[str, Any],
    request_count: int,
) -> dict[str, Any]:
    guard = config.get("quality_guard", {})
    errors: list[str] = []
    invalid_included: list[dict[str, Any]] = []

    for match in matches:
        if match.decision != "include":
            continue
        missing = []
        for field_name in ("kickoff", "event_start", "event_end", "home_team", "away_team", "venue_raw", "team_name"):
            if not getattr(match, field_name):
                missing.append(field_name)
        if not match.detail_url or not extract_detail_id(match.detail_url):
            missing.append("detail_id")
        if missing:
            invalid_included.append({
                "external_id": match.external_id,
                "team": match.team_name,
                "missing": missing,
            })
    if invalid_included:
        errors.append(f"{len(invalid_included)} aufzunehmende Spiele sind unvollständig.")

    if bool(guard.get("require_no_review", True)):
        review_count = sum(1 for match in matches if match.decision == "review")
        if review_count:
            errors.append(f"{review_count} Spiele benötigen noch eine Platzprüfung.")

    for audit in window_audits:
        label = f"{audit.get('date_from')}–{audit.get('date_to')}"
        if audit.get("missing_detail_ids"):
            errors.append(f"{label}: nicht verarbeitete Spiel-IDs vorhanden.")
        if audit.get("duplicate_detail_ids"):
            errors.append(f"{label}: doppelte Spiel-IDs vorhanden.")
        if audit.get("accepted") and audit.get("truncated"):
            errors.append(f"{label}: möglicherweise gekürzte Antwort wurde fälschlich akzeptiert.")

    date_from = str(config.get("date_from") or "")
    date_to = str(config.get("date_to") or "")
    if not windows_cover_range(window_audits, date_from, date_to):
        errors.append("Die akzeptierten Zeitfenster decken den Saisonzeitraum nicht lückenlos ab.")

    return {
        "publishable": not errors,
        "errors": errors,
        "invalid_included": invalid_included,
        "by_calendar": {
            "Rasen": sum(1 for m in matches if m.decision == "include" and m.calendar == "Rasen"),
            "Kunstrasen": sum(1 for m in matches if m.decision == "include" and m.calendar == "Kunstrasen"),
        },
        "event_timing": {
            "before_minutes": int(config.get("event_timing", {}).get("before_minutes", 60)),
            "after_minutes": int(config.get("event_timing", {}).get("after_minutes", 60)),
            "default_match_duration_minutes": int(config.get("event_timing", {}).get("default_match_duration_minutes", 90)),
        },
        "request_count": request_count,
        "accepted_windows": [
            {"date_from": item.get("date_from"), "date_to": item.get("date_to"), "competition_rows": item.get("competition_rows", 0)}
            for item in window_audits
            if item.get("accepted")
        ],
        "window_audits": window_audits,
    }


def load_config(path: Path) -> dict[str, Any]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise SystemExit(f"Konfiguration kann nicht gelesen werden: {exc}") from exc


def write_failed_teams(output_dir: Path, failed: list[dict[str, str]]) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "failed_teams.json").write_text(
        json.dumps(failed, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def run(
    config_path: Path,
    output_dir: Path,
    state_path: Path | None = None,
    registry_path: Path | None = None,
) -> int:
    config = load_config(config_path)
    client = Client(config, state_path=state_path)
    run_status = "failed"
    failed: list[dict[str, str]] = []
    accepted_matches: list[Match] = []
    window_audits: list[dict[str, Any]] = []

    try:
        client.assert_not_blocked()
        date_from = str(config.get("date_from") or "")
        date_to = str(config.get("date_to") or "")
        parse_iso_date(date_from, "date_from")
        parse_iso_date(date_to, "date_to")

        months = int(config.get("adaptive_windows", {}).get("initial_window_months", 3))
        queue = build_initial_windows(date_from, date_to, months)
        response_limit = int(config.get("request", {}).get("max_rows_per_response", 50))
        max_depth = int(config.get("adaptive_windows", {}).get("max_split_depth", 8))
        work_queue: list[tuple[str, str, int]] = [(start, end, 0) for start, end in queue]

        rules = [VenueRule(**item) for item in config.get("venue_rules", [])]
        default_decision = str(config.get("default_decision", "exclude"))
        local_venue_pattern = str(config.get("local_venue_pattern") or "")
        save_raw = bool(config.get("diagnostics", {}).get("save_raw_responses", True))
        raw_output_dir = output_dir / "raw" if save_raw else None
        duplicate_resolver = build_duplicate_detail_resolver(
            client, config, raw_output_dir
        )

        while work_queue:
            window_from, window_to, depth = work_queue.pop(0)
            LOG.info("Lade Vereinsspielplan %s bis %s", window_from, window_to)
            body, source_url = fetch_club_matchplan(client, config, window_from, window_to)
            if raw_output_dir is not None:
                raw_output_dir.mkdir(parents=True, exist_ok=True)
                (raw_output_dir / f"club_{window_from}_{window_to}.html").write_text(
                    body, encoding="utf-8"
                )
            audit: dict[str, Any] = {
                "date_from": window_from,
                "date_to": window_to,
                "depth": depth,
            }
            window_matches = parse_club_matchplan(
                body,
                source_url,
                config,
                audit=audit,
                duplicate_resolver=duplicate_resolver,
            )
            audit["request_count_after_parse"] = client.request_count
            truncated = bool(audit.get("has_more")) or int(audit.get("competition_rows", 0)) >= response_limit
            audit["truncated"] = truncated

            if truncated:
                children = split_window(window_from, window_to)
                if not children or depth >= max_depth:
                    audit["accepted"] = False
                    window_audits.append(audit)
                    raise ScrapeError(
                        f"Zeitfenster {window_from}–{window_to} bleibt trotz Teilung möglicherweise gekürzt."
                    )
                if client.request_count + len(work_queue) + 2 > client.max_requests:
                    audit["accepted"] = False
                    window_audits.append(audit)
                    raise RequestBudgetExceeded(
                        "Die automatische Zeitfensterteilung würde die harte Obergrenze von "
                        f"{client.max_requests} Requests überschreiten."
                    )
                audit["accepted"] = False
                audit["split_into"] = [
                    {"date_from": start, "date_to": end} for start, end in children
                ]
                window_audits.append(audit)
                work_queue = [(start, end, depth + 1) for start, end in children] + work_queue
                continue

            for match in window_matches:
                if match.kickoff and not (window_from <= match.kickoff[:10] <= window_to):
                    continue
                apply_venue_rules(match, rules, default_decision, local_venue_pattern)
                accepted_matches.append(match)
            audit["accepted"] = True
            window_audits.append(audit)

        matches = deduplicate(accepted_matches)
        previous_registry = load_previous_registry(registry_path)
        registry = build_team_registry(matches, previous_registry, extract_club_id(config))
        quality = evaluate_quality(matches, window_audits, config, client.request_count)
        write_outputs(output_dir, matches, quality, registry)
        write_failed_teams(output_dir, failed)

        if not quality["publishable"]:
            run_status = "quality_failed"
            LOG.error("Kein neuer Feed wird veröffentlicht: %s", " | ".join(quality["errors"]))
            return 2

        run_status = "success"
        LOG.info(
            "Fertig: %s Spiele aus %s Mannschaften, %s HTTP-Abrufe",
            len(matches),
            len(registry.get("changes", {}).get("new", [])) + len(registry.get("changes", {}).get("known", [])),
            client.request_count,
        )
        return 0

    except (RateLimitError, SecurityLockError, RequestBudgetExceeded, ScrapeError) as exc:
        LOG.error("Lauf beendet: %s", exc)
        failed.append({"team": "Vereinsspielplan", "team_id": extract_club_id(config), "error": str(exc)})
        quality = {
            "publishable": False,
            "errors": [str(exc)],
            "request_count": client.request_count,
            "window_audits": window_audits,
            "event_timing": config.get("event_timing", {}),
        }
        registry = build_team_registry([], load_previous_registry(registry_path), extract_club_id(config))
        write_outputs(output_dir, deduplicate(accepted_matches), quality, registry)
        write_failed_teams(output_dir, failed)
        if isinstance(exc, RateLimitError):
            run_status = "rate_limited"
        elif isinstance(exc, SecurityLockError):
            run_status = "security_locked"
        elif isinstance(exc, RequestBudgetExceeded):
            run_status = "request_budget_exceeded"
        else:
            run_status = "failed"
        return 2
    finally:
        client.finish_run(run_status)


def main() -> int:
    parser = argparse.ArgumentParser(description="SSV53 FUSSBALL.DE Vereinsspielplan PoC")
    parser.add_argument("--config", default="config.json", type=Path)
    parser.add_argument("--output", default=Path("output"), type=Path)
    parser.add_argument(
        "--state",
        default=Path("state/request_state.json"),
        type=Path,
        help="Persistenter Schutzstatus für 429 sowie 403/406/Challenge-Sperren",
    )
    parser.add_argument(
        "--team-registry",
        default=Path("state/team_registry.json"),
        type=Path,
        help="Bisher erkannte Vereinsmannschaften",
    )
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args()
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )
    return run(args.config, args.output, state_path=args.state, registry_path=args.team_registry)


if __name__ == "__main__":
    sys.exit(main())
