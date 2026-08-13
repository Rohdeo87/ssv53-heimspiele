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
from mower.hydrawise import evaluate_continuous_clear_confirmation
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
    {"training", "match", "irrigation", "hydrawise_unconfirmed", "continuous"}
)
ACTIVE_IRRIGATION_PHASES = frozenset(
    {"PLANNED", "SUSPENDING", "READY", "START_RESERVED", "RUNNING"}
)
PARK_GUARD_BLOCK_SOURCES = frozenset({"training", "match", "irrigation"})


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


def _active_relay_ids(details: dict[str, Any]) -> set[int]:
    safety = _as_dict(_as_dict(details.get("hydrawise")).get("safety"))
    values = safety.get("active_relay_ids")
    if not isinstance(values, list):
        return set()
    return {int(value) for value in values}


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
        "irrigation_completed_relay_ids": _json_ints(
            state.irrigation_completed_relay_ids_json
        ),
        "irrigation_completed_utc": state.irrigation_completed_utc,
        "irrigation_failed_reason": state.irrigation_failed_reason,
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
        irrigation_completed_relay_ids_json=None,
        irrigation_current_relay_id=None,
        irrigation_zone_start_reserved_utc=None,
        irrigation_zone_started_utc=None,
        irrigation_zone_clear_since_utc=None,
        irrigation_completed_utc=None,
        irrigation_failed_reason=None,
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
    expected_zones = _env_int(
        environment,
        "HYDRAWISE_EXPECTED_ZONE_COUNT",
        7,
        minimum=1,
        maximum=24,
    )

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
            plan_id, zones = _validated_upcoming_plan(
                details,
                now_utc=now,
                expected_zone_count=expected_zones,
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
                irrigation_completed_relay_ids_json="[]",
                irrigation_current_relay_id=None,
                irrigation_zone_start_reserved_utc=None,
                irrigation_zone_started_utc=None,
                irrigation_zone_clear_since_utc=None,
                irrigation_completed_utc=None,
                irrigation_failed_reason=None,
            )
        except Exception as exc:
            state = _failed_irrigation(state, f"{type(exc).__name__}: {exc}")

    occupancy_sources = _source_parts(block_source) & frozenset({"training", "match"})
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

    park_guard_required = (
        bool(_source_parts(block_source) & PARK_GUARD_BLOCK_SOURCES)
        or state.irrigation_phase in ACTIVE_IRRIGATION_PHASES
        or state.irrigation_phase == "FAILED"
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
            block_source
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
        if (
            not state.parked_by_automation
            or park_confirmed is None
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
                message="Beregnung wartet auf die fortlaufend bestätigte Parkposition.",
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
        if len(zones) != expected_zones or len(set(all_ids)) != expected_zones:
            failed = _failed_irrigation(state, "Gespeicherter Sieben-Zonen-Plan ist unvollständig.")
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

        if state.irrigation_phase in {"PLANNED", "SUSPENDING"}:
            pending = next((zone for zone in zones if int(zone["relay_id"]) not in suspended), None)
            if pending is None:
                ready = replace(state, revision=state.revision + 1, irrigation_phase="READY")
                return _persist_result(
                    store=store,
                    original=original,
                    state=ready,
                    result=result,
                    details=details,
                    settings=settings,
                    decision_code="IRRIGATION_SCHEDULE_SUSPENDED",
                    message="Alle sieben späteren Planstarts sind suspendiert; der vorgezogene Lauf ist bereit.",
                )
            original_end = max(
                _parse_time(zone["scheduled_end_utc"]) for zone in zones
            )
            if original_end is None:
                raise RuntimeError("Beregnungsende fehlt.")
            suspend_until = original_end + timedelta(
                minutes=_env_int(
                    environment,
                    "IRRIGATION_SUSPEND_MARGIN_MINUTES",
                    60,
                    minimum=15,
                    maximum=180,
                )
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

        if state.irrigation_phase == "READY":
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
                    message="Alle sieben Zonen sind bestätigt beendet; der 90-Minuten-Nachlauf beginnt.",
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
                    "Alle sieben Zonen sind bestätigt beendet; der 90-Minuten-Nachlauf beginnt."
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
        90,
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
    details["hydrawise_release_gate"] = release.to_dict()
    if not release.allowed:
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
            decision_code="HYDRAWISE_90_MINUTE_HOLD",
            message=release.reason,
        )

    manual_lock = activity in MANUAL_ACTIVITIES or mower_state in MANUAL_STATES
    park_override_is_ours = (
        state.parked_by_automation
        and override_action in PARK_OVERRIDE_ACTIONS
        and int(external_reason or 0) == AUTOMATION_EXTERNAL_REASON
    )
    external_override = (
        override_action not in NO_OVERRIDE
        and not park_override_is_ours
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

    if activity in MOWING_ACTIVITIES:
        return _persist_result(
            store=store,
            original=original,
            state=state,
            result=result,
            details=details,
            settings=settings,
            decision_code="CONTINUOUS_MOWING_ACTIVE",
            message="Der Mäher arbeitet bereits und mäht bis zur nächsten sicheren Sperre weiter.",
        )
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
    if activity not in PARKED_ACTIVITIES and not turnaround_before_dock:
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
    if battery < required_battery:
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
    remaining = int((window_end - now).total_seconds() // 60)
    duration = min(
        _env_int(
            environment,
            "MAX_AUTOMATIC_START_MINUTES",
            720,
            minimum=30,
            maximum=1440,
        ),
        max(0, remaining - 5),
    )
    if remaining < minimum_window or duration < minimum_window:
        return _persist_result(
            store=store,
            original=original,
            state=state,
            result=result,
            details=details,
            settings=settings,
            decision_code="MOWING_WINDOW_TOO_SHORT",
            message="Das verbleibende freie Mähfenster ist zu kurz.",
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
            valid_until_utc=window_end,
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
            park_until_utc=window_end,
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
            "continuous-turnaround"
            if turnaround_before_dock
            else "continuous"
        )
        + f"|{window_end.isoformat()}|hydrawise-clear:{state.hydrawise_clear_since_utc}",
        valid_until_utc=window_end,
    )
    gate = evaluate_command_gate(state=state, intent=intent, now_utc=now)
    details["start_gate"] = {
        "allowed": gate.allowed,
        "code": gate.code,
        "reason": gate.reason,
        "duration_minutes": duration,
        "work_area_id": work_area_id,
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
    command_state = state.record_command(
        fingerprint=intent.fingerprint,
        sent_utc=now,
        action="START",
        work_area_id=work_area_id,
        mowing_window_end_utc=window_end,
        continuous_mowing=True,
    )
    if state.irrigation_phase == "COMPLETE_HOLD":
        command_state = _clear_irrigation(command_state)
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
    client_id = str(environment.get("HUSQVARNA_CLIENT_ID", "")).strip()
    client_secret = str(environment.get("HUSQVARNA_CLIENT_SECRET", "")).strip()
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
        "turnaround_before_dock": turnaround_before_dock,
        "hydrawise_release_minutes": release_minutes,
    }
    return replace(
        result,
        decision_code=(
            "CONTINUOUS_MOWING_TURNAROUND_SENT"
            if turnaround_before_dock
            else "CONTINUOUS_MOWING_START_SENT"
        ),
        message=(
            "Der ausreichend geladene Mäher wurde vor der Station sicher erneut in die Rasenfläche geschickt."
            if turnaround_before_dock
            else "Der Mäher wurde im sicheren freien Fenster zum kontinuierlichen Mähen gestartet."
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
