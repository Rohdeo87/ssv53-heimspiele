from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from typing import Any, Mapping

from mower.decision import (
    AUTOMATION_EXTERNAL_REASON,
    MANUAL_ACTIVITIES,
    MANUAL_STATES,
    NO_OVERRIDE,
    PARK_OVERRIDE_ACTIONS,
)
from mower.dry_run import run_read_only_cycle
from mower.husqvarna_actions import park_until_further_notice
from mower.husqvarna_start_actions import start_in_work_area
from mower.hydrawise import (
    evaluate_continuous_clear_confirmation,
    parse_relay_id_allowlist,
)
from mower.hydrawise_actions import start_zone_for, suspend_zone_until
from mower.runtime import ControlMode, CycleResult, RuntimeSettings
from mower.safety import CommandIntent, evaluate_command_gate
from mower.state import AutomationState
from mower.state_store import AzureTableStateStore, StateConflictError, StateStore


ReadOnlyRunner = Callable[..., CycleResult]
StateStoreFactory = Callable[[Mapping[str, str]], StateStore]
ParkSender = Callable[[str, str, str], dict[str, Any]]
StartSender = Callable[[str, str, str, int, int], dict[str, Any]]
SuspendZoneSender = Callable[[str, int, int, str | int | None], dict[str, Any]]
StartZoneSender = Callable[[str, int, int, str | int | None], dict[str, Any]]

PARKABLE_ACTIVITIES = frozenset({"MOWING", "LEAVING"})
PARK_COMMAND_ACTIVITIES = frozenset(
    {"MOWING", "LEAVING", "GOING_HOME", "PARKED_IN_CS", "CHARGING"}
)
PARKED_ACTIVITIES = frozenset({"PARKED_IN_CS", "CHARGING"})
MOWING_ACTIVITIES = frozenset({"MOWING", "LEAVING"})
ERROR_STATES = frozenset(
    {"ERROR", "FATAL_ERROR", "ERROR_AT_POWER_UP", "WAIT_UPDATING", "WAIT_POWER_UP"}
)
SAFE_PARK_SOURCES = frozenset(
    {"training", "match", "special", "irrigation", "hydrawise_unconfirmed", "continuous"}
)
ACTIVE_IRRIGATION_PHASES = frozenset(
    {"PLANNED", "SUSPENDING", "READY", "START_RESERVED", "RUNNING"}
)
PARK_GUARD_BLOCK_SOURCES = frozenset({"training", "match", "special", "irrigation"})


def _as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _parse_time(value: Any) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("Zeitangaben müssen eine Zeitzone enthalten.")
    return parsed.astimezone(timezone.utc)


def _env_int(
    environment: Mapping[str, str],
    name: str,
    default: int,
    *,
    minimum: int,
    maximum: int,
) -> int:
    try:
        value = int(str(environment.get(name, default)).strip())
    except ValueError as exc:
        raise RuntimeError(f"{name} muss eine ganze Zahl sein.") from exc
    if not minimum <= value <= maximum:
        raise RuntimeError(f"{name} muss zwischen {minimum} und {maximum} liegen.")
    return value


def _source_parts(source: Any) -> frozenset[str]:
    return frozenset(
        part.strip().lower()
        for part in str(source or "").split("+")
        if part.strip()
    )


def _restart_allowed(source: str) -> bool:
    parts = _source_parts(source)
    return bool(parts) and parts.issubset(SAFE_PARK_SOURCES)


def _operator_action(state: AutomationState, now_utc: datetime) -> str | None:
    if state.operator_request_status != "PENDING":
        return None
    expires = _parse_time(state.operator_request_expires_utc)
    if expires is None or now_utc >= expires:
        return None
    return str(state.operator_request_action or "").strip().upper() or None


def _finish_operator_request(
    state: AutomationState,
    result: str,
    *,
    status: str = "COMPLETED",
) -> AutomationState:
    return replace(
        state,
        revision=state.revision + 1,
        operator_request_status=status,
        operator_request_result=result,
    )


def _park_valid_until(
    *,
    now_utc: datetime,
    state: AutomationState,
    parking_block: Mapping[str, Any],
) -> datetime:
    block_end = _parse_time(parking_block.get("end"))
    if block_end is not None and block_end > now_utc:
        return block_end
    owned_end = _parse_time(state.automation_park_until_utc)
    if owned_end is not None and owned_end > now_utc:
        return owned_end
    # A stable fallback keeps the command fingerprint constant within a UTC day.
    return now_utc.replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(days=2)


def _json_ints(value: str | None) -> list[int]:
    if not value:
        return []
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError as exc:
        raise RuntimeError("Gespeicherte Relaisliste ist ungültig.") from exc
    if not isinstance(parsed, list):
        raise RuntimeError("Gespeicherte Relaisliste ist kein Array.")
    return [int(item) for item in parsed]


def _plan_from_state(state: AutomationState) -> list[dict[str, Any]]:
    if not state.irrigation_plan_json:
        return []
    try:
        parsed = json.loads(state.irrigation_plan_json)
    except json.JSONDecodeError as exc:
        raise RuntimeError("Gespeicherter Beregnungsplan ist ungültig.") from exc
    if not isinstance(parsed, list) or not all(isinstance(item, dict) for item in parsed):
        raise RuntimeError("Gespeicherter Beregnungsplan hat ein falsches Format.")
    return [dict(item) for item in parsed]


def _validated_upcoming_plan(
    details: dict[str, Any],
    *,
    now_utc: datetime,
    expected_zone_count: int,
    expected_relay_ids: frozenset[int],
    max_lead_minutes: int,
) -> tuple[str, list[dict[str, Any]]]:
    raw_zones = _as_dict(details.get("hydrawise")).get("zones")
    if not isinstance(raw_zones, list) or len(raw_zones) != expected_zone_count:
        raise RuntimeError(
            f"Hydrawise muss exakt {expected_zone_count} ausgewählte Zonen liefern."
        )
    zones: list[dict[str, Any]] = []
    relay_ids: set[int] = set()
    zone_numbers: set[int] = set()
    for raw in raw_zones:
        zone = _as_dict(raw)
        relay_id = int(zone.get("relay_id") or 0)
        zone_number = int(zone.get("zone") or 0)
        run_seconds = int(zone.get("run_seconds") or 0)
        start = _parse_time(zone.get("scheduled_start_utc"))
        if (
            relay_id <= 0
            or zone_number <= 0
            or not 60 <= run_seconds <= 7200
            or start is None
            or bool(zone.get("running"))
        ):
            raise RuntimeError("Hydrawise-Zonenplan enthält ungültige Werte.")
        if relay_id in relay_ids or zone_number in zone_numbers:
            raise RuntimeError("Hydrawise-Zonenplan enthält doppelte Zonen.")
        relay_ids.add(relay_id)
        zone_numbers.add(zone_number)
        zones.append(
            {
                "relay_id": relay_id,
                "zone": zone_number,
                "name": str(zone.get("name") or f"Zone {zone_number}"),
                "run_seconds": run_seconds,
                "scheduled_start_utc": start.isoformat(),
                "scheduled_end_utc": (start + timedelta(seconds=run_seconds)).isoformat(),
            }
        )
    if relay_ids != expected_relay_ids:
        raise RuntimeError(
            "Hydrawise-Zonenplan entspricht nicht exakt der freigegebenen Relay-ID-Liste."
        )
    zones.sort(key=lambda item: (_parse_time(item["scheduled_start_utc"]), item["zone"]))
    first_start = _parse_time(zones[0]["scheduled_start_utc"])
    if first_start is None or first_start <= now_utc:
        raise RuntimeError("Der zu übernehmende Hydrawise-Lauf liegt nicht in der Zukunft.")
    if first_start - now_utc > timedelta(minutes=max_lead_minutes):
        raise RuntimeError("Der Hydrawise-Lauf liegt außerhalb des sicheren Übernahmefensters.")
    previous_end: datetime | None = None
    for zone in zones:
        start = _parse_time(zone["scheduled_start_utc"])
        end = _parse_time(zone["scheduled_end_utc"])
        if start is None or end is None or end <= start:
            raise RuntimeError("Hydrawise-Zonenzeit ist ungültig.")
        if previous_end is not None:
            gap = (start - previous_end).total_seconds()
            if not -5 <= gap <= 120:
                raise RuntimeError("Die sieben Hydrawise-Zonen bilden keinen lückenlosen Lauf.")
        previous_end = end
    canonical = json.dumps(zones, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest(), zones


def _validated_operator_plan(
    details: dict[str, Any],
    *,
    now_utc: datetime,
    expected_zone_count: int,
    expected_relay_ids: frozenset[int],
) -> tuple[str, list[dict[str, Any]]]:
    observations = _zone_observation_by_relay(details)
    if set(observations) != set(expected_relay_ids):
        raise RuntimeError("Hydrawise liefert nicht exakt die sieben freigegebenen Zonen.")
    start = now_utc + timedelta(minutes=45)
    zones: list[dict[str, Any]] = []
    ordered = sorted(observations.values(), key=lambda item: int(item.get("zone") or 0))
    for raw in ordered:
        relay_id = int(raw.get("relay_id") or 0)
        zone_number = int(raw.get("zone") or 0)
        run_seconds = int(raw.get("run_seconds") or 0)
        if (
            raw.get("valid") is not True
            or bool(raw.get("running"))
            or relay_id not in expected_relay_ids
            or zone_number <= 0
            or not 60 <= run_seconds <= 7200
        ):
            raise RuntimeError("Mindestens eine Zone besitzt keine sicher bestätigte Laufzeit.")
        end = start + timedelta(seconds=run_seconds)
        zones.append(
            {
                "relay_id": relay_id,
                "zone": zone_number,
                "name": str(raw.get("name") or f"Zone {zone_number}"),
                "run_seconds": run_seconds,
                "scheduled_start_utc": start.isoformat(),
                "scheduled_end_utc": end.isoformat(),
                "operator_manual": True,
            }
        )
        start = end
    if len(zones) != expected_zone_count:
        raise RuntimeError("Der manuelle Beregnungsplan enthält nicht exakt sieben Zonen.")
    canonical = json.dumps(zones, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest(), zones


def _active_relay_ids(details: dict[str, Any]) -> set[int]:
    safety = _as_dict(_as_dict(details.get("hydrawise")).get("safety"))
    values = safety.get("active_relay_ids")
    if not isinstance(values, list):
        return set()
    return {int(value) for value in values}


def _live_zone_by_relay(details: dict[str, Any]) -> dict[int, dict[str, Any]]:
    raw_zones = _as_dict(details.get("hydrawise")).get("zones")
    if not isinstance(raw_zones, list):
        return {}
    zones: dict[int, dict[str, Any]] = {}
    for raw_zone in raw_zones:
        zone = _as_dict(raw_zone)
        try:
            relay_id = int(zone.get("relay_id") or 0)
        except (TypeError, ValueError):
            continue
        if relay_id > 0:
            zones[relay_id] = zone
    return zones


def _zone_observation_by_relay(details: dict[str, Any]) -> dict[int, dict[str, Any]]:
    hydrawise = _as_dict(details.get("hydrawise"))
    raw_observations = hydrawise.get("zone_observations")
    if not isinstance(raw_observations, list):
        # Abwärtskompatibilität für Tests und ältere Read-only-Payloads.
        raw_observations = [
            {
                **_as_dict(zone),
                "valid": True,
                "scheduled": True,
            }
            for zone in hydrawise.get("zones", [])
            if isinstance(zone, dict)
        ]
    observations: dict[int, dict[str, Any]] = {}
    for raw in raw_observations:
        observation = _as_dict(raw)
        try:
            relay_id = int(observation.get("relay_id") or 0)
        except (TypeError, ValueError):
            continue
        if relay_id > 0:
            observations[relay_id] = observation
    return observations


def _plan_change_fingerprint(kind: str, payload: Any) -> str:
    canonical = json.dumps(
        {"kind": kind, "payload": payload},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _candidate_confirmation(
    state: AutomationState,
    *,
    fingerprint: str,
    now_utc: datetime,
    required_minutes: int,
) -> tuple[AutomationState, bool]:
    since = _parse_time(state.irrigation_change_candidate_since_utc)
    if state.irrigation_change_candidate_hash != fingerprint or since is None:
        return (
            replace(
                state,
                revision=state.revision + 1,
                irrigation_change_candidate_hash=fingerprint,
                irrigation_change_candidate_since_utc=now_utc.isoformat(),
            ),
            False,
        )
    return state, now_utc - since >= timedelta(minutes=required_minutes)


def _clear_change_candidate(state: AutomationState) -> AutomationState:
    if (
        state.irrigation_change_candidate_hash is None
        and state.irrigation_change_candidate_since_utc is None
    ):
        return state
    return replace(
        state,
        revision=state.revision + 1,
        irrigation_change_candidate_hash=None,
        irrigation_change_candidate_since_utc=None,
    )


def _reconcile_prestart_plan(
    *,
    plan: list[dict[str, Any]],
    suspended_relay_ids: set[int],
    details: dict[str, Any],
    now_utc: datetime,
    expected_relay_ids: frozenset[int],
    capture_max_lead_minutes: int,
    tolerance_seconds: int = 120,
) -> tuple[str, list[dict[str, Any]] | None, str]:
    """Ordnet bestätigbare Hydrawise-Änderungen vor dem ersten Wasserstart ein."""

    if plan and all(zone.get("operator_manual") is True for zone in plan):
        return "UNCHANGED", plan, "Vom Platzwart bestätigter Sieben-Zonen-Lauf."

    live_by_relay = _live_zone_by_relay(details)
    observations = _zone_observation_by_relay(details)
    if set(observations) != set(expected_relay_ids) or any(
        observation.get("valid") is not True
        for observation in observations.values()
    ):
        return (
            "INVALID",
            None,
            "Die sieben Relay-Beobachtungen sind für eine Planänderung nicht vollständig.",
        )

    pending_ids = set(expected_relay_ids) - set(suspended_relay_ids)
    stored_ends = [
        end
        for zone in plan
        if (end := _parse_time(zone.get("scheduled_end_utc"))) is not None
    ]
    stored_end = max(stored_ends, default=None)
    if stored_end is None:
        return "INVALID", None, "Der gespeicherte Beregnungsplan besitzt kein Ende."

    if pending_ids:
        pending_starts = {
            relay_id: _parse_time(
                _as_dict(live_by_relay.get(relay_id)).get("scheduled_start_utc")
            )
            for relay_id in pending_ids
        }
        all_pending_deferred = all(
            start is None or start > stored_end + timedelta(minutes=30)
            for start in pending_starts.values()
        )
        no_suspension_yet = not suspended_relay_ids
        future_starts = [start for start in pending_starts.values() if start is not None]
        first_live_start = min(future_starts, default=None)
        stored_first_start = min(
            (
                start
                for zone in plan
                if int(zone["relay_id"]) in pending_ids
                and (start := _parse_time(zone.get("scheduled_start_utc"))) is not None
            ),
            default=None,
        )
        moved_outside_capture = (
            no_suspension_yet
            and first_live_start is not None
            and stored_first_start is not None
            and abs((first_live_start - stored_first_start).total_seconds())
            > tolerance_seconds
            and first_live_start - now_utc
            > timedelta(minutes=capture_max_lead_minutes)
        )
        if all_pending_deferred or moved_outside_capture:
            return (
                "CANCELLED_OR_DEFERRED",
                None,
                "Hydrawise hat den übernommenen Lauf vollständig ausgesetzt oder aus dem Übernahmefenster verschoben.",
            )
        if any(start is None for start in pending_starts.values()):
            return (
                "INVALID",
                None,
                "Nur ein Teil der noch nicht suspendierten Zonen besitzt einen nachvollziehbaren nächsten Lauf.",
            )

    reconciled: list[dict[str, Any]] = []
    for planned in plan:
        relay_id = int(planned["relay_id"])
        observation = observations[relay_id]
        try:
            live_zone = int(observation.get("zone") or 0)
            live_duration = int(observation.get("run_seconds") or 0)
            planned_start = _parse_time(planned["scheduled_start_utc"])
        except (TypeError, ValueError) as exc:
            return "INVALID", None, f"Relay {relay_id} besitzt ungültige Live-Werte: {exc}"
        if live_zone != int(planned["zone"]) or not 60 <= live_duration <= 7200:
            return "INVALID", None, f"Relay {relay_id} besitzt ungültige Zonen- oder Laufzeitwerte."
        if planned_start is None:
            return "INVALID", None, f"Relay {relay_id} besitzt keine gespeicherte Startzeit."
        live_start = _parse_time(
            _as_dict(live_by_relay.get(relay_id)).get("scheduled_start_utc")
        )
        if relay_id not in suspended_relay_ids:
            if live_start is None:
                return "INVALID", None, f"Relay {relay_id} fehlt im aktuellen Restplan."
            start = live_start
        else:
            start = planned_start
        reconciled.append(
            {
                **planned,
                "name": str(observation.get("name") or planned.get("name")),
                "run_seconds": live_duration,
                "scheduled_start_utc": start.isoformat(),
                "scheduled_end_utc": (
                    start + timedelta(seconds=live_duration)
                ).isoformat(),
            }
        )

    changed = False
    for old, new in zip(plan, reconciled, strict=True):
        old_start = _parse_time(old.get("scheduled_start_utc"))
        new_start = _parse_time(new.get("scheduled_start_utc"))
        if (
            int(old.get("run_seconds") or 0) != int(new["run_seconds"])
            or old_start is None
            or new_start is None
            or abs((old_start - new_start).total_seconds()) > tolerance_seconds
        ):
            changed = True
            break
    if not changed:
        return "UNCHANGED", plan, "Der Hydrawise-Plan entspricht weiterhin der Übernahme."
    return (
        "UPDATED",
        reconciled,
        "Hydrawise hat Startzeiten oder Laufzeiten nachvollziehbar geändert.",
    )


def _reconcile_remaining_durations(
    *,
    plan: list[dict[str, Any]],
    completed_relay_ids: set[int],
    current_relay_id: int | None,
    details: dict[str, Any],
    expected_relay_ids: frozenset[int],
) -> tuple[str, list[dict[str, Any]] | None, str]:
    """Übernimmt App-Laufzeitänderungen nur für noch nicht gestartete Zonen."""

    observations = _zone_observation_by_relay(details)
    if set(observations) != set(expected_relay_ids) or any(
        observation.get("valid") is not True
        for observation in observations.values()
    ):
        return (
            "INVALID",
            None,
            "Die sieben Relay-Beobachtungen sind für die Laufzeitänderung nicht vollständig.",
        )

    immutable_ids = set(completed_relay_ids)
    if current_relay_id is not None:
        immutable_ids.add(int(current_relay_id))
    reconciled: list[dict[str, Any]] = []
    changed = False
    for planned in plan:
        relay_id = int(planned["relay_id"])
        observation = observations[relay_id]
        try:
            live_zone = int(observation.get("zone") or 0)
            live_duration = int(observation.get("run_seconds") or 0)
            planned_start = _parse_time(planned.get("scheduled_start_utc"))
        except (TypeError, ValueError) as exc:
            return "INVALID", None, f"Relay {relay_id} besitzt ungültige Live-Werte: {exc}"
        if live_zone != int(planned["zone"]) or not 60 <= live_duration <= 7200:
            return "INVALID", None, f"Relay {relay_id} besitzt ungültige Zonen- oder Laufzeitwerte."
        if relay_id in immutable_ids:
            reconciled.append(dict(planned))
            continue
        if planned_start is None:
            return "INVALID", None, f"Relay {relay_id} besitzt keine gespeicherte Startzeit."
        updated = {
            **planned,
            "name": str(observation.get("name") or planned.get("name")),
            "run_seconds": live_duration,
            "scheduled_end_utc": (
                planned_start + timedelta(seconds=live_duration)
            ).isoformat(),
        }
        changed = changed or int(planned.get("run_seconds") or 0) != live_duration
        reconciled.append(updated)

    if not changed:
        return "UNCHANGED", plan, "Die Laufzeiten der verbleibenden Zonen sind unverändert."
    return (
        "UPDATED",
        reconciled,
        "Hydrawise hat die Laufzeit mindestens einer noch nicht gestarteten Zone geändert.",
    )


def _projected_irrigation_end(
    *,
    plan: list[dict[str, Any]],
    completed_relay_ids: set[int],
    current_relay_id: int | None,
    current_started_utc: datetime | None,
    now_utc: datetime,
    end_confirmation_minutes: int,
) -> datetime:
    """Berechnet konservativ das Ende der bereits übernommenen manuellen Folge."""

    seconds = 0
    remaining_zone_count = 0
    for zone in plan:
        relay_id = int(zone["relay_id"])
        if relay_id in completed_relay_ids:
            continue
        duration = int(zone["run_seconds"])
        if relay_id == current_relay_id and current_started_utc is not None:
            elapsed = max(0, int((now_utc - current_started_utc).total_seconds()))
            duration = max(0, duration - elapsed)
        seconds += duration
        remaining_zone_count += 1
    seconds += remaining_zone_count * end_confirmation_minutes * 60
    return now_utc + timedelta(seconds=seconds)


def _next_scheduled_irrigation_start(
    details: dict[str, Any],
    *,
    now_utc: datetime,
) -> datetime | None:
    raw_zones = _as_dict(details.get("hydrawise")).get("zones")
    if not isinstance(raw_zones, list):
        return None
    starts = [
        start
        for raw_zone in raw_zones
        if (
            start := _parse_time(
                _as_dict(raw_zone).get("scheduled_start_utc")
            )
        ) is not None
        and start > now_utc
    ]
    return min(starts, default=None)


def _cycle_state(
    state: AutomationState,
    result: CycleResult,
    now_utc: datetime,
) -> AutomationState:
    details = _as_dict(result.details)
    mower = _as_dict(details.get("mower"))
    safety = _as_dict(_as_dict(details.get("hydrawise")).get("safety"))
    observed = _parse_time(safety.get("observed_at_utc"))
    fresh = bool(safety.get("available")) and bool(safety.get("fresh"))
    clear = fresh and bool(safety.get("clear_now"))
    zones = _as_dict(details.get("hydrawise")).get("zones")
    next_irrigation: datetime | None = None
    if isinstance(zones, list):
        starts = [
            value
            for item in zones
            if (value := _parse_time(_as_dict(item).get("scheduled_start_utc"))) is not None
            and value > now_utc
        ]
        next_irrigation = min(starts, default=None)
    return state.record_cycle(
        started_utc=now_utc,
        success=True,
        decision_code=result.decision_code,
        mower_activity=str(mower.get("activity") or "") or None,
        mower_state=str(mower.get("state") or "") or None,
        error_code=int(mower.get("error_code") or 0),
        hydrawise_success_utc=now_utc if fresh else None,
        hydrawise_observed_utc=observed,
        hydrawise_clear=clear,
        hydrawise_active_count=int(safety.get("active_zone_count") or 0),
        next_irrigation_start_utc=next_irrigation,
    )


def _state_details(state: AutomationState, *, persisted: bool, error: str | None = None) -> dict[str, Any]:
    return {
        "revision": state.revision,
        "persisted": persisted,
        "error": error,
        "parked_by_automation": state.parked_by_automation,
        "automation_park_source": state.automation_park_source,
        "automation_restart_allowed": state.automation_restart_allowed,
        "park_command_sent_utc": state.park_command_sent_utc,
        "park_confirmed_utc": state.park_confirmed_utc,
        "continuous_mowing_owned": state.continuous_mowing_owned,
        "irrigation_phase": state.irrigation_phase,
        "irrigation_plan_id": state.irrigation_plan_id,
        "irrigation_current_relay_id": state.irrigation_current_relay_id,
        "irrigation_suspended_relay_ids": _json_ints(
            state.irrigation_suspended_relay_ids_json
        ),
        "irrigation_suspension_until_utc": (
            state.irrigation_suspension_until_utc
        ),
        "irrigation_suspension_completed_utc": (
            state.irrigation_suspension_completed_utc
        ),
        "irrigation_completed_relay_ids": _json_ints(
            state.irrigation_completed_relay_ids_json
        ),
        "irrigation_completed_utc": state.irrigation_completed_utc,
        "irrigation_failed_reason": state.irrigation_failed_reason,
        "irrigation_change_candidate_since_utc": (
            state.irrigation_change_candidate_since_utc
        ),
        "irrigation_cancelled_without_run_utc": (
            state.irrigation_cancelled_without_run_utc
        ),
        "hydrawise_clear_since_utc": state.hydrawise_clear_since_utc,
    }


def _decorate(
    details: dict[str, Any],
    *,
    state: AutomationState,
    settings: RuntimeSettings,
    persisted: bool,
    command_sent: bool,
    error: str | None = None,
) -> dict[str, Any]:
    updated = dict(details)
    updated["mode"] = "full_failsafe_locked_or_live"
    updated["automation_state"] = _state_details(state, persisted=persisted, error=error)
    safety = dict(_as_dict(updated.get("safety")))
    safety.update(
        {
            "read_only": not command_sent,
            "command_sent": command_sent,
            "park_gate_enabled": settings.enable_park_commands,
            "start_gate_enabled": settings.enable_start_commands,
            "irrigation_gate_enabled": settings.enable_irrigation_commands,
            "full_mower_confirmation_valid": settings.full_mower_write_gate_enabled,
            "full_failsafe_confirmation_valid": settings.full_failsafe_write_gate_enabled,
            "irrigation_command_functions_present": True,
        }
    )
    updated["safety"] = safety
    return updated


def _persist_result(
    *,
    store: StateStore,
    original: AutomationState,
    state: AutomationState,
    result: CycleResult,
    details: dict[str, Any],
    settings: RuntimeSettings,
    decision_code: str,
    message: str,
    command_sent: bool = False,
) -> CycleResult:
    try:
        store.save(state, expected_revision=original.revision)
        persisted, error = True, None
    except StateConflictError as exc:
        persisted, error = False, str(exc)
    return replace(
        result,
        decision_code=decision_code,
        message=message,
        command_sent=command_sent,
        details=_decorate(
            details,
            state=state,
            settings=settings,
            persisted=persisted,
            command_sent=command_sent,
            error=error,
        ),
    )


def _failed_irrigation(state: AutomationState, reason: str) -> AutomationState:
    return replace(
        state,
        revision=state.revision + 1,
        irrigation_phase="FAILED",
        irrigation_failed_reason=reason,
    )


def _clear_irrigation(state: AutomationState) -> AutomationState:
    return replace(
        state,
        revision=state.revision + 1,
        irrigation_phase=None,
        irrigation_plan_id=None,
        irrigation_plan_json=None,
        irrigation_suspended_relay_ids_json=None,
        irrigation_suspension_until_utc=None,
        irrigation_suspension_completed_utc=None,
        irrigation_completed_relay_ids_json=None,
        irrigation_current_relay_id=None,
        irrigation_zone_start_reserved_utc=None,
        irrigation_zone_started_utc=None,
        irrigation_zone_clear_since_utc=None,
        irrigation_completed_utc=None,
        irrigation_failed_reason=None,
        irrigation_change_candidate_hash=None,
        irrigation_change_candidate_since_utc=None,
        irrigation_cancelled_without_run_utc=None,
    )


def _cancel_irrigation_without_run(
    state: AutomationState,
    *,
    now_utc: datetime,
) -> AutomationState:
    """Verwirft einen bestätigten, noch nicht gestarteten Hydrawise-Lauf."""

    return replace(
        state,
        revision=state.revision + 1,
        irrigation_phase=None,
        irrigation_plan_id=None,
        irrigation_plan_json=None,
        irrigation_suspended_relay_ids_json=None,
        irrigation_suspension_until_utc=None,
        irrigation_suspension_completed_utc=None,
        irrigation_completed_relay_ids_json=None,
        irrigation_current_relay_id=None,
        irrigation_zone_start_reserved_utc=None,
        irrigation_zone_started_utc=None,
        irrigation_zone_clear_since_utc=None,
        irrigation_completed_utc=None,
        irrigation_failed_reason=None,
        irrigation_change_candidate_hash=None,
        irrigation_change_candidate_since_utc=None,
        irrigation_cancelled_without_run_utc=now_utc.isoformat(),
    )


def run_full_failsafe_cycle(
    *,
    now_utc: datetime,
    settings: RuntimeSettings,
    environment: Mapping[str, str],
    past_due: bool,
    source: str,
    read_only_runner: ReadOnlyRunner = run_read_only_cycle,
    state_store_factory: StateStoreFactory = AzureTableStateStore.from_environment,
    park_sender: ParkSender = park_until_further_notice,
    start_sender: StartSender = start_in_work_area,
    suspend_zone_sender: SuspendZoneSender = suspend_zone_until,
    start_zone_sender: StartZoneSender = start_zone_for,
) -> CycleResult:
    """Fail-closed Gesamtsteuerung für Mäher, Belegung und sieben Zonen."""

    if settings.control_mode is not ControlMode.FULL_FAILSAFE:
        raise RuntimeError("run_full_failsafe_cycle benötigt CONTROL_MODE=FULL_FAILSAFE.")
    if not settings.enable_live_reads:
        raise RuntimeError("FULL_FAILSAFE benötigt ENABLE_LIVE_READS=true.")
    if now_utc.tzinfo is None or now_utc.utcoffset() is None:
        raise ValueError("now_utc muss zeitzonenbewusst sein.")
    now = now_utc.astimezone(timezone.utc)

    expected_zones = _env_int(
        environment,
        "HYDRAWISE_EXPECTED_ZONE_COUNT",
        7,
        minimum=1,
        maximum=24,
    )
    expected_relay_ids = frozenset(
        parse_relay_id_allowlist(
            environment.get("HYDRAWISE_EXPECTED_RELAY_IDS"),
            expected_count=expected_zones,
            required=True,
        )
    )

    result = read_only_runner(
        now_utc=now,
        settings=settings,
        environment=environment,
        past_due=past_due,
        source=source,
    )
    details = dict(result.details)
    current_plan = _as_dict(details.get("current_plan"))
    parking_block = _as_dict(current_plan.get("parking_block"))
    blocked_now = _as_dict(current_plan.get("blocked_now"))
    decision = _as_dict(details.get("decision"))
    mower = _as_dict(details.get("mower"))
    mower_id = str(mower.get("mower_id") or "").strip()
    activity = str(mower.get("activity") or "").strip().upper()
    mower_state = str(mower.get("state") or "").strip().upper()
    override_action = str(mower.get("override_action") or "").strip().upper()
    external_reason = mower.get("external_reason_id")
    error_code = int(mower.get("error_code") or 0)
    battery = int(mower.get("battery_percent") or 0)
    target_area = _as_dict(mower.get("target_work_area"))

    store = state_store_factory(environment)
    original = store.load()
    previous_activity = str(original.last_mower_activity or "").upper()
    state = _cycle_state(original, result, now)
    operator_action = _operator_action(state, now)
    if state.operator_request_status == "PENDING" and operator_action is None:
        state = _finish_operator_request(
            state,
            "Die Bedienanforderung ist ohne Gerätebefehl abgelaufen.",
            status="EXPIRED",
        )

    if state.maintenance_mode:
        return _persist_result(
            store=store,
            original=original,
            state=state,
            result=result,
            details=details,
            settings=settings,
            decision_code="MAINTENANCE_MODE",
            message="Wartungsmodus ist aktiv; alle automatischen Befehle bleiben gesperrt.",
        )

    block_source = str(parking_block.get("source") or "").strip().lower()
    irrigation_due = "irrigation" in _source_parts(block_source)
    hydra_safety = _as_dict(_as_dict(details.get("hydrawise")).get("safety"))
    observed_relay_ids = {
        int(value) for value in hydra_safety.get("observed_relay_ids", [])
    }
    reported_expected_relay_ids = {
        int(value) for value in hydra_safety.get("expected_relay_ids", [])
    }
    relay_allowlist_valid = (
        hydra_safety.get("relay_set_valid") is True
        and observed_relay_ids == expected_relay_ids
        and reported_expected_relay_ids == expected_relay_ids
    )
    details["hydrawise_relay_allowlist"] = {
        "valid": relay_allowlist_valid,
        "expected_relay_ids": sorted(expected_relay_ids),
        "observed_relay_ids": sorted(observed_relay_ids),
    }
    irrigation_failsafe_lead_minutes = _env_int(
        environment,
        "IRRIGATION_FAILSAFE_DOCK_LEAD_MINUTES",
        40,
        minimum=30,
        maximum=120,
    )
    next_irrigation_start = _next_scheduled_irrigation_start(
        details,
        now_utc=now,
    )
    irrigation_capture_max_lead_minutes = _env_int(
        environment,
        "IRRIGATION_CAPTURE_MAX_LEAD_MINUTES",
        45,
        minimum=30,
        maximum=120,
    )
    irrigation_capture_due = (
        next_irrigation_start is not None
        and timedelta(0)
        <= next_irrigation_start - now
        <= timedelta(minutes=irrigation_capture_max_lead_minutes)
    )
    irrigation_due = (
        irrigation_due
        or irrigation_capture_due
        or operator_action == "START_IRRIGATION"
    )
    if (
        operator_action == "START_IRRIGATION"
        and state.irrigation_phase in ACTIVE_IRRIGATION_PHASES
    ):
        state = _finish_operator_request(
            state,
            "Der sichere Beregnungsablauf läuft bereits.",
        )
        operator_action = None
    irrigation_failsafe_deadline = (
        next_irrigation_start
        - timedelta(minutes=irrigation_failsafe_lead_minutes)
        if next_irrigation_start is not None
        else None
    )
    details["irrigation_outage_guard"] = {
        "next_scheduled_start_utc": (
            next_irrigation_start.isoformat()
            if next_irrigation_start is not None
            else None
        ),
        "mower_return_deadline_utc": (
            irrigation_failsafe_deadline.isoformat()
            if irrigation_failsafe_deadline is not None
            else None
        ),
        "required_lead_minutes": irrigation_failsafe_lead_minutes,
        "capture_due": irrigation_capture_due,
        "capture_max_lead_minutes": irrigation_capture_max_lead_minutes,
    }

    # Ein abgeschlossener alter Lauf darf die Vorbereitung des naechsten
    # Hydrawise-Plans nicht blockieren. Der vorherige Nachlauf wird dadurch
    # nicht aufgehoben: Der Maeher bleibt geparkt und der neue Lauf beginnt
    # erst nach allen unveraenderten Sicherheitsnachweisen.
    if state.irrigation_phase == "COMPLETE_HOLD" and irrigation_capture_due:
        try:
            upcoming_plan_id, _upcoming_zones = _validated_upcoming_plan(
                details,
                now_utc=now,
                expected_zone_count=expected_zones,
                expected_relay_ids=expected_relay_ids,
                max_lead_minutes=irrigation_capture_max_lead_minutes,
            )
        except Exception as exc:
            details["irrigation_state_rollover"] = {
                "cleared": False,
                "reason": f"{type(exc).__name__}: {exc}",
            }
        else:
            new_plan = upcoming_plan_id != state.irrigation_plan_id
            safe_to_prepare = (
                hydra_safety.get("available") is True
                and hydra_safety.get("fresh") is True
                and hydra_safety.get("clear_now") is True
                and relay_allowlist_valid
                and not _active_relay_ids(details)
            )
            details["irrigation_state_rollover"] = {
                "cleared": bool(new_plan and safe_to_prepare),
                "new_plan": new_plan,
                "safe_to_prepare": safe_to_prepare,
                "previous_plan_id": state.irrigation_plan_id,
                "upcoming_plan_id": upcoming_plan_id,
            }
            if new_plan and safe_to_prepare:
                state = _clear_irrigation(state)

    if (
        irrigation_due
        and state.irrigation_phase not in ACTIVE_IRRIGATION_PHASES
        and state.irrigation_phase not in {None, "COMPLETE_HOLD", "FAILED"}
    ):
        return _persist_result(
            store=store,
            original=original,
            state=state,
            result=result,
            details=details,
            settings=settings,
            decision_code="IRRIGATION_STATE_INVALID_HOLD",
            message="Ein unbekannter Beregnungszustand sperrt jede automatische Fortsetzung.",
        )
    if irrigation_due and state.irrigation_phase is None:
        try:
            if operator_action == "START_IRRIGATION":
                plan_id, zones = _validated_operator_plan(
                    details,
                    now_utc=now,
                    expected_zone_count=expected_zones,
                    expected_relay_ids=expected_relay_ids,
                )
            else:
                plan_id, zones = _validated_upcoming_plan(
                    details,
                    now_utc=now,
                    expected_zone_count=expected_zones,
                    expected_relay_ids=expected_relay_ids,
                    max_lead_minutes=_env_int(
                        environment,
                        "IRRIGATION_CAPTURE_MAX_LEAD_MINUTES",
                        45,
                        minimum=30,
                        maximum=120,
                    ),
                )
            state = replace(
                state,
                revision=state.revision + 1,
                irrigation_phase="PLANNED",
                irrigation_plan_id=plan_id,
                irrigation_plan_json=json.dumps(
                    zones,
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                ),
                irrigation_suspended_relay_ids_json="[]",
                irrigation_suspension_until_utc=None,
                irrigation_suspension_completed_utc=None,
                irrigation_completed_relay_ids_json="[]",
                irrigation_current_relay_id=None,
                irrigation_zone_start_reserved_utc=None,
                irrigation_zone_started_utc=None,
                irrigation_zone_clear_since_utc=None,
                irrigation_completed_utc=None,
                irrigation_failed_reason=None,
                irrigation_change_candidate_hash=None,
                irrigation_change_candidate_since_utc=None,
                irrigation_cancelled_without_run_utc=None,
            )
            if operator_action == "START_IRRIGATION":
                state = _finish_operator_request(
                    state,
                    "Der sichere Sieben-Zonen-Ablauf wurde vorbereitet.",
                )
                operator_action = None
        except Exception as exc:
            if operator_action == "START_IRRIGATION":
                rejected = _finish_operator_request(
                    state,
                    f"Beregnungsstart abgelehnt: {exc}",
                    status="REJECTED",
                )
                return _persist_result(
                    store=store,
                    original=original,
                    state=rejected,
                    result=result,
                    details=details,
                    settings=settings,
                    decision_code="OPERATOR_IRRIGATION_REJECTED",
                    message="Der manuelle Beregnungsstart erfüllt die Sicherheitsbedingungen nicht.",
                )
            state = _failed_irrigation(state, f"{type(exc).__name__}: {exc}")

    occupancy_sources = _source_parts(block_source) & frozenset({"training", "match", "special"})
    external_park_evidence = (
        override_action in PARK_OVERRIDE_ACTIONS
        or (
            activity in PARKED_ACTIVITIES
            and not state.continuous_mowing_owned
        )
    )
    if (
        parking_block
        and occupancy_sources
        and not state.parked_by_automation
        and external_park_evidence
    ):
        respected = state
        if state.continuous_mowing_owned:
            respected = replace(
                state,
                revision=state.revision + 1,
                continuous_mowing_owned=False,
                continuous_mowing_work_area_id=None,
                continuous_mowing_window_end_utc=None,
            )
        details["external_park_guard"] = {
            "active": True,
            "sources": sorted(occupancy_sources),
            "activity": activity,
            "override_action": override_action,
            "automation_ownership_acquired": False,
            "continuous_mowing_ownership_relinquished": bool(
                state.continuous_mowing_owned
            ),
        }
        return _persist_result(
            store=store,
            original=original,
            state=respected,
            result=result,
            details=details,
            settings=settings,
            decision_code="EXTERNAL_PARK_RESPECTED_DURING_OCCUPANCY",
            message=(
                "Der vorhandene externe Parkzustand bleibt unangetastet; "
                "die Automatik erwirbt kein späteres Startrecht."
            ),
        )

    wants_park = str(decision.get("hypothetical_command") or "").upper() == "PARK"
    if operator_action == "PARK_MOWER":
        wants_park = True
    if (
        state.irrigation_phase in ACTIVE_IRRIGATION_PHASES
        and not state.parked_by_automation
    ):
        wants_park = True
    owns_matching_park = (
        state.parked_by_automation
        and bool(_source_parts(block_source))
        and _source_parts(block_source).issubset(
            _source_parts(state.automation_park_source)
        )
    )
    if parking_block and not owns_matching_park:
        wants_park = True
    if result.decision_code.startswith("HYDRAWISE_") and activity in PARKABLE_ACTIVITIES:
        wants_park = True
    irrigation_park_already_safe = (
        state.parked_by_automation
        and "irrigation" in _source_parts(state.automation_park_source)
        and activity in PARKED_ACTIVITIES
    )
    irrigation_outage_park_due = (
        irrigation_failsafe_deadline is not None
        and now >= irrigation_failsafe_deadline
        and activity in PARK_COMMAND_ACTIVITIES
        and not irrigation_park_already_safe
    )
    if irrigation_outage_park_due:
        wants_park = True

    park_guard_required = (
        bool(_source_parts(block_source) & PARK_GUARD_BLOCK_SOURCES)
        or state.irrigation_phase in ACTIVE_IRRIGATION_PHASES
        or state.irrigation_phase == "FAILED"
        or (
            state.parked_by_automation
            and state.automation_park_source == "operator"
            and not state.automation_restart_allowed
        )
    )
    automation_park_lost = (
        state.parked_by_automation
        and park_guard_required
        and activity in PARKABLE_ACTIVITIES
    )
    park_reassert_grace_minutes = _env_int(
        environment,
        "MOWER_PARK_PROGRESS_GRACE_MINUTES",
        3,
        minimum=1,
        maximum=10,
    )
    park_sent = _parse_time(state.park_command_sent_utc)
    park_confirmed = _parse_time(state.park_confirmed_utc)
    park_reassert_due = automation_park_lost and (
        park_confirmed is not None
        or park_sent is None
        or now - park_sent >= timedelta(minutes=park_reassert_grace_minutes)
    )
    if park_reassert_due:
        wants_park = True
    elif automation_park_lost:
        details["park_guard"] = {
            "active": True,
            "reassert_due": False,
            "grace_minutes": park_reassert_grace_minutes,
            "park_command_sent_utc": state.park_command_sent_utc,
            "activity": activity,
        }
        return _persist_result(
            store=store,
            original=original,
            state=state,
            result=result,
            details=details,
            settings=settings,
            decision_code="PARK_REASSERT_GRACE",
            message="Der frische Parkbefehl erhält kurz Zeit für die Statusübernahme; der Platz bleibt gesperrt.",
        )

    if wants_park:
        park_source = (
            ("operator" if operator_action == "PARK_MOWER" else "")
            or block_source
            or (
                "irrigation"
                if irrigation_outage_park_due
                or state.irrigation_phase in ACTIVE_IRRIGATION_PHASES
                else ""
            )
            or (str(state.automation_park_source or "").strip().lower() if park_reassert_due else "")
            or "hydrawise_unconfirmed"
        )
        block_end = _park_valid_until(
            now_utc=now,
            state=state,
            parking_block=parking_block,
        )
        if not settings.enable_park_commands:
            return _persist_result(
                store=store,
                original=original,
                state=state,
                result=result,
                details=details,
                settings=settings,
                decision_code="FULL_FAILSAFE_PARK_LOCKED",
                message="Der notwendige Parkbefehl bleibt durch ENABLE_PARK_COMMANDS=false gesperrt.",
            )
        if (
            not mower_id
            or error_code != 0
            or mower_state in ERROR_STATES
            or activity not in PARK_COMMAND_ACTIVITIES
        ):
            return _persist_result(
                store=store,
                original=original,
                state=state,
                result=result,
                details=details,
                settings=settings,
                decision_code="MOWER_NOT_SAFE_FOR_PARK_COMMAND",
                message="Der aktuelle Mäherzustand erlaubt keinen sicheren Parkbefehl.",
            )
        intent = CommandIntent(
            action="PARK",
            target=mower_id,
            reason=(
                f"reassert|{park_source}|{parking_block.get('start', '')}|"
                f"{parking_block.get('end', '')}|{state.irrigation_phase or ''}"
                if park_reassert_due
                else f"{park_source}|{parking_block.get('start', '')}|{parking_block.get('end', '')}"
            ),
            valid_until_utc=block_end,
        )
        gate = evaluate_command_gate(
            state=original,
            intent=intent,
            now_utc=now,
            dedupe_minutes=(
                park_reassert_grace_minutes if park_reassert_due else 10
            ),
        )
        details["park_gate"] = {
            "allowed": gate.allowed,
            "code": gate.code,
            "reason": gate.reason,
            "source": park_source,
            "fingerprint": intent.fingerprint,
            "reasserted": park_reassert_due,
        }
        if not gate.allowed:
            return _persist_result(
                store=store,
                original=original,
                state=state,
                result=result,
                details=details,
                settings=settings,
                decision_code=gate.code,
                message=gate.reason,
            )
        client_id = str(environment.get("HUSQVARNA_CLIENT_ID", "")).strip()
        client_secret = str(environment.get("HUSQVARNA_CLIENT_SECRET", "")).strip()
        response = park_sender(client_id, client_secret, mower_id)
        command_state = state.record_command(
            fingerprint=intent.fingerprint,
            sent_utc=now,
            action="PARK",
            park_until_utc=block_end,
            park_source=park_source,
            restart_allowed=_restart_allowed(park_source),
        )
        if operator_action == "PARK_MOWER":
            command_state = _finish_operator_request(
                command_state,
                "Der sichere Parkbefehl wurde gesendet.",
            )
        details["park_action"] = {
            "type": "ParkUntilFurtherNotice",
            "response": response,
            "source": park_source,
            "reasserted": park_reassert_due,
        }
        return _persist_result(
            store=store,
            original=original,
            state=command_state,
            result=result,
            details=details,
            settings=settings,
            decision_code=("PARK_COMMAND_REASSERTED" if park_reassert_due else "PARK_COMMAND_SENT"),
            message=(
                "Der automatische Parkbefehl wurde wegen erneuter Platzfahrt sicher wiederholt."
                if park_reassert_due
                else "ParkUntilFurtherNotice wurde sicher gesendet."
            ),
            command_sent=True,
        )

    if state.irrigation_phase == "FAILED":
        return _persist_result(
            store=store,
            original=original,
            state=state,
            result=result,
            details=details,
            settings=settings,
            decision_code="IRRIGATION_FAILED_HOLD",
            message="Beregnungsfehler gespeichert; automatischer Start bleibt gesperrt.",
        )

    if state.irrigation_phase in ACTIVE_IRRIGATION_PHASES:
        park_confirmed = _parse_time(state.park_confirmed_utc)
        confirmation_minutes = _env_int(
            environment,
            "MOWER_PARK_CONFIRMATION_MINUTES",
            1,
            minimum=1,
            maximum=15,
        )
        if not state.parked_by_automation:
            return _persist_result(
                store=store,
                original=original,
                state=state,
                result=result,
                details=details,
                settings=settings,
                decision_code="IRRIGATION_WAIT_FOR_CONFIRMED_PARK",
                message="Beregnung wartet zunächst auf den eigenen sicheren Parkbefehl.",
            )
        if not settings.full_failsafe_write_gate_enabled:
            return _persist_result(
                store=store,
                original=original,
                state=state,
                result=result,
                details=details,
                settings=settings,
                decision_code="FULL_FAILSAFE_IRRIGATION_LOCKED",
                message="Beregnungsbefehle bleiben durch die unabhängigen Live-Gates gesperrt.",
            )

        hydra_safety = _as_dict(_as_dict(details.get("hydrawise")).get("safety"))
        if (
            not hydra_safety.get("available")
            or not hydra_safety.get("fresh")
            or not relay_allowlist_valid
            or int(hydra_safety.get("selected_zone_count") or 0) != expected_zones
        ):
            return _persist_result(
                store=store,
                original=original,
                state=state,
                result=result,
                details=details,
                settings=settings,
                decision_code="IRRIGATION_STATUS_NOT_SAFE",
                message="Hydrawise ist nicht frisch und vollständig; der Mäher bleibt geparkt.",
            )

        zones = _plan_from_state(state)
        active_ids = _active_relay_ids(details)
        completed = _json_ints(state.irrigation_completed_relay_ids_json)
        suspended = _json_ints(state.irrigation_suspended_relay_ids_json)
        all_ids = [int(zone["relay_id"]) for zone in zones]
        if (
            len(zones) != expected_zones
            or len(set(all_ids)) != expected_zones
            or set(all_ids) != expected_relay_ids
        ):
            failed = _failed_irrigation(
                state,
                "Gespeicherter Sieben-Zonen-Plan ist unvollständig oder nicht freigegeben.",
            )
            return _persist_result(
                store=store,
                original=original,
                state=failed,
                result=result,
                details=details,
                settings=settings,
                decision_code="IRRIGATION_PLAN_INVALID",
                message=failed.irrigation_failed_reason or "Beregnungsplan ungültig.",
            )

        if (
            operator_action == "STOP_IRRIGATION_AFTER_ZONE"
            and state.irrigation_phase in {"PLANNED", "SUSPENDING"}
            and not active_ids
        ):
            stopped = _finish_operator_request(
                _cancel_irrigation_without_run(state, now_utc=now),
                "Die Beregnungsfolge wurde vor dem Wasserstart beendet.",
            )
            return _persist_result(
                store=store,
                original=original,
                state=stopped,
                result=result,
                details=details,
                settings=settings,
                decision_code="IRRIGATION_OPERATOR_CANCELLED_BEFORE_RUN",
                message="Die Beregnung wurde vor dem Wasserstart sicher beendet.",
            )

        if state.irrigation_phase in {"PLANNED", "SUSPENDING"}:
            if active_ids:
                failed = _failed_irrigation(
                    state,
                    "Mindestens eine Hydrawise-Zone läuft bereits, bevor der reguläre "
                    "Sieben-Zonen-Plan vollständig suspendiert wurde.",
                )
                details["irrigation_active_during_suspension"] = {
                    "active_relay_ids": sorted(active_ids),
                    "suspended_relay_ids": sorted(set(suspended)),
                }
                return _persist_result(
                    store=store,
                    original=original,
                    state=failed,
                    result=result,
                    details=details,
                    settings=settings,
                    decision_code="IRRIGATION_ACTIVE_DURING_SUSPENSION",
                    message=failed.irrigation_failed_reason
                    or "Beregnung läuft bereits während der Suspendierung.",
                )
            capture_max_lead_minutes = irrigation_capture_max_lead_minutes
            change_kind, reconciled_plan, change_reason = _reconcile_prestart_plan(
                plan=zones,
                suspended_relay_ids=set(suspended),
                details=details,
                now_utc=now,
                expected_relay_ids=expected_relay_ids,
                capture_max_lead_minutes=capture_max_lead_minutes,
            )
            if change_kind == "UNCHANGED":
                state = _clear_change_candidate(state)
            else:
                observations = _zone_observation_by_relay(details)
                fingerprint_payload: Any = (
                    reconciled_plan
                    if reconciled_plan is not None
                    else [
                        {
                            "relay_id": relay_id,
                            "scheduled": observation.get("scheduled"),
                            "run_seconds": observation.get("run_seconds"),
                            "scheduled_start_utc": observation.get(
                                "scheduled_start_utc"
                            ),
                        }
                        for relay_id, observation in sorted(observations.items())
                    ]
                )
                fingerprint = _plan_change_fingerprint(
                    change_kind,
                    fingerprint_payload,
                )
                confirmation_minutes = _env_int(
                    environment,
                    "IRRIGATION_PLAN_CHANGE_CONFIRMATION_MINUTES",
                    2,
                    minimum=1,
                    maximum=10,
                )
                candidate_state, confirmed = _candidate_confirmation(
                    state,
                    fingerprint=fingerprint,
                    now_utc=now,
                    required_minutes=confirmation_minutes,
                )
                details["irrigation_plan_reconciliation"] = {
                    "kind": change_kind,
                    "confirmed": confirmed,
                    "required_minutes": confirmation_minutes,
                    "reason": change_reason,
                    "suspended_relay_ids": sorted(set(suspended)),
                }
                if not confirmed:
                    return _persist_result(
                        store=store,
                        original=original,
                        state=candidate_state,
                        result=result,
                        details=details,
                        settings=settings,
                        decision_code="IRRIGATION_PLAN_CHANGE_CONFIRMING",
                        message=(
                            "Hydrawise-Änderung erkannt; sie wird vor jeder Aktion "
                            "über mehrere frische Minutenzyklen bestätigt."
                        ),
                    )
                if change_kind == "CANCELLED_OR_DEFERRED":
                    cancelled = _cancel_irrigation_without_run(
                        candidate_state,
                        now_utc=now,
                    )
                    return _persist_result(
                        store=store,
                        original=original,
                        state=cancelled,
                        result=result,
                        details=details,
                        settings=settings,
                        decision_code="IRRIGATION_PLAN_CANCELLED_OR_DEFERRED",
                        message=(
                            "Die bestätigte Hydrawise-Aussetzung gibt das bisherige "
                            "Beregnungsfenster ohne Wasserlauf wieder zum Mähen frei."
                        ),
                    )
                if change_kind == "UPDATED" and reconciled_plan is not None:
                    canonical = json.dumps(
                        reconciled_plan,
                        ensure_ascii=False,
                        sort_keys=True,
                        separators=(",", ":"),
                    )
                    updated = replace(
                        candidate_state,
                        revision=candidate_state.revision + 1,
                        irrigation_plan_id=hashlib.sha256(
                            canonical.encode("utf-8")
                        ).hexdigest(),
                        irrigation_plan_json=json.dumps(
                            reconciled_plan,
                            ensure_ascii=False,
                            sort_keys=True,
                            separators=(",", ":"),
                        ),
                        irrigation_change_candidate_hash=None,
                        irrigation_change_candidate_since_utc=None,
                    )
                    return _persist_result(
                        store=store,
                        original=original,
                        state=updated,
                        result=result,
                        details=details,
                        settings=settings,
                        decision_code="IRRIGATION_PLAN_UPDATED",
                        message=(
                            "Die bestätigten Hydrawise-Start- und Laufzeiten wurden "
                            "für den noch nicht gestarteten Lauf übernommen."
                        ),
                    )
                failed = _failed_irrigation(
                    candidate_state,
                    "Hydrawise-Plan blieb nach der Änderungsbestätigung unvollständig: "
                    f"{change_reason}",
                )
                return _persist_result(
                    store=store,
                    original=original,
                    state=failed,
                    result=result,
                    details=details,
                    settings=settings,
                    decision_code="IRRIGATION_PLAN_CHANGED",
                    message=failed.irrigation_failed_reason or "Beregnungsplan unklar.",
                )
            pending = next((zone for zone in zones if int(zone["relay_id"]) not in suspended), None)
            if pending is None:
                failed = _failed_irrigation(
                    state,
                    "Alle Relay-IDs gelten als suspendiert, aber der Abschlussnachweis fehlt.",
                )
                return _persist_result(
                    store=store,
                    original=original,
                    state=failed,
                    result=result,
                    details=details,
                    settings=settings,
                    decision_code="IRRIGATION_SUSPENSION_PROOF_MISSING",
                    message=failed.irrigation_failed_reason or "Suspendierungsnachweis fehlt.",
                )
            original_end = max(
                _parse_time(zone["scheduled_end_utc"]) for zone in zones
            )
            if original_end is None:
                raise RuntimeError("Beregnungsende fehlt.")
            suspension_margin_minutes = _env_int(
                environment,
                "IRRIGATION_SUSPEND_MARGIN_MINUTES",
                60,
                minimum=15,
                maximum=180,
            )
            stored_suspend_until = _parse_time(
                state.irrigation_suspension_until_utc
            )
            suspend_until = stored_suspend_until or (
                original_end + timedelta(minutes=suspension_margin_minutes)
            )
            if original_end > suspend_until:
                failed = _failed_irrigation(
                    state,
                    "Die geänderten Laufzeiten überschreiten den bereits bestätigten Suspendierungszeitraum.",
                )
                return _persist_result(
                    store=store,
                    original=original,
                    state=failed,
                    result=result,
                    details=details,
                    settings=settings,
                    decision_code="IRRIGATION_DURATION_CHANGE_UNSAFE",
                    message=failed.irrigation_failed_reason or "Suspendierungszeitraum zu kurz.",
                )
            api_key = str(environment.get("HYDRAWISE_API_KEY", "")).strip()
            controller_id = str(environment.get("HYDRAWISE_CONTROLLER_ID", "")).strip() or None
            response = suspend_zone_sender(
                api_key,
                int(pending["relay_id"]),
                int(suspend_until.timestamp()),
                controller_id,
            )
            suspended.append(int(pending["relay_id"]))
            updated = replace(
                state,
                revision=state.revision + 1,
                irrigation_phase="READY" if len(suspended) == expected_zones else "SUSPENDING",
                irrigation_suspended_relay_ids_json=json.dumps(sorted(set(suspended))),
                irrigation_suspension_until_utc=suspend_until.isoformat(),
                irrigation_suspension_completed_utc=(
                    now.isoformat() if len(set(suspended)) == expected_zones else None
                ),
            )
            details["irrigation_action"] = {
                "type": "SuspendScheduledZone",
                "relay_id": int(pending["relay_id"]),
                "suspend_until_utc": suspend_until.isoformat(),
                "response": response,
            }
            return _persist_result(
                store=store,
                original=original,
                state=updated,
                result=result,
                details=details,
                settings=settings,
                decision_code="IRRIGATION_ZONE_SUSPENDED",
                message=f"Planstart für Zone {pending['zone']} wurde sicher suspendiert.",
                command_sent=True,
            )

        if (
            park_confirmed is None
            or now - park_confirmed < timedelta(minutes=confirmation_minutes)
            or activity not in PARKED_ACTIVITIES
            or error_code != 0
            or mower_state in ERROR_STATES
        ):
            return _persist_result(
                store=store,
                original=original,
                state=state,
                result=result,
                details=details,
                settings=settings,
                decision_code="IRRIGATION_WAIT_FOR_CONFIRMED_PARK",
                message="Der Wasserstart wartet auf die fortlaufend bestätigte Parkposition.",
            )

        occupancy_conflict_sources = (
            _source_parts(blocked_now.get("source"))
            | _source_parts(parking_block.get("source"))
        ) & frozenset({"training", "match"})
        if occupancy_conflict_sources:
            details["irrigation_occupancy_guard"] = {
                "active": True,
                "sources": sorted(occupancy_conflict_sources),
            }
            return _persist_result(
                store=store,
                original=original,
                state=state,
                result=result,
                details=details,
                settings=settings,
                decision_code="IRRIGATION_WAIT_FOR_OCCUPANCY_CLEAR",
                message=(
                    "Die regulären Planstarts sind suspendiert; der vorgezogene "
                    "Wasserstart wartet auf das Ende von Training oder Spiel."
                ),
            )

        current_id = state.irrigation_current_relay_id
        if active_ids and (current_id is None or active_ids != {int(current_id)}):
            failed = _failed_irrigation(
                state,
                "Eine unerwartete oder parallele Hydrawise-Zone wurde erkannt.",
            )
            return _persist_result(
                store=store,
                original=original,
                state=failed,
                result=result,
                details=details,
                settings=settings,
                decision_code="IRRIGATION_UNEXPECTED_ACTIVE_ZONE",
                message=failed.irrigation_failed_reason or "Unerwartete Zone.",
            )

        if state.irrigation_phase in {"READY", "RUNNING"} and (
            completed or current_id is not None
        ):
            duration_kind, duration_plan, duration_reason = (
                _reconcile_remaining_durations(
                    plan=zones,
                    completed_relay_ids=set(completed),
                    current_relay_id=current_id,
                    details=details,
                    expected_relay_ids=expected_relay_ids,
                )
            )
            if duration_kind == "UNCHANGED":
                state = _clear_change_candidate(state)
            else:
                observations = _zone_observation_by_relay(details)
                fingerprint = _plan_change_fingerprint(
                    f"REMAINING_DURATIONS_{duration_kind}",
                    duration_plan
                    if duration_plan is not None
                    else [
                        {
                            "relay_id": relay_id,
                            "valid": observation.get("valid"),
                            "run_seconds": observation.get("run_seconds"),
                        }
                        for relay_id, observation in sorted(observations.items())
                    ],
                )
                duration_confirmation_minutes = _env_int(
                    environment,
                    "IRRIGATION_PLAN_CHANGE_CONFIRMATION_MINUTES",
                    2,
                    minimum=1,
                    maximum=10,
                )
                candidate_state, confirmed = _candidate_confirmation(
                    state,
                    fingerprint=fingerprint,
                    now_utc=now,
                    required_minutes=duration_confirmation_minutes,
                )
                details["irrigation_duration_reconciliation"] = {
                    "kind": duration_kind,
                    "confirmed": confirmed,
                    "required_minutes": duration_confirmation_minutes,
                    "reason": duration_reason,
                    "current_relay_id": current_id,
                    "completed_relay_ids": sorted(set(completed)),
                }
                if not confirmed:
                    if state.irrigation_phase == "RUNNING" and active_ids == {
                        int(current_id or 0)
                    }:
                        candidate_state = replace(
                            candidate_state,
                            revision=candidate_state.revision + 1,
                            irrigation_zone_clear_since_utc=None,
                        )
                    return _persist_result(
                        store=store,
                        original=original,
                        state=candidate_state,
                        result=result,
                        details=details,
                        settings=settings,
                        decision_code="IRRIGATION_DURATION_CHANGE_CONFIRMING",
                        message=(
                            "Eine geänderte Laufzeit für eine noch nicht gestartete "
                            "Zone wird über mehrere frische Zyklen bestätigt."
                        ),
                    )
                if duration_kind != "UPDATED" or duration_plan is None:
                    failed = _failed_irrigation(candidate_state, duration_reason)
                    return _persist_result(
                        store=store,
                        original=original,
                        state=failed,
                        result=result,
                        details=details,
                        settings=settings,
                        decision_code="IRRIGATION_PLAN_CHANGED",
                        message=failed.irrigation_failed_reason or "Beregnungsplan unklar.",
                    )
                end_confirmation_minutes = _env_int(
                    environment,
                    "IRRIGATION_ZONE_END_CONFIRMATION_MINUTES",
                    2,
                    minimum=1,
                    maximum=10,
                )
                projected_end = _projected_irrigation_end(
                    plan=duration_plan,
                    completed_relay_ids=set(completed),
                    current_relay_id=current_id,
                    current_started_utc=_parse_time(
                        state.irrigation_zone_started_utc
                    ),
                    now_utc=now,
                    end_confirmation_minutes=end_confirmation_minutes,
                )
                suspension_until = _parse_time(
                    state.irrigation_suspension_until_utc
                )
                details["irrigation_duration_reconciliation"].update(
                    {
                        "projected_end_utc": projected_end.isoformat(),
                        "suspension_until_utc": (
                            suspension_until.isoformat()
                            if suspension_until is not None
                            else None
                        ),
                    }
                )
                if suspension_until is None or projected_end > suspension_until:
                    failed = _failed_irrigation(
                        candidate_state,
                        "Die geänderten Restlaufzeiten reichen über die bestätigte Suspendierung hinaus.",
                    )
                    return _persist_result(
                        store=store,
                        original=original,
                        state=failed,
                        result=result,
                        details=details,
                        settings=settings,
                        decision_code="IRRIGATION_DURATION_CHANGE_UNSAFE",
                        message=failed.irrigation_failed_reason or "Laufzeitänderung nicht sicher.",
                    )
                canonical = json.dumps(
                    duration_plan,
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                )
                updated = replace(
                    candidate_state,
                    revision=candidate_state.revision + 1,
                    irrigation_plan_id=hashlib.sha256(
                        canonical.encode("utf-8")
                    ).hexdigest(),
                    irrigation_plan_json=canonical,
                    irrigation_change_candidate_hash=None,
                    irrigation_change_candidate_since_utc=None,
                    irrigation_zone_clear_since_utc=(
                        None
                        if state.irrigation_phase == "RUNNING"
                        else state.irrigation_zone_clear_since_utc
                    ),
                )
                return _persist_result(
                    store=store,
                    original=original,
                    state=updated,
                    result=result,
                    details=details,
                    settings=settings,
                    decision_code="IRRIGATION_REMAINING_DURATIONS_UPDATED",
                    message=(
                        "Die bestätigten App-Laufzeiten der noch nicht gestarteten "
                        "Zonen wurden übernommen."
                    ),
                )

        if state.irrigation_phase == "READY":
            if operator_action == "STOP_IRRIGATION_AFTER_ZONE":
                stopped = replace(
                    _finish_operator_request(
                        state,
                        "Die Beregnungsfolge wurde vor dem nächsten Zonenstart beendet.",
                    ),
                    revision=state.revision + 2,
                    irrigation_phase="COMPLETE_HOLD",
                    irrigation_completed_utc=now.isoformat(),
                    hydrawise_clear_since_utc=now.isoformat(),
                )
                return _persist_result(
                    store=store,
                    original=original,
                    state=stopped,
                    result=result,
                    details=details,
                    settings=settings,
                    decision_code="IRRIGATION_OPERATOR_STOPPED_BETWEEN_ZONES",
                    message=(
                        "Keine weitere Zone startet; der 120-Minuten-"
                        "Sicherheitsnachlauf beginnt."
                    ),
                )
            if not completed and current_id is None:
                change_kind, reconciled_plan, change_reason = _reconcile_prestart_plan(
                    plan=zones,
                    suspended_relay_ids=set(suspended),
                    details=details,
                    now_utc=now,
                    expected_relay_ids=expected_relay_ids,
                    capture_max_lead_minutes=_env_int(
                        environment,
                        "IRRIGATION_CAPTURE_MAX_LEAD_MINUTES",
                        45,
                        minimum=30,
                        maximum=120,
                    ),
                )
                if change_kind == "UNCHANGED":
                    state = _clear_change_candidate(state)
                else:
                    fingerprint = _plan_change_fingerprint(
                        change_kind,
                        reconciled_plan
                        if reconciled_plan is not None
                        else change_reason,
                    )
                    confirmation_minutes = _env_int(
                        environment,
                        "IRRIGATION_PLAN_CHANGE_CONFIRMATION_MINUTES",
                        2,
                        minimum=1,
                        maximum=10,
                    )
                    candidate_state, confirmed = _candidate_confirmation(
                        state,
                        fingerprint=fingerprint,
                        now_utc=now,
                        required_minutes=confirmation_minutes,
                    )
                    details["irrigation_plan_reconciliation"] = {
                        "kind": change_kind,
                        "confirmed": confirmed,
                        "required_minutes": confirmation_minutes,
                        "reason": change_reason,
                    }
                    if not confirmed:
                        return _persist_result(
                            store=store,
                            original=original,
                            state=candidate_state,
                            result=result,
                            details=details,
                            settings=settings,
                            decision_code="IRRIGATION_PLAN_CHANGE_CONFIRMING",
                            message="Eine Hydrawise-Laufzeitänderung wird stabil bestätigt.",
                        )
                    if change_kind == "UPDATED" and reconciled_plan is not None:
                        canonical = json.dumps(
                            reconciled_plan,
                            ensure_ascii=False,
                            sort_keys=True,
                            separators=(",", ":"),
                        )
                        updated = replace(
                            candidate_state,
                            revision=candidate_state.revision + 1,
                            irrigation_plan_id=hashlib.sha256(
                                canonical.encode("utf-8")
                            ).hexdigest(),
                            irrigation_plan_json=canonical,
                            irrigation_change_candidate_hash=None,
                            irrigation_change_candidate_since_utc=None,
                        )
                        return _persist_result(
                            store=store,
                            original=original,
                            state=updated,
                            result=result,
                            details=details,
                            settings=settings,
                            decision_code="IRRIGATION_PLAN_UPDATED",
                            message="Die bestätigten neuen Zonenlaufzeiten wurden übernommen.",
                        )
                    failed = _failed_irrigation(candidate_state, change_reason)
                    return _persist_result(
                        store=store,
                        original=original,
                        state=failed,
                        result=result,
                        details=details,
                        settings=settings,
                        decision_code="IRRIGATION_PLAN_CHANGED",
                        message=failed.irrigation_failed_reason or "Beregnungsplan unklar.",
                    )
            next_zone = next((zone for zone in zones if int(zone["relay_id"]) not in completed), None)
            if next_zone is None:
                complete = replace(
                    state,
                    revision=state.revision + 1,
                    irrigation_phase="COMPLETE_HOLD",
                    irrigation_completed_utc=now.isoformat(),
                    hydrawise_clear_since_utc=now.isoformat(),
                )
                return _persist_result(
                    store=store,
                    original=original,
                    state=complete,
                    result=result,
                    details=details,
                    settings=settings,
                    decision_code="IRRIGATION_ALL_ZONES_CONFIRMED_COMPLETE",
                    message="Alle sieben Zonen sind bestätigt beendet; der konfigurierte Sicherheitsnachlauf beginnt.",
                )
            if not completed:
                suspension_completed = _parse_time(
                    state.irrigation_suspension_completed_utc
                )
                plan_lease_minutes = _env_int(
                    environment,
                    "IRRIGATION_PLAN_LEASE_MINUTES",
                    3,
                    minimum=2,
                    maximum=10,
                )
                first_planned_start = min(
                    _parse_time(zone["scheduled_start_utc"]) for zone in zones
                )
                suspension_proof_valid = (
                    set(suspended) == expected_relay_ids
                    and suspension_completed is not None
                    and timedelta(0)
                    <= now - suspension_completed
                    <= timedelta(minutes=plan_lease_minutes)
                    and first_planned_start is not None
                    and now < first_planned_start
                )
                if not suspension_proof_valid:
                    failed = _failed_irrigation(
                        state,
                        "Der bestätigte Suspendierungsnachweis ist unvollständig, abgelaufen "
                        "oder der ursprüngliche Beregnungsstart ist bereits erreicht.",
                    )
                    details["irrigation_plan_lease"] = {
                        "valid": False,
                        "required_minutes": plan_lease_minutes,
                        "suspension_completed_utc": state.irrigation_suspension_completed_utc,
                        "suspended_relay_ids": sorted(set(suspended)),
                    }
                    return _persist_result(
                        store=store,
                        original=original,
                        state=failed,
                        result=result,
                        details=details,
                        settings=settings,
                        decision_code="IRRIGATION_PLAN_LEASE_EXPIRED",
                        message=failed.irrigation_failed_reason or "Beregnungsfreigabe abgelaufen.",
                    )
            end_confirmation_minutes = _env_int(
                environment,
                "IRRIGATION_ZONE_END_CONFIRMATION_MINUTES",
                2,
                minimum=1,
                maximum=10,
            )
            projected_end = _projected_irrigation_end(
                plan=zones,
                completed_relay_ids=set(completed),
                current_relay_id=None,
                current_started_utc=None,
                now_utc=now,
                end_confirmation_minutes=end_confirmation_minutes,
            )
            suspension_until = _parse_time(
                state.irrigation_suspension_until_utc
            )
            details["irrigation_sequence_window"] = {
                "projected_end_utc": projected_end.isoformat(),
                "suspension_until_utc": (
                    suspension_until.isoformat()
                    if suspension_until is not None
                    else None
                ),
            }
            if suspension_until is None or projected_end > suspension_until:
                failed = _failed_irrigation(
                    state,
                    "Die verbleibende Zonenfolge passt nicht vollständig in den bestätigten Suspendierungszeitraum.",
                )
                return _persist_result(
                    store=store,
                    original=original,
                    state=failed,
                    result=result,
                    details=details,
                    settings=settings,
                    decision_code="IRRIGATION_DURATION_CHANGE_UNSAFE",
                    message=failed.irrigation_failed_reason or "Beregnungsfenster zu kurz.",
                )
            if active_ids:
                failed = _failed_irrigation(state, "Vor dem Zonenstart läuft bereits eine Zone.")
                return _persist_result(
                    store=store,
                    original=original,
                    state=failed,
                    result=result,
                    details=details,
                    settings=settings,
                    decision_code="IRRIGATION_START_COLLISION",
                    message=failed.irrigation_failed_reason or "Startkollision.",
                )
            reserved = replace(
                state,
                revision=state.revision + 1,
                irrigation_phase="START_RESERVED",
                irrigation_current_relay_id=int(next_zone["relay_id"]),
                irrigation_zone_start_reserved_utc=now.isoformat(),
                irrigation_zone_started_utc=None,
                irrigation_zone_clear_since_utc=None,
            )
            try:
                store.save(reserved, expected_revision=original.revision)
            except Exception as exc:
                return replace(
                    result,
                    decision_code="IRRIGATION_START_RESERVATION_FAILED",
                    message="Zonenstart wurde wegen fehlender persistenter Reservierung nicht gesendet.",
                    command_sent=False,
                    details=_decorate(
                        details,
                        state=state,
                        settings=settings,
                        persisted=False,
                        command_sent=False,
                        error=f"{type(exc).__name__}: {exc}",
                    ),
                )
            api_key = str(environment.get("HYDRAWISE_API_KEY", "")).strip()
            controller_id = str(environment.get("HYDRAWISE_CONTROLLER_ID", "")).strip() or None
            response = start_zone_sender(
                api_key,
                int(next_zone["relay_id"]),
                int(next_zone["run_seconds"]),
                controller_id,
            )
            details["irrigation_action"] = {
                "type": "StartZone",
                "relay_id": int(next_zone["relay_id"]),
                "zone": int(next_zone["zone"]),
                "run_seconds": int(next_zone["run_seconds"]),
                "response": response,
            }
            return replace(
                result,
                decision_code="IRRIGATION_ZONE_START_SENT",
                message=f"Zone {next_zone['zone']} wurde mit der geplanten Laufzeit gestartet.",
                command_sent=True,
                details=_decorate(
                    details,
                    state=reserved,
                    settings=settings,
                    persisted=True,
                    command_sent=True,
                ),
            )

        if state.irrigation_phase == "START_RESERVED":
            reserved_at = _parse_time(state.irrigation_zone_start_reserved_utc)
            if current_id is not None and active_ids == {int(current_id)}:
                running = replace(
                    state,
                    revision=state.revision + 1,
                    irrigation_phase="RUNNING",
                    irrigation_zone_started_utc=now.isoformat(),
                )
                return _persist_result(
                    store=store,
                    original=original,
                    state=running,
                    result=result,
                    details=details,
                    settings=settings,
                    decision_code="IRRIGATION_ZONE_CONFIRMED_RUNNING",
                    message="Der angeforderte Hydrawise-Zonenstart ist physisch bestätigt.",
                )
            start_timeout = _env_int(
                environment,
                "IRRIGATION_START_CONFIRMATION_MINUTES",
                5,
                minimum=2,
                maximum=15,
            )
            if reserved_at is not None and now - reserved_at <= timedelta(minutes=start_timeout):
                return _persist_result(
                    store=store,
                    original=original,
                    state=state,
                    result=result,
                    details=details,
                    settings=settings,
                    decision_code="IRRIGATION_WAIT_FOR_ZONE_START",
                    message="Hydrawise-Zonenstart ist reserviert und wartet auf die Live-Bestätigung.",
                )
            failed = _failed_irrigation(state, "Der reservierte Zonenstart wurde nicht rechtzeitig bestätigt.")
            return _persist_result(
                store=store,
                original=original,
                state=failed,
                result=result,
                details=details,
                settings=settings,
                decision_code="IRRIGATION_ZONE_START_UNCONFIRMED",
                message=failed.irrigation_failed_reason or "Zonenstart unbestätigt.",
            )

        if state.irrigation_phase == "RUNNING":
            if current_id is not None and active_ids == {int(current_id)}:
                running = replace(
                    state,
                    revision=state.revision + 1,
                    irrigation_zone_clear_since_utc=None,
                )
                return _persist_result(
                    store=store,
                    original=original,
                    state=running,
                    result=result,
                    details=details,
                    settings=settings,
                    decision_code="IRRIGATION_ZONE_RUNNING",
                    message="Die erwartete Hydrawise-Zone läuft; der Mäher bleibt geparkt.",
                )
            if active_ids or not hydra_safety.get("clear_now"):
                failed = _failed_irrigation(state, "Das Ende der laufenden Zone ist nicht eindeutig.")
                return _persist_result(
                    store=store,
                    original=original,
                    state=failed,
                    result=result,
                    details=details,
                    settings=settings,
                    decision_code="IRRIGATION_ZONE_END_UNCLEAR",
                    message=failed.irrigation_failed_reason or "Zonenende unklar.",
                )
            clear_since = _parse_time(state.irrigation_zone_clear_since_utc)
            if clear_since is None:
                confirming = replace(
                    state,
                    revision=state.revision + 1,
                    irrigation_zone_clear_since_utc=now.isoformat(),
                )
                return _persist_result(
                    store=store,
                    original=original,
                    state=confirming,
                    result=result,
                    details=details,
                    settings=settings,
                    decision_code="IRRIGATION_CONFIRM_ZONE_END",
                    message="Hydrawise meldet die Zone erstmals beendet; Bestätigung läuft.",
                )
            end_confirmation = _env_int(
                environment,
                "IRRIGATION_ZONE_END_CONFIRMATION_MINUTES",
                2,
                minimum=1,
                maximum=10,
            )
            if now - clear_since < timedelta(minutes=end_confirmation):
                return _persist_result(
                    store=store,
                    original=original,
                    state=state,
                    result=result,
                    details=details,
                    settings=settings,
                    decision_code="IRRIGATION_CONFIRM_ZONE_END",
                    message="Das Zonenende wird fortlaufend bestätigt.",
                )
            if operator_action == "STOP_IRRIGATION_AFTER_ZONE":
                stopped = replace(
                    _finish_operator_request(
                        state,
                        "Die laufende Zone ist bestätigt beendet; keine weitere Zone startet.",
                    ),
                    revision=state.revision + 2,
                    irrigation_phase="COMPLETE_HOLD",
                    irrigation_current_relay_id=None,
                    irrigation_zone_start_reserved_utc=None,
                    irrigation_zone_started_utc=None,
                    irrigation_zone_clear_since_utc=None,
                    irrigation_completed_utc=now.isoformat(),
                    hydrawise_clear_since_utc=now.isoformat(),
                )
                return _persist_result(
                    store=store,
                    original=original,
                    state=stopped,
                    result=result,
                    details=details,
                    settings=settings,
                    decision_code="IRRIGATION_OPERATOR_STOPPED_AFTER_ZONE",
                    message=(
                        "Die Zone ist bestätigt beendet; der 120-Minuten-"
                        "Sicherheitsnachlauf beginnt."
                    ),
                )
            started_at = _parse_time(state.irrigation_zone_started_utc)
            current_zone = next(
                (
                    zone
                    for zone in zones
                    if int(zone["relay_id"]) == int(current_id or 0)
                ),
                None,
            )
            if started_at is None or current_zone is None:
                failed = _failed_irrigation(
                    state,
                    "Der Beginn oder die Laufzeit der aktiven Zone fehlt im persistenten Nachweis.",
                )
                return _persist_result(
                    store=store,
                    original=original,
                    state=failed,
                    result=result,
                    details=details,
                    settings=settings,
                    decision_code="IRRIGATION_ZONE_END_UNCLEAR",
                    message=failed.irrigation_failed_reason or "Zonenende unklar.",
                )
            early_stop_tolerance_seconds = _env_int(
                environment,
                "IRRIGATION_EARLY_STOP_TOLERANCE_SECONDS",
                120,
                minimum=30,
                maximum=600,
            )
            earliest_normal_end = started_at + timedelta(
                seconds=(
                    int(current_zone["run_seconds"])
                    - early_stop_tolerance_seconds
                )
            )
            if now < earliest_normal_end:
                cancelled = replace(
                    state,
                    revision=state.revision + 1,
                    irrigation_phase="COMPLETE_HOLD",
                    irrigation_current_relay_id=None,
                    irrigation_zone_start_reserved_utc=None,
                    irrigation_zone_started_utc=None,
                    irrigation_zone_clear_since_utc=None,
                    irrigation_completed_utc=now.isoformat(),
                    hydrawise_clear_since_utc=now.isoformat(),
                    irrigation_change_candidate_hash=None,
                    irrigation_change_candidate_since_utc=None,
                )
                details["irrigation_early_stop"] = {
                    "relay_id": int(current_id or 0),
                    "started_utc": started_at.isoformat(),
                    "planned_run_seconds": int(current_zone["run_seconds"]),
                    "confirmed_clear_utc": now.isoformat(),
                    "remaining_zones_cancelled": (
                        expected_zones - len(set(completed))
                    ),
                }
                return _persist_result(
                    store=store,
                    original=original,
                    state=cancelled,
                    result=result,
                    details=details,
                    settings=settings,
                    decision_code="IRRIGATION_RUN_CANCELLED_EARLY",
                    message=(
                        "Die laufende Hydrawise-Folge wurde bestätigt vorzeitig beendet; "
                        "keine weitere Zone startet und der konfigurierte Sicherheitsnachlauf beginnt."
                    ),
                )
            completed.append(int(current_id or 0))
            all_complete = len(set(completed)) == expected_zones
            advanced = replace(
                state,
                revision=state.revision + 1,
                irrigation_phase="COMPLETE_HOLD" if all_complete else "READY",
                irrigation_completed_relay_ids_json=json.dumps(sorted(set(completed))),
                irrigation_current_relay_id=None,
                irrigation_zone_start_reserved_utc=None,
                irrigation_zone_started_utc=None,
                irrigation_zone_clear_since_utc=None,
                irrigation_completed_utc=now.isoformat() if all_complete else None,
                hydrawise_clear_since_utc=now.isoformat() if all_complete else state.hydrawise_clear_since_utc,
            )
            return _persist_result(
                store=store,
                original=original,
                state=advanced,
                result=result,
                details=details,
                settings=settings,
                decision_code=(
                    "IRRIGATION_ALL_ZONES_CONFIRMED_COMPLETE"
                    if all_complete
                    else "IRRIGATION_ZONE_CONFIRMED_COMPLETE"
                ),
                message=(
                    "Alle sieben Zonen sind bestätigt beendet; der konfigurierte Sicherheitsnachlauf beginnt."
                    if all_complete
                    else "Zone ist bestätigt beendet; die nächste Planzone wird vorbereitet."
                ),
            )

    if blocked_now or parking_block:
        return _persist_result(
            store=store,
            original=original,
            state=state,
            result=result,
            details=details,
            settings=settings,
            decision_code="OCCUPANCY_OR_IRRIGATION_HOLD",
            message="Spiel, Training oder Beregnung sperrt den Platz weiterhin.",
        )

    hydra_safety = _as_dict(_as_dict(details.get("hydrawise")).get("safety"))
    release_minutes = _env_int(
        environment,
        "HYDRAWISE_CLEAR_CONFIRMATION_MINUTES",
        120,
        minimum=1,
        maximum=1440,
    )
    release = evaluate_continuous_clear_confirmation(
        available=bool(hydra_safety.get("available")),
        fresh=bool(hydra_safety.get("fresh")),
        clear_now=bool(hydra_safety.get("clear_now")),
        physical_reason=str(hydra_safety.get("reason") or "Hydrawise ist nicht frei."),
        clear_since_utc=state.hydrawise_clear_since_utc,
        now_utc=now,
        required_clear_minutes=release_minutes,
        persistent_state_available=True,
    )
    cancelled_without_run_release = (
        state.irrigation_cancelled_without_run_utc is not None
        and bool(hydra_safety.get("available"))
        and bool(hydra_safety.get("fresh"))
        and bool(hydra_safety.get("clear_now"))
        and relay_allowlist_valid
        and int(hydra_safety.get("active_zone_count") or 0) == 0
        and int(hydra_safety.get("imminent_zone_count") or 0) == 0
    )
    details["hydrawise_release_gate"] = {
        **release.to_dict(),
        "cancelled_without_run_release": cancelled_without_run_release,
        "effective_allowed": release.allowed or cancelled_without_run_release,
    }
    if not release.allowed and not cancelled_without_run_release:
        if activity in PARKABLE_ACTIVITIES and settings.enable_park_commands:
            intent = CommandIntent(
                action="PARK",
                target=mower_id,
                reason="hydrawise_unconfirmed|continuous-release-hold",
                valid_until_utc=now + timedelta(days=1),
            )
            gate = evaluate_command_gate(state=original, intent=intent, now_utc=now)
            if gate.allowed and mower_id and error_code == 0 and mower_state not in ERROR_STATES:
                client_id = str(environment.get("HUSQVARNA_CLIENT_ID", "")).strip()
                client_secret = str(environment.get("HUSQVARNA_CLIENT_SECRET", "")).strip()
                response = park_sender(client_id, client_secret, mower_id)
                parked = state.record_command(
                    fingerprint=intent.fingerprint,
                    sent_utc=now,
                    action="PARK",
                    park_until_utc=now + timedelta(days=1),
                    park_source="hydrawise_unconfirmed",
                    restart_allowed=True,
                )
                details["park_action"] = {"type": "ParkUntilFurtherNotice", "response": response}
                return _persist_result(
                    store=store,
                    original=original,
                    state=parked,
                    result=result,
                    details=details,
                    settings=settings,
                    decision_code="PARK_COMMAND_SENT_FOR_HYDRAWISE_HOLD",
                    message="Mäher wurde wegen der unvollständigen Hydrawise-Freigabe geparkt.",
                    command_sent=True,
                )
        return _persist_result(
            store=store,
            original=original,
            state=state,
            result=result,
            details=details,
            settings=settings,
            decision_code="HYDRAWISE_CLEAR_CONFIRMATION_HOLD",
            message=release.reason,
        )

    manual_lock = activity in MANUAL_ACTIVITIES or mower_state in MANUAL_STATES
    park_override_is_ours = (
        state.parked_by_automation
        and override_action in PARK_OVERRIDE_ACTIONS
        and int(external_reason or 0) == AUTOMATION_EXTERNAL_REASON
    )
    owned_park_release = (
        state.parked_by_automation
        and state.automation_restart_allowed
        and activity in PARKED_ACTIVITIES
    )
    external_override = (
        override_action not in NO_OVERRIDE
        and not park_override_is_ours
        and not owned_park_release
        and not state.continuous_mowing_owned
    )
    if manual_lock or error_code != 0 or mower_state in ERROR_STATES or external_override:
        return _persist_result(
            store=store,
            original=original,
            state=state,
            result=result,
            details=details,
            settings=settings,
            decision_code="MANUAL_OR_ERROR_HOLD",
            message="Manueller, externer oder fehlerhafter Zustand wird nicht automatisch aufgehoben.",
        )

    if (
        state.parked_by_automation
        and not state.automation_restart_allowed
        and operator_action != "START_MOWING"
    ):
        return _persist_result(
            store=store,
            original=original,
            state=state,
            result=result,
            details=details,
            settings=settings,
            decision_code="OPERATOR_PARK_HOLD",
            message=(
                "Der Platzwart hat den Mäher geparkt; ein ausdrücklicher "
                "sicherer Start ist erforderlich."
            ),
        )

    if (
        state.continuous_mowing_owned
        and override_action in PARK_OVERRIDE_ACTIONS
        and not state.parked_by_automation
    ):
        relinquished = replace(
            state,
            revision=state.revision + 1,
            continuous_mowing_owned=False,
            continuous_mowing_work_area_id=None,
            continuous_mowing_window_end_utc=None,
        )
        return _persist_result(
            store=store,
            original=original,
            state=relinquished,
            result=result,
            details=details,
            settings=settings,
            decision_code="EXTERNAL_PARK_RESPECTED",
            message="Ein externer Parkbefehl beendet den automatischen Mähauftrag.",
        )

    mowing_now = activity in MOWING_ACTIVITIES
    continue_threshold = _env_int(
        environment,
        "MOWER_CONTINUE_MIN_BATTERY_PERCENT",
        60,
        minimum=30,
        maximum=95,
    )
    restart_threshold = _env_int(
        environment,
        "MOWER_RESTART_BATTERY_PERCENT",
        90,
        minimum=60,
        maximum=100,
    )
    continuing_owned_job = (
        state.continuous_mowing_owned
        or previous_activity in MOWING_ACTIVITIES
    )
    turnaround_before_dock = activity == "GOING_HOME" and state.continuous_mowing_owned

    if activity == "GOING_HOME" and not turnaround_before_dock:
        return _persist_result(
            store=store,
            original=original,
            state=state,
            result=result,
            details=details,
            settings=settings,
            decision_code="WAIT_FOR_MOWER_AT_STATION",
            message="Die Rückfahrt wird nicht unterbrochen; danach entscheidet der Akkustand.",
        )
    if (
        activity not in PARKED_ACTIVITIES
        and not turnaround_before_dock
        and not mowing_now
    ):
        return _persist_result(
            store=store,
            original=original,
            state=state,
            result=result,
            details=details,
            settings=settings,
            decision_code="MOWER_STATE_UNCLEAR_HOLD",
            message="Der Mäherzustand ist für einen automatischen Start nicht eindeutig.",
        )

    required_battery = continue_threshold if continuing_owned_job else restart_threshold
    if battery < required_battery and not mowing_now:
        return _persist_result(
            store=store,
            original=original,
            state=state,
            result=result,
            details=details,
            settings=settings,
            decision_code=(
                "MOWER_LOW_BATTERY_HOME_ALLOWED"
                if turnaround_before_dock
                else "MOWER_BATTERY_CHARGING"
            ),
            message=(
                f"Der Akku liegt bei {battery} %; die Heimfahrt zum Laden wird nicht unterbrochen."
                if turnaround_before_dock
                else f"Der Akku liegt bei {battery} %; Start ab {required_battery} %."
            ),
        )

    window = _as_dict(current_plan.get("mowing_window_now"))
    window_end = _parse_time(window.get("end"))
    if window_end is None:
        return _persist_result(
            store=store,
            original=original,
            state=state,
            result=result,
            details=details,
            settings=settings,
            decision_code="NO_SAFE_MOWING_WINDOW",
            message="Es gibt kein ausreichend langes freies Mähfenster.",
        )
    minimum_window = _env_int(
        environment,
        "MINIMUM_MOWING_WINDOW_MINUTES",
        30,
        minimum=15,
        maximum=180,
    )
    planner_return_deadline = window_end - timedelta(
        minutes=settings.park_lookahead_minutes
    )
    safe_command_deadline = min(
        deadline
        for deadline in (
            planner_return_deadline,
            irrigation_failsafe_deadline,
        )
        if deadline is not None
    )
    remaining = int((safe_command_deadline - now).total_seconds() // 60)
    duration = min(
        _env_int(
            environment,
            "MAX_AUTOMATIC_START_MINUTES",
            720,
            minimum=30,
            maximum=1440,
        ),
        max(0, remaining),
    )
    existing_command_end = _parse_time(state.continuous_mowing_window_end_utc)
    failsafe_refresh = (
        mowing_now
        and state.continuous_mowing_owned
        and (
            existing_command_end is None
            or existing_command_end > safe_command_deadline
        )
    )
    details["mower_outage_guard"] = {
        "planner_window_end_utc": window_end.isoformat(),
        "planner_return_deadline_utc": planner_return_deadline.isoformat(),
        "irrigation_return_deadline_utc": (
            irrigation_failsafe_deadline.isoformat()
            if irrigation_failsafe_deadline is not None
            else None
        ),
        "command_deadline_utc": safe_command_deadline.isoformat(),
        "existing_command_end_utc": (
            existing_command_end.isoformat()
            if existing_command_end is not None
            else None
        ),
        "failsafe_refresh_required": failsafe_refresh,
    }
    if mowing_now and not failsafe_refresh:
        if operator_action == "START_MOWING":
            state = _finish_operator_request(
                state,
                "Der Mäher mäht bereits innerhalb des sicheren Zeitfensters.",
            )
        return _persist_result(
            store=store,
            original=original,
            state=state,
            result=result,
            details=details,
            settings=settings,
            decision_code="CONTINUOUS_MOWING_ACTIVE",
            message=(
                "Der Mäher arbeitet mit einem ausfallsicher begrenzten Auftrag "
                "bis zur nächsten sicheren Rückkehrfrist weiter."
            ),
        )
    if (
        (not failsafe_refresh and (remaining < minimum_window or duration < minimum_window))
        or (failsafe_refresh and duration < 1)
    ):
        return _persist_result(
            store=store,
            original=original,
            state=state,
            result=result,
            details=details,
            settings=settings,
            decision_code="MOWING_WINDOW_TOO_SHORT",
            message="Das verbleibende ausfallsichere Mähfenster ist zu kurz.",
        )
    work_area_id = int(target_area.get("id") or 0)
    if work_area_id <= 0 or target_area.get("enabled") is False:
        return _persist_result(
            store=store,
            original=original,
            state=state,
            result=result,
            details=details,
            settings=settings,
            decision_code="WORK_AREA_NOT_SAFE",
            message="Die Rasenfläche ist nicht eindeutig und aktiv identifiziert.",
        )
    if not settings.full_failsafe_write_gate_enabled:
        return _persist_result(
            store=store,
            original=original,
            state=state,
            result=result,
            details=details,
            settings=settings,
            decision_code="FULL_FAILSAFE_START_LOCKED",
            message="Der sichere Start ist berechnet, aber die Live-Gates sind noch gesperrt.",
        )

    if not state.parked_by_automation and not state.continuous_mowing_owned:
        intent = CommandIntent(
            action="PARK",
            target=mower_id,
            reason="continuous|bootstrap-owned-park",
            valid_until_utc=safe_command_deadline,
        )
        gate = evaluate_command_gate(state=original, intent=intent, now_utc=now)
        if not gate.allowed:
            return _persist_result(
                store=store,
                original=original,
                state=state,
                result=result,
                details=details,
                settings=settings,
                decision_code=gate.code,
                message=gate.reason,
            )
        client_id = str(environment.get("HUSQVARNA_CLIENT_ID", "")).strip()
        client_secret = str(environment.get("HUSQVARNA_CLIENT_SECRET", "")).strip()
        response = park_sender(client_id, client_secret, mower_id)
        owned = state.record_command(
            fingerprint=intent.fingerprint,
            sent_utc=now,
            action="PARK",
            park_until_utc=safe_command_deadline,
            park_source="continuous",
            restart_allowed=True,
        )
        details["park_action"] = {"type": "ParkUntilFurtherNotice", "response": response}
        return _persist_result(
            store=store,
            original=original,
            state=owned,
            result=result,
            details=details,
            settings=settings,
            decision_code="CONTINUOUS_MOWING_OWNERSHIP_ESTABLISHED",
            message="Sichere Automationshoheit wurde übernommen; Start folgt nach Parkbestätigung.",
            command_sent=True,
        )

    intent = CommandIntent(
        action="START",
        target=mower_id,
        reason=(
            "continuous-failsafe-refresh"
            if failsafe_refresh
            else (
                "continuous-turnaround"
                if turnaround_before_dock
                else "continuous"
            )
        )
        + f"|{safe_command_deadline.isoformat()}|hydrawise-clear:{state.hydrawise_clear_since_utc}",
        valid_until_utc=safe_command_deadline,
    )
    gate = evaluate_command_gate(state=state, intent=intent, now_utc=now)
    details["start_gate"] = {
        "allowed": gate.allowed,
        "code": gate.code,
        "reason": gate.reason,
        "duration_minutes": duration,
        "work_area_id": work_area_id,
        "command_deadline_utc": safe_command_deadline.isoformat(),
        "failsafe_refresh": failsafe_refresh,
        "fingerprint": intent.fingerprint,
    }
    if not gate.allowed:
        return _persist_result(
            store=store,
            original=original,
            state=state,
            result=result,
            details=details,
            settings=settings,
            decision_code=gate.code,
            message=gate.reason,
        )
    command_end = min(
        safe_command_deadline,
        now + timedelta(minutes=duration),
    )
    command_state = state.record_command(
        fingerprint=intent.fingerprint,
        sent_utc=now,
        action="START",
        work_area_id=work_area_id,
        mowing_window_end_utc=command_end,
        continuous_mowing=True,
    )
    if operator_action == "START_MOWING":
        command_state = _finish_operator_request(
            command_state,
            "Der sichere Mähstart wurde gesendet.",
        )
    if (
        state.irrigation_phase == "COMPLETE_HOLD"
        or state.irrigation_cancelled_without_run_utc is not None
    ):
        command_state = _clear_irrigation(command_state)
    client_id = str(environment.get("HUSQVARNA_CLIENT_ID", "")).strip()
    client_secret = str(environment.get("HUSQVARNA_CLIENT_SECRET", "")).strip()
    if failsafe_refresh:
        response = start_sender(
            client_id,
            client_secret,
            mower_id,
            work_area_id,
            duration,
        )
        try:
            store.save(command_state, expected_revision=original.revision)
        except Exception as exc:
            details["start_action"] = {
                "type": "StartInWorkArea",
                "response": response,
                "duration_minutes": duration,
                "work_area_id": work_area_id,
                "continuous_mowing": True,
                "command_end_utc": command_end.isoformat(),
                "failsafe_refresh": True,
                "state_confirmation_error": f"{type(exc).__name__}: {exc}",
            }
            return replace(
                result,
                decision_code="CONTINUOUS_MOWING_FAILSAFE_REFRESH_SENT_STATE_UNCONFIRMED",
                message=(
                    "Husqvarna hat die sichere kürzere Laufzeit angenommen; "
                    "die Zustandsbestätigung wird im nächsten Zyklus wiederholt."
                ),
                command_sent=True,
                details=_decorate(
                    details,
                    state=state,
                    settings=settings,
                    persisted=False,
                    command_sent=True,
                    error=f"{type(exc).__name__}: {exc}",
                ),
            )
    else:
        try:
            store.save(command_state, expected_revision=original.revision)
        except Exception as exc:
            return replace(
                result,
                decision_code="MOWER_START_RESERVATION_FAILED",
                message="Mäherstart wurde wegen fehlender persistenter Reservierung nicht gesendet.",
                command_sent=False,
                details=_decorate(
                    details,
                    state=state,
                    settings=settings,
                    persisted=False,
                    command_sent=False,
                    error=f"{type(exc).__name__}: {exc}",
                ),
            )
        response = start_sender(
            client_id,
            client_secret,
            mower_id,
            work_area_id,
            duration,
        )
    details["start_action"] = {
        "type": "StartInWorkArea",
        "response": response,
        "duration_minutes": duration,
        "work_area_id": work_area_id,
        "continuous_mowing": True,
        "command_end_utc": command_end.isoformat(),
        "failsafe_refresh": failsafe_refresh,
        "turnaround_before_dock": turnaround_before_dock,
        "hydrawise_release_minutes": release_minutes,
    }
    return replace(
        result,
        decision_code=(
            "CONTINUOUS_MOWING_FAILSAFE_REFRESHED"
            if failsafe_refresh
            else (
                "CONTINUOUS_MOWING_TURNAROUND_SENT"
                if turnaround_before_dock
                else "CONTINUOUS_MOWING_START_SENT"
            )
        ),
        message=(
            "Der laufende Mähauftrag wurde im Mäher selbst bis zur sicheren Rückkehrfrist begrenzt."
            if failsafe_refresh
            else (
                "Der ausreichend geladene Mäher wurde vor der Station sicher erneut in die Rasenfläche geschickt."
                if turnaround_before_dock
                else "Der Mäher wurde im sicheren freien Fenster zum kontinuierlichen Mähen gestartet."
            )
        ),
        command_sent=True,
        details=_decorate(
            details,
            state=command_state,
            settings=settings,
            persisted=True,
            command_sent=True,
        ),
    )
