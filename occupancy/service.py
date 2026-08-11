from __future__ import annotations

import json
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable
from zoneinfo import ZoneInfo

from occupancy.match_model import (
    normalize_legacy_ics_description,
    normalize_legacy_ics_summary,
)


WEEKDAYS = {
    "monday": 0,
    "montag": 0,
    "tuesday": 1,
    "dienstag": 1,
    "wednesday": 2,
    "mittwoch": 2,
    "thursday": 3,
    "donnerstag": 3,
    "friday": 4,
    "freitag": 4,
    "saturday": 5,
    "samstag": 5,
    "sunday": 6,
    "sonntag": 6,
}

MAX_RANGE_DAYS = 63
DEFAULT_SEASON = "Sommer"

def load_config(path: str | Path) -> dict[str, Any]:
    with Path(path).open("r", encoding="utf-8") as handle:
        value = json.load(handle)
    if not isinstance(value, dict):
        raise ValueError("Belegungskonfiguration muss ein JSON-Objekt sein.")
    if int(value.get("schema_version", 0)) != 1:
        raise ValueError("Unbekannte Belegungskonfigurations-Version.")
    return value


def _parse_request_datetime(value: str, tz: ZoneInfo) -> datetime:
    raw = value.strip()
    if not raw:
        raise ValueError("Datum darf nicht leer sein.")
    parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=tz)
    return parsed.astimezone(tz)


def parse_range(start: str, end: str, tz: ZoneInfo) -> tuple[datetime, datetime]:
    range_start = _parse_request_datetime(start, tz)
    range_end = _parse_request_datetime(end, tz)
    if range_end <= range_start:
        raise ValueError("end muss nach start liegen.")
    if range_end - range_start > timedelta(days=MAX_RANGE_DAYS):
        raise ValueError(f"Maximal {MAX_RANGE_DAYS} Tage pro Abfrage sind erlaubt.")
    return range_start, range_end


def resolve_season(config: dict[str, Any], season: str | None) -> str:
    requested = (season or DEFAULT_SEASON).strip().casefold()
    seasons = config.get("seasons", {})
    for name in seasons:
        if str(name).casefold() == requested:
            return str(name)
    raise ValueError("Unbekannte Saison. Erlaubt sind: " + ", ".join(map(str, seasons)))


def _weekday_number(value: str | int) -> int:
    if isinstance(value, int) and 0 <= value <= 6:
        return value
    normalized = str(value).strip().casefold()
    if normalized not in WEEKDAYS:
        raise ValueError(f"Ungültiger Wochentag: {value!r}")
    return WEEKDAYS[normalized]


def _clock(value: str) -> time:
    return time.fromisoformat(str(value))


def _config_date(config: dict[str, Any], name: str) -> date | None:
    value = str(config.get(name, "")).strip()
    return date.fromisoformat(value) if value else None


def _event_overlaps(event: dict[str, Any], start: datetime, end: datetime) -> bool:
    event_start = datetime.fromisoformat(
        str(event.get("occupancyStart") or event["start"])
    )
    event_end = datetime.fromisoformat(
        str(event.get("occupancyEnd") or event["end"])
    )
    return event_end > start and event_start < end


def _training_events(
    config: dict[str, Any],
    *,
    season: str,
    range_start: datetime,
    range_end: datetime,
    tz: ZoneInfo,
) -> list[dict[str, Any]]:
    effective_from = _config_date(config, "effective_from")
    effective_to = _config_date(config, "effective_to")
    cancelled = {
        (str(item.get("schedule_id", "")), str(item.get("date", "")))
        for item in config.get("cancelled_occurrences", [])
        if isinstance(item, dict)
    }
    weekly = list(config.get("seasons", {}).get(season, {}).get("weekly", []))
    first_day = range_start.date() - timedelta(days=1)
    last_day = range_end.date() + timedelta(days=1)

    events: list[dict[str, Any]] = []
    day = first_day
    while day <= last_day:
        if effective_from is not None and day < effective_from:
            day += timedelta(days=1)
            continue
        if effective_to is not None and day > effective_to:
            day += timedelta(days=1)
            continue
        for session in weekly:
            if _weekday_number(session["weekday"]) != day.weekday():
                continue
            schedule_id = str(session["id"])
            if (schedule_id, day.isoformat()) in cancelled:
                continue
            start = datetime.combine(day, _clock(session["start"]), tzinfo=tz)
            end = datetime.combine(day, _clock(session["end"]), tzinfo=tz)
            if end <= start:
                end += timedelta(days=1)
            event = {
                "id": f"training:{season.casefold()}:{schedule_id}:{day.isoformat()}",
                "title": str(session.get("team", "Training")),
                "start": start.isoformat(),
                "end": end.isoformat(),
                "resourceId": str(session["resource_id"]),
                "source": "training",
                "season": season,
                "team": str(session.get("team", "Training")),
                "area": str(session.get("area", "")),
                "description": str(session.get("description", "")),
            }
            if _event_overlaps(event, range_start, range_end):
                events.append(event)
        day += timedelta(days=1)
    return events


def _unfold_ics(text: str) -> list[str]:
    result: list[str] = []
    for raw in text.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        if raw.startswith((" ", "\t")) and result:
            result[-1] += raw[1:]
        else:
            result.append(raw)
    return result


def _ics_unescape(value: str) -> str:
    return (
        value.replace("\\n", "\n")
        .replace("\\N", "\n")
        .replace("\\,", ",")
        .replace("\\;", ";")
        .replace("\\\\", "\\")
    )


def _parse_ics_datetime(key: str, value: str, default_tz: ZoneInfo) -> datetime:
    tz = default_tz
    for parameter in key.split(";")[1:]:
        if parameter.upper().startswith("TZID="):
            tz = ZoneInfo(parameter.split("=", 1)[1])
    value = value.strip()
    if value.endswith("Z"):
        return datetime.strptime(value, "%Y%m%dT%H%M%SZ").replace(tzinfo=timezone.utc).astimezone(default_tz)
    if "T" not in value:
        return datetime.strptime(value, "%Y%m%d").replace(tzinfo=tz)
    return datetime.strptime(value, "%Y%m%dT%H%M%S").replace(tzinfo=tz).astimezone(default_tz)


def _iter_ics_events(path: str | Path) -> Iterable[dict[str, str]]:
    source = Path(path)
    if not source.is_file():
        return []
    parsed: list[dict[str, str]] = []
    current: dict[str, str] | None = None
    for line in _unfold_ics(source.read_text(encoding="utf-8")):
        if line == "BEGIN:VEVENT":
            current = {}
            continue
        if line == "END:VEVENT":
            if current is not None:
                parsed.append(current)
            current = None
            continue
        if current is None or ":" not in line:
            continue
        key, value = line.split(":", 1)
        current[key] = value
    return parsed


def _legacy_ics_match_events(
    config: dict[str, Any],
    *,
    matches_path: str | Path,
    range_start: datetime,
    range_end: datetime,
    tz: ZoneInfo,
) -> list[dict[str, Any]]:
    match_config = config.get("matches", {})
    resource_id = str(match_config.get("resource_id", "rasen"))
    before = timedelta(minutes=int(match_config.get("buffer_before_minutes", 0)))
    after = timedelta(minutes=int(match_config.get("buffer_after_minutes", 0)))
    events: list[dict[str, Any]] = []

    for raw in _iter_ics_events(matches_path):
        start_key = next((key for key in raw if key.upper().startswith("DTSTART")), None)
        end_key = next((key for key in raw if key.upper().startswith("DTEND")), None)
        if not start_key or not end_key:
            continue
        blocked_start = _parse_ics_datetime(start_key, raw[start_key], tz)
        blocked_end = _parse_ics_datetime(end_key, raw[end_key], tz)
        display_start = blocked_start + before
        display_end = blocked_end - after
        if display_end <= display_start:
            display_start = blocked_start
            display_end = blocked_end
        uid = _ics_unescape(raw.get("UID", ""))
        title, team = normalize_legacy_ics_summary(
            _ics_unescape(raw.get("SUMMARY", "Heimspiel"))
        )
        description, detail_link = normalize_legacy_ics_description(
            _ics_unescape(raw.get("DESCRIPTION", ""))
        )
        event = {
            "id": f"match:{uid or display_start.isoformat()}",
            "title": title,
            "start": display_start.isoformat(),
            "end": display_end.isoformat(),
            "resourceId": resource_id,
            "source": "match",
            "season": None,
            "team": team,
            "area": "vorne & hinten",
            "description": description,
            "detailLink": detail_link,
            "location": _ics_unescape(raw.get("LOCATION", "")),
            "occupancyStart": blocked_start.isoformat(),
            "occupancyEnd": blocked_end.isoformat(),
        }
        if _event_overlaps(event, range_start, range_end):
            events.append(event)
    return events


def _structured_match_events(
    config: dict[str, Any],
    *,
    matches_path: str | Path,
    range_start: datetime,
    range_end: datetime,
    tz: ZoneInfo,
) -> list[dict[str, Any]]:
    source = Path(matches_path)
    with source.open("r", encoding="utf-8") as handle:
        payload = json.load(handle)
    if not isinstance(payload, dict) or int(payload.get("schemaVersion", 0)) != 2:
        raise ValueError("matches.json muss dem strukturierten Schema 2 entsprechen.")
    if payload.get("status") != "ok" or not isinstance(payload.get("matches"), list):
        raise ValueError("matches.json enthält keinen freigegebenen Matchbestand.")

    match_config = config.get("matches", {})
    required_before = timedelta(
        minutes=int(match_config.get("buffer_before_minutes", 60))
    )
    required_after = timedelta(
        minutes=int(match_config.get("buffer_after_minutes", 60))
    )
    events: list[dict[str, Any]] = []
    for item in payload["matches"]:
        if not isinstance(item, dict):
            raise ValueError("matches.json enthält einen ungültigen Eintrag.")
        calendar = str(item.get("calendar") or "").strip()
        place = str(item.get("place") or "").strip().casefold()
        resource_id = {
            "Rasen": "rasen",
            "Kunstrasen": "kunstrasen",
        }.get(calendar, place)
        if resource_id not in {"rasen", "kunstrasen"}:
            raise ValueError(f"Unbekannte Spielressource: {calendar or place!r}")

        display_start = _parse_request_datetime(str(item.get("start") or ""), tz)
        display_end = _parse_request_datetime(str(item.get("end") or ""), tz)
        kickoff = _parse_request_datetime(str(item.get("kickoff") or ""), tz)
        blocked_start = _parse_request_datetime(
            str(item.get("occupancyStart") or ""), tz
        )
        blocked_end = _parse_request_datetime(
            str(item.get("occupancyEnd") or ""), tz
        )
        duration_minutes = int(item.get("matchDurationMinutes") or 0)
        if display_end <= display_start or duration_minutes <= 0:
            raise ValueError("matches.json enthält eine ungültige sichtbare Spielzeit.")
        if display_end - display_start != timedelta(minutes=duration_minutes):
            raise ValueError("Match-Dauer und sichtbare Spielzeit widersprechen sich.")
        if kickoff != display_start:
            raise ValueError("Anstoß und Beginn der sichtbaren Spielzeit widersprechen sich.")
        if display_start - blocked_start != required_before:
            raise ValueError("Der verpflichtende 60-Minuten-Spielvorlauf fehlt.")
        if blocked_end - display_end != required_after:
            raise ValueError("Der verpflichtende 60-Minuten-Spielnachlauf fehlt.")

        event = {
            "id": "match:" + str(item.get("id") or "").removeprefix("dfb:"),
            "title": str(item.get("title") or "Heimspiel"),
            "start": display_start.isoformat(),
            "end": display_end.isoformat(),
            "resourceId": resource_id,
            "source": "match",
            "season": None,
            "team": str(item.get("team") or ""),
            "teamCategory": str(item.get("teamCategory") or ""),
            "teamRole": str(item.get("teamRole") or "unknown"),
            "homeTeam": str(item.get("homeTeam") or ""),
            "awayTeam": str(item.get("awayTeam") or ""),
            "competition": str(item.get("competition") or ""),
            "competitionFormat": str(item.get("competitionFormat") or ""),
            "matchType": str(item.get("matchType") or ""),
            "matchDurationMinutes": duration_minutes,
            "durationRule": str(item.get("durationRule") or ""),
            "kickoff": kickoff.isoformat(),
            "area": "vorne & hinten",
            "description": str(item.get("description") or ""),
            "detailLink": str(item.get("detailLink") or ""),
            "location": str(item.get("location") or ""),
            "occupancyStart": blocked_start.isoformat(),
            "occupancyEnd": blocked_end.isoformat(),
        }
        for field in ("id", "team", "durationRule", "competitionFormat"):
            if not str(event.get(field) or "").strip():
                raise ValueError(f"Strukturiertes Match-Feld fehlt: {field}")
        if _event_overlaps(event, range_start, range_end):
            events.append(event)
    return events


def _match_events(
    config: dict[str, Any],
    *,
    matches_path: str | Path,
    range_start: datetime,
    range_end: datetime,
    tz: ZoneInfo,
) -> list[dict[str, Any]]:
    source = Path(matches_path)
    if source.suffix.casefold() == ".json":
        return _structured_match_events(
            config,
            matches_path=source,
            range_start=range_start,
            range_end=range_end,
            tz=tz,
        )
    return _legacy_ics_match_events(
        config,
        matches_path=source,
        range_start=range_start,
        range_end=range_end,
        tz=tz,
    )


def _one_off_events(
    config: dict[str, Any],
    *,
    range_start: datetime,
    range_end: datetime,
    tz: ZoneInfo,
) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    for item in config.get("one_off_events", []):
        if not isinstance(item, dict):
            continue
        start = _parse_request_datetime(str(item["start"]), tz)
        end = _parse_request_datetime(str(item["end"]), tz)
        event = {
            "id": "one-off:" + str(item["id"]),
            "title": str(item["title"]),
            "start": start.isoformat(),
            "end": end.isoformat(),
            "resourceId": str(item["resource_id"]),
            "source": str(item.get("source", "special")),
            "season": item.get("season"),
            "team": str(item.get("team", "")),
            "area": str(item.get("area", "vorne & hinten")),
            "description": str(item.get("description", "")),
        }
        if _event_overlaps(event, range_start, range_end):
            events.append(event)
    return events


def build_occupancy_payload(
    *,
    config_path: str | Path,
    matches_path: str | Path,
    start: str,
    end: str,
    season: str | None = None,
    generated_at: datetime | None = None,
) -> dict[str, Any]:
    config = load_config(config_path)
    tz = ZoneInfo(str(config.get("timezone", "Europe/Berlin")))
    range_start, range_end = parse_range(start, end, tz)
    selected_season = resolve_season(config, season)

    events = [
        *_training_events(
            config,
            season=selected_season,
            range_start=range_start,
            range_end=range_end,
            tz=tz,
        ),
        *_match_events(
            config,
            matches_path=matches_path,
            range_start=range_start,
            range_end=range_end,
            tz=tz,
        ),
        *_one_off_events(
            config,
            range_start=range_start,
            range_end=range_end,
            tz=tz,
        ),
    ]
    events.sort(key=lambda item: (item["start"], item["resourceId"], item["title"]))
    generated = (generated_at or datetime.now(timezone.utc)).astimezone(timezone.utc)
    return {
        "schema_version": 1,
        "timezone": str(tz),
        "season": selected_season,
        "range": {"start": range_start.isoformat(), "end": range_end.isoformat()},
        "effective_from": config.get("effective_from"),
        "effective_to": config.get("effective_to"),
        "generated_at_utc": generated.isoformat(),
        "resources": list(config.get("resources", [])),
        "events": events,
    }
