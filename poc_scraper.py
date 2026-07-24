#!/usr/bin/env python3
"""SSV53 PoC: FUSSBALL.DE/DFBnet-Heimspiele mit Spielstätte auslesen.

Der PoC schreibt bewusst noch nicht in Appack. Er erzeugt stattdessen eine
prüfbare Vorschau als JSON/CSV sowie getrennte ICS-Dateien für Rasen und
Kunstrasen.
"""
from __future__ import annotations

import argparse
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
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup, Tag

LOG = logging.getLogger("ssv53-dfbnet-poc")
BASE_URL = "https://www.fussball.de"
DATE_TIME_PATTERNS = (
    re.compile(r"(?P<date>\d{2}\.\d{2}\.\d{4})\s*(?:-|,)?\s*(?P<time>\d{2}:\d{2})"),
    re.compile(r"(?P<date>\d{2}\.\d{2}\.\d{2})\s*(?:-|,)?\s*(?P<time>\d{2}:\d{2})"),
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
class Team:
    name: str
    team_id: str
    lead_minutes: int = 90
    post_kickoff_minutes: int = 150
    home_aliases: list[str] = field(default_factory=list)


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


class Client:
    """Zurückhaltender HTTP-Client mit fest eingebauten Schutzgrenzen."""

    ABSOLUTE_MAX_REQUESTS = 10
    ABSOLUTE_MAX_RETRIES = 1
    MIN_DELAY_SECONDS = 3.0
    DEFAULT_RATE_LIMIT_BLOCK_SECONDS = 6 * 60 * 60
    RETRYABLE_STATUS_CODES = {502, 503, 504}
    SECURITY_STATUS_CODES = {403, 406}
    CHALLENGE_MARKERS = (
        "cf-chl-",
        "/cdn-cgi/challenge-platform/",
        "challenge-platform",
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
                "SSV53-Belegungsplan-PoC/5.0 "
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

        for marker in cls.CHALLENGE_MARKERS:
            if marker in sample:
                return f"Challenge-Marker erkannt: {marker}"

        # Zusätzliche strukturelle Signale typischer Challenge-Seiten. Ein
        # einzelner Cloudflare-Header reicht bewusst nicht aus, da reguläre
        # Seiten ebenfalls über ein CDN ausgeliefert werden können.
        if "cloudflare" in server and (
            "<title>just a moment" in sample
            or "cf-ray" in str(response.headers).casefold() and "challenge" in sample
        ):
            return "Cloudflare-Sicherheitsseite erkannt"

        if response.status_code == 200 and "text/html" in content_type:
            title_match = re.search(r"<title[^>]*>(.*?)</title>", sample, re.DOTALL)
            title = normalize_space(title_match.group(1)) if title_match else ""
            if title in {"forbidden", "not acceptable", "access denied"}:
                return f"Sicherheitsseite anhand Seitentitel erkannt: {title}"

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
            return datetime.strptime(f"{raw_date} {match.group('time')}", fmt)
        except ValueError:
            continue
    return None


def extract_team_id(url: str) -> str:
    match = re.search(r"/team-id/([A-Z0-9]+)", url, re.IGNORECASE)
    return match.group(1) if match else ""


def discover_teams(html_text: str, season_code: str) -> list[Team]:
    soup = BeautifulSoup(html_text, "lxml")
    found: dict[str, Team] = {}
    season_fragment = f"/saison/{season_code}/"
    for link in soup.select('a[href*="/mannschaft/"][href*="/team-id/"]'):
        href = str(link.get("href") or "")
        if season_fragment not in href:
            continue
        team_id = extract_team_id(href)
        if not team_id:
            continue
        name = normalize_space(link.get_text(" ", strip=True))
        if not name:
            container = link.find_parent(["h3", "h4", "div", "article"])
            name = normalize_space(container.get_text(" ", strip=True)) if container else team_id
        found.setdefault(team_id, Team(name=name or team_id, team_id=team_id))
    return list(found.values())


def primary_matchplan_url(
    config: dict[str, Any], team_id: str, date_from: str, date_to: str
) -> str:
    template = str(
        config.get("request", {}).get(
            "matchplan_endpoint_template",
            BASE_URL
            + "/ajax.team.matchplan/-/mode/PAGE/"
            + "mime-type/HTML/show-venues/true/match-type/1/"
            + "datum-von/{date_from}/datum-bis/{date_to}/team-id/{team_id}",
        )
    )
    return template.format(
        base_url=BASE_URL,
        team_id=team_id,
        date_from=date_from,
        date_to=date_to,
    )


def diagnostic_endpoint_candidates(
    team_id: str, date_from: str, date_to: str
) -> list[str]:
    common = "mime-type/HTML/show-venues/true/match-type/1"
    return [
        f"{BASE_URL}/ajax.team.matchplan/-/mode/PAGE/{common}/datum-von/{date_from}/datum-bis/{date_to}/team-id/{team_id}",
        f"{BASE_URL}/ajax.team.matchplan/-/{common}/datum-von/{date_from}/datum-bis/{date_to}/team-id/{team_id}",
        f"{BASE_URL}/ajax.team.matchplan/-/mode/PAGE/{common}/team-id/{team_id}",
        f"{BASE_URL}/ajax.team.matchplan/-/{common}/team-id/{team_id}",
        f"{BASE_URL}/ajax.team.matchplan/-/mime-type/HTML/show-venues/true/team-id/{team_id}",
    ]


def fetch_matchplan(
    client: Client,
    config: dict[str, Any],
    team: Team,
    date_from: str,
    date_to: str,
    diagnostic_endpoints: bool = False,
) -> tuple[str, str]:
    urls = (
        diagnostic_endpoint_candidates(team.team_id, date_from, date_to)
        if diagnostic_endpoints
        else [primary_matchplan_url(config, team.team_id, date_from, date_to)]
    )
    errors: list[str] = []
    for url in urls:
        try:
            body = client.get_text(url)
            if "row-competition" in body or "club-matchplan-table" in body:
                return body, url
            errors.append(f"{url}: erwartete Spielplanstruktur fehlt")
            if not diagnostic_endpoints:
                break
        except (RateLimitError, RequestBudgetExceeded):
            raise
        except ScrapeError as exc:
            errors.append(str(exc))
            if not diagnostic_endpoints:
                break
    mode = "Diagnose" if diagnostic_endpoints else "regulärer Endpunkt"
    raise ScrapeError(f"Kein funktionierender {mode}. " + " | ".join(errors))


def row_classes(row: Tag) -> set[str]:
    value = row.get("class") or []
    return {str(item) for item in value}


def block_rows(start_row: Tag) -> list[Tag]:
    rows = [start_row]
    sibling = start_row.find_next_sibling("tr")
    while sibling is not None and "row-competition" not in row_classes(sibling):
        rows.append(sibling)
        sibling = sibling.find_next_sibling("tr")
    return rows


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

    # Robuster Fallback: einzelne Zellen nach typischen Spielstättenbegriffen prüfen.
    for cell in select_from_rows(rows, "td, div"):
        text = normalize_space(cell.get_text(" ", strip=True))
        normalized = normalize_match_text(text)
        if any(token in normalized for token in ("sportplatz", "rasenplatz", "kunstrasen", "stadion")):
            if 5 <= len(text) <= 300:
                candidates.append(text)

    if not candidates:
        return ""
    # Der spezifischste, aber nicht ausufernde Kandidat gewinnt.
    candidates = sorted(set(candidates), key=lambda item: (len(item), item))
    for candidate in candidates:
        normalized = normalize_match_text(candidate)
        if "sportplatz" in normalized and ("rasen" in normalized or "platz" in normalized):
            return candidate
    return candidates[0]


def extract_competition(row: Tag) -> str:
    explicit = row.select_one('[class*="competition"], .column-competition')
    text = normalize_space(explicit.get_text(" ", strip=True)) if explicit else normalize_space(row.get_text(" ", strip=True))
    text = re.sub(r"\b\d{2}\.\d{2}\.\d{2,4}\b", " ", text)
    text = re.sub(r"\b\d{2}:\d{2}\b", " ", text)
    text = re.sub(r"\b\d{9}\b", " ", text)
    text = re.sub(r"\b(?:ME|PO|FS|FR|TU|PR)\b", " ", text)
    return normalize_space(text.strip(" -|"))


def extract_status(block_text: str) -> str:
    lower = block_text.casefold()
    for term in STATUS_TERMS:
        if term.casefold() in lower:
            return term
    return ""


def parse_matchplan(html_text: str, team: Team, source_url: str) -> list[Match]:
    soup = BeautifulSoup(html_text, "lxml")
    competition_rows = soup.select("tr.row-competition")
    if not competition_rows:
        raise ScrapeError(f"Keine Spielzeilen für {team.name} gefunden")

    matches: list[Match] = []
    for competition_row in competition_rows:
        rows = block_rows(competition_row)
        block_text = normalize_space(" ".join(row.get_text(" ", strip=True) for row in rows))
        kickoff = parse_datetime(block_text)
        warnings: list[str] = []
        if kickoff is None:
            warnings.append("Datum oder Anstoßzeit nicht eindeutig lesbar")

        clubs = unique_texts(select_from_rows(rows, ".club-name"))
        if len(clubs) < 2:
            # Fallback für geänderte Klassen: kurze Texte aus Club-Spalten.
            clubs = unique_texts(select_from_rows(rows, '[class*="club"]'))
            clubs = [value for value in clubs if len(value) <= 120][:2]
        home_team = clubs[0] if clubs else ""
        away_team = clubs[1] if len(clubs) > 1 else ""
        if not home_team or not away_team:
            warnings.append("Heim- oder Gastmannschaft fehlt")

        links = select_from_rows(rows, 'a[href*="/spiel/"]')
        detail_url = ""
        for link in links:
            href = str(link.get("href") or "")
            if href:
                detail_url = urljoin(BASE_URL, href)
                break

        id_match = re.search(r"/spiel/([A-Z0-9]+)", detail_url, re.IGNORECASE)
        detail_id = id_match.group(1) if id_match else ""
        number_match = re.search(r"\b(\d{9})\b", block_text)
        match_number = number_match.group(1) if number_match else ""
        external_id = detail_id or match_number
        if not external_id:
            seed = f"{team.team_id}|{kickoff}|{home_team}|{away_team}"
            external_id = hashlib.sha256(seed.encode("utf-8")).hexdigest()[:24]
            warnings.append("Keine offizielle Spiel-ID gefunden; Ersatz-ID gebildet")

        code_match = re.search(r"\b(ME|PO|FS|FR|TU|PR)\b", block_text)
        match_type = code_match.group(1) if code_match else ""
        venue = extract_venue(rows)
        if not venue:
            warnings.append("Spielstätte fehlt")

        match = Match(
            external_id=external_id,
            match_number=match_number,
            team_id=team.team_id,
            team_name=team.name,
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
        )
        if kickoff:
            match.event_start = (kickoff - timedelta(minutes=team.lead_minutes)).isoformat(timespec="minutes")
            match.event_end = (kickoff + timedelta(minutes=team.post_kickoff_minutes)).isoformat(timespec="minutes")
        matches.append(match)
    return matches


def apply_venue_rules(match: Match, rules: list[VenueRule], default_decision: str) -> None:
    normalized_venue = normalize_space(match.venue_raw)
    for rule in rules:
        if rule.compiled().search(normalized_venue):
            match.decision = rule.decision
            match.calendar = rule.calendar
            match.venue_rule = rule.name
            break
    else:
        match.decision = default_decision
        if not match.venue_raw:
            match.warnings.append("Keine automatische Platzzuordnung möglich")
        else:
            match.warnings.append("Unbekannte Spielstätte; manuelle Prüfung erforderlich")

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
        # Der Datensatz mit besserer Spielstätten-/Linkinformation gewinnt.
        current_score = bool(match.venue_raw) + bool(match.detail_url) + bool(match.kickoff)
        existing_score = bool(existing.venue_raw) + bool(existing.detail_url) + bool(existing.kickoff)
        if current_score > existing_score:
            result[match.external_id] = match
    return sorted(result.values(), key=lambda item: (item.kickoff or "9999", item.external_id))


def iso_to_ics(value: str) -> str:
    dt = datetime.fromisoformat(value)
    return dt.strftime("%Y%m%dT%H%M%S")


def ics_escape(value: str) -> str:
    return value.replace("\\", "\\\\").replace(";", "\\;").replace(",", "\\,").replace("\n", "\\n")


def write_ics(path: Path, matches: list[Match], calendar_name: str) -> None:
    lines = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "PRODID:-//SSV53//DFBnet PoC//DE",
        "CALSCALE:GREGORIAN",
        f"X-WR-CALNAME:{ics_escape(calendar_name)}",
    ]
    now = datetime.utcnow().strftime("%Y%m%dT%H%M%SZ")
    for match in matches:
        if not match.event_start or not match.event_end:
            continue
        title = f"Heimspiel {match.team_name}: {match.home_team} – {match.away_team}"
        description_parts = [match.competition, f"Spielnummer: {match.match_number}" if match.match_number else ""]
        if match.detail_url:
            description_parts.append(match.detail_url)
        lines.extend([
            "BEGIN:VEVENT",
            f"UID:fussball-{ics_escape(match.external_id)}@ssv53.de",
            f"DTSTAMP:{now}",
            f"DTSTART:{iso_to_ics(match.event_start)}",
            f"DTEND:{iso_to_ics(match.event_end)}",
            f"SUMMARY:{ics_escape(title)}",
            f"DESCRIPTION:{ics_escape(' | '.join(filter(None, description_parts)))}",
            f"URL:{match.detail_url}" if match.detail_url else "",
            "END:VEVENT",
        ])
    lines.append("END:VCALENDAR")
    path.write_text("\r\n".join(line for line in lines if line) + "\r\n", encoding="utf-8")


def write_outputs(output_dir: Path, matches: list[Match]) -> None:
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

    for calendar in ("Rasen", "Kunstrasen"):
        items = [m for m in matches if m.decision == "include" and m.calendar == calendar]
        write_ics(output_dir / f"{calendar.lower()}.ics", items, f"SSV53 {calendar} – Heimspiele")

    summary = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "total": len(matches),
        "included": len(groups["included_matches"]),
        "review": len(groups["review_matches"]),
        "excluded": len(groups["excluded_matches"]),
        "by_calendar": {
            "Rasen": sum(1 for m in matches if m.calendar == "Rasen" and m.decision == "include"),
            "Kunstrasen": sum(1 for m in matches if m.calendar == "Kunstrasen" and m.decision == "include"),
        },
    }
    (output_dir / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")


def load_config(path: Path) -> dict[str, Any]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise SystemExit(f"Konfiguration kann nicht gelesen werden: {exc}") from exc


def build_teams(config: dict[str, Any], client: Client) -> list[Team]:
    configured = [Team(**item) for item in config.get("teams", [])]
    if configured:
        return configured
    if not config.get("discover_teams", True):
        raise SystemExit("Keine Teams konfiguriert und automatische Erkennung deaktiviert")
    club_url = str(config.get("club_url") or "")
    season_code = str(config.get("season_code") or "")
    if not club_url or not season_code:
        raise SystemExit("club_url und season_code sind für die Team-Erkennung erforderlich")
    html_text = client.get_text(club_url)
    teams = discover_teams(html_text, season_code)
    if not teams:
        raise SystemExit("Keine Teams auf der Vereinsseite gefunden")
    return teams


def write_failed_teams(output_dir: Path, failed_teams: list[dict[str, str]]) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "failed_teams.json").write_text(
        json.dumps(failed_teams, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def run(
    config_path: Path,
    output_dir: Path,
    state_path: Path | None = None,
    diagnostic_endpoints: bool = False,
) -> int:
    config = load_config(config_path)
    client = Client(config, state_path=state_path)
    all_matches: list[Match] = []
    failed_teams: list[dict[str, str]] = []
    run_status = "failed"

    try:
        client.assert_not_blocked()
        teams = build_teams(config, client)
        date_from = str(config.get("date_from"))
        date_to = str(config.get("date_to"))
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", date_from) or not re.fullmatch(
            r"\d{4}-\d{2}-\d{2}", date_to
        ):
            raise SystemExit("date_from und date_to müssen YYYY-MM-DD verwenden")

        rules = [VenueRule(**item) for item in config.get("venue_rules", [])]
        default_decision = str(config.get("default_decision", "review"))

        for team in teams:
            LOG.info("Lade %s (%s)", team.name, team.team_id)
            try:
                body, source_url = fetch_matchplan(
                    client,
                    config,
                    team,
                    date_from,
                    date_to,
                    diagnostic_endpoints=diagnostic_endpoints,
                )
                matches = parse_matchplan(body, team, source_url)
                for match in matches:
                    if match.kickoff and not (
                        date_from <= match.kickoff[:10] <= date_to
                    ):
                        continue
                    apply_venue_rules(match, rules, default_decision)
                    all_matches.append(match)
            except (RateLimitError, SecurityLockError, RequestBudgetExceeded) as exc:
                LOG.error("Gesamter Lauf wird zum Schutz des Servers beendet: %s", exc)
                failed_teams.append({
                    "team": team.name,
                    "team_id": team.team_id,
                    "error": str(exc),
                })
                if isinstance(exc, RateLimitError):
                    run_status = "rate_limited"
                elif isinstance(exc, SecurityLockError):
                    run_status = "security_locked"
                else:
                    run_status = "request_budget_exceeded"
                break
            except ScrapeError as exc:
                LOG.error("Team fehlgeschlagen: %s", exc)
                failed_teams.append({
                    "team": team.name,
                    "team_id": team.team_id,
                    "error": str(exc),
                })

        matches = deduplicate(all_matches)
        write_outputs(output_dir, matches)
        write_failed_teams(output_dir, failed_teams)

        if failed_teams:
            if run_status == "failed":
                run_status = "partial_failure"
            LOG.error(
                "Kein neuer Feed wird veröffentlicht: %s Teamfehler", len(failed_teams)
            )
            return 2

        run_status = "success"
        LOG.info(
            "Fertig: %s Spiele, %s HTTP-Abrufe", len(matches), client.request_count
        )
        return 0

    except (RateLimitError, SecurityLockError, RequestBudgetExceeded) as exc:
        LOG.error("Lauf vor dem Teamabruf beendet: %s", exc)
        failed_teams.append({"team": "", "team_id": "", "error": str(exc)})
        write_outputs(output_dir, [])
        write_failed_teams(output_dir, failed_teams)
        if isinstance(exc, RateLimitError):
            run_status = "rate_limited"
        elif isinstance(exc, SecurityLockError):
            run_status = "security_locked"
        else:
            run_status = "request_budget_exceeded"
        return 2
    finally:
        client.finish_run(run_status)


def main() -> int:
    parser = argparse.ArgumentParser(description="SSV53 DFBnet/FUSSBALL.DE PoC")
    parser.add_argument("--config", default="config.json", type=Path)
    parser.add_argument("--output", default=Path("output"), type=Path)
    parser.add_argument(
        "--state",
        default=Path("state/request_state.json"),
        type=Path,
        help="Persistenter Schutzstatus für 429 sowie 403/406/Challenge-Sperren",
    )
    parser.add_argument(
        "--diagnostic-endpoints",
        action="store_true",
        help="Nur manuell verwenden: alternative Endpunkte bis zum Request-Limit testen",
    )
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args()
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )
    return run(
        args.config,
        args.output,
        state_path=args.state,
        diagnostic_endpoints=args.diagnostic_endpoints,
    )


if __name__ == "__main__":
    sys.exit(main())
