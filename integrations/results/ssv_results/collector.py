"""Bounded server-side collection, independent from the Appack web view."""
from __future__ import annotations

import base64
import copy
import hashlib
import json
import re
import time
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from typing import Any
from urllib.parse import urljoin, urlsplit
from urllib.robotparser import RobotFileParser

import requests

from .parsers import (
    BERLIN, HB_CLUB, HB_HOST, VB_HOST, VB_MAIN, ParseError, handball_matches,
    handball_schedule_url, handball_standings, handball_teams, safe_source_url,
    volleyball_links, volleyball_matches,
)

AGENT = "SSV53Results/1.0"
SPORTS = ("handball", "volleyball")


def timestamp(now: datetime | None = None) -> str:
    return (now or datetime.now(timezone.utc)).astimezone(timezone.utc).isoformat(timespec="seconds")


class FetchError(RuntimeError):
    def __init__(self, reason: str, retry_seconds: int = 3600):
        super().__init__(reason)
        self.retry_seconds = retry_seconds


class Fetcher:
    """No arbitrary URL proxy, parallel fetching, anti-bot bypass or login.

    A failed/forbidden robots request is NOT treated as permission. Explicit
    robots.txt 404 means no published robot rules; it is not a data-use licence.
    """
    def __init__(self, user_agent: str = AGENT):
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": user_agent, "Accept": "text/html,image/jpeg,image/png;q=0.8"})
        self.agent = user_agent
        self.robots: dict[str, RobotFileParser] = {}
        self.last_request: dict[str, float] = {}
        self.blocked: set[str] = set()
        self.request_count = 0
        self.started = time.monotonic()
        self.memo: dict[str, tuple[bytes, dict]] = {}

    def close(self) -> None:
        self.session.close()

    def _request(self, url: str, max_bytes: int) -> tuple[bytes, dict]:
        url = safe_source_url(url, url)
        original_host = urlsplit(url).hostname
        for redirect in range(3):
            host = urlsplit(url).hostname
            if host in self.blocked:
                raise FetchError("Quelle für diesen Abruf gesperrt.", 86400)
            if self.request_count >= 40 or time.monotonic() - self.started > 110:
                raise FetchError("Abrufbudget erreicht.")
            delay = 2.0
            rp = self.robots.get(host)
            if rp:
                delay = max(delay, float(rp.crawl_delay(self.agent) or rp.crawl_delay("*") or 0))
            wait = delay - (time.monotonic() - self.last_request.get(host, 0))
            if wait > 0:
                if time.monotonic() - self.started + wait > 110:
                    raise FetchError("Abrufbudget für Crawl-Delay reicht nicht aus.")
                time.sleep(wait)
            self.request_count += 1
            self.last_request[host] = time.monotonic()
            try:
                with self.session.get(url, timeout=(5, 12), allow_redirects=False, stream=True) as response:
                    status = response.status_code
                    if status in {403, 406, 429}:
                        self.blocked.add(host)
                        pause = 86400 if status != 429 else 3600
                        retry = response.headers.get("Retry-After", "")
                        if retry.isdigit():
                            pause = max(pause, int(retry))
                        elif retry:
                            try:
                                pause = max(pause, int((parsedate_to_datetime(retry) - datetime.now(timezone.utc)).total_seconds()))
                            except (ValueError, TypeError):
                                pass
                        raise FetchError(f"Quelle lehnt Abruf ab ({status}).", max(3600, pause))
                    if status in {301, 302, 303, 307, 308}:
                        target = safe_source_url(response.headers.get("Location", ""), url)
                        if target == url or urlsplit(target).hostname != original_host or redirect == 2:
                            raise FetchError("Nicht erlaubte Quellenweiterleitung.")
                        url = target
                        continue
                    if status == 404 and urlsplit(url).path == "/robots.txt":
                        return b"", {"status": 404, "content-type": "text/plain"}
                    if status != 200:
                        raise FetchError(f"Quelle vorübergehend nicht verfügbar ({status}).")
                    length = response.headers.get("Content-Length", "")
                    if length.isdigit() and int(length) > max_bytes:
                        raise FetchError("Quellenantwort zu groß.")
                    data = bytearray()
                    for chunk in response.iter_content(16384):
                        data.extend(chunk)
                        if len(data) > max_bytes or time.monotonic() - self.started > 110:
                            raise FetchError("Quellenantwort überschreitet Abrufbudget.")
                    return bytes(data), {k.lower(): v for k, v in response.headers.items()}
            except requests.RequestException as exc:
                raise FetchError("Quellenabruf fehlgeschlagen.") from exc
        raise FetchError("Zu viele Weiterleitungen.")

    def get(self, url: str, max_bytes: int = 2_000_000) -> tuple[bytes, dict]:
        url = safe_source_url(url, url)
        if url in self.memo:
            return self.memo[url]
        host = urlsplit(url).hostname
        if host not in self.robots:
            raw, headers = self._request(f"https://{host}/robots.txt", 150_000)
            rp = RobotFileParser()
            if headers.get("status") == 404:
                rp.parse(["User-agent: *", "Disallow:"])
            else:
                content = raw.decode("utf-8", errors="replace")
                if "<html" in content.casefold() or "<form" in content.casefold():
                    raise FetchError("robots.txt nicht zuverlässig lesbar.")
                rp.parse(content.splitlines())
            self.robots[host] = rp
        if not self.robots[host].can_fetch(self.agent, url):
            self.blocked.add(host)
            raise FetchError("robots.txt erlaubt diesen Abruf nicht.", 86400)
        data = self._request(url, max_bytes)
        self.memo[url] = data
        return data

    def html(self, url: str) -> str:
        data, headers = self.get(url)
        ctype = headers.get("content-type", "").lower()
        if "html" not in ctype:
            raise FetchError("Keine HTML-Antwort der Quelle.")
        charset = re.search(r"charset\s*=\s*[\"']?([a-zA-Z0-9_-]+)", ctype)
        meta_charset = re.search(br'charset\s*=\s*["\']?\s*([A-Za-z0-9_-]+)', data[:10000], re.I)
        encoding = charset[1] if charset else (meta_charset[1].decode("ascii") if meta_charset else "utf-8")
        try:
            result = data.decode(encoding)
        except (LookupError, UnicodeDecodeError):
            # lxml/BeautifulSoup may recover legacy declarations, but no guessing
            # is preferable here: a broken name can select the wrong team.
            raise FetchError("Zeichenkodierung der Quelle nicht sicher lesbar.")
        challenge = re.search(r"cf-chl-|verify you are human|captcha-container|access denied", result, re.I)
        if challenge:
            self.blocked.add(urlsplit(url).hostname)
            raise FetchError("Quellenschutz erkannt; kein Umgehungsversuch.", 86400)
        return result

    def image(self, url: str) -> tuple[str, str]:
        data, _ = self.get(url, 1_000_000)
        if data.startswith(b"\xff\xd8\xff"):
            mime = "image/jpeg"
        elif data.startswith(b"\x89PNG\r\n\x1a\n"):
            mime = "image/png"
        elif data.startswith(b"RIFF") and data[8:12] == b"WEBP":
            mime = "image/webp"
        else:
            raise FetchError("Tabellengrafik hat kein unterstütztes Bildformat.")
        return "data:" + mime + ";base64," + base64.b64encode(data).decode("ascii"), hashlib.sha256(data).hexdigest()


def old_team(previous: dict | None, team_id: str) -> dict | None:
    return next((t for t in (previous or {}).get("teams", []) if t["id"] == team_id), None)


def check_loss(previous: dict | None, current: dict) -> None:
    if not previous or previous.get("season") != current.get("season") or current.get("teamStatus") == "withdrawn":
        return
    before = len(previous.get("matches", []))
    after = len(current.get("matches", []))
    if before >= 5 and after < before * 0.7:
        raise ParseError("Auffälliger Verlust von Spielansetzungen.")
    if (previous.get("standings", {}).get("kind") in {"table", "participants"}
            and current.get("standings", {}).get("kind") == "unavailable"):
        raise ParseError("Bisher vorhandene Tabelle fehlt unerwartet.")


def unavailable_team(team: dict, prior: dict | None) -> dict:
    result = copy.deepcopy(prior or team)
    result.update({"stale": True, "warning": "Quelle konnte nicht aktualisiert werden."})
    result.setdefault("matches", [])
    result.setdefault("fetchedAt", None)
    result.setdefault("standings", {"kind": "unavailable", "rows": [], "message": "Daten vorübergehend nicht verfügbar."})
    return result


def collect_handball(fetcher: Fetcher, season_start: int, now: str, previous: dict | None) -> list[dict]:
    teams = handball_teams(fetcher.html(HB_CLUB), season_start)
    result = []
    for team in teams:
        prior = old_team(previous, team["id"])
        try:
            html = fetcher.html(team["sourceUrl"])
            standings = handball_standings(html)
            withdrawn = next((r for r in standings.get("rows", []) if r.get("isSSV") and r.get("status") == "withdrawn"), None)
            if withdrawn:
                matches = []
            else:
                schedule_url = handball_schedule_url(html, team["sourceUrl"])
                schedule_html = html if schedule_url == team["sourceUrl"] else fetcher.html(schedule_url)
                matches = handball_matches(schedule_html, team["groupId"], schedule_url,
                                           season_start, festivals=standings["kind"] == "participants")
            item = {**team, "standings": standings, "matches": matches, "fetchedAt": now,
                    "stale": False, "teamStatus": "withdrawn" if withdrawn else "active",
                    "teamNote": withdrawn.get("note", "") if withdrawn else ""}
            check_loss(prior, item)
            result.append(item)
        except (FetchError, ParseError) as exc:
            failed = unavailable_team(team, prior)
            failed["retrySeconds"] = getattr(exc, "retry_seconds", 3600)
            result.append(failed)
    known = {t["id"] for t in result}
    for old in (previous or {}).get("teams", []):
        if old["id"] not in known and old.get("season") == teams[0]["season"]:
            retained = unavailable_team(old, old)
            retained["warning"] = "Mannschaft derzeit nicht eindeutig in der Vereinsübersicht auffindbar. Letzter Stand."
            result.append(retained)
    return result


def collect_volleyball(fetcher: Fetcher, season_start: int, now: str, previous: dict | None) -> list[dict]:
    main = fetcher.html(VB_MAIN)
    links = volleyball_links(main, season_start)
    matches = volleyball_matches(fetcher.html(links["resultsUrl"]), links["resultsUrl"], season_start)
    image_data, digest = fetcher.image(links["imageUrl"])
    item = {"id": "vb-mixed-2", "label": "Mixed", "league": "2. Kreisklasse Mixed",
            "season": f"{season_start}/{str(season_start + 1)[-2:]}", "sourceUrl": VB_MAIN,
            "fetchedAt": now, "stale": False, "teamStatus": "active", "matches": matches,
            "standings": {"kind": "image", "rows": [], "imageData": image_data,
                          "imageUrl": links["imageUrl"], "imageSha256": digest,
                          "sourcePublishedAt": links["sourcePublishedAt"],
                          "message": "Die KSV veröffentlicht diese Tabelle als Grafik. Hier siehst du das unveränderte Original."}}
    check_loss(old_team(previous, item["id"]), item)
    return [item]


def collect(sport: str, fetcher: Fetcher, previous: dict | None = None,
            now: datetime | None = None, season_start: int | None = None) -> dict:
    if sport not in SPORTS:
        raise ValueError("Unbekannte Abteilung.")
    dt = now or datetime.now(timezone.utc)
    local = dt.astimezone(BERLIN)
    start = season_start or (local.year if local.month >= 7 else local.year - 1)
    stamp = timestamp(dt)
    envelope = {"schemaVersion": 1, "sport": sport, "lastAttemptAt": stamp,
                "lastSuccessAt": (previous or {}).get("lastSuccessAt"), "stale": False,
                "source": {"label": "nuLiga · HV Brandenburg" if sport == "handball" else "KSV Oberhavel",
                           "url": HB_CLUB if sport == "handball" else VB_MAIN}, "teams": []}
    try:
        envelope["teams"] = (collect_handball if sport == "handball" else collect_volleyball)(fetcher, start, stamp, previous)
        envelope["stale"] = any(t.get("stale") for t in envelope["teams"])
        if not envelope["stale"]:
            envelope["lastSuccessAt"] = stamp
        elif max((t.get("retrySeconds", 0) for t in envelope["teams"]), default=0) > 3600:
            seconds = max(t.get("retrySeconds", 0) for t in envelope["teams"])
            envelope["nextAttemptAt"] = timestamp(dt + timedelta(seconds=seconds))
    except (FetchError, ParseError) as exc:
        if previous:
            envelope = copy.deepcopy(previous)
        envelope.update({"stale": True, "lastAttemptAt": stamp,
                         "warning": "Quelle konnte nicht aktualisiert werden. Angezeigt wird gegebenenfalls der letzte erfolgreiche Stand.",
                         "nextAttemptAt": timestamp(dt + timedelta(seconds=getattr(exc, "retry_seconds", 3600)))})
        for team in envelope.get("teams", []):
            team["stale"] = True
    return envelope
