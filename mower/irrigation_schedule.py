from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from typing import Any, Mapping


SCHEDULE_ACTIONS = frozenset(
    {
        "SKIP_NEXT_IRRIGATION",
        "PAUSE_IRRIGATION_UNTIL",
        "RESUME_IRRIGATION_SCHEDULE",
        "CUSTOMIZE_NEXT_IRRIGATION",
    }
)
MINIMUM_CUSTOM_LEAD_MINUTES = 45
MAXIMUM_CUSTOM_DAYS = 14
MAXIMUM_PAUSE_DAYS = 30
MINIMUM_PAUSE_MINUTES = 5
HISTORY_LIMIT = 12


class IrrigationScheduleValidationError(ValueError):
    pass


def parse_utc(value: Any, field_name: str) -> datetime:
    text = str(value or "").strip()
    if not text:
        raise IrrigationScheduleValidationError(f"{field_name} fehlt.")
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as exc:
        raise IrrigationScheduleValidationError(
            f"{field_name} enthält keine gültige Zeit."
        ) from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise IrrigationScheduleValidationError(
            f"{field_name} muss eine Zeitzone enthalten."
        )
    return parsed.astimezone(timezone.utc)


def validate_schedule_request(
    action: str,
    payload: Mapping[str, Any] | None,
    *,
    now_utc: datetime,
    expected_zone_count: int,
) -> dict[str, Any]:
    normalized = str(action or "").strip().upper()
    if normalized not in SCHEDULE_ACTIONS:
        raise IrrigationScheduleValidationError(
            "Diese Beregnungsplan-Aktion ist nicht erlaubt."
        )
    if now_utc.tzinfo is None or now_utc.utcoffset() is None:
        raise IrrigationScheduleValidationError("now_utc muss zeitzonenbewusst sein.")
    now = now_utc.astimezone(timezone.utc)
    raw = dict(payload or {})

    if normalized == "PAUSE_IRRIGATION_UNTIL":
        until = parse_utc(raw.get("pauseUntil"), "Ende der Beregnungspause")
        if until < now + timedelta(minutes=MINIMUM_PAUSE_MINUTES):
            raise IrrigationScheduleValidationError(
                "Die Pause muss mindestens fünf Minuten in der Zukunft enden."
            )
        if until > now + timedelta(days=MAXIMUM_PAUSE_DAYS):
            raise IrrigationScheduleValidationError(
                "Die Pause darf höchstens 30 Tage dauern."
            )
        return {"pauseUntil": until.isoformat()}

    if normalized == "CUSTOMIZE_NEXT_IRRIGATION":
        desired = parse_utc(raw.get("desiredStart"), "Gewünschter Beregnungsstart")
        if desired < now + timedelta(minutes=MINIMUM_CUSTOM_LEAD_MINUTES):
            raise IrrigationScheduleValidationError(
                "Ein geänderter Lauf benötigt mindestens 45 Minuten sicheren Vorlauf."
            )
        if desired > now + timedelta(days=MAXIMUM_CUSTOM_DAYS):
            raise IrrigationScheduleValidationError(
                "Der geänderte Lauf darf höchstens 14 Tage in der Zukunft liegen."
            )
        raw_zones = raw.get("zones")
        if not isinstance(raw_zones, list) or len(raw_zones) != expected_zone_count:
            raise IrrigationScheduleValidationError(
                f"Es müssen exakt {expected_zone_count} Zonen übermittelt werden."
            )
        zones: list[dict[str, Any]] = []
        seen: set[int] = set()
        selected_count = 0
        for raw_zone in raw_zones:
            if not isinstance(raw_zone, Mapping):
                raise IrrigationScheduleValidationError("Eine Zonenangabe ist ungültig.")
            try:
                zone = int(raw_zone.get("zone"))
                seconds = int(raw_zone.get("runSeconds"))
            except (TypeError, ValueError) as exc:
                raise IrrigationScheduleValidationError(
                    "Zone und Laufzeit müssen ganze Zahlen sein."
                ) from exc
            selected = raw_zone.get("selected") is not False
            if zone < 1 or zone > 99 or zone in seen:
                raise IrrigationScheduleValidationError(
                    "Die Zonenliste enthält eine ungültige oder doppelte Zone."
                )
            if not 60 <= seconds <= 7200:
                raise IrrigationScheduleValidationError(
                    "Jede Zonenlaufzeit muss zwischen 1 und 120 Minuten liegen."
                )
            seen.add(zone)
            selected_count += int(selected)
            zones.append({"zone": zone, "runSeconds": seconds, "selected": selected})
        if selected_count < 1:
            raise IrrigationScheduleValidationError(
                "Mindestens eine Zone muss für den nächsten Lauf aktiviert bleiben."
            )
        zones.sort(key=lambda item: int(item["zone"]))
        return {"desiredStart": desired.isoformat(), "zones": zones}

    if raw:
        # Verhindert, dass versehentlich alte Datums- oder Zonenwerte an eine
        # Aktion ohne Parameter gekoppelt werden.
        raise IrrigationScheduleValidationError(
            "Diese Beregnungsplan-Aktion erwartet keine weiteren Angaben."
        )
    return {}


def load_object(value: str | None, field_name: str) -> dict[str, Any] | None:
    if not value:
        return None
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"{field_name} enthält ungültiges JSON.") from exc
    if not isinstance(parsed, dict):
        raise RuntimeError(f"{field_name} muss ein Objekt enthalten.")
    return dict(parsed)


def dump_object(value: Mapping[str, Any] | None) -> str | None:
    if value is None:
        return None
    return json.dumps(
        dict(value), ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )


def load_history(value: str | None) -> list[dict[str, Any]]:
    if not value:
        return []
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError as exc:
        raise RuntimeError("Beregnungsplan-Verlauf enthält ungültiges JSON.") from exc
    if not isinstance(parsed, list) or not all(isinstance(item, dict) for item in parsed):
        raise RuntimeError("Beregnungsplan-Verlauf muss eine Objektliste enthalten.")
    return [dict(item) for item in parsed[:HISTORY_LIMIT]]


def append_history(
    value: str | None,
    *,
    now_utc: datetime,
    action: str,
    status: str,
    summary: str,
) -> str:
    entries = load_history(value)
    entry = {
        "at": now_utc.astimezone(timezone.utc).isoformat(),
        "action": str(action)[:48],
        "status": str(status)[:24],
        "summary": str(summary)[:240],
    }
    return json.dumps(
        [entry, *entries][:HISTORY_LIMIT],
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
