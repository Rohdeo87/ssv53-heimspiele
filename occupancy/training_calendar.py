"""Prepared shared training source; no runtime registration or device access.

An unavailable resolution means RETAIN existing training, never an empty free
calendar. Approval metadata is a release attestation from trusted configuration,
not authentication and not permission for an HTTP client to edit occupancy.
"""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from types import MappingProxyType
from typing import Any, Iterable, Mapping
from zoneinfo import ZoneInfo


UTC = timezone.utc
WEEKDAYS = {
    "montag": 0, "monday": 0, "dienstag": 1, "tuesday": 1,
    "mittwoch": 2, "wednesday": 2, "donnerstag": 3, "thursday": 3,
    "freitag": 4, "friday": 4, "samstag": 5, "saturday": 5,
    "sonntag": 6, "sunday": 6,
}


def content_digest(value: Any) -> str:
    return hashlib.sha256(json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False,
    ).encode("utf-8")).hexdigest()


def calendar_digest(document: Mapping[str, Any]) -> str:
    """Approval binds every content field; enabling does not change content."""
    return content_digest({key: value for key, value in document.items() if key not in {"enabled", "approval"}})


def legacy_source_hashes(occupancy_config: Mapping[str, Any], mower_config: Mapping[str, Any]) -> dict[str, str]:
    return {
        "occupancy_training_sha256": content_digest({
            key: occupancy_config.get(key)
            for key in ("timezone", "effective_from", "effective_to", "resources", "seasons", "cancelled_occurrences")
        }),
        "mower_training_sha256": content_digest({
            key: mower_config.get(key) for key in ("timezone", "training")
        }),
    }


def load_calendar(path: str | Path) -> dict[str, Any]:
    def unique_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        value: dict[str, Any] = {}
        for key, item in pairs:
            if key in value:
                raise ValueError(f"Doppelter Kalenderschlüssel: {key}")
            value[key] = item
        return value

    value = json.loads(Path(path).read_text(encoding="utf-8"), object_pairs_hook=unique_keys)
    if not isinstance(value, dict):
        raise ValueError("Der Trainingskalender muss ein Objekt sein.")
    return value


def _day(value: Any) -> date:
    if not isinstance(value, str) or len(value) != 10:
        raise ValueError("Kalendertag im Format YYYY-MM-DD erforderlich.")
    return date.fromisoformat(value)


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("Ein Zeitpunkt benötigt einen expliziten UTC-Offset.")
    return value.astimezone(UTC)


def _clock(value: Any) -> time:
    if not isinstance(value, str) or len(value) != 5:
        raise ValueError("Trainingszeit im Format HH:MM erforderlich.")
    parsed = time.fromisoformat(value)
    if parsed.tzinfo is not None:
        raise ValueError("Wochenmuster enthalten örtliche Vereinszeiten.")
    return parsed


def _weekday(value: Any) -> int:
    if type(value) is int and 0 <= value <= 6:
        return value
    if isinstance(value, str) and value.casefold() in WEEKDAYS:
        return WEEKDAYS[value.casefold()]
    raise ValueError("Unbekannter Wochentag.")


def _local_instant(day: date, clock: time, tz: ZoneInfo) -> datetime:
    naive = datetime.combine(day, clock)
    candidates = {
        aware.astimezone(UTC)
        for fold in (0, 1)
        for aware in (naive.replace(tzinfo=tz, fold=fold),)
        if aware.astimezone(UTC).astimezone(tz).replace(tzinfo=None) == naive
    }
    if len(candidates) != 1:
        raise ValueError(f"Training um {naive.isoformat()} ist wegen Zeitumstellung unklar.")
    return next(iter(candidates))


def _period(value: Mapping[str, Any]) -> tuple[date, date]:
    first, last = _day(value.get("from")), _day(value.get("through"))
    if last < first:
        raise ValueError("Das Ende eines Kalenderbereichs liegt vor seinem Anfang.")
    return first, last


def _nonempty(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


@dataclass(frozen=True)
class CalendarValidation:
    errors: tuple[str, ...]
    activation_blockers: tuple[str, ...]
    content_sha256: str | None

    @property
    def ready(self) -> bool:
        return not self.errors and not self.activation_blockers

    def to_dict(self) -> dict[str, Any]:
        return {"ready": self.ready, "errors": list(self.errors),
                "activationBlockers": list(self.activation_blockers), "contentSha256": self.content_sha256}


def validate_calendar(
    document: Mapping[str, Any], *, now_utc: datetime,
    occupancy_config: Mapping[str, Any] | None = None,
    mower_config: Mapping[str, Any] | None = None,
) -> CalendarValidation:
    """Validate shape and list precise missing migration/activation evidence."""
    errors: list[str] = []
    blockers: list[str] = []
    digest: str | None = None
    try:
        now = _utc(now_utc)
        digest = calendar_digest(document)
        if set(document) != {"schema_version", "calendar_id", "revision", "enabled", "timezone",
                            "coverage", "buffers", "weekly_patterns", "season_periods", "holidays",
                            "excluded_occurrences", "legacy_sources", "approval"}:
            raise ValueError("Unvollständige oder unbekannte Felder im Trainingskalender.")
        if type(document["schema_version"]) is not int or document["schema_version"] != 1:
            raise ValueError("Unbekannte Trainingskalender-Version.")
        if not _nonempty(document["calendar_id"]) or type(document["revision"]) is not int or document["revision"] < 1:
            raise ValueError("Kalender-ID und positive ganzzahlige Revision erforderlich.")
        if type(document["enabled"]) is not bool:
            raise ValueError("enabled muss ein expliziter Wahrheitswert sein.")
        if not document["enabled"]:
            blockers.append("CALENDAR_DISABLED: Gemeinsamer Trainingskalender ist deaktiviert.")
        if document["timezone"] != "Europe/Berlin":
            raise ValueError("Der Vereinskalender benötigt Europe/Berlin.")
        coverage = _period(document["coverage"])
        if (coverage[1] - coverage[0]).days > 730:
            raise ValueError("Ein Kalender darf höchstens 731 Kalendertage abdecken.")
        buffers = document["buffers"]
        if set(buffers) != {"before_minutes", "after_minutes"} or any(
            type(buffers[key]) is not int or not 30 <= buffers[key] <= 180 for key in buffers
        ):
            raise ValueError("Trainingspuffer müssen 30 bis 180 ganze Minuten betragen.")
        patterns = document["weekly_patterns"]
        if not isinstance(patterns, dict) or not patterns:
            raise ValueError("Benannte Wochenmuster fehlen.")
        sessions: dict[str, Mapping[str, Any]] = {}
        for season, weekly in patterns.items():
            if not _nonempty(season) or not isinstance(weekly, list):
                raise ValueError("Ungültiges Saison-Wochenmuster.")
            for session in weekly:
                if not isinstance(session, dict) or not {"id", "weekday", "start", "end", "team", "resource_id"} <= set(session):
                    raise ValueError("Ein Wochenmuster benötigt ID, Tag, Zeiten, Mannschaft und Platz.")
                if set(session) - {"id", "weekday", "start", "end", "team", "resource_id", "area", "description"}:
                    raise ValueError("Unbekanntes Feld im Wochenmuster.")
                key = session["id"]
                if not _nonempty(key) or key in sessions or not _nonempty(session["team"]):
                    raise ValueError("Trainings-IDs müssen vorhanden und global eindeutig sein.")
                if session["resource_id"] not in {"rasen", "kunstrasen"}:
                    raise ValueError("Unbekannter Trainingsplatz.")
                _weekday(session["weekday"])
                if _clock(session["start"]) == _clock(session["end"]):
                    raise ValueError("Trainingsbeginn und -ende dürfen nicht gleich sein.")
                sessions[key] = session
        periods = document["season_periods"]
        if not isinstance(periods, list):
            raise ValueError("Saisonbereiche müssen eine Liste sein.")
        assigned: dict[date, str] = {}
        for period in periods:
            if not isinstance(period, dict) or set(period) != {"from", "through", "season"} or period["season"] not in patterns:
                raise ValueError("Ungültige Saisonzuordnung.")
            first, last = _period(period)
            if first < coverage[0] or last > coverage[1]:
                raise ValueError("Saisonbereich liegt außerhalb des geprüften Kalenderbereichs.")
            day = first
            while day <= last:
                if day in assigned:
                    raise ValueError(f"Mehrere Saisonzuordnungen für {day.isoformat()}.")
                assigned[day] = period["season"]
                day += timedelta(days=1)
        missing = (coverage[1] - coverage[0]).days + 1 - len(assigned)
        if missing:
            blockers.append(f"SEASON_DATES_UNCONFIRMED: {missing} Tage ohne verbindliche Saisonzuordnung.")
        holidays = document["holidays"]
        if not isinstance(holidays, dict) or set(holidays) != {"reviewed", "periods"} or type(holidays["reviewed"]) is not bool:
            raise ValueError("Ferienprüfung muss ausdrücklich angegeben werden.")
        if not holidays["reviewed"]:
            blockers.append("HOLIDAY_POLICY_UNCONFIRMED: Training in Ferien und Ausnahmetage sind nicht vollständig geprüft.")
        if not isinstance(holidays["periods"], list):
            raise ValueError("Ferienregeln müssen eine Liste sein.")
        for holiday in holidays["periods"]:
            if not isinstance(holiday, dict) or set(holiday) != {"from", "through", "schedule_ids", "reference"}:
                raise ValueError("Eine Ferienausnahme benötigt Zeitraum, konkrete Trainings-IDs und Beleg.")
            first, last = _period(holiday)
            ids = holiday["schedule_ids"]
            if first < coverage[0] or last > coverage[1] or not _nonempty(holiday["reference"]):
                raise ValueError("Ferienausnahme liegt außerhalb des Bereichs oder hat keinen Beleg.")
            if not isinstance(ids, list) or not ids or any(key not in sessions for key in ids) or len(set(ids)) != len(ids):
                raise ValueError("Ferienausnahme muss eindeutige vorhandene Trainings-IDs nennen.")
        exclusions = document["excluded_occurrences"]
        if not isinstance(exclusions, list):
            raise ValueError("Einzelabsagen müssen eine Liste sein.")
        exclusion_keys: set[tuple[str, date]] = set()
        for exclusion in exclusions:
            if not isinstance(exclusion, dict) or set(exclusion) != {"schedule_id", "date"}:
                raise ValueError("Ungültige Einzelabsage.")
            key, day = exclusion["schedule_id"], _day(exclusion["date"])
            if key not in sessions or not coverage[0] <= day <= coverage[1] or (key, day) in exclusion_keys:
                raise ValueError("Unbekannte, doppelte oder außerhalb liegende Einzelabsage.")
            if _weekday(sessions[key]["weekday"]) != day.weekday():
                raise ValueError("Einzelabsage passt nicht zum Trainingstag.")
            exclusion_keys.add((key, day))
        if occupancy_config is None or mower_config is None:
            blockers.append("LEGACY_SOURCES_REQUIRED: Beide bisherigen Trainingsquellen müssen zur Migration verglichen werden.")
        else:
            if document["legacy_sources"] != legacy_source_hashes(occupancy_config, mower_config):
                blockers.append("LEGACY_SOURCE_CHANGED: Trainingsquellen weichen vom geprüften Ausgangsstand ab.")
            legacy_buffers = mower_config.get("training", {})
            if any(buffers[key] < int(legacy_buffers.get(key, 30)) for key in buffers):
                raise ValueError("Ein bestehender Trainingspuffer darf nicht verkürzt werden.")
        approval = document["approval"]
        if not isinstance(approval, dict) or set(approval) != {"status", "reference", "approved_at_utc", "content_sha256"}:
            raise ValueError("Ungültiger Kalender-Freigabenachweis.")
        if approval["status"] != "approved" or not _nonempty(approval["reference"]):
            blockers.append("CALENDAR_APPROVAL_REQUIRED: Fachliche Kalenderfreigabe mit Beleg fehlt.")
        if approval["content_sha256"] != digest:
            blockers.append("APPROVAL_CONTENT_MISMATCH: Die Freigabe bindet nicht den aktuellen Kalenderinhalt.")
        try:
            approved_at = _utc(datetime.fromisoformat(str(approval["approved_at_utc"]).replace("Z", "+00:00")))
            if approved_at > now:
                raise ValueError("Freigabezeit liegt in der Zukunft.")
        except (TypeError, ValueError):
            blockers.append("APPROVAL_TIME_REQUIRED: Gültiger, nicht zukünftiger Freigabezeitpunkt fehlt.")
    except (KeyError, TypeError, ValueError, AttributeError, OverflowError) as exc:
        errors.append(str(exc))
    return CalendarValidation(tuple(errors), tuple(blockers), digest)


@dataclass(frozen=True)
class TrainingCancellation:
    schedule_id: str
    day: date
    requested_at_utc: datetime
    release_not_before_utc: datetime

    def __post_init__(self) -> None:
        if not _nonempty(self.schedule_id) or type(self.day) is not date:
            raise ValueError("Eine Absage benötigt die vorhandene Trainings-ID und den örtlichen Tag.")
        requested, release = _utc(self.requested_at_utc), _utc(self.release_not_before_utc)
        if release < requested:
            raise ValueError("Eine Absage darf nicht vor ihrer Anforderung freigeben.")
        object.__setattr__(self, "requested_at_utc", requested)
        object.__setattr__(self, "release_not_before_utc", release)


@dataclass(frozen=True)
class TrainingBatch:
    calendar_id: str
    revision: int
    content_sha256: str
    range_start_utc: datetime
    range_end_utc: datetime
    evaluated_at_utc: datetime
    events: tuple[Mapping[str, Any], ...]

    def __post_init__(self) -> None:
        object.__setattr__(self, "events", tuple(MappingProxyType(dict(event)) for event in self.events))

    def app_events(self) -> list[dict[str, Any]]:
        return [dict(event) for event in self.events]

    def mower_blocks(self) -> list[Any]:
        """Project the same effective occupancy; never reapply its buffer."""
        from mower.planner import Block

        return [Block(
            start=datetime.fromisoformat(event["occupancyStart"]),
            end=datetime.fromisoformat(event["occupancyEnd"]),
            source="training", title=f"Training {event['team']}",
            details={"team": event["team"], "schedule_id": event["scheduleId"],
                     "nominal_start": event["start"], "nominal_end": event["end"],
                     "calendar_revision": self.revision, "calendar_sha256": self.content_sha256,
                     "occurrence_id": event["id"]},
        ) for event in self.events if event["resourceId"] == "rasen" and event["blocking"]]


def validate_batch_for_range(batch: TrainingBatch, range_start: datetime, range_end: datetime) -> None:
    """Validate an active batch before either projection consumes it."""
    if not isinstance(batch, TrainingBatch):
        raise TypeError("training_batch muss ein TrainingBatch sein.")
    if type(batch.revision) is not int or batch.revision < 1:
        raise ValueError("TrainingBatch benötigt eine positive ganzzahlige Revision.")
    if not isinstance(batch.calendar_id, str) or not batch.calendar_id.strip():
        raise ValueError("TrainingBatch benötigt eine nichtleere Kalender-ID.")
    if not isinstance(batch.content_sha256, str) or not re.fullmatch(r"[0-9a-fA-F]{64}", batch.content_sha256):
        raise ValueError("TrainingBatch benötigt einen gültigen SHA-256-Inhaltshash.")
    start, end = _utc(range_start), _utc(range_end)
    batch_start, batch_end = _utc(batch.range_start_utc), _utc(batch.range_end_utc)
    if batch.evaluated_at_utc.tzinfo is None or batch.evaluated_at_utc.utcoffset() is None:
        raise ValueError("TrainingBatch-Auswertungszeit benötigt einen expliziten Offset.")
    evaluated = _utc(batch.evaluated_at_utc)
    if end <= start or batch_end <= batch_start or batch_start > start or batch_end < end:
        raise ValueError("TrainingBatch deckt den positiven angefragten Zeitraum nicht ab.")
    ids: set[str] = set()
    for event in batch.events:
        if not isinstance(event, Mapping):
            raise TypeError("TrainingBatch-Ereignisse müssen Mapping-Objekte sein.")
        event_id = str(event.get("id") or "")
        if not event_id or event_id in ids:
            raise ValueError("TrainingBatch-Ereignis-IDs müssen eindeutig sein.")
        ids.add(event_id)
        if event.get("calendarId") != batch.calendar_id or event.get("calendarRevision") != batch.revision or event.get("calendarSha256") != batch.content_sha256:
            raise ValueError("TrainingBatch-Ereignis widerspricht Kalender-ID, Revision oder Hash.")
        if type(event.get("calendarRevision")) is not int:
            raise ValueError("TrainingBatch-Ereignisrevision muss eine echte Ganzzahl sein.")
        for key in ("id", "scheduleId", "team", "start", "end"):
            if not isinstance(event.get(key), str) or not event[key].strip():
                raise ValueError(f"TrainingBatch-Feld {key} fehlt oder ist leer.")
        if event.get("resourceId") not in {"rasen", "kunstrasen"}:
            raise ValueError("TrainingBatch enthält einen unbekannten Platz.")
        event_evaluated = datetime.fromisoformat(str(event.get("evaluatedAtUtc") or ""))
        if event_evaluated.tzinfo is None or event_evaluated.utcoffset() is None or _utc(event_evaluated) != evaluated:
            raise ValueError("TrainingBatch-Ereignis besitzt eine abweichende Auswertungszeit.")
        parsed = []
        for key in ("occupancyStart", "start", "end", "occupancyEnd"):
            value = datetime.fromisoformat(str(event.get(key) or ""))
            if value.tzinfo is None or value.utcoffset() is None:
                raise ValueError("TrainingBatch-Zeitpunkte benötigen einen expliziten Offset.")
            parsed.append(value)
        if not parsed[0] <= parsed[1] < parsed[2] <= parsed[3]:
            raise ValueError("TrainingBatch-Zeitreihenfolge ist ungültig.")
        if type(event.get("blocking")) is not bool or type(event.get("cancelled")) is not bool:
            raise ValueError("TrainingBatch blocking/cancelled müssen boolesche Werte sein.")
        release = event.get("releaseNotBeforeUtc")
        if event["cancelled"]:
            if not isinstance(release, str):
                raise ValueError("Abgesagte Batch-Ereignisse benötigen eine Freigabezeit.")
            release_dt = datetime.fromisoformat(release)
            if release_dt.tzinfo is None or release_dt.utcoffset() is None:
                raise ValueError("Freigabezeit benötigt einen expliziten Offset.")
            if event["blocking"] != (_utc(release_dt) > evaluated):
                raise ValueError("Absage-Sperrstatus widerspricht der Freigabezeit.")
        elif release is not None:
            raise ValueError("Nicht abgesagte Batch-Ereignisse dürfen keine Freigabezeit tragen.")
        elif event["blocking"] is not True:
            raise ValueError("Nicht abgesagte Batch-Ereignisse müssen blockierend sein.")


@dataclass(frozen=True)
class CalendarResolution:
    validation: CalendarValidation
    batch: TrainingBatch | None = None

    @property
    def retain_existing(self) -> bool:
        return self.batch is None


def resolve_training_calendar(
    document: Mapping[str, Any], *, range_start: datetime, range_end: datetime,
    now_utc: datetime, occupancy_config: Mapping[str, Any], mower_config: Mapping[str, Any],
    cancellations: Iterable[TrainingCancellation] = (),
) -> CalendarResolution:
    """Return one shared batch after all calendar and migration gates pass.

    Runtime wiring is default-off. Consumers must retain the existing safe
    block when active resolution is unavailable; this never means a free
    legacy plan. Cancellations must come from the authenticated server store.
    """
    validation = validate_calendar(document, now_utc=now_utc,
                                   occupancy_config=occupancy_config, mower_config=mower_config)
    if not validation.ready:
        return CalendarResolution(validation)
    try:
        start, end, now = _utc(range_start), _utc(range_end), _utc(now_utc)
        tz = ZoneInfo(document["timezone"])
        local_span = end.astimezone(tz).replace(tzinfo=None) - start.astimezone(tz).replace(tzinfo=None)
        if end <= start or local_span > timedelta(days=64):
            raise ValueError("Kalenderabfrage benötigt einen positiven Bereich von höchstens 64 Tagen.")
        # Include previous-day overnight sessions and next-day pre-event buffers.
        first_anchor = start.astimezone(tz).date() - timedelta(days=1)
        last_anchor = end.astimezone(tz).date()
        first_covered, last_covered = _period(document["coverage"])
        if first_anchor < first_covered or last_anchor > last_covered:
            raise ValueError("CALENDAR_COVERAGE_INCOMPLETE: Auch angrenzende Ankertage für Nachttermine und Puffer müssen geprüft sein.")
        cancellation_map: dict[tuple[str, date], TrainingCancellation] = {}
        for item in cancellations:
            if not isinstance(item, TrainingCancellation) or item.requested_at_utc > now:
                raise ValueError("Ungeprüfte oder zukünftige Trainingsabsage.")
            key = (item.schedule_id, item.day)
            previous = cancellation_map.get(key)
            if previous is None or previous.release_not_before_utc < item.release_not_before_utc:
                cancellation_map[key] = item
        exclusions = {(item["schedule_id"], _day(item["date"])) for item in document["excluded_occurrences"]}
        events: list[dict[str, Any]] = []
        day = first_anchor
        while day <= last_anchor:
            season = next(period["season"] for period in document["season_periods"] if _period(period)[0] <= day <= _period(period)[1])
            for session in document["weekly_patterns"][season]:
                schedule_id = session["id"]
                if _weekday(session["weekday"]) != day.weekday() or (schedule_id, day) in exclusions:
                    continue
                if any(schedule_id in holiday["schedule_ids"] and _period(holiday)[0] <= day <= _period(holiday)[1]
                       for holiday in document["holidays"]["periods"]):
                    continue
                start_clock, end_clock = _clock(session["start"]), _clock(session["end"])
                nominal_start = _local_instant(day, start_clock, tz)
                nominal_end = _local_instant(day + timedelta(days=int(end_clock <= start_clock)), end_clock, tz)
                blocked_start = nominal_start - timedelta(minutes=document["buffers"]["before_minutes"])
                blocked_end = nominal_end + timedelta(minutes=document["buffers"]["after_minutes"])
                if blocked_end <= start or blocked_start >= end:
                    continue
                cancellation = cancellation_map.get((schedule_id, day))
                blocking = cancellation is None or cancellation.release_not_before_utc > now
                events.append({
                    "id": f"training:{season.casefold()}:{schedule_id}:{day.isoformat()}",
                    "title": session["team"], "team": session["team"], "source": "training",
                    "season": season, "scheduleId": schedule_id, "resourceId": session["resource_id"],
                    "start": nominal_start.astimezone(tz).isoformat(), "end": nominal_end.astimezone(tz).isoformat(),
                    "occupancyStart": blocked_start.astimezone(tz).isoformat(),
                    "occupancyEnd": blocked_end.astimezone(tz).isoformat(),
                    "area": session.get("area", ""), "description": session.get("description", ""),
                    "cancelled": cancellation is not None, "blocking": blocking,
                    "releaseNotBeforeUtc": cancellation.release_not_before_utc.isoformat() if cancellation else None,
                    "calendarId": document["calendar_id"], "calendarRevision": document["revision"],
                    "calendarSha256": validation.content_sha256,
                    "evaluatedAtUtc": now.isoformat(),
                })
            day += timedelta(days=1)
        events.sort(key=lambda event: (datetime.fromisoformat(event["start"]).astimezone(UTC), event["resourceId"], event["id"]))
        return CalendarResolution(validation, TrainingBatch(
            document["calendar_id"], document["revision"], validation.content_sha256 or "", start, end, now, tuple(events),
        ))
    except (KeyError, TypeError, ValueError, StopIteration, AttributeError) as exc:
        return CalendarResolution(CalendarValidation(validation.errors + (str(exc),), validation.activation_blockers,
                                                     validation.content_sha256))
