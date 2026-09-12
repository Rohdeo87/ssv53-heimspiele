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
from mower.config_source import InputUnavailable
from mower.input_failure_guard import run_input_failure_guard
from mower.coordination_request import canonical_schedule_id as coordination_source_plan_id
from mower.coordination_execution import (
    consume_request as consume_coordination_request,
    enabled as coordination_execution_enabled,
    mark_terminal as mark_coordination_terminal,
    reserve as reserve_coordination,
    start_authorized as coordination_start_authorized,
)
from mower.cutting_height import (
    cutting_height_mm_to_percent,
    supports_metric_cutting_height,
)
from mower.husqvarna_actions import park_until_further_notice
from mower.husqvarna_cutting_height_actions import set_work_area_cutting_height
from mower.husqvarna_statistics_actions import reset_cutting_blade_usage_time
from mower.husqvarna_start_actions import start_in_work_area
from mower.full_height_control import reconcile_full_height_sends, run_full_height_control
from mower.start_dispatch_guard import (
    StartDispatchBlocked,
    StartReservationReleaseError,
    prepare_start_dispatch,
    release_start_reservation,
)
from mower.manual_session import (
    ManualSessionError,
    block_key as manual_block_key,
    dump_manual_session,
    load_manual_session,
    observe_session,
    resolve_manual_permissions,
    with_session_status,
)
from mower.device_send_guard import (
    DeviceSendBlocked,
    dispatch_device_send,
    load_device_send_journal,
    ordinary_suspension_until,
    reconcile_device_send,
    unresolved_device_sends,
)
from mower.manual_water_conflict import (
    ManualWaterConflictError,
    dump_transaction as dump_manual_water_transaction,
    load_transaction as load_manual_water_transaction,
    new_transaction as new_manual_water_transaction,
    update_transaction as update_manual_water_transaction,
    water_conflict_context,
)
from mower.hydrawise import (
    evaluate_continuous_clear_confirmation,
    parse_relay_id_allowlist,
)
from mower.hydrawise_actions import start_zone_for, stop_zone_now, suspend_zone_until
from mower.irrigation_schedule import (
    SCHEDULE_ACTIONS,
    append_history as append_irrigation_schedule_history,
    dump_object as dump_irrigation_schedule_object,
    load_object as load_irrigation_schedule_object,
    parse_utc as parse_irrigation_schedule_utc,
    START_BLOCKING_SCHEDULE_STATUSES,
)
from mower.irrigation_operating_window import (
    OperatingWindowResult,
    operating_bounds,
    validate_fresh_start,
    validate_zone_sequence,
)
from mower import irrigation_park_hold, onsite_dock_proof, automatic_takeover
from mower.runtime import ControlMode, CycleResult, RuntimeSettings
from mower.safety import CommandIntent, evaluate_command_gate, occupancy_override_allowed
from mower.state import AutomationState
from mower.state_store import AzureTableStateStore, StateConflictError, StateStore


ReadOnlyRunner = Callable[..., CycleResult]
StateStoreFactory = Callable[[Mapping[str, str]], StateStore]
ParkSender = Callable[[str, str, str], dict[str, Any]]
StartSender = Callable[[str, str, str, int, int], dict[str, Any]]
SuspendZoneSender = Callable[[str, int, int, str | int | None], dict[str, Any]]
StartZoneSender = Callable[[str, int, int, str | int | None], dict[str, Any]]
StopZoneSender = Callable[[str, int, str | int | None], dict[str, Any]]
CuttingHeightSender = Callable[[str, str, str, int, int], dict[str, Any]]
BladeUsageResetSender = Callable[[str, str, str], dict[str, Any]]
InputFailureRunner = Callable[..., CycleResult]
Clock = Callable[[], datetime]

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
    {"PLANNED", "SUSPENDING", "READY", "START_RESERVED", "RUNNING", "STOPPING"}
)
RECOVERABLE_EXTERNAL_PLAN_FAILURE = (
    "RuntimeError: Hydrawise-Zonenplan enthält ungültige Werte."
)
PARTIAL_IRRIGATION_WINDOW_FAILURE = (
    "Die verbleibende Zonenfolge passt nicht vollständig in den bestätigten "
    "Suspendierungszeitraum."
)
EXPIRED_IRRIGATION_PLAN_LEASE_FAILURE = (
    "Der bestätigte Suspendierungsnachweis ist unvollständig, abgelaufen "
    "oder der ursprüngliche Beregnungsstart ist bereits erreicht."
)
ONSITE_DOCK_PROOF_LOST_STOP_REASON = (
    "Der gebundene Vor-Ort-Stationsnachweis ist verloren; "
    "die zugeordnete Wasserzone wird sicher beendet."
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


def _env_enabled(environment: Mapping[str, str], name: str) -> bool:
    return str(environment.get(name) or "false").strip().lower() == "true"


def _manual_dry_release_proof(
    state: AutomationState,
    hydrawise_safety: Mapping[str, Any],
    *,
    now_utc: datetime,
    environment: Mapping[str, str],
) -> dict[str, Any]:
    full_origins = {
        "IRRIGATION_ACTIVE", "IRRIGATION_END", "POSSIBLE_IRRIGATION_DURING_GAP",
    }
    origin = state.hydrawise_clear_origin or (
        "IRRIGATION_END" if state.irrigation_phase == "COMPLETE_HOLD" else "DATA_GAP"
    )
    minutes = _env_int(
        environment,
        "POST_IRRIGATION_DRYING_MINUTES" if origin in full_origins
        else "HYDRAWISE_DATA_GAP_CONFIRMATION_MINUTES",
        150 if origin in full_origins else 2,
        minimum=150 if origin in full_origins else 1,
        maximum=1440,
    )
    proof = evaluate_continuous_clear_confirmation(
        available=bool(hydrawise_safety.get("available")),
        fresh=bool(hydrawise_safety.get("fresh")),
        clear_now=bool(hydrawise_safety.get("clear_now")),
        physical_reason=str(hydrawise_safety.get("reason") or "Hydrawise ist nicht frei."),
        clear_since_utc=state.hydrawise_clear_since_utc,
        now_utc=now_utc,
        required_clear_minutes=minutes,
        persistent_state_available=True,
        drying_since_utc=state.hydrawise_drying_since_utc,
        telemetry_confirmation_minutes=_env_int(
            environment, "HYDRAWISE_DATA_GAP_CONFIRMATION_MINUTES", 2,
            minimum=1, maximum=1440,
        ),
    ).to_dict()
    return {
        **proof,
        "current_water_clear": (
            hydrawise_safety.get("clear_now") is True
            and int(hydrawise_safety.get("active_zone_count") or 0) == 0
            and int(hydrawise_safety.get("imminent_zone_count") or 0) == 0
        ),
    }


def _reconcile_observed_device_writes(
    state: AutomationState,
    mower: Mapping[str, Any],
    details: Mapping[str, Any],
    *,
    now_utc: datetime,
) -> AutomationState:
    """Confirm only device effects proven by a newer source observation."""
    current = state
    try:
        pending = unresolved_device_sends(current)
    except DeviceSendBlocked:
        return current
    mower_observed = None
    try:
        mower_observed = datetime.fromtimestamp(
            float(mower.get("status_timestamp_ms")) / 1000, tz=timezone.utc,
        )
    except (TypeError, ValueError, OSError):
        pass
    hydra_observed = _hydrawise_source_observation(dict(details), now_utc=now_utc)
    active = _active_relay_ids(dict(details))
    hydra_safety = _as_dict(_as_dict(details.get("hydrawise")).get("safety"))
    observations = _zone_observation_by_relay(dict(details))
    try:
        observed_relays = {int(value) for value in hydra_safety.get("observed_relay_ids", [])}
        expected_relays = {int(value) for value in hydra_safety.get("expected_relay_ids", [])}
        active_count = int(hydra_safety.get("active_zone_count"))
    except (TypeError, ValueError):
        observed_relays, expected_relays, active_count = set(), set(), -1
    exact_relay_state = bool(
        observed_relays
        and observed_relays == expected_relays
        and active_count == len(active)
    )
    for entry in pending:
        dispatched = _parse_time(entry.get("dispatched_at_utc"))
        if dispatched is None:
            continue
        kind = entry.get("kind")
        proven = False
        evidence = ""
        if (
            kind == "PARK" and mower_observed is not None
            and mower_observed > dispatched
            and str(mower.get("mode") or "").strip().upper() == "HOME"
            and str(mower.get("activity") or "").strip().upper() in PARKED_ACTIVITIES
        ):
            proven, evidence = True, "fresh-home-station"
        elif kind == "WATER_START" and hydra_observed is not None and hydra_observed > dispatched:
            try:
                relay = int(entry.get("target"))
            except (TypeError, ValueError):
                relay = 0
            if relay > 0 and active == {relay}:
                proven, evidence = True, f"fresh-active-relay-{relay}"
        elif kind in {"SUSPEND", "WATER_STOP"} and hydra_observed is not None and hydra_observed > dispatched:
            try:
                relay = int(entry.get("target"))
            except (TypeError, ValueError):
                relay = 0
            observation = observations.get(relay, {})
            suspend_until = ordinary_suspension_until(entry)
            next_start = _parse_time(observation.get("scheduled_start_utc"))
            if (
                kind == "SUSPEND" and relay > 0 and exact_relay_state
                and observation.get("valid") is True
                and observation.get("scheduled") is False
            ):
                proven, evidence = True, f"fresh-suspended-relay-{relay}"
            elif (
                kind == "SUSPEND" and suspend_until is not None
                and exact_relay_state and relay in expected_relays and not active
                and observation.get("valid") is True
                and observation.get("scheduled") is True
                and next_start is not None and next_start > max(suspend_until, now_utc)
            ):
                # A next run AFTER this write's absolute suspension is positive
                # schedule evidence, even if compaction changed the plan id.
                # Requires a returned transport and newer complete vendor read;
                # an unknown/delayed transport is never cleared by this branch.
                proven, evidence = True, f"fresh-next-run-after-suspension-relay-{relay}"
            elif (
                kind == "WATER_STOP" and relay > 0 and exact_relay_state
                and relay not in active
            ):
                proven, evidence = True, f"fresh-inactive-relay-{relay}"
        if proven:
            current = reconcile_device_send(
                current, str(entry["intent_key"]), now_utc, evidence,
            )
    return current


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


def _mower_status_is_fresh(
    mower: Mapping[str, Any],
    *,
    now_utc: datetime,
    max_age_seconds: int,
) -> bool:
    raw = mower.get("status_timestamp_ms")
    try:
        observed = datetime.fromtimestamp(float(raw) / 1000, tz=timezone.utc)
    except (TypeError, ValueError, OSError):
        return False
    age_seconds = (now_utc.astimezone(timezone.utc) - observed).total_seconds()
    return -60 <= age_seconds <= max_age_seconds


def _park_confirmation_ready(
    state: AutomationState,
    *,
    now_utc: datetime,
    activity: str,
    mower_state: str,
    mower_mode: str,
    confirmation_minutes: int,
    required_observations: int,
) -> bool:
    confirmed = _parse_time(state.park_confirmed_utc)
    normalized_activity = str(activity or "").strip().upper()
    normalized_state = str(mower_state or "").strip().upper()
    normalized_mode = str(mower_mode or "").strip().upper()
    safe_station_status = normalized_activity in PARKED_ACTIVITIES or (
        normalized_activity == "NOT_APPLICABLE"
        and normalized_state == "PAUSED"
        and confirmed is not None
        and int(state.park_confirmed_observations or 0) >= required_observations
    )
    return (
        state.parked_by_automation
        and normalized_mode == "HOME"
        and safe_station_status
        and confirmed is not None
        and now_utc - confirmed >= timedelta(minutes=confirmation_minutes)
        and int(state.park_confirmed_observations or 0) >= required_observations
    )


def _water_park_authorized(state, mower, *, now_utc, environment,
                           max_age_seconds, expected_json=None,
                           expected_onsite_json=None,
                           allowed_intent_key=None):
    if onsite_dock_proof.valid_for_plan(
        state,
        mower,
        environment,
        now_utc,
        expected_json=expected_onsite_json,
        allowed_intent_key=allowed_intent_key,
    ):
        return True
    if irrigation_park_hold.enabled(environment):
        # An active manual START uses its existing per-conflict authorization
        # and fresh-event checks. It never acquires the unattended HOME proof.
        try:
            session = load_manual_session(state)
        except ManualSessionError:
            return False
        if session and session.get("kind") == "START" and session.get("status") != "ENDED":
            return _mower_status_is_fresh(mower, now_utc=now_utc, max_age_seconds=max_age_seconds)
        return irrigation_park_hold.valid(state, mower, now_utc=now_utc,
                                          expected_json=expected_json)
    return _mower_status_is_fresh(mower, now_utc=now_utc, max_age_seconds=max_age_seconds)


def _water_dispatch_station_authorized(
    state, mower, *, now_utc, environment, max_age_seconds,
    expected_json=None, expected_onsite_json=None, allowed_intent_key=None,
):
    if onsite_dock_proof.valid_for_plan(
        state, mower, environment, now_utc,
        expected_json=expected_onsite_json,
        allowed_intent_key=allowed_intent_key,
    ):
        return True
    normal_station_safe = (
        str(mower.get("mode") or "").strip().upper() == "HOME"
        and mower.get("connected") is True
        and str(mower.get("activity") or "").strip().upper() in PARKED_ACTIVITIES
        and type(mower.get("error_code")) is int
        and mower.get("error_code") == 0
        and str(mower.get("state") or "").strip().upper()
        not in ERROR_STATES | {"OFF"}
    )
    if not normal_station_safe:
        return False
    return _water_park_authorized(
        state, mower, now_utc=now_utc, environment=environment,
        max_age_seconds=max_age_seconds, expected_json=expected_json,
    )


def _revoke_park_hold_after_read_failure(factory, environment, now):
    """Persist station-proof uncertainty before the protective PARK path."""
    for _attempt in range(2):
        try:
            store = factory(environment)
            previous = store.load()
            revoked = previous
            if irrigation_park_hold.enabled(environment):
                revoked = irrigation_park_hold.invalidate(
                    revoked, now_utc=now, reason="INPUT_UNAVAILABLE",
                )
            if (
                onsite_dock_proof.enabled(environment)
                and previous.irrigation_onsite_dock_proof_json is not None
            ):
                revoked = onsite_dock_proof.invalidate(
                    revoked, now_utc=now, reason="INPUT_UNAVAILABLE",
                )
            store.save(replace(revoked, revision=previous.revision + 1),
                       expected_revision=previous.revision)
            return "REVOKED"
        except StateConflictError:
            continue
        except Exception:
            return "STATE_UNAVAILABLE"
    return "STATE_CONFLICT"


def _occupancy_block_key(block: Mapping[str, Any]) -> str | None:
    """Bindet eine Platzwart-Freigabe an genau den angezeigten Belegungsblock."""

    start = str(block.get("start") or "").strip()
    end = str(block.get("end") or "").strip()
    source = str(block.get("source") or "").strip().lower()
    if not start or not end or not source:
        return None
    return "|".join((start, end, source))


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


def _operator_manual_irrigation(
    plan: list[dict[str, Any]],
    *,
    plan_id: str | None,
    expected_relay_ids: frozenset[int],
) -> bool:
    """Verify the persisted identity of an explicit operator plan."""

    if not plan or not plan_id or not all(
        zone.get("operator_manual") is True for zone in plan
    ):
        return False
    try:
        relay_ids = [zone.get("relay_id") for zone in plan]
        canonical = json.dumps(
            plan, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        )
        return (
            len(relay_ids) == len(expected_relay_ids)
            and all(type(relay_id) is int and relay_id > 0 for relay_id in relay_ids)
            and len(set(relay_ids)) == len(expected_relay_ids)
            and set(relay_ids) == set(expected_relay_ids)
            and hashlib.sha256(canonical.encode("utf-8")).hexdigest() == plan_id
        )
    except (AttributeError, TypeError, ValueError):
        return False


def _operator_manual_water_is_owned(
    state: AutomationState,
    *,
    plan: list[dict[str, Any]],
    active_relay_ids: set[int],
    expected_relay_ids: frozenset[int],
) -> bool:
    """Prove that observed water is the relay currently owned by a manual run."""

    current_relay = state.irrigation_current_relay_id
    if (
        state.irrigation_phase not in {"START_RESERVED", "RUNNING", "STOPPING"}
        or type(current_relay) is not int
        or active_relay_ids != {current_relay}
        or not _operator_manual_irrigation(
            plan,
            plan_id=state.irrigation_plan_id,
            expected_relay_ids=expected_relay_ids,
        )
    ):
        return False
    return any(
        zone.get("relay_id") == current_relay
        and zone.get("selected", True) is not False
        for zone in plan
    )


def _schedule_override(state: AutomationState) -> dict[str, Any] | None:
    return load_irrigation_schedule_object(
        state.irrigation_schedule_override_json,
        "Beregnungsplan-Anpassung",
    )


def _schedule_request(state: AutomationState) -> dict[str, Any]:
    return load_irrigation_schedule_object(
        state.operator_request_irrigation_schedule_json,
        "Beregnungsplan-Anfrage",
    ) or {}


def _customized_schedule_plan(
    zones: list[dict[str, Any]],
    request: dict[str, Any],
) -> list[dict[str, Any]]:
    desired = parse_irrigation_schedule_utc(
        request.get("desiredStart"), "Gewünschter Beregnungsstart"
    )
    requested = {
        int(item["zone"]): item
        for item in request.get("zones", [])
        if isinstance(item, dict)
    }
    if len(requested) != len(zones):
        raise RuntimeError("Die angepasste Zonenliste ist nicht vollständig.")
    result: list[dict[str, Any]] = []
    cursor = desired
    for original in sorted(zones, key=lambda item: int(item["zone"])):
        zone_number = int(original["zone"])
        item = requested.get(zone_number)
        if item is None:
            raise RuntimeError(f"Zone {zone_number} fehlt in der Anpassung.")
        seconds = int(item["runSeconds"])
        selected = item.get("selected") is not False
        if not 60 <= seconds <= 7200:
            raise RuntimeError("Eine angepasste Laufzeit liegt außerhalb der Grenzen.")
        end = cursor + timedelta(seconds=seconds)
        result.append(
            {
                **original,
                "run_seconds": seconds,
                "selected": selected,
                "scheduled_start_utc": cursor.isoformat(),
                "scheduled_end_utc": end.isoformat(),
                "operator_schedule_override": True,
            }
        )
        cursor = end
    if not any(zone.get("selected") is not False for zone in result):
        raise RuntimeError("Mindestens eine Zone muss aktiviert bleiben.")
    return result


def _schedule_override_summary(override: dict[str, Any]) -> str:
    kind = str(override.get("kind") or "")
    if kind == "PAUSE":
        return f"Beregnung pausiert bis {override.get('suspend_until_utc', '')}."
    if kind == "SKIP_NEXT":
        return "Nächster Beregnungslauf wurde ausgesetzt."
    if kind == "CUSTOM_NEXT":
        selected = sum(
            1 for zone in override.get("zones", []) if zone.get("selected") is not False
        )
        return (
            f"Nächster Lauf angepasst: {selected} Zonen ab "
            f"{override.get('desired_start_utc', '')}."
        )
    if kind == "RESUME":
        return "Automatischer Hydrawise-Plan wird wieder freigegeben."
    return "Beregnungsplan wurde angepasst."


def _schedule_possible_irrigation_start(
    override: dict[str, Any] | None,
) -> datetime | None:
    if not override:
        return None
    kind = str(override.get("kind") or "")
    status = str(override.get("status") or "")
    if kind == "CUSTOM_NEXT" and status in {
        "VERIFYING",
        "APPLYING",
        "CONFIRMING",
        "ACTIVE",
        "EXECUTING",
    }:
        return _parse_time(override.get("desired_start_utc"))
    if kind in {"PAUSE", "SKIP_NEXT", "RESUME"}:
        return _parse_time(override.get("suspend_until_utc"))
    return None


def _new_schedule_override(
    *,
    action: str,
    request: dict[str, Any],
    current: dict[str, Any] | None,
    details: dict[str, Any],
    state: AutomationState,
    now_utc: datetime,
    expected_zone_count: int,
    expected_relay_ids: frozenset[int],
) -> dict[str, Any] | None:
    base = {
        "version": 1,
        "request_id": state.operator_request_id,
        "created_utc": now_utc.isoformat(),
        "commanded_relay_ids": [],
        "confirm_since_utc": None,
    }
    if action == "RESUME_IRRIGATION_SCHEDULE":
        if current is None:
            return None
        return {
            **base,
            "kind": "RESUME",
            "status": "PARKING",
            # Ein kurzer Endzeitpunkt in der Zukunft wird von Hydrawise als
            # endliche Suspendierung verarbeitet. Danach gilt wieder der
            # unveränderte, in Hydrawise gepflegte Wochenplan.
            "suspend_until_utc": (now_utc + timedelta(minutes=1)).isoformat(),
            "replaces_kind": current.get("kind"),
        }
    if current is not None and (
        str(current.get("status") or "") not in {"COMPLETED", "REJECTED"}
        or bool(current.get("commanded_relay_ids"))
    ):
        raise RuntimeError(
            "Es besteht bereits eine Beregnungsplan-Anpassung. Diese muss zuerst beendet werden."
        )
    if action == "PAUSE_IRRIGATION_UNTIL":
        until = parse_irrigation_schedule_utc(
            request.get("pauseUntil"), "Ende der Beregnungspause"
        )
        return {
            **base,
            "kind": "PAUSE",
            "status": "APPLYING",
            "suspend_until_utc": until.isoformat(),
        }

    plan_id, original_zones = _validated_upcoming_plan(
        details,
        now_utc=now_utc,
        expected_zone_count=expected_zone_count,
        expected_relay_ids=expected_relay_ids,
        max_lead_minutes=14 * 24 * 60,
    )
    original_start = min(
        _parse_time(zone["scheduled_start_utc"]) for zone in original_zones
    )
    original_end = max(
        _parse_time(zone["scheduled_end_utc"]) for zone in original_zones
    )
    if original_start is None or original_end is None:
        raise RuntimeError("Der nächste Hydrawise-Lauf besitzt keine sicheren Zeiten.")
    if original_start - now_utc < timedelta(minutes=12):
        raise RuntimeError(
            "Der nächste Lauf beginnt zu früh für Prüfung, sieben Zonenbefehle und Bestätigung."
        )
    if action == "SKIP_NEXT_IRRIGATION":
        return {
            **base,
            "kind": "SKIP_NEXT",
            "status": "VERIFYING",
            "verify_since_utc": now_utc.isoformat(),
            "source_plan_id": plan_id,
            "source_start_utc": original_start.isoformat(),
            "source_end_utc": original_end.isoformat(),
            # 45 Minuten nach dem ausgelassenen Lauf bleibt der Plan noch
            # suspendiert. Mit 40 Minuten Ausfallvorlauf kann der Mäher so den
            # gesamten freigewordenen Lauf nutzen und ist trotzdem fünf
            # Minuten vor der möglichen Hydrawise-Rückkehr im Dock.
            "suspend_until_utc": (original_end + timedelta(minutes=45)).isoformat(),
            "source_zones": original_zones,
        }
    if action == "CUSTOMIZE_NEXT_IRRIGATION":
        customized = _customized_schedule_plan(original_zones, request)
        desired_start = min(
            _parse_time(zone["scheduled_start_utc"]) for zone in customized
        )
        desired_end = max(
            _parse_time(zone["scheduled_end_utc"]) for zone in customized
        )
        if desired_start is None or desired_end is None:
            raise RuntimeError("Der angepasste Lauf besitzt keine sicheren Zeiten.")
        suspension_end = max(original_end, desired_end) + timedelta(minutes=180)
        return {
            **base,
            "kind": "CUSTOM_NEXT",
            "status": "VERIFYING",
            "verify_since_utc": now_utc.isoformat(),
            "source_plan_id": plan_id,
            "source_start_utc": original_start.isoformat(),
            "source_end_utc": original_end.isoformat(),
            "desired_start_utc": desired_start.isoformat(),
            "desired_end_utc": desired_end.isoformat(),
            "suspend_until_utc": suspension_end.isoformat(),
            "source_zones": original_zones,
            "zones": customized,
        }
    raise RuntimeError("Unbekannte Beregnungsplan-Anpassung.")


def _validated_upcoming_plan(
    details: dict[str, Any],
    *,
    now_utc: datetime,
    expected_zone_count: int,
    expected_relay_ids: frozenset[int],
    max_lead_minutes: int,
    preserve_approved_gaps: bool = False,
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
            if preserve_approved_gaps and gap < 0:
                raise RuntimeError("Freigegebene Bewässerungszonen dürfen sich nicht überschneiden.")
            if not preserve_approved_gaps and not -5 <= gap <= 120:
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


def _validated_operator_single_zone_plan(
    details: dict[str, Any],
    *,
    now_utc: datetime,
    expected_zone_count: int,
    expected_relay_ids: frozenset[int],
    requested_zone: int | None,
    requested_run_seconds: int | None,
) -> tuple[str, list[dict[str, Any]]]:
    if requested_zone is None or requested_run_seconds is None:
        raise RuntimeError("Zone oder Laufzeit fehlt.")
    if not 60 <= requested_run_seconds <= 7200:
        raise RuntimeError("Die Laufzeit muss zwischen 1 und 120 Minuten liegen.")
    _plan_id, zones = _validated_operator_plan(
        details,
        now_utc=now_utc,
        expected_zone_count=expected_zone_count,
        expected_relay_ids=expected_relay_ids,
    )
    selected = [zone for zone in zones if int(zone["zone"]) == requested_zone]
    if len(selected) != 1:
        raise RuntimeError("Die gewählte Zone gehört nicht zu den sieben freigegebenen Zonen.")
    start = now_utc + timedelta(minutes=45)
    for zone in zones:
        zone["selected"] = int(zone["zone"]) == requested_zone
        zone["operator_single_zone"] = True
        if zone["selected"]:
            zone["run_seconds"] = requested_run_seconds
        end = start + timedelta(seconds=int(zone["run_seconds"]))
        zone["scheduled_start_utc"] = start.isoformat()
        zone["scheduled_end_utc"] = end.isoformat()
        start = end
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


def _hydrawise_source_observation(
    details: Mapping[str, Any], *, now_utc: datetime
) -> datetime | None:
    """Return one usable Hydrawise source observation, never a cycle timestamp.

    The cache may have been populated by another reader.  Its source timestamp
    is therefore a valid independent observation for this controller, whereas
    the local ``new_observation`` flag deliberately is not required here.
    A source timestamp must agree with the safety projection and may not be
    future dated.  Callers compare this value with their persisted predecessor
    before they count it or let a deadline advance.
    """
    hydrawise = _as_dict(details.get("hydrawise"))
    safety = _as_dict(hydrawise.get("safety"))
    if not (
        safety.get("available") is True
        and safety.get("fresh") is True
        and safety.get("relay_set_valid") is True
    ):
        return None
    observed = _parse_time(safety.get("observed_at_utc"))
    cache = _as_dict(hydrawise.get("cache"))
    # Direct reads and cache reads both prove only the manufacturer's time.
    # A controller tick is never a substitute for a source observation.
    if not cache:
        if observed is None or observed > now_utc + timedelta(seconds=30):
            return None
        return observed
    cached_observed = _parse_time(cache.get("source_observed_at_utc"))
    if cache and (cached_observed is None or cached_observed != observed):
        return None
    if observed is None or observed > now_utc + timedelta(seconds=30):
        return None
    return observed


def _candidate_confirmation(
    state: AutomationState,
    *,
    fingerprint: str,
    now_utc: datetime,
    observed_utc: datetime | None,
    required_minutes: int,
    max_gap_minutes: int = 3,
    not_before_utc: datetime | None = None,
) -> tuple[AutomationState, bool]:
    # A timer tick, cache hit, stale source, or a source from before the
    # requested effect has no proof value.  Keep the candidate so a later
    # independent source can still complete the same proof.
    if observed_utc is None or (
        not_before_utc is not None and observed_utc <= not_before_utc
    ):
        return state, False
    since = _parse_time(state.irrigation_change_candidate_since_utc)
    last_observed = _parse_time(state.irrigation_change_candidate_observed_utc)
    if (
        state.irrigation_change_candidate_hash != fingerprint
        or since is None
        or last_observed is None
    ):
        return (
            replace(
                state,
                revision=state.revision + 1,
                irrigation_change_candidate_hash=fingerprint,
                irrigation_change_candidate_since_utc=observed_utc.isoformat(),
                irrigation_change_candidate_observed_utc=observed_utc.isoformat(),
            ),
            False,
        )
    if observed_utc <= last_observed:
        return state, False
    if observed_utc - last_observed > timedelta(minutes=max_gap_minutes):
        return (
            replace(
                state,
                revision=state.revision + 1,
                irrigation_change_candidate_since_utc=observed_utc.isoformat(),
                irrigation_change_candidate_observed_utc=observed_utc.isoformat(),
            ),
            False,
        )
    updated = replace(
        state,
        revision=state.revision + 1,
        irrigation_change_candidate_observed_utc=observed_utc.isoformat(),
    )
    return updated, observed_utc - since >= timedelta(minutes=required_minutes)


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
        irrigation_change_candidate_observed_utc=None,
    )


def _record_suspension_revalidation_observation(
    state: AutomationState,
    *,
    now_utc: datetime,
    observed_utc: datetime | None,
    not_before_utc: datetime | None = None,
    max_gap_seconds: int,
    required_observations: int,
) -> tuple[AutomationState, bool]:
    """Bestätigt eine weiterhin wirksame Suspendierung über getrennte Zyklen."""

    if observed_utc is None or (
        not_before_utc is not None and observed_utc <= not_before_utc
    ):
        return state, False
    last_seen = _parse_time(state.irrigation_suspension_revalidation_last_seen_utc)
    last_observed = _parse_time(state.irrigation_suspension_revalidation_observed_utc)
    if last_observed is not None and observed_utc <= last_observed:
        return state, False
    consecutive = (
        last_seen is not None
        and timedelta(0) < observed_utc - last_seen <= timedelta(seconds=max_gap_seconds)
    )
    observations = (
        int(state.irrigation_suspension_revalidation_observations or 0) + 1
        if consecutive
        else 1
    )
    updated = replace(
        state,
        revision=state.revision + 1,
        irrigation_suspension_revalidation_last_seen_utc=observed_utc.isoformat(),
        irrigation_suspension_revalidation_observed_utc=observed_utc.isoformat(),
        irrigation_suspension_revalidation_observations=observations,
    )
    return updated, observations >= required_observations


def _record_zone_clear_observation(
    state: AutomationState,
    *,
    observed_utc: datetime | None,
    not_before_utc: datetime | None,
) -> tuple[AutomationState, bool]:
    """Record a distinct post-command clear source, never a control tick."""
    if observed_utc is None or (
        not_before_utc is not None and observed_utc <= not_before_utc
    ):
        return state, False
    since = _parse_time(state.irrigation_zone_clear_since_utc)
    last = _parse_time(state.irrigation_zone_clear_observed_utc)
    if since is None:
        return replace(
            state,
            revision=state.revision + 1,
            irrigation_zone_clear_since_utc=observed_utc.isoformat(),
            irrigation_zone_clear_observed_utc=observed_utc.isoformat(),
        ), False
    # Older persisted runs predate the observation-identity field.  Their
    # already stored clear boundary is retained as a conservative predecessor;
    # only a later source can migrate it forward.
    if last is None:
        last = since
    if observed_utc <= last:
        return state, False
    if observed_utc - last > timedelta(minutes=3):
        return replace(
            state, revision=state.revision + 1,
            irrigation_zone_clear_since_utc=observed_utc.isoformat(),
            irrigation_zone_clear_observed_utc=observed_utc.isoformat(),
        ), False
    return replace(
        state,
        revision=state.revision + 1,
        irrigation_zone_clear_observed_utc=observed_utc.isoformat(),
    ), True


def _own_prefix_suspension_compacted(
    plan: list[dict[str, Any]], suspended: set[int],
    live: dict[int, dict[str, Any]], observations: dict[int, dict[str, Any]],
    *, safety: dict[str, Any], tolerance_seconds: int,
) -> bool:
    """Recognize only the observed native shift caused by suspending a prefix.

    Hydrawise brings the remaining contiguous queue forward after we suspend
    its first zones. Mixing those shifted starts with our retained first zones
    creates overlaps. Preserve the original plan only when the whole shift is
    explained by that exact prefix and no zone identity or duration changed.
    """
    if not 0 < len(suspended) < len(plan):
        return False
    if (safety.get("available") is not True or safety.get("fresh") is not True
        or safety.get("active_zone_count") != 0 or safety.get("active_relay_ids") != []):
        return False
    try:
        ordered = sorted(plan, key=lambda z: _parse_time(z["scheduled_start_utc"]))
        ids = [int(z["relay_id"]) for z in ordered]
        starts = [_parse_time(z["scheduled_start_utc"]) for z in ordered]
        durations = [int(z["run_seconds"]) for z in ordered]
        if set(ids[:len(suspended)]) != suspended or len(set(ids)) != len(ids):
            return False
        if any(start is None for start in starts) or any(d <= 0 for d in durations):
            return False
        if any(starts[i] != starts[i-1] + timedelta(seconds=durations[i-1]) for i in range(1,len(starts))):
            return False
        original_end = starts[-1] + timedelta(seconds=durations[-1])
        removed = timedelta(seconds=sum(durations[:len(suspended)]))
        for i, zone in enumerate(ordered):
            observed = observations[ids[i]]
            if (observed.get("valid") is not True or observed.get("running") is not False
                or int(observed.get("zone") or 0) != int(zone["zone"])
                or int(observed.get("run_seconds") or 0) != durations[i]):
                return False
            current_start = _parse_time(_as_dict(live.get(ids[i])).get("scheduled_start_utc"))
            if ids[i] in suspended:
                if current_start is not None and current_start <= original_end:
                    return False
            elif current_start is None or abs((current_start-(starts[i]-removed)).total_seconds()) > tolerance_seconds:
                return False
    except (KeyError, TypeError, ValueError, OverflowError):
        return False
    return True


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

    if plan and all(
        zone.get("operator_manual") is True
        or zone.get("operator_schedule_override") is True
        for zone in plan
    ):
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

    if _own_prefix_suspension_compacted(
        plan, suspended_relay_ids, live_by_relay, observations,
        safety=_as_dict(_as_dict(details.get("hydrawise")).get("safety")),
        tolerance_seconds=tolerance_seconds,
    ):
        return "UNCHANGED", plan, "Eigene Zonen-Suspendierung hat den Hydrawise-Restplan vorgezogen; der ursprüngliche Ablauf bleibt erhalten."

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

    # A duration-only edit may push later zones back. Preserve the original
    # pauses and never pull a zone forward or save overlapping execution times.
    duration_only = all(
        _parse_time(old["scheduled_start_utc"]) == _parse_time(new["scheduled_start_utc"])
        for old, new in zip(plan, reconciled, strict=True)
    ) and any(int(old["run_seconds"]) != int(new["run_seconds"])
              for old, new in zip(plan, reconciled, strict=True))
    if duration_only:
        originals = sorted(plan, key=lambda z: _parse_time(z["scheduled_start_utc"]))
        by_id = {int(z["relay_id"]): z for z in reconciled}
        previous_end = None
        for index, old in enumerate(originals):
            new = by_id[int(old["relay_id"])]
            start = _parse_time(old["scheduled_start_utc"])
            if index:
                prior = originals[index-1]
                gap = start - (_parse_time(prior["scheduled_start_utc"]) + timedelta(seconds=int(prior["run_seconds"])))
                if gap < timedelta(0):
                    return "INVALID", None, "Der bisherige Bewässerungsplan enthält überschneidende Zonen."
                start = max(start, previous_end + gap)
            previous_end = start + timedelta(seconds=int(new["run_seconds"]))
            new.update(scheduled_start_utc=start.isoformat(), scheduled_end_utc=previous_end.isoformat())

    ordered = sorted(reconciled, key=lambda zone: _parse_time(zone["scheduled_start_utc"]))
    if any(
        _parse_time(ordered[i]["scheduled_start_utc"])
        < _parse_time(ordered[i-1]["scheduled_end_utc"])
        for i in range(1, len(ordered))
    ):
        return "INVALID", None, "Die geänderten Bewässerungszeiten überschneiden sich; der gespeicherte Ablauf wird nicht ersetzt."

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

    # A simple sum loses deliberate native inter-zone pauses.  Advance a
    # cursor over persisted timestamps for every plan, carrying a real delay
    # of a preceding zone and each confirmed-end wait forward conservatively.
    cursor = now_utc.astimezone(timezone.utc)
    previous_physical_end: datetime | None = None
    previous_planned_end: datetime | None = None
    for zone in plan:
        relay_id = int(zone["relay_id"])
        if relay_id in completed_relay_ids:
            continue
        scheduled_start = _parse_time(zone.get("scheduled_start_utc"))
        if scheduled_start is None:
            raise RuntimeError("Gespeicherter Beregnungsplan enthält keine Startzeit.")
        duration = int(zone["run_seconds"])
        if relay_id == current_relay_id and current_started_utc is not None:
            elapsed = max(0, int((cursor - current_started_utc).total_seconds()))
            duration = max(0, duration - elapsed)
        earliest = cursor
        if previous_physical_end is not None and previous_planned_end is not None:
            gap = max(timedelta(0), scheduled_start - previous_planned_end)
            earliest = max(earliest, previous_physical_end + gap)
        previous_physical_end = earliest + timedelta(seconds=duration)
        previous_planned_end = scheduled_start + timedelta(seconds=int(zone["run_seconds"]))
        cursor = previous_physical_end + timedelta(minutes=end_confirmation_minutes)
    return cursor


def _irrigation_operating_window(
    *,
    plan: list[dict[str, Any]],
    completed_relay_ids: set[int],
    now_utc: datetime,
    end_confirmation_minutes: int,
    automatic: bool = True,
) -> OperatingWindowResult:
    """Validate a remaining sequence and, for automatic runs, its time window.

    The first centrally dispatched zone would start at ``now``. Before the
    local earliest bound, project from 03:30 so automatic callers can wait without
    shortening a zone or allowing the suppressed native plan to resume.
    Persisted absolute starts provide every native pause; confirmation waits
    are carried by ``_projected_irrigation_end`` as existing control time.
    """

    if (
        not isinstance(end_confirmation_minutes, int)
        or isinstance(end_confirmation_minutes, bool)
        or not 1 <= end_confirmation_minutes <= 10
    ):
        return OperatingWindowResult("INVALID", reason="invalid end confirmation")
    if any(
        not isinstance(relay_id, int) or isinstance(relay_id, bool)
        for relay_id in completed_relay_ids
    ):
        return OperatingWindowResult("INVALID", reason="invalid completed relay identity")
    try:
        remaining = []
        for zone in plan:
            if not isinstance(zone, dict):
                return OperatingWindowResult("INVALID", reason="invalid persisted zone")
            relay_id = zone.get("relay_id")
            run_seconds = zone.get("run_seconds")
            if (
                not isinstance(relay_id, int) or isinstance(relay_id, bool) or relay_id <= 0
                or not isinstance(run_seconds, int) or isinstance(run_seconds, bool)
                or not 1 <= run_seconds <= 7200
            ):
                return OperatingWindowResult("INVALID", reason="invalid persisted zone values")
            if relay_id not in completed_relay_ids:
                remaining.append(zone)
    except (TypeError, ValueError, AttributeError, OverflowError):
        return OperatingWindowResult("INVALID", reason="invalid persisted zone sequence")
    if not remaining:
        return OperatingWindowResult("INVALID", reason="no remaining zones")
    try:
        ordered = sorted(
            remaining,
            key=lambda zone: _parse_time(zone["scheduled_start_utc"]),
        )
        starts = [_parse_time(zone["scheduled_start_utc"]) for zone in ordered]
        durations = [int(zone["run_seconds"]) for zone in ordered]
    except (KeyError, TypeError, ValueError, AttributeError, OverflowError):
        return OperatingWindowResult("INVALID", reason="invalid persisted zone sequence")
    if any(start is None for start in starts):
        return OperatingWindowResult("INVALID", reason="missing persisted zone start")
    assert all(start is not None for start in starts)
    for index in range(1, len(starts)):
        if starts[index] < starts[index - 1] + timedelta(seconds=durations[index - 1]):
            return OperatingWindowResult("INVALID", reason="persisted zones overlap")
    try:
        normalized_now = now_utc.astimezone(timezone.utc)
    except (TypeError, ValueError, AttributeError, OverflowError):
        return OperatingWindowResult("INVALID", reason="invalid operating window time")
    if not automatic:
        try:
            projected_end = _projected_irrigation_end(
                plan=ordered,
                completed_relay_ids=set(),
                current_relay_id=None,
                current_started_utc=None,
                now_utc=normalized_now,
                end_confirmation_minutes=end_confirmation_minutes,
            )
        except (KeyError, TypeError, ValueError, AttributeError):
            return OperatingWindowResult("INVALID", reason="invalid projected zone sequence")
        return OperatingWindowResult("OK", projected_end_utc=projected_end)
    try:
        bounds = operating_bounds(now_utc)
    except (TypeError, ValueError, AttributeError, OverflowError):
        return OperatingWindowResult("INVALID", reason="invalid operating window time")
    if bounds is None:
        return OperatingWindowResult("INVALID", reason="operating timezone unavailable")
    earliest, deadline = bounds
    effective_start = max(normalized_now, earliest)
    first_planned = starts[0]
    try:
        shifted_starts = [effective_start + (start - first_planned) for start in starts]
        sequence = validate_zone_sequence(shifted_starts, durations)
    except (TypeError, ValueError, AttributeError, OverflowError):
        return OperatingWindowResult("INVALID", reason="invalid persisted zone sequence")
    if sequence.code == "INVALID":
        return sequence
    try:
        projected_end = _projected_irrigation_end(
            plan=ordered,
            completed_relay_ids=set(),
            current_relay_id=None,
            current_started_utc=None,
            now_utc=effective_start,
            end_confirmation_minutes=end_confirmation_minutes,
        )
    except (KeyError, TypeError, ValueError, AttributeError):
        return OperatingWindowResult("INVALID", reason="invalid projected zone sequence")
    span_seconds = max(0, int((projected_end - effective_start).total_seconds()))
    projected = validate_fresh_start(
        effective_start,
        duration_seconds=span_seconds,
    )
    if projected.code != "OK":
        return projected
    if normalized_now < earliest:
        return OperatingWindowResult(
            "TOO_EARLY", earliest, deadline, projected_end,
            projected.latest_start_utc,
            "earliest local start is 03:30 Europe/Berlin",
        )
    return OperatingWindowResult(
        "OK", earliest, deadline, projected_end, projected.latest_start_utc,
    )


def _delay_coordinated_remaining_zones(
    plan: list[dict[str, Any]], *, completed: set[int], current_id: int,
    proved_clear_since: datetime,
) -> list[dict[str, Any]]:
    """Carry observed lateness forward without shortening the source's pauses."""
    current = next(zone for zone in plan if int(zone["relay_id"]) == current_id)
    planned_end = _parse_time(current.get("scheduled_end_utc"))
    if planned_end is None:
        raise RuntimeError("Die gespeicherte Zonenendzeit fehlt.")
    delay = max(timedelta(0), proved_clear_since - planned_end)
    adjusted = []
    for zone in plan:
        updated = dict(zone)
        if int(zone["relay_id"]) not in completed:
            start = _parse_time(zone.get("scheduled_start_utc"))
            end = _parse_time(zone.get("scheduled_end_utc"))
            if start is None or end is None:
                raise RuntimeError("Die gespeicherten Zonenzeiten fehlen.")
            updated["scheduled_start_utc"] = (start + delay).isoformat()
            updated["scheduled_end_utc"] = (end + delay).isoformat()
        adjusted.append(updated)
    return adjusted


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


def _external_irrigation_is_running_or_changing_zone(
    state: AutomationState,
    details: dict[str, Any],
    *,
    now_utc: datetime,
    previous_state: AutomationState,
) -> bool:
    """Erkennt einen bereits extern gestarteten Hydrawise-Lauf.

    Ein in Hydrawise oder am Controller gestarteter Lauf darf nicht nachträglich
    als neuer Automatikplan übernommen werden. Insbesondere ist eine laufende
    Zone kein ungültiger Zonenplan. Während der kurzen Umschaltpause zwischen
    zwei Zonen bleibt die Erkennung erhalten; die konfigurierte Trocknungssperre
    wird weiterhin allein aus den fortlaufenden Live-Beobachtungen abgeleitet.
    """

    if _active_relay_ids(details):
        return True
    clear_since = _parse_time(state.hydrawise_clear_since_utc)
    previous_clear_since = _parse_time(previous_state.hydrawise_clear_since_utc)
    previous_cycle_proves_transition = (
        int(previous_state.last_hydrawise_active_count or 0) > 0
        or (
            previous_state.hydrawise_clear_origin == "IRRIGATION_END"
            and previous_clear_since is not None
            and timedelta(0)
            <= now_utc - previous_clear_since
            <= timedelta(minutes=5)
        )
    )
    return bool(
        previous_cycle_proves_transition
        and state.hydrawise_clear_origin == "IRRIGATION_END"
        and clear_since is not None
        and timedelta(0) <= now_utc - clear_since <= timedelta(minutes=5)
    )


def _cycle_state(
    state: AutomationState,
    result: CycleResult,
    now_utc: datetime,
) -> AutomationState:
    details = _as_dict(result.details)
    mower = _as_dict(details.get("mower"))
    safety = _as_dict(_as_dict(details.get("hydrawise")).get("safety"))
    observed = _hydrawise_source_observation(details, now_utc=now_utc)
    fresh = (
        bool(safety.get("available"))
        and bool(safety.get("fresh"))
        and safety.get("relay_set_valid") is True
    )
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
        hydrawise_success_utc=observed if fresh else None,
        hydrawise_observed_utc=observed,
        hydrawise_clear=clear,
        hydrawise_active_count=(
            int(safety.get("active_zone_count") or 0) if fresh else None
        ),
        next_irrigation_start_utc=next_irrigation,
    )


def _state_details(state: AutomationState, *, persisted: bool, error: str | None = None) -> dict[str, Any]:
    schedule_override = _schedule_override(state)
    return {
        "revision": state.revision,
        "maintenance_mode": state.maintenance_mode,
        "persisted": persisted,
        "error": error,
        "parked_by_automation": state.parked_by_automation,
        "automation_park_source": state.automation_park_source,
        "automation_restart_allowed": state.automation_restart_allowed,
        "park_command_sent_utc": state.park_command_sent_utc,
        "park_confirmed_utc": state.park_confirmed_utc,
        "park_confirmed_observations": state.park_confirmed_observations,
        "continuous_mowing_owned": state.continuous_mowing_owned,
        "continuous_mowing_observed_takeover": state.continuous_mowing_observed_takeover,
        "continuous_mowing_takeover_deadline_utc": state.continuous_mowing_takeover_deadline_utc,
        "continuous_mowing_takeover_hold_utc": state.continuous_mowing_takeover_hold_utc,
        "mower_start_pending_since_utc": state.mower_start_pending_since_utc,
        "mower_start_pending_deadline_utc": state.mower_start_pending_deadline_utc,
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
        "irrigation_suspension_revalidation_last_seen_utc": (
            state.irrigation_suspension_revalidation_last_seen_utc
        ),
        "irrigation_suspension_revalidation_observations": (
            state.irrigation_suspension_revalidation_observations
        ),
        "irrigation_cancelled_without_run_utc": (
            state.irrigation_cancelled_without_run_utc
        ),
        # Die Bedienmetadaten sind ausschließlich Telemetrie. Sie erlauben
        # dem Dashboard, manuell angestoßene Beregnungen zu zählen, ohne
        # personenbezogene Daten oder Zugangsdaten zu protokollieren.
        "operator_request_id": state.operator_request_id,
        "operator_request_action": state.operator_request_action,
        "operator_request_status": state.operator_request_status,
        "irrigation_schedule_override_kind": (
            schedule_override.get("kind") if schedule_override else None
        ),
        "irrigation_schedule_override_status": (
            schedule_override.get("status") if schedule_override else None
        ),
        "hydrawise_clear_since_utc": state.hydrawise_clear_since_utc,
        "hydrawise_clear_origin": state.hydrawise_clear_origin,
        "hydrawise_drying_since_utc": state.hydrawise_drying_since_utc,
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
    # Persist the decision that this write cycle actually reached.  The
    # read-only planner result can still describe a raw Husqvarna override,
    # while the full failsafe has safely adopted or otherwise handled it.
    state = replace(state, last_decision_code=decision_code)
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


def _hold_unconfirmed_mower_start(
    *, store: StateStore, original: AutomationState, state: AutomationState,
    result: CycleResult, details: dict[str, Any], settings: RuntimeSettings,
    environment: Mapping[str, str], now_utc: datetime,
    mower_status_fresh: bool, expected_relay_ids: frozenset[int],
    park_sender: ParkSender, stop_zone_sender: StopZoneSender,
    command_clock: Clock,
) -> CycleResult:
    """An ambiguous START is a durable stop latch, never an expired lease.

    A later dock observation does not prove that the vendor's command queue
    is empty. Only protective parking and an explicit immediate water stop
    are permitted here. Automatic water/start/resume commands stay disabled
    until an operator reconciles the vendor/device state outside this loop.
    """

    mower = _as_dict(details.get("mower"))
    hydra = _as_dict(_as_dict(details.get("hydrawise")).get("safety"))
    action = _operator_action(state, now_utc)
    details["mower_start_outcome"] = {
        "status": "UNCONFIRMED",
        "reserved_at_utc": state.mower_start_pending_since_utc,
        "requested_deadline_utc": state.mower_start_pending_deadline_utc,
        "last_acknowledged_deadline_utc": state.continuous_mowing_window_end_utc,
        "automatic_retry_allowed": False,
        "dock_observation_clears_latch": False,
        "resolution": "Gerätewarteschlange, nativen Zeitplan und sicheren Parkzustand vor manueller Freigabe abgleichen.",
    }
    safe_stop_ids = _active_relay_ids(details)
    if (
        action == "STOP_IRRIGATION_NOW"
        and settings.enable_irrigation_commands
        and hydra.get("available") is True and hydra.get("fresh") is True
        and hydra.get("relay_set_valid") is True
        and set(hydra.get("observed_relay_ids", [])) == expected_relay_ids
        and len(safe_stop_ids) == 1 and safe_stop_ids.issubset(expected_relay_ids)
    ):
        reserved = _finish_operator_request(
            state, "Direkter Wasserstopp reserviert; Mäherstart bleibt ungeklärt.",
            status="SENT_UNCONFIRMED",
        )
        try:
            store.save(reserved, expected_revision=original.revision)
        except Exception as exc:
            return replace(result, decision_code="IRRIGATION_STOP_RESERVATION_FAILED", command_sent=False,
                           message="Wasserstopp konnte nicht persistent reserviert werden.",
                           details=_decorate(details, state=state, settings=settings, persisted=False,
                                             command_sent=False, error=type(exc).__name__))
        relay_id = next(iter(safe_stop_ids))
        try:
            args = (str(environment.get("HYDRAWISE_API_KEY", "")), relay_id,
                    environment.get("HYDRAWISE_CONTROLLER_ID") or None)
            if settings.enable_manual_sessions:
                sent = dispatch_device_send(
                    store=store, original=reserved, state=reserved,
                    sender=stop_zone_sender, args=args, kind="WATER_STOP",
                    target=str(relay_id),
                    intent_key=f"unconfirmed-start:water-stop:{relay_id}:{state.operator_request_id}",
                    now_utc=now_utc, clock=command_clock,
                    before_send_check=lambda latest, at: None,
                    deadline_utc=now_utc + timedelta(seconds=30),
                )
                response = sent.response
                reserved = sent.state
            else:
                response = stop_zone_sender(*args)
        except DeviceSendBlocked as exc:
            blocked_state = exc.state if isinstance(exc.state, AutomationState) else reserved
            return replace(
                result, decision_code=exc.code, command_sent=exc.transport_started,
                message="Wasserstopp bleibt bis zum Live-Abgleich ungeklärt.",
                details=_decorate(details, state=blocked_state, settings=settings,
                                  persisted=True, command_sent=exc.transport_started),
            )
        except Exception as exc:
            details["irrigation_action"] = {"type": "StopZone", "relay_id": relay_id,
                                             "outcome": "UNCONFIRMED", "error_type": type(exc).__name__}
        else:
            details["irrigation_action"] = {"type": "StopZone", "relay_id": relay_id, "response": response}
        return replace(result, decision_code="MOWER_START_OUTCOME_UNCONFIRMED", command_sent=True,
                       message="Wasserstopp angefordert; die ungeklärte Mäheraktion sperrt weitere Starts.",
                       details=_decorate(details, state=reserved, settings=settings, persisted=True, command_sent=True))
    if action and action != "PARK_MOWER":
        state = _finish_operator_request(state, "Startwirkung ungeklärt; zuerst Geräteaktion abgleichen.", status="REJECTED")

    activity = str(mower.get("activity") or "").upper()
    mower_state = str(mower.get("state") or "").upper()
    safe_to_park = (
        settings.enable_park_commands and mower_status_fresh and mower.get("connected") is True
        and now_utc - _parse_time(state.mower_start_pending_since_utc) >= timedelta(seconds=90)
        and bool(mower.get("mower_id")) and int(mower.get("error_code") or 0) == 0
        and mower_state not in ERROR_STATES | MANUAL_STATES
        and activity in PARK_COMMAND_ACTIVITIES
        and (not state.parked_by_automation or activity in PARKABLE_ACTIVITIES
             or str(mower.get("override_action") or "").upper() not in PARK_OVERRIDE_ACTIONS)
    )
    if safe_to_park:
        intent = CommandIntent(action="PARK", target=str(mower["mower_id"]),
                               reason=f"unconfirmed-start|{state.mower_start_pending_since_utc}")
        gate = evaluate_command_gate(state=original, intent=intent, now_utc=now_utc, dedupe_minutes=3)
        if gate.allowed:
            parked = state.record_command(fingerprint=intent.fingerprint, sent_utc=now_utc,
                                           action="PARK", park_source="start_outcome_unknown", restart_allowed=False)
            try:
                store.save(parked, expected_revision=original.revision)
            except Exception as exc:
                return replace(result, decision_code="MOWER_START_OUTCOME_UNCONFIRMED", command_sent=False,
                               message="Startwirkung ungeklärt; Schutzparkierung konnte nicht reserviert werden.",
                               details=_decorate(details, state=state, settings=settings, persisted=False,
                                                 command_sent=False, error=type(exc).__name__))
            try:
                args = (str(environment.get("HUSQVARNA_CLIENT_ID", "")),
                        str(environment.get("HUSQVARNA_CLIENT_SECRET", "")), str(mower["mower_id"]))
                if settings.enable_manual_sessions:
                    sent = dispatch_device_send(
                        store=store, original=parked, state=parked,
                        sender=park_sender, args=args, kind="PARK",
                        target=str(mower["mower_id"]),
                        intent_key=f"park:{intent.fingerprint}", now_utc=now_utc,
                        clock=command_clock, before_send_check=lambda latest, at: None,
                        deadline_utc=now_utc + timedelta(seconds=30),
                    )
                    response = sent.response
                    parked = sent.state
                else:
                    response = park_sender(*args)
            except DeviceSendBlocked as exc:
                parked = exc.state if isinstance(exc.state, AutomationState) else parked
                details["park_action"] = {
                    "type": "ParkUntilFurtherNotice", "outcome": "UNCONFIRMED",
                    "error_type": exc.code,
                }
            except Exception as exc:
                details["park_action"] = {"type": "ParkUntilFurtherNotice", "outcome": "UNCONFIRMED", "error_type": type(exc).__name__}
            else:
                details["park_action"] = {"type": "ParkUntilFurtherNotice", "response": response}
            return replace(result, decision_code="MOWER_START_OUTCOME_UNCONFIRMED", command_sent=True,
                           message="Schutzparkierung angefordert; ungeklärte Startwirkung erfordert manuellen Abgleich.",
                           details=_decorate(details, state=parked, settings=settings, persisted=True, command_sent=True))
    return _persist_result(store=store, original=original, state=state, result=result, details=details,
                           settings=settings, decision_code="MOWER_START_OUTCOME_UNCONFIRMED",
                           message="Die frühere Startwirkung ist ungeklärt; Parkbeobachtung allein hebt diese Sperre nicht auf.")


def _partial_irrigation_end_proof(
    state: AutomationState,
    details: dict[str, Any],
    *,
    expected_relay_ids: frozenset[int],
    relay_allowlist_valid: bool,
) -> dict[str, Any]:
    """Belegt konservativ das sichere Ende eines nur teilweise gelaufenen Plans."""

    plan = _plan_from_state(state)
    plan_relay_ids = {
        int(zone["relay_id"])
        for zone in plan
        if zone.get("relay_id") is not None
    }
    completed = set(_json_ints(state.irrigation_completed_relay_ids_json))
    suspended = set(_json_ints(state.irrigation_suspended_relay_ids_json))
    observations = _zone_observation_by_relay(details)
    safety = _as_dict(_as_dict(details.get("hydrawise")).get("safety"))
    active_ids = _active_relay_ids(details)
    clear_since = _parse_time(state.hydrawise_clear_since_utc)
    observed_relay_ids = set(observations)
    pending_zone_state = any(
        value is not None
        for value in (
            state.irrigation_current_relay_id,
            state.irrigation_zone_start_reserved_utc,
            state.irrigation_zone_started_utc,
            state.irrigation_zone_clear_since_utc,
        )
    )
    eligible = (
        bool(plan_relay_ids)
        and 0 < len(completed) < len(plan_relay_ids)
        and completed < plan_relay_ids
        and plan_relay_ids.issubset(expected_relay_ids)
        and suspended == expected_relay_ids
        and state.irrigation_suspension_completed_utc is not None
        and not pending_zone_state
        and safety.get("available") is True
        and safety.get("fresh") is True
        and safety.get("clear_now") is True
        and relay_allowlist_valid
        and int(safety.get("active_zone_count") or 0) == 0
        and int(safety.get("imminent_zone_count") or 0) == 0
        and not active_ids
        and observed_relay_ids == expected_relay_ids
        and all(
            observation.get("valid") is True
            for observation in observations.values()
        )
        and clear_since is not None
        and state.hydrawise_clear_origin == "IRRIGATION_END"
    )
    return {
        "eligible": eligible,
        "completed_relay_ids": sorted(completed),
        "remaining_relay_ids": sorted(plan_relay_ids - completed),
        "suspended_relay_ids": sorted(suspended),
        "observed_relay_ids": sorted(observed_relay_ids),
        "clear_since_utc": clear_since.isoformat() if clear_since is not None else None,
        "clear_origin": state.hydrawise_clear_origin,
        "pending_zone_state": pending_zone_state,
        "active_relay_ids": sorted(active_ids),
        "imminent_zone_count": int(safety.get("imminent_zone_count") or 0),
        "requires_complete_fresh_relay_set": True,
        "requires_confirmed_irrigation_end": True,
    }


def _complete_partial_irrigation(
    state: AutomationState,
    *,
    now_utc: datetime,
) -> AutomationState:
    """Beendet nur die Restfolge; der volle Nachlauf bleibt unverändert aktiv."""

    return replace(
        state,
        revision=state.revision + 1,
        irrigation_phase="COMPLETE_HOLD",
        irrigation_current_relay_id=None,
        irrigation_zone_start_reserved_utc=None,
        irrigation_zone_started_utc=None,
        irrigation_zone_clear_since_utc=None,
        irrigation_completed_utc=state.irrigation_completed_utc or now_utc.isoformat(),
        irrigation_failed_reason=None,
        irrigation_change_candidate_hash=None,
        irrigation_change_candidate_since_utc=None,
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
        irrigation_suspension_revalidation_last_seen_utc=None,
        irrigation_suspension_revalidation_observed_utc=None,
        irrigation_suspension_revalidation_observations=0,
        irrigation_cancelled_without_run_utc=None,
        irrigation_onsite_dock_proof_json=None,
    )


def _retire_completed_irrigation(
    state: AutomationState, details: Mapping[str, Any], *,
    now_utc: datetime, expected_relay_ids: set[int], max_age_seconds: int,
) -> AutomationState | None:
    """Retire terminal bookkeeping independently of mowing; retain dry proof.

    This is not a device command or a release of the physical STOP. A pending
    send, changed source, active valve or nonterminal program prevents cleanup.
    """
    if (state.irrigation_phase != "COMPLETE_HOLD" or state.maintenance_mode
        or state.operator_request_status == "PENDING" or state.mower_start_pending_since_utc
        or state.irrigation_current_relay_id is not None
        or state.irrigation_zone_start_reserved_utc is not None
        or state.irrigation_zone_started_utc is not None
        or state.irrigation_schedule_override_json):
        return None
    completed = _parse_time(state.irrigation_completed_utc)
    if completed is None or completed > now_utc:
        return None
    safety = _as_dict(_as_dict(details.get("hydrawise")).get("safety"))
    observed = _parse_time(safety.get("observed_at_utc"))
    if (safety.get("available") is not True or safety.get("fresh") is not True
        or safety.get("relay_set_valid") is not True or safety.get("clear_now") is not True
        or type(safety.get("active_zone_count")) is not int or safety["active_zone_count"] != 0
        or type(safety.get("imminent_zone_count")) is not int or safety["imminent_zone_count"] != 0
        or safety.get("active_relay_ids") != []
        or safety.get("imminent_relay_ids") != []
        or not expected_relay_ids
        or not isinstance(safety.get("observed_relay_ids"), list)
        or observed is None or observed < completed
        or not timedelta(0) <= now_utc - observed <= timedelta(seconds=max_age_seconds)):
        return None
    try:
        observed_ids = safety["observed_relay_ids"]
        if (any(type(relay) is not int for relay in observed_ids)
            or len(observed_ids) != len(expected_relay_ids)
            or set(observed_ids) != expected_relay_ids or unresolved_device_sends(state)):
            return None
    except (TypeError, DeviceSendBlocked):
        return None
    # Preserve the existing fallback origin explicitly before dropping phase.
    # Otherwise an absent origin would change from IRRIGATION_END to DATA_GAP
    # and shorten the downstream release rule from 150 to two minutes.
    return replace(
        _clear_irrigation(state),
        hydrawise_clear_origin=state.hydrawise_clear_origin or "IRRIGATION_END",
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
        irrigation_suspension_revalidation_last_seen_utc=None,
        irrigation_suspension_revalidation_observed_utc=None,
        irrigation_suspension_revalidation_observations=0,
        irrigation_cancelled_without_run_utc=now_utc.isoformat(),
        irrigation_onsite_dock_proof_json=None,
    )


def _handle_onsite_proof_loss_stop(
    *, store: StateStore, original: AutomationState, state: AutomationState,
    result: CycleResult, details: dict[str, Any], settings: RuntimeSettings,
    environment: Mapping[str, str], now_utc: datetime,
    expected_relay_ids: frozenset[int], park_sender: ParkSender,
    stop_zone_sender: StopZoneSender,
    command_clock: Clock,
) -> CycleResult | None:
    """Stop the exact owned water zone after a bound dock proof is lost."""
    newly_lost = (
        state.irrigation_phase in {"START_RESERVED", "RUNNING"}
        and onsite_dock_proof.invalidated_for_active_plan(state)
    )
    continuing = (
        state.irrigation_phase == "STOPPING"
        and state.irrigation_failed_reason == ONSITE_DOCK_PROOF_LOST_STOP_REASON
    )
    if not newly_lost and not continuing:
        return None

    stopping = state
    if newly_lost:
        stopping = replace(
            state,
            revision=state.revision + 1,
            irrigation_phase="STOPPING",
            irrigation_failed_reason=ONSITE_DOCK_PROOF_LOST_STOP_REASON,
            irrigation_zone_clear_since_utc=None,
            irrigation_zone_clear_observed_utc=None,
            irrigation_zone_stop_requested_utc=None,
        )
    elif stopping.revision <= original.revision:
        stopping = replace(stopping, revision=original.revision + 1)

    current_id = stopping.irrigation_current_relay_id
    try:
        relay = int(current_id) if current_id is not None else None
    except (TypeError, ValueError):
        relay = None
    try:
        plan_relays = [
            int(zone["relay_id"])
            for zone in _plan_from_state(stopping)
            if zone.get("selected", True) is not False
        ]
    except (KeyError, TypeError, ValueError):
        plan_relays = []
    if (
        relay is None
        or relay not in expected_relay_ids
        or plan_relays.count(relay) != 1
    ):
        return _persist_result(
            store=store, original=original, state=stopping, result=result,
            details=details, settings=settings,
            decision_code="ONSITE_DOCK_PROOF_LOST_STOP_TARGET_UNCLEAR",
            message=(
                "Der Stationsnachweis ist verloren; ohne eindeutige eigene Zone "
                "wird kein Relais angesprochen und der Lauf bleibt gesperrt."
            ),
        )

    safety = _as_dict(_as_dict(details.get("hydrawise")).get("safety"))
    observed_ids = safety.get("observed_relay_ids")
    hydra_complete = (
        safety.get("available") is True
        and safety.get("fresh") is True
        and safety.get("relay_set_valid") is True
        and isinstance(observed_ids, list)
        and all(type(item) is int for item in observed_ids)
        and set(observed_ids) == expected_relay_ids
        and int(safety.get("selected_zone_count") or 0) == len(expected_relay_ids)
    )
    physically_clear = (
        hydra_complete
        and safety.get("clear_now") is True
        and type(safety.get("active_zone_count")) is int
        and safety.get("active_zone_count") == 0
        and type(safety.get("imminent_zone_count")) is int
        and safety.get("imminent_zone_count") == 0
        and safety.get("active_relay_ids") == []
        and safety.get("imminent_relay_ids") == []
    )
    if not physically_clear and (
        stopping.irrigation_zone_clear_since_utc is not None
        or stopping.irrigation_zone_clear_observed_utc is not None
    ):
        stopping = replace(
            stopping,
            revision=stopping.revision + 1,
            irrigation_zone_clear_since_utc=None,
            irrigation_zone_clear_observed_utc=None,
        )
    intent_key = f"onsite-proof-loss:water-stop:{stopping.irrigation_plan_id}:{relay}"
    try:
        journal = load_device_send_journal(stopping)
    except DeviceSendBlocked as exc:
        return _persist_result(
            store=store, original=original, state=stopping, result=result,
            details=details, settings=settings, decision_code=exc.code,
            message="Das persistente Sendejournal ist unklar; weitere Zonen bleiben gesperrt.",
        )
    prior = next(
        (entry for entry in reversed(journal)
         if entry.get("intent_key") == intent_key and entry.get("status") != "REJECTED"),
        None,
    )

    if prior is None:
        if (
            not settings.full_failsafe_write_gate_enabled
            or not settings.enable_irrigation_commands
            or not settings.enable_manual_sessions
        ):
            return _persist_result(
                store=store, original=original, state=stopping, result=result,
                details=details, settings=settings,
                decision_code="ONSITE_DOCK_PROOF_LOST_STOP_LOCKED",
                message=(
                    "Der Stationsnachweis ist verloren; der Zonenstopp bleibt "
                    "durch die Geräteschreibsperre vorgemerkt."
                ),
            )
        stopping = replace(
            stopping, irrigation_zone_stop_requested_utc=now_utc.isoformat(),
        )
        api_key = str(environment.get("HYDRAWISE_API_KEY", "")).strip()
        controller_id = str(environment.get("HYDRAWISE_CONTROLLER_ID", "")).strip() or None

        def check_water_stop(latest: AutomationState, _at: datetime) -> None:
            if (
                latest.irrigation_phase != "STOPPING"
                or latest.irrigation_failed_reason != ONSITE_DOCK_PROOF_LOST_STOP_REASON
                or latest.irrigation_current_relay_id != relay
                or not onsite_dock_proof.invalidated_for_active_plan(latest)
            ):
                raise DeviceSendBlocked("ONSITE_DOCK_PROOF_LOST_STOP_REVOKED")

        try:
            sent = dispatch_device_send(
                store=store, original=original, state=stopping,
                sender=stop_zone_sender, args=(api_key, relay, controller_id),
                kind="WATER_STOP", target=str(relay), intent_key=intent_key,
                now_utc=now_utc, clock=command_clock,
                before_send_check=check_water_stop,
                deadline_utc=now_utc + timedelta(seconds=30),
            )
        except DeviceSendBlocked as exc:
            blocked = exc.state if isinstance(exc.state, AutomationState) else stopping
            return replace(
                result, decision_code=exc.code,
                message=(
                    "Der genaue Zonenstopp ist persistent gesperrt oder ungeklärt; "
                    "keine weitere Zone startet."
                ),
                command_sent=exc.transport_started,
                details=_decorate(
                    details, state=blocked, settings=settings, persisted=True,
                    command_sent=exc.transport_started,
                ),
            )
        details["irrigation_action"] = {
            "type": "StopZone", "reason": "ONSITE_DOCK_PROOF_LOST",
            "relay_id": relay, "response": sent.response,
        }
        return replace(
            result, decision_code="ONSITE_DOCK_PROOF_LOST_STOP_SENT",
            message=(
                "Der gebundene Stationsnachweis ist verloren; der genaue "
                "Zonenstopp wurde gesendet und wird nun physisch bestätigt."
            ),
            command_sent=sent.sent,
            details=_decorate(
                details, state=sent.state, settings=settings, persisted=True,
                command_sent=sent.sent,
            ),
        )

    # WATER_STOP has already been durably dispatched or confirmed.  If the
    # mower left the station, reserve its protective PARK through the same
    # durable journal. The retained INVALID proof keeps this stop latch alive.
    mower = _as_dict(details.get("mower"))
    mower_activity = str(mower.get("activity") or "").strip().upper()
    mower_state = str(mower.get("state") or "").strip().upper()
    try:
        bound_mower_id = json.loads(stopping.irrigation_onsite_dock_proof_json or "null").get("mower_id")
    except (TypeError, ValueError, AttributeError):
        bound_mower_id = None
    configured_mower_id = str(environment.get("HUSQVARNA_MOWER_ID") or "").strip()
    park_protection_due = (
        bool(configured_mower_id)
        and str(mower.get("mower_id") or "").strip() == configured_mower_id
        and bound_mower_id == configured_mower_id
        and mower.get("connected") is True
        and _mower_status_is_fresh(
            mower,
            now_utc=now_utc,
            max_age_seconds=_env_int(
                environment, "MOWER_STATUS_MAX_AGE_SECONDS", 180,
                minimum=30, maximum=900,
            ),
        )
        and type(mower.get("error_code")) is int
        and mower.get("error_code") == 0
        and mower_state not in ERROR_STATES
        and mower_activity in PARK_COMMAND_ACTIVITIES - PARKED_ACTIVITIES
    )
    if park_protection_due:
        details["onsite_dock_proof_loss_stop"] = {
            "target_relay_id": relay,
            "water_stop_persisted": True,
            "protective_park_due": True,
        }
        if (
            settings.full_failsafe_write_gate_enabled
            and settings.enable_park_commands
            and settings.enable_manual_sessions
            and str(mower.get("mower_id") or "").strip()
        ):
            mower_id = str(mower["mower_id"]).strip()
            park_key = (
                f"onsite-proof-loss:park:{stopping.irrigation_plan_id}:{mower_id}"
            )
            park_until = now_utc + timedelta(hours=12)
            command_state = stopping.record_command(
                fingerprint=hashlib.sha256(park_key.encode("utf-8")).hexdigest(),
                sent_utc=now_utc,
                action="PARK",
                park_until_utc=park_until,
                park_source="irrigation",
                restart_allowed=False,
            )

            def check_protective_park(latest: AutomationState, _at: datetime) -> None:
                if (
                    latest.maintenance_mode
                    or latest.irrigation_phase != "STOPPING"
                    or latest.irrigation_current_relay_id != relay
                    or not onsite_dock_proof.invalidated_for_active_plan(latest)
                ):
                    raise DeviceSendBlocked("ONSITE_DOCK_PROOF_LOST_PARK_REVOKED")

            try:
                parked = dispatch_device_send(
                    store=store, original=original, state=command_state,
                    sender=park_sender,
                    args=(
                        str(environment.get("HUSQVARNA_CLIENT_ID", "")).strip(),
                        str(environment.get("HUSQVARNA_CLIENT_SECRET", "")).strip(),
                        mower_id,
                    ),
                    kind="PARK", target=mower_id, intent_key=park_key,
                    now_utc=now_utc, clock=command_clock,
                    before_send_check=check_protective_park,
                    deadline_utc=now_utc + timedelta(seconds=30),
                )
            except DeviceSendBlocked as exc:
                blocked = exc.state if isinstance(exc.state, AutomationState) else stopping
                return replace(
                    result, decision_code=exc.code,
                    message=(
                        "Der Schutz-Parkbefehl ist persistent gesperrt oder ungeklärt; "
                        "Wasser- und Mäherstarts bleiben gesperrt."
                    ),
                    command_sent=exc.transport_started,
                    details=_decorate(
                        details, state=blocked, settings=settings, persisted=True,
                        command_sent=exc.transport_started,
                    ),
                )
            return replace(
                result,
                decision_code="ONSITE_DOCK_PROOF_LOST_PARK_SENT",
                message=(
                    "Nach dem persistenten Wasserstopp wurde der abfahrende Mäher "
                    "mit einem persistenten Schutz-Parkbefehl zurückgerufen."
                ),
                command_sent=parked.sent,
                details=_decorate(
                    details, state=parked.state, settings=settings, persisted=True,
                    command_sent=parked.sent,
                ),
            )

    if not hydra_complete:
        return _persist_result(
            store=store, original=original, state=stopping, result=result,
            details=details, settings=settings,
            decision_code="ONSITE_DOCK_PROOF_LOST_STOP_INPUTS_UNSAFE",
            message=(
                "Der genaue Zonenstopp ist vorgemerkt; das physische Ende kann "
                "erst mit vollständigen frischen Hydrawise-Daten bestätigt werden."
            ),
        )

    active_ids = _active_relay_ids(details)
    if active_ids:
        details["onsite_dock_proof_loss_stop"] = {
            "target_relay_id": relay,
            "active_relay_ids": sorted(active_ids),
            "foreign_relays_targeted": False,
        }
        return _persist_result(
            store=store, original=original, state=stopping, result=result,
            details=details, settings=settings,
            decision_code="ONSITE_DOCK_PROOF_LOST_WAIT_FOR_STOP",
            message=(
                "Das physische Zonenende ist noch nicht vollständig bestätigt; "
                "andere Relais werden nicht angesprochen."
            ),
        )
    if not physically_clear:
        return _persist_result(
            store=store, original=original, state=stopping, result=result,
            details=details, settings=settings,
            decision_code="ONSITE_DOCK_PROOF_LOST_STOP_END_UNCLEAR",
            message="Das physische Zonenende ist noch nicht eindeutig bestätigt.",
        )

    requested_at = _parse_time(stopping.irrigation_zone_stop_requested_utc)
    dispatched_at = _parse_time(prior.get("dispatched_at_utc"))
    confirming, advanced = _record_zone_clear_observation(
        stopping,
        observed_utc=_hydrawise_source_observation(details, now_utc=now_utc),
        not_before_utc=dispatched_at or requested_at,
    )
    clear_since = _parse_time(confirming.irrigation_zone_clear_since_utc)
    if clear_since is None or not advanced:
        return _persist_result(
            store=store, original=original, state=confirming, result=result,
            details=details, settings=settings,
            decision_code="ONSITE_DOCK_PROOF_LOST_CONFIRM_STOP",
            message="Hydrawise meldet die Zone erstmals beendet; die Bestätigung läuft.",
        )
    end_confirmation = _env_int(
        environment, "IRRIGATION_ZONE_END_CONFIRMATION_MINUTES", 2,
        minimum=1, maximum=10,
    )
    observed_at = _hydrawise_source_observation(details, now_utc=now_utc)
    if observed_at is None or observed_at - clear_since < timedelta(minutes=end_confirmation):
        return _persist_result(
            store=store, original=original, state=confirming, result=result,
            details=details, settings=settings,
            decision_code="ONSITE_DOCK_PROOF_LOST_CONFIRM_STOP",
            message="Das physische Zonenende wird fortlaufend bestätigt.",
        )

    confirmed = confirming
    if prior.get("status") in {"RESERVED", "DISPATCHING", "SENT_UNCONFIRMED", "UNKNOWN"}:
        confirmed = reconcile_device_send(
            confirmed, intent_key, now_utc,
            f"relay-{relay}-continuously-inactive-after-onsite-proof-loss",
        )
    stopped = replace(
        confirmed,
        revision=confirmed.revision + 1,
        irrigation_phase="COMPLETE_HOLD",
        irrigation_failed_reason=None,
        irrigation_current_relay_id=None,
        irrigation_zone_start_reserved_utc=None,
        irrigation_zone_started_utc=None,
        irrigation_zone_stop_requested_utc=None,
        irrigation_zone_clear_since_utc=None,
        irrigation_zone_clear_observed_utc=None,
        irrigation_completed_utc=now_utc.isoformat(),
        hydrawise_clear_since_utc=now_utc.isoformat(),
        hydrawise_clear_origin="IRRIGATION_END",
        hydrawise_drying_since_utc=(
            confirmed.hydrawise_drying_since_utc or now_utc.isoformat()
        ),
        last_hydrawise_active_count=0,
    )
    return _persist_result(
        store=store, original=original, state=stopped, result=result,
        details=details, settings=settings,
        decision_code="ONSITE_DOCK_PROOF_LOST_STOP_CONFIRMED",
        message=(
            "Hydrawise hat das Ende bestätigt; keine weitere Zone startet und "
            "die vollständige Trocknungssperre läuft."
        ),
    )


def _expired_unused_irrigation_proof(
    state: AutomationState,
    previous_state: AutomationState,
    details: dict[str, Any],
    *,
    now_utc: datetime,
    expected_relay_ids: frozenset[int],
    relay_allowlist_valid: bool,
    confirmation_minutes: int,
) -> dict[str, Any]:
    """Belegt konservativ, dass ein abgelaufener Lauf nie Wasser gestartet hat."""

    plan = _plan_from_state(state)
    stored_plan_end = max(
        (
            end
            for zone in plan
            if (end := _parse_time(zone.get("scheduled_end_utc"))) is not None
        ),
        default=None,
    )
    stored_suspend_until = _parse_time(state.irrigation_suspension_until_utc)
    hydrawise_clear_since = _parse_time(state.hydrawise_clear_since_utc)
    observations = _zone_observation_by_relay(details)
    active_ids = _active_relay_ids(details)
    safety = _as_dict(_as_dict(details.get("hydrawise")).get("safety"))
    completed = _json_ints(state.irrigation_completed_relay_ids_json)
    suspended = _json_ints(state.irrigation_suspended_relay_ids_json)
    live_schedule_is_later_or_absent = (
        stored_suspend_until is not None
        and set(observations) == expected_relay_ids
        and all(
            (
                (start := _parse_time(observation.get("scheduled_start_utc")))
                is None
                or start > stored_suspend_until
            )
            for observation in observations.values()
        )
    )
    no_start_evidence = (
        not completed
        and state.irrigation_completed_utc is None
        and state.irrigation_current_relay_id is None
        and state.irrigation_zone_start_reserved_utc is None
        and state.irrigation_zone_started_utc is None
        and int(previous_state.last_hydrawise_active_count or 0) == 0
        and not active_ids
    )
    eligible = (
        stored_suspend_until is not None
        and stored_plan_end is not None
        and now_utc >= max(stored_suspend_until, stored_plan_end)
        and set(suspended) == expected_relay_ids
        and safety.get("available") is True
        and safety.get("fresh") is True
        and safety.get("clear_now") is True
        and relay_allowlist_valid
        and int(safety.get("active_zone_count") or 0) == 0
        and int(safety.get("imminent_zone_count") or 0) == 0
        and set(observations) == expected_relay_ids
        and all(
            observation.get("valid") is True
            for observation in observations.values()
        )
        and live_schedule_is_later_or_absent
        and hydrawise_clear_since is not None
        and now_utc - hydrawise_clear_since
        >= timedelta(minutes=confirmation_minutes)
        and state.hydrawise_clear_origin == "DATA_GAP"
        and no_start_evidence
    )
    return {
        "eligible": eligible,
        "stored_plan_end_utc": (
            stored_plan_end.isoformat() if stored_plan_end is not None else None
        ),
        "suspend_until_utc": (
            stored_suspend_until.isoformat()
            if stored_suspend_until is not None
            else None
        ),
        "hydrawise_clear_since_utc": (
            hydrawise_clear_since.isoformat()
            if hydrawise_clear_since is not None
            else None
        ),
        "clear_origin": state.hydrawise_clear_origin,
        "previous_active_zone_count": int(
            previous_state.last_hydrawise_active_count or 0
        ),
        "completed_relay_ids": sorted(set(completed)),
        "suspended_relay_ids": sorted(set(suspended)),
        "has_zone_start_reservation": (
            state.irrigation_zone_start_reserved_utc is not None
        ),
        "has_zone_start_observation": state.irrigation_zone_started_utc is not None,
        "live_schedule_is_later_or_absent": live_schedule_is_later_or_absent,
        "requires_fresh_clear_zones": True,
        "requires_no_active_or_imminent_zone": True,
        "requires_no_zone_start_record": True,
        "confirmation_minutes": confirmation_minutes,
    }


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
    stop_zone_sender: StopZoneSender = stop_zone_now,
    cutting_height_sender: CuttingHeightSender = set_work_area_cutting_height,
    blade_usage_reset_sender: BladeUsageResetSender = reset_cutting_blade_usage_time,
    input_failure_runner: InputFailureRunner = run_input_failure_guard,
    command_clock: Clock = lambda: datetime.now(timezone.utc),
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

    if str(environment.get("HYDRAWISE_STATUS_CACHE_MODE") or "OFF").strip().upper() != "OFF":
        raise RuntimeError("Der gemeinsame Statuscache ist für Gerätebetriebsarten noch nicht freigegeben.")

    read_only_kwargs = {
        "now_utc": now,
        "settings": settings,
        "environment": environment,
        "past_due": past_due,
        "source": source,
    }
    if read_only_runner is run_read_only_cycle:
        # The controller uses direct device reads. Publishing this independent
        # dashboard snapshot is best-effort inside the canonical reader and
        # never changes control decisions or specialized injected runners.
        read_only_kwargs["publish_dashboard_snapshot"] = True
    try:
        result = read_only_runner(**read_only_kwargs)
    except Exception as exc:
        revocation = None
        if irrigation_park_hold.enabled(environment) or onsite_dock_proof.enabled(environment):
            revocation = _revoke_park_hold_after_read_failure(state_store_factory, environment, now)
        if not isinstance(exc, InputUnavailable):
            raise
        guarded = input_failure_runner(
            now_utc=now,
            settings=settings,
            environment=environment,
            past_due=past_due,
            source=source,
            cause=exc,
            state_store_factory=state_store_factory,
            park_sender=park_sender,
        )
        if revocation is not None:
            guarded = replace(guarded, details={**guarded.details,
                "irrigation_park_hold": {"held": False, "status": revocation, "reason": "INPUT_UNAVAILABLE"}})
        # A bound proof loss has one protective write that does not depend on
        # inferring field availability: stop the already persisted exact water
        # relay.  No physical end is accepted until full Hydrawise reads return.
        if revocation is not None:
            try:
                recovery_store = state_store_factory(environment)
                recovery_original = recovery_store.load()
                recovery = _handle_onsite_proof_loss_stop(
                    store=recovery_store,
                    original=recovery_original,
                    state=recovery_original,
                    result=guarded,
                    details=dict(guarded.details),
                    settings=settings,
                    environment=environment,
                    now_utc=now,
                    expected_relay_ids=expected_relay_ids,
                    park_sender=park_sender,
                    stop_zone_sender=stop_zone_sender,
                    command_clock=command_clock,
                )
                if recovery is not None:
                    return recovery
            except Exception as stop_exc:
                guarded = replace(
                    guarded,
                    details={
                        **guarded.details,
                        "onsite_dock_proof_loss_stop": {
                            "status": "FAILED_CLOSED",
                            "error_type": type(stop_exc).__name__,
                        },
                    },
                )
        return guarded
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
    mower_status_max_age_seconds = _env_int(
        environment,
        "MOWER_STATUS_MAX_AGE_SECONDS",
        180,
        minimum=30,
        maximum=900,
    )
    hydrawise_status_max_age_seconds = _env_int(
        environment,
        "HYDRAWISE_STATUS_MAX_AGE_SECONDS",
        180,
        minimum=30,
        maximum=900,
    )
    mower_status_fresh = _mower_status_is_fresh(
        mower,
        now_utc=now,
        max_age_seconds=mower_status_max_age_seconds,
    )
    details["mower_status_gate"] = {
        "connected": mower.get("connected") is True,
        "fresh": mower_status_fresh,
        "max_age_seconds": mower_status_max_age_seconds,
        "status_timestamp_ms": mower.get("status_timestamp_ms"),
    }

    store = state_store_factory(environment)
    original = store.load()
    previous_activity = str(original.last_mower_activity or "").upper()
    state = _cycle_state(original, result, now)
    # Revoke before any water/occupancy early return can persist the cycle.
    # A later idle report cannot silently restore automatic start authority.
    if state.continuous_mowing_observed_takeover and (
        activity in MANUAL_ACTIVITIES or mower_state in MANUAL_STATES
        or error_code != 0 or mower_state in ERROR_STATES
        or (override_action in PARK_OVERRIDE_ACTIONS
            and not (state.parked_by_automation and external_reason == AUTOMATION_EXTERNAL_REASON))
    ):
        state = replace(state, continuous_mowing_owned=False,
                        continuous_mowing_observed_takeover=False,
                        continuous_mowing_takeover_deadline_utc=None,
                        continuous_mowing_takeover_hold_utc=now.isoformat(),
                        continuous_mowing_work_area_id=None, continuous_mowing_window_end_utc=None)
    if settings.enable_manual_sessions:
        state = _reconcile_observed_device_writes(
            state, mower, details, now_utc=now,
        )
        if state.device_send_journal_json != original.device_send_journal_json:
            before = unresolved_device_sends(original)
            after_ids = {entry["id"] for entry in unresolved_device_sends(state)}
            details["device_write_reconciliation"] = {
                "remaining": len(after_ids),
                "resolved": [{"kind": entry["kind"], "dispatched_at_utc": entry.get("dispatched_at_utc")}
                             for entry in before if entry["id"] not in after_ids],
            }
        height_projection = reconcile_full_height_sends(
            state, mower, environment, now,
        )
        if height_projection is not state:
            # Height receipts are independent of the currently selected
            # operator action, so persist their fresh physical evidence before
            # any action-specific early return.  Downstream work receives a
            # new projection over this actual CAS predecessor.
            try:
                store.save(height_projection, expected_revision=original.revision)
            except StateConflictError:
                return replace(
                    result,
                    decision_code="HEIGHT_CONFIRMATION_STATE_CHANGED",
                    message="Die Schnitthöhenbestätigung wurde wegen einer parallelen Zustandsänderung nicht übernommen.",
                    details=_decorate(
                        details, state=height_projection, settings=settings,
                        persisted=False, command_sent=False,
                    ),
                )
            original = height_projection
            state = replace(height_projection, revision=height_projection.revision + 1)
    state = onsite_dock_proof.observe(state, mower, environment, now)
    if irrigation_park_hold.enabled(environment):
        state, details["irrigation_park_hold"] = irrigation_park_hold.observe(
            state, mower, now_utc=now, event_fresh=mower_status_fresh,
            confirmation_minutes=_env_int(environment, "MOWER_PARK_CONFIRMATION_MINUTES", 1, minimum=1, maximum=15),
            required_observations=_env_int(environment, "MOWER_PARK_CONFIRMATION_CYCLES", 2, minimum=2, maximum=10),
        )
    elif state.irrigation_park_hold_json is not None:
        state = replace(state, irrigation_park_hold_json=None)
    proof_loss_latched = (
        onsite_dock_proof.invalidated_for_active_plan(state)
        or (
            state.irrigation_phase == "STOPPING"
            and state.irrigation_failed_reason == ONSITE_DOCK_PROOF_LOST_STOP_REASON
        )
    )
    if proof_loss_latched:
        loss_safety = _as_dict(_as_dict(details.get("hydrawise")).get("safety"))
        exact_clear = (
            loss_safety.get("available") is True
            and loss_safety.get("fresh") is True
            and loss_safety.get("relay_set_valid") is True
            and loss_safety.get("clear_now") is True
            and type(loss_safety.get("active_zone_count")) is int
            and loss_safety.get("active_zone_count") == 0
            and type(loss_safety.get("imminent_zone_count")) is int
            and loss_safety.get("imminent_zone_count") == 0
            and loss_safety.get("active_relay_ids") == []
            and loss_safety.get("imminent_relay_ids") == []
            and isinstance(loss_safety.get("observed_relay_ids"), list)
            and set(loss_safety["observed_relay_ids"]) == expected_relay_ids
        )
        if not exact_clear and (
            state.irrigation_zone_clear_since_utc is not None
            or state.irrigation_zone_clear_observed_utc is not None
        ):
            state = replace(
                state,
                revision=state.revision + 1,
                irrigation_zone_clear_since_utc=None,
                irrigation_zone_clear_observed_utc=None,
            )
    proof_loss_stop = _handle_onsite_proof_loss_stop(
        store=store,
        original=original,
        state=state,
        result=result,
        details=details,
        settings=settings,
        environment=environment,
        now_utc=now,
        expected_relay_ids=expected_relay_ids,
        park_sender=park_sender,
        stop_zone_sender=stop_zone_sender,
        command_clock=command_clock,
    )
    if proof_loss_stop is not None:
        return proof_loss_stop
    if _env_enabled(environment, "IRRIGATION_TERMINAL_CLEANUP_ENABLED"):
        # Direct source reads finish after the timer starts. Compare their age
        # with the actual check time, not the earlier timer timestamp. A bad
        # clock or excessively delayed cycle must not retire any state.
        try:
            cleanup_checked_at = command_clock()
        except Exception:
            cleanup_checked_at = None
        cleanup_clock_valid = (
            isinstance(cleanup_checked_at, datetime)
            and cleanup_checked_at.tzinfo is not None
            and cleanup_checked_at.utcoffset() is not None
            and timedelta(0) <= cleanup_checked_at - now
            <= timedelta(seconds=hydrawise_status_max_age_seconds)
        )
        retired = (_retire_completed_irrigation(
            state, details, now_utc=cleanup_checked_at, expected_relay_ids=expected_relay_ids,
            max_age_seconds=hydrawise_status_max_age_seconds,
        ) if cleanup_clock_valid else None)
        if retired is not None:
            details["irrigation_terminal_cleanup"] = {
                "prepared": True, "completed_utc": state.irrigation_completed_utc,
                "drying_preserved": True, "checked_at_utc": cleanup_checked_at.isoformat(),
            }
            # Keep processing safety, manual stop and park decisions in this
            # same cycle. The normal CAS persists this projection together
            # with those decisions; cleanup must never delay a protective park.
            state = retired
    operator_action = _operator_action(state, now)

    manual_session: dict[str, Any] | None = None
    manual_permission: dict[str, Any] = {
        "allowed": False,
        "code": "MANUAL_SESSIONS_DISABLED",
        "dry_override": False,
    }
    if settings.enable_manual_sessions:
        try:
            manual_session = load_manual_session(state)
        except ManualSessionError:
            return _persist_result(
                store=store, original=original, state=state, result=result,
                details=details, settings=settings,
                decision_code="MANUAL_SESSION_INVALID",
                message="Die manuelle Sitzung ist nicht eindeutig lesbar; alle Freigaben bleiben gesperrt.",
            )
        if manual_session is not None:
            try:
                existing_water_tx = load_manual_water_transaction(state)
            except ManualWaterConflictError:
                existing_water_tx = None
            water_coordination_return = bool(
                existing_water_tx
                and existing_water_tx.get("choice") == "MOWER"
                and existing_water_tx.get("phase") not in {
                    "CLEAR_CONFIRMED", "IRRIGATION_COMPLETED", "FAILED",
                }
            )
            water_coordination_return = water_coordination_return or bool(
                state.parked_by_automation
                and state.automation_park_source == "manual_water_conflict"
            )
            if water_coordination_return:
                observed_session, session_changed = manual_session, False
            else:
                observed_session, session_changed = observe_session(
                    manual_session, mower, now_utc=now,
                )
            if session_changed:
                manual_session = observed_session
                state = replace(
                    state, revision=state.revision + 1,
                    manual_session_json=dump_manual_session(manual_session),
                )
            if (
                manual_session.get("kind") == "PARK"
                and manual_session.get("status") == "ENDED"
                and state.parked_by_automation
                and state.automation_park_source == "operator"
                and state.mower_start_pending_since_utc is None
            ):
                # Ending the durable PARK latch restores ordinary eligibility;
                # it does not clear any ambiguous device-send/start journal.
                state = replace(
                    state, revision=state.revision + 1,
                    automation_restart_allowed=True,
                )

        expected_kind = {
            "START_MOWING": "START",
            "PARK_MOWER": "PARK",
        }.get(operator_action or "")
        if expected_kind is not None:
            session_matches_request = (
                manual_session is not None
                and manual_session.get("kind") == expected_kind
                and manual_session.get("mower_id") == mower_id
                and manual_session.get("status") in {"PREPARED", "PENDING", "ACTIVE"}
                and state.operator_request_session_id == manual_session.get("session_id")
                and state.operator_request_session_epoch == manual_session.get("epoch")
            )
            if not session_matches_request:
                rejected = _finish_operator_request(
                    state,
                    "Manuelle Anforderung ohne passende persistente Sitzung abgelehnt.",
                    status="REJECTED",
                )
                return _persist_result(
                    store=store, original=original, state=rejected, result=result,
                    details=details, settings=settings,
                    decision_code="MANUAL_SESSION_REQUEST_MISMATCH",
                    message="Die manuelle Anforderung wurde nicht an eine aktuelle Sitzung gebunden.",
                )
            if manual_session["status"] == "PREPARED":
                manual_session = with_session_status(
                    manual_session, "PENDING", now_utc=now,
                )
                state = replace(
                    state, revision=state.revision + 1,
                    manual_session_json=dump_manual_session(manual_session),
                )

        if manual_session is not None and manual_session.get("kind") == "START":
            session_hydra_safety = _as_dict(
                _as_dict(details.get("hydrawise")).get("safety")
            )
            manual_permission = resolve_manual_permissions(
                manual_session,
                mower_id=mower_id,
                now_utc=now,
                blocked_now=blocked_now,
                parking_block=parking_block,
                hydrawise_safety=session_hydra_safety,
                hydrawise_release=_manual_dry_release_proof(
                    state, session_hydra_safety, now_utc=now,
                    environment=environment,
                ),
            )
        details["manual_session"] = {
            "enabled": True,
            "session_id": manual_session.get("session_id") if manual_session else None,
            "epoch": manual_session.get("epoch") if manual_session else None,
            "kind": manual_session.get("kind") if manual_session else None,
            "status": manual_session.get("status") if manual_session else None,
            "permission_code": manual_permission.get("code"),
        }
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

    if state.mower_start_pending_since_utc is not None:
        return _hold_unconfirmed_mower_start(
            store=store, original=original, state=state, result=result, details=details,
            settings=settings, environment=environment, now_utc=now,
            mower_status_fresh=mower_status_fresh, expected_relay_ids=expected_relay_ids,
            park_sender=park_sender, stop_zone_sender=stop_zone_sender,
            command_clock=command_clock,
        )
    if settings.enable_manual_sessions and manual_session is not None:
        if (
            manual_session.get("kind") == "START"
            and manual_session.get("status") == "ENDED"
        ):
            # ENDED removes the manual exceptions; it is not a permanent stop.
            # Continue through water/occupancy/command-journal safety on EVERY
            # cycle, even if the physical mower is still moving. Keep the
            # session as an audit/fencing record and respect the charge return
            # below. A durable manual PARK is a different, unchanged latch.
            if state.operator_occupancy_override_key or state.operator_occupancy_override_until_utc:
                state = replace(
                    state, revision=state.revision + 1,
                    operator_occupancy_override_key=None,
                    operator_occupancy_override_until_utc=None,
                )
        if (
            manual_session.get("kind") == "PARK"
            and manual_session.get("status") != "ENDED"
            and operator_action != "PARK_MOWER"
        ):
            if activity in PARKABLE_ACTIVITIES and settings.enable_park_commands:
                base_key = f"manual-park:{manual_session['session_id']}:{manual_session['epoch']}"
                previous = [
                    entry for entry in load_device_send_journal(state)
                    if str(entry.get("intent_key") or "").startswith(base_key)
                ]
                last_confirmed = next(
                    (entry for entry in reversed(previous) if entry.get("status") == "CONFIRMED"),
                    None,
                )
                park_key = (
                    f"{base_key}:{last_confirmed.get('confirmed_at_utc')}"
                    if last_confirmed is not None else base_key
                )
                intent = CommandIntent(
                    action="PARK", target=mower_id,
                    reason=park_key, valid_until_utc=now + timedelta(days=1),
                )
                working = state.record_command(
                    fingerprint=intent.fingerprint, sent_utc=now, action="PARK",
                    park_until_utc=now + timedelta(days=1), park_source="operator",
                    restart_allowed=False,
                )
                try:
                    sent = dispatch_device_send(
                        store=store, original=original, state=working,
                        sender=park_sender,
                        args=(str(environment.get("HUSQVARNA_CLIENT_ID", "")),
                              str(environment.get("HUSQVARNA_CLIENT_SECRET", "")), mower_id),
                        kind="PARK", target=mower_id, intent_key=park_key,
                        now_utc=now, clock=command_clock,
                        before_send_check=lambda latest, at: None,
                        deadline_utc=now + timedelta(seconds=30),
                    )
                except DeviceSendBlocked as exc:
                    blocked_state = exc.state if isinstance(exc.state, AutomationState) else state
                    return replace(
                        result, decision_code=exc.code, command_sent=exc.transport_started,
                        message="Die bestätigte Park-Sitzung hält einen ungeklärten Gerätestart fest.",
                        details=_decorate(details, state=blocked_state, settings=settings,
                                          persisted=True, command_sent=exc.transport_started),
                    )
                return replace(
                    result, decision_code="MANUAL_PARK_SESSION_REASSERTED",
                    message="Der externe Mäherstart wurde durch die bestätigte Park-Sitzung zurückgerufen.",
                    command_sent=sent.sent,
                    details=_decorate(details, state=sent.state, settings=settings,
                                      persisted=True, command_sent=sent.sent),
                )
            return _persist_result(
                store=store, original=original, state=state, result=result,
                details=details, settings=settings,
                decision_code="MANUAL_PARK_SESSION_HOLD",
                message="Die bestätigte Park-Sitzung bleibt bis zur ausdrücklichen Freigabe aktiv.",
            )

    block_source = str(parking_block.get("source") or "").strip().lower()
    irrigation_due = "irrigation" in _source_parts(block_source)
    hydra_safety = _as_dict(_as_dict(details.get("hydrawise")).get("safety"))
    try:
        manual_water_context = water_conflict_context(state, details, now)
    except ManualWaterConflictError:
        manual_water_context = {
            "id": None, "required": True, "known": False, "phase": "INVALID",
        }
    if settings.enable_manual_sessions and manual_session is not None:
        selected_conflict = manual_session.get("water_conflict_id")
        selected_choice = manual_session.get("water_choice")
        if manual_water_context.get("required") is True:
            replaceable_terminal_tx = False
            choice_matches = (
                manual_water_context.get("known") is True
                and selected_conflict == manual_water_context.get("id")
                and selected_choice in {"MOWER", "IRRIGATION"}
            )
            if not choice_matches:
                manual_permission = {
                    "allowed": False,
                    "code": (
                        "MANUAL_WATER_CONFLICT_UNKNOWN"
                        if manual_water_context.get("known") is not True
                        else "MANUAL_WATER_CHOICE_REQUIRED"
                    ),
                    "dry_override": False,
                }
            elif selected_choice == "IRRIGATION":
                manual_permission = {
                    "allowed": False,
                    "code": "MANUAL_WATER_IRRIGATION_PRIORITY",
                    "dry_override": False,
                }
            else:
                try:
                    existing_manual_tx = load_manual_water_transaction(state)
                except ManualWaterConflictError:
                    existing_manual_tx = None
                replaceable_terminal_tx = (
                    existing_manual_tx is not None
                    and existing_manual_tx.get("phase") in {
                        "CLEAR_CONFIRMED", "IRRIGATION_COMPLETED", "FAILED",
                    }
                    and existing_manual_tx.get("id") != manual_water_context.get("id")
                )
            if choice_matches and selected_choice in {"MOWER", "IRRIGATION"} and (
                state.manual_water_conflict_json is None or replaceable_terminal_tx
            ):
                try:
                    transaction = new_manual_water_transaction(
                        manual_water_context, session=manual_session, now_utc=now,
                    )
                except ManualWaterConflictError:
                    manual_permission = {
                        "allowed": False, "code": "MANUAL_WATER_PLAN_UNKNOWN",
                        "dry_override": False,
                    }
                else:
                    transaction_changes: dict[str, Any] = {
                        "manual_water_conflict_json": dump_manual_water_transaction(transaction),
                    }
                    if selected_choice == "IRRIGATION" and state.irrigation_phase is None:
                        controlled_plan = list(transaction.get("plan") or [])
                        transaction_changes.update({
                            "irrigation_phase": "PLANNED",
                            "irrigation_plan_id": str(transaction.get("id")),
                            "irrigation_plan_json": json.dumps(
                                controlled_plan, sort_keys=True, separators=(",", ":"),
                            ),
                            "irrigation_suspended_relay_ids_json": "[]",
                            "irrigation_completed_relay_ids_json": "[]",
                        })
                    state = replace(
                        state, revision=state.revision + 1, **transaction_changes,
                    )
                    manual_permission = {
                        "allowed": False, "code": "MANUAL_WATER_COORDINATION_PENDING",
                        "dry_override": False,
                    }
    details["manual_water_conflict"] = {
        key: value for key, value in manual_water_context.items() if key != "plan"
    }
    manual_water_transaction = None
    if settings.enable_manual_sessions:
        try:
            manual_water_transaction = load_manual_water_transaction(state)
        except ManualWaterConflictError:
            return _persist_result(
                store=store, original=original, state=state, result=result,
                details=details, settings=settings,
                decision_code="MANUAL_WATER_TRANSACTION_INVALID",
                message="Die persistente Wasserkonflikt-Transaktion ist ungültig; es wurde kein Befehl gesendet.",
            )
    if (
        manual_water_transaction is not None
        and manual_water_transaction.get("phase") != "CLEAR_CONFIRMED"
    ):
        manual_permission = {
            "allowed": False, "code": "MANUAL_WATER_COORDINATION_PENDING",
            "dry_override": False,
        }

    # A MOWER choice first suppresses the exact original native occurrence,
    # then stops at most one positively identified active relay.  Every write
    # is reserved by the shared journal before transport; an ambiguous result
    # is observed, never replayed.
    if (
        settings.enable_manual_sessions
        and manual_water_transaction is not None
        and manual_water_transaction.get("choice") == "MOWER"
        and manual_water_transaction.get("phase") not in {"CLEAR_CONFIRMED", "FAILED"}
    ):
        transaction = manual_water_transaction
        if (
            manual_session is None
            or transaction.get("session_id") != manual_session.get("session_id")
            or transaction.get("session_epoch") != manual_session.get("epoch")
            or transaction.get("id") != manual_session.get("water_conflict_id")
        ):
            failed_tx = update_manual_water_transaction(
                transaction, now_utc=now, phase="FAILED",
                error_code="MANUAL_WATER_SESSION_CHANGED",
            )
            failed_state = replace(
                state, revision=state.revision + 1,
                manual_water_conflict_json=dump_manual_water_transaction(failed_tx),
            )
            return _persist_result(
                store=store, original=original, state=failed_state, result=result,
                details=details, settings=settings,
                decision_code="MANUAL_WATER_SESSION_CHANGED",
                message="Die Wasserkonflikt-Freigabe passt nicht mehr zur manuellen Sitzung.",
            )
        plan = list(transaction.get("plan") or [])
        suspend_until = _parse_time(transaction.get("suspend_until_utc"))
        if suspend_until is None:
            ends = [_parse_time(zone.get("scheduled_end_utc")) for zone in plan]
            if not ends or any(value is None for value in ends):
                failed_tx = update_manual_water_transaction(
                    transaction, now_utc=now, phase="FAILED",
                    error_code="MANUAL_WATER_PLAN_UNKNOWN",
                )
                failed_state = replace(
                    state, revision=state.revision + 1,
                    manual_water_conflict_json=dump_manual_water_transaction(failed_tx),
                )
                return _persist_result(
                    store=store, original=original, state=failed_state, result=result,
                    details=details, settings=settings,
                    decision_code="MANUAL_WATER_PLAN_UNKNOWN",
                    message="Der ursprüngliche Hydrawise-Lauf ist nicht vollständig nachweisbar.",
                )
            suspend_until = max(value for value in ends if value is not None) + timedelta(minutes=60)
            transaction = update_manual_water_transaction(
                transaction, now_utc=now,
                suspend_until_utc=suspend_until.isoformat(),
            )

        active_ids = _active_relay_ids(details)
        if active_ids and activity in PARKABLE_ACTIVITIES:
            if not settings.enable_park_commands:
                return _persist_result(
                    store=store, original=original, state=state, result=result,
                    details=details, settings=settings,
                    decision_code="MANUAL_WATER_PROTECTIVE_PARK_LOCKED",
                    message="Mäher und Wasser sind gleichzeitig aktiv; der Schutz-Parkbefehl ist gesperrt.",
                )
            if transaction.get("protective_park_reserved_utc") is not None:
                return _persist_result(
                    store=store, original=original, state=state, result=result,
                    details=details, settings=settings,
                    decision_code="MANUAL_WATER_PROTECTIVE_PARK_CONFIRMING",
                    message="Der einmal gesendete Schutz-Parkbefehl wartet auf den Live-Nachweis.",
                )
            park_tx = update_manual_water_transaction(
                transaction, now_utc=now, phase="PARKING",
                protective_park_reserved_utc=now.isoformat(),
            )
            park_intent = CommandIntent(
                action="PARK", target=mower_id,
                reason=f"manual-water-conflict|{transaction['id']}",
                valid_until_utc=now + timedelta(minutes=10),
            )
            working = state.record_command(
                fingerprint=park_intent.fingerprint, sent_utc=now,
                action="PARK", park_until_utc=now + timedelta(minutes=10),
                park_source="manual_water_conflict", restart_allowed=False,
            )
            working = replace(
                working,
                manual_water_conflict_json=dump_manual_water_transaction(park_tx),
            )
            try:
                sent = dispatch_device_send(
                    store=store, original=original, state=working,
                    sender=park_sender,
                    args=(str(environment.get("HUSQVARNA_CLIENT_ID", "")),
                          str(environment.get("HUSQVARNA_CLIENT_SECRET", "")), mower_id),
                    kind="PARK", target=mower_id,
                    intent_key=f"manual-water:{transaction['id']}:protective-park",
                    now_utc=now, clock=command_clock,
                    before_send_check=lambda latest, at: None,
                    deadline_utc=now + timedelta(seconds=30),
                )
            except DeviceSendBlocked as exc:
                blocked_state = exc.state if isinstance(exc.state, AutomationState) else state
                return replace(
                    result, decision_code=exc.code, command_sent=exc.transport_started,
                    message="Der Schutz-Parkbefehl bleibt bis zum Live-Abgleich ungeklärt.",
                    details=_decorate(details, state=blocked_state, settings=settings,
                                      persisted=True, command_sent=exc.transport_started),
                )
            return replace(
                result, decision_code="MANUAL_WATER_PROTECTIVE_PARK_SENT",
                command_sent=sent.sent,
                message="Der Mäher wurde vor der Wasserkoordination schützend nach Hause geschickt.",
                details=_decorate(details, state=sent.state, settings=settings,
                                  persisted=True, command_sent=sent.sent),
            )

        observations = _zone_observation_by_relay(details)
        suspended_ids = {int(v) for v in transaction.get("suspended_relay_ids") or []}
        reserved_relay = transaction.get("suspend_reserved_relay_id")
        if reserved_relay is not None:
            relay = int(reserved_relay)
            intent_key = f"manual-water:{transaction['id']}:suspend:{relay}"
            journal_entry = next(
                (
                    entry for entry in load_device_send_journal(state)
                    if entry.get("intent_key") == intent_key
                ),
                None,
            )
            if journal_entry is None or journal_entry.get("status") != "CONFIRMED":
                return _persist_result(
                    store=store, original=original, state=state, result=result,
                    details=details, settings=settings,
                    decision_code="MANUAL_WATER_SUSPEND_CONFIRMING",
                    message="Die Unterdrückung des ursprünglichen Hydrawise-Laufs wird bestätigt.",
                )
            suspended_ids.add(relay)
            transaction = update_manual_water_transaction(
                transaction, now_utc=now,
                suspended_relay_ids=sorted(suspended_ids),
                suspend_reserved_relay_id=None,
            )
            state = replace(
                state, revision=state.revision + 1,
                manual_water_conflict_json=dump_manual_water_transaction(transaction),
            )

        if transaction.get("stop_reserved_utc") is not None:
            stopped_relay = int((transaction.get("active_relay_ids") or [0])[0])
            stop_key = f"manual-water:{transaction['id']}:stop:{stopped_relay}"
            stop_entry = next(
                (
                    entry for entry in load_device_send_journal(state)
                    if entry.get("intent_key") == stop_key
                ),
                None,
            )
            if stop_entry is None or stop_entry.get("status") != "CONFIRMED":
                return _persist_result(
                    store=store, original=original, state=state, result=result,
                    details=details, settings=settings,
                    decision_code="MANUAL_WATER_STOP_CONFIRMING",
                    message="Der Wasserstopp wartet auf einen neuen Hydrawise-Quellnachweis.",
                )

        pending_zone = None if active_ids else next(
            (zone for zone in plan if int(zone.get("relay_id") or 0) not in suspended_ids),
            None,
        )
        if pending_zone is not None:
            relay = int(pending_zone["relay_id"])
            reserved_tx = update_manual_water_transaction(
                transaction, now_utc=now, phase="SUSPENDING",
                suspend_reserved_relay_id=relay,
            )
            working = replace(
                state, revision=state.revision + 1,
                manual_water_conflict_json=dump_manual_water_transaction(reserved_tx),
            )

            def check_manual_suspend(latest: AutomationState, at: datetime) -> None:
                current_session = load_manual_session(latest)
                current_tx = load_manual_water_transaction(latest)
                if (
                    latest.maintenance_mode
                    or current_session is None or current_tx is None
                    or current_session.get("session_id") != transaction.get("session_id")
                    or current_session.get("epoch") != transaction.get("session_epoch")
                    or current_session.get("water_choice") != "MOWER"
                    or current_tx.get("id") != transaction.get("id")
                    or int(current_tx.get("suspend_reserved_relay_id") or 0) != relay
                    or at >= suspend_until
                ):
                    raise DeviceSendBlocked("MANUAL_WATER_SUSPEND_REVOKED")

            try:
                sent = dispatch_device_send(
                    store=store, original=original, state=working,
                    sender=suspend_zone_sender,
                    args=(str(environment.get("HYDRAWISE_API_KEY", "")).strip(), relay,
                          int(suspend_until.timestamp()),
                          str(environment.get("HYDRAWISE_CONTROLLER_ID", "")).strip() or None),
                    kind="SUSPEND", target=str(relay),
                    intent_key=f"manual-water:{transaction['id']}:suspend:{relay}",
                    now_utc=now, clock=command_clock,
                    before_send_check=check_manual_suspend,
                    deadline_utc=min(suspend_until, now + timedelta(seconds=30)),
                )
            except DeviceSendBlocked as exc:
                blocked_state = exc.state if isinstance(exc.state, AutomationState) else state
                return replace(
                    result, decision_code=exc.code, command_sent=exc.transport_started,
                    message="Der Hydrawise-Suspendierungsbefehl wurde nicht wiederholt.",
                    details=_decorate(details, state=blocked_state, settings=settings,
                                      persisted=True, command_sent=exc.transport_started),
                )
            return replace(
                result, decision_code="MANUAL_WATER_SUSPEND_SENT", command_sent=sent.sent,
                message="Eine Zone des ursprünglichen Hydrawise-Laufs wurde zur Bestätigung suspendiert.",
                details=_decorate(details, state=sent.state, settings=settings,
                                  persisted=True, command_sent=sent.sent),
            )

        if len(active_ids) > 1:
            failed_tx = update_manual_water_transaction(
                transaction, now_utc=now, phase="FAILED",
                error_code="MANUAL_WATER_STOP_TARGET_UNCLEAR",
            )
            failed_state = replace(
                state, revision=state.revision + 1,
                manual_water_conflict_json=dump_manual_water_transaction(failed_tx),
            )
            return _persist_result(
                store=store, original=original, state=failed_state, result=result,
                details=details, settings=settings,
                decision_code="MANUAL_WATER_STOP_TARGET_UNCLEAR",
                message="Mehrere aktive Relays verhindern einen eindeutigen Wasserstopp.",
            )
        if active_ids:
            relay = next(iter(active_ids))
            stop_key = f"manual-water:{transaction['id']}:stop:{relay}"
            if transaction.get("stop_reserved_utc") is not None:
                return _persist_result(
                    store=store, original=original, state=state, result=result,
                    details=details, settings=settings,
                    decision_code="MANUAL_WATER_STOP_CONFIRMING",
                    message="Der einmal gesendete Wasserstopp wartet auf den Live-Nachweis.",
                )
            stopping_tx = update_manual_water_transaction(
                transaction, now_utc=now, phase="STOPPING",
                active_relay_ids=[relay], stop_reserved_utc=now.isoformat(),
            )
            working = replace(
                state, revision=state.revision + 1,
                manual_water_conflict_json=dump_manual_water_transaction(stopping_tx),
            )

            def check_manual_stop(latest: AutomationState, at: datetime) -> None:
                current_tx = load_manual_water_transaction(latest)
                if (
                    current_tx is None or current_tx.get("id") != transaction.get("id")
                    or current_tx.get("phase") != "STOPPING"
                    or list(current_tx.get("active_relay_ids") or []) != [relay]
                ):
                    raise DeviceSendBlocked("MANUAL_WATER_STOP_REVOKED")

            try:
                sent = dispatch_device_send(
                    store=store, original=original, state=working,
                    sender=stop_zone_sender,
                    args=(str(environment.get("HYDRAWISE_API_KEY", "")), relay,
                          environment.get("HYDRAWISE_CONTROLLER_ID") or None),
                    kind="WATER_STOP", target=str(relay), intent_key=stop_key,
                    now_utc=now, clock=command_clock,
                    before_send_check=check_manual_stop,
                    deadline_utc=now + timedelta(seconds=30),
                )
            except DeviceSendBlocked as exc:
                blocked_state = exc.state if isinstance(exc.state, AutomationState) else state
                return replace(
                    result, decision_code=exc.code, command_sent=exc.transport_started,
                    message="Der Wasserstopp bleibt bis zum Live-Abgleich ungeklärt.",
                    details=_decorate(details, state=blocked_state, settings=settings,
                                      persisted=True, command_sent=exc.transport_started),
                )
            return replace(
                result, decision_code="MANUAL_WATER_STOP_SENT", command_sent=sent.sent,
                message="Der eindeutig aktive Hydrawise-Relay wurde einmalig gestoppt.",
                details=_decorate(details, state=sent.state, settings=settings,
                                  persisted=True, command_sent=sent.sent),
            )

        observed = _hydrawise_source_observation(details, now_utc=now)
        clear_since = _parse_time(transaction.get("clear_since_utc"))
        if hydra_safety.get("clear_now") is not True or observed is None:
            return _persist_result(
                store=store, original=original, state=state, result=result,
                details=details, settings=settings,
                decision_code="MANUAL_WATER_CLEAR_UNKNOWN",
                message="Das Wasserende ist noch nicht eindeutig bestätigt.",
            )
        if clear_since is None:
            confirming_tx = update_manual_water_transaction(
                transaction, now_utc=now, phase="CLEAR_CONFIRMING",
                clear_since_utc=observed.isoformat(),
            )
            confirming = replace(
                state, revision=state.revision + 1,
                manual_water_conflict_json=dump_manual_water_transaction(confirming_tx),
            )
            return _persist_result(
                store=store, original=original, state=confirming, result=result,
                details=details, settings=settings,
                decision_code="MANUAL_WATER_CLEAR_CONFIRMING",
                message="Der erste freie Hydrawise-Nachweis wurde gespeichert.",
            )
        if observed - clear_since < timedelta(minutes=2):
            return _persist_result(
                store=store, original=original, state=state, result=result,
                details=details, settings=settings,
                decision_code="MANUAL_WATER_CLEAR_CONFIRMING",
                message="Das Wasserende wird fortlaufend bestätigt.",
            )
        confirmed_tx = update_manual_water_transaction(
            transaction, now_utc=now, phase="CLEAR_CONFIRMED",
        )
        state = replace(
            state, revision=state.revision + 1,
            manual_water_conflict_json=dump_manual_water_transaction(confirmed_tx),
        )
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
    raw_next_irrigation_start = _next_scheduled_irrigation_start(
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
    schedule_override = _schedule_override(state)
    coordination_gate = coordination_execution_enabled(
        environment, full_failsafe_gate=settings.full_failsafe_write_gate_enabled
    )
    execution_input = details.get("coordination_execution_input")
    details["coordination_execution"] = {
        "enabled": coordination_gate,
        "reserved": state.coordination_execution_request_json is not None,
        "accepted": False,
        "reason": None,
    }
    # OFF never permits a latent reservation to wake up after an arbitrary
    # delay.  Clear only the active request; durable dedup evidence survives.
    if not coordination_gate and state.coordination_execution_request_json is not None:
        state = mark_coordination_terminal(state, status="DISABLED", now_utc=now)
        schedule_override = _schedule_override(state)
        details["coordination_execution"].update(
            reserved=False, reason="COORDINATION_EXECUTION_DISABLED"
        )
    # Terminalize a reservation before normal operator handling.  In
    # particular, a new PARK_MOWER request must continue through its own path
    # instead of being consumed by the coordination revalidation return.
    if (
        coordination_gate and state.coordination_execution_request_json is not None
        and (
            state.maintenance_mode or state.operator_request_status == "PENDING"
            or state.irrigation_phase is not None or state.irrigation_schedule_override_json
            or (state.parked_by_automation and "operator" in str(state.automation_park_source or "").lower())
        )
    ):
        state = mark_coordination_terminal(state, status="INTERRUPTED", now_utc=now)
        schedule_override = _schedule_override(state)
        details["coordination_execution"].update(
            reserved=False, reason="MANUAL_OR_IRRIGATION_CHANGED"
        )
    # The read-only handover cannot authorize a command.  It may only cause a
    # CAS-persisted reservation; no Hydrawise call happens in this cycle.
    if coordination_gate and schedule_override is None and state.coordination_execution_request_json is None and execution_input is not None:
        reserved, outcome = reserve_coordination(
            state, cycle=result.to_dict(), execution_input=execution_input, now_utc=now,
        )
        details["coordination_execution"].update(outcome)
        if outcome.get("accepted"):
            return _persist_result(
                store=store, original=original, state=reserved, result=result,
                details=details, settings=settings,
                decision_code="COORDINATION_EXECUTION_RESERVED",
                message="Der freigegebene Bewässerungsbedarf wurde einmalig reserviert; es wurde kein Gerätebefehl gesendet.",
            )
    # A second current observation must still support the entire shadow draft
    # before the reservation becomes the existing CUSTOM_NEXT transaction.
    if coordination_gate and schedule_override is None and state.coordination_execution_request_json is not None:
        checked, reserved_request, reason = consume_coordination_request(
            state, cycle=result.to_dict(), execution_input=execution_input, now_utc=now,
        )
        if reserved_request is None:
            terminal = mark_coordination_terminal(checked, status="INVALID_OR_CHANGED", now_utc=now)
            details["coordination_execution"].update(reason=reason, reserved=False)
            return _persist_result(
                store=store, original=original, state=terminal, result=result,
                details=details, settings=settings,
                decision_code="COORDINATION_EXECUTION_REVALIDATION_FAILED",
                message="Die reservierte Koordination wurde ohne Gerätebefehl endgültig verworfen.",
            )
        try:
            desired_start = _parse_time(reserved_request.get("selected_start_utc"))
            valid_until = _parse_time(reserved_request.get("valid_until_utc"))
            if desired_start is None or valid_until is None or not now < desired_start <= valid_until:
                raise RuntimeError("COORDINATION_RESERVATION_EXPIRED")
            live_plan_id, source_zones = _validated_upcoming_plan(
                details, now_utc=now, expected_zone_count=expected_zones,
                expected_relay_ids=expected_relay_ids, max_lead_minutes=14 * 24 * 60,
                preserve_approved_gaps=True,
            )
            if coordination_source_plan_id(source_zones) != str(reserved_request.get("source_plan_id") or ""):
                raise RuntimeError("COORDINATION_SOURCE_PLAN_CHANGED")
            draft_zones = reserved_request.get("zones")
            if not isinstance(draft_zones, list) or len(draft_zones) != len(source_zones):
                raise RuntimeError("COORDINATION_ZONE_SEQUENCE_INVALID")
            by_relay = {int(item["relay_id"]): dict(item) for item in source_zones}
            custom_zones = []
            for item in draft_zones:
                if not isinstance(item, Mapping):
                    raise RuntimeError("COORDINATION_ZONE_SEQUENCE_INVALID")
                relay = int(item.get("relay_id") or 0)
                base = by_relay.pop(relay, None)
                start = _parse_time(item.get("scheduled_start_utc"))
                seconds = item.get("run_seconds")
                if base is None or start is None or type(seconds) is not int or seconds != base.get("run_seconds"):
                    raise RuntimeError("COORDINATION_DURATION_OR_RELAY_CHANGED")
                base_start = _parse_time(base.get("scheduled_start_utc"))
                selected = _parse_time(reserved_request.get("selected_start_utc"))
                original_first = min(_parse_time(zone.get("scheduled_start_utc")) for zone in source_zones)
                declared_offset = item.get("offset_seconds")
                if (base_start is None or selected is None or original_first is None
                        or type(declared_offset) is not int
                        or declared_offset != int((base_start - original_first).total_seconds())
                        or start != selected + timedelta(seconds=declared_offset)):
                    raise RuntimeError("COORDINATION_ZONE_OFFSET_CHANGED")
                custom_zones.append({
                    **base, "scheduled_start_utc": start.isoformat(),
                    "scheduled_end_utc": (start + timedelta(seconds=seconds)).isoformat(),
                    "coordination_execution": True,
                })
            if by_relay or [z["relay_id"] for z in custom_zones] != [int(z["relay_id"]) for z in source_zones]:
                raise RuntimeError("COORDINATION_ZONE_ORDER_CHANGED")
            source_start = min(_parse_time(zone["scheduled_start_utc"]) for zone in source_zones)
            source_end = max(_parse_time(zone["scheduled_end_utc"]) for zone in source_zones)
            desired_end = max(_parse_time(zone["scheduled_end_utc"]) for zone in custom_zones)
            assert source_start is not None and source_end is not None and desired_end is not None
            override = {
                "version": 1, "kind": "CUSTOM_NEXT", "status": "VERIFYING",
                "request_id": str(reserved_request["request_id"]),
                "coordination_execution": True,
                "coordination_need_id": str(reserved_request["need_id"]),
                "coordination_need_sha256": str(reserved_request["need_sha256"]),
                "created_utc": now.isoformat(), "verify_since_utc": now.isoformat(),
                # CUSTOM_NEXT keeps its established internal plan hash.  The
                # approved coordination need remains bound separately to the
                # wire-order identity used by the read-only producer.
                "source_plan_id": live_plan_id,
                "coordination_source_plan_id": str(reserved_request["source_plan_id"]),
                "source_start_utc": source_start.isoformat(),
                "source_end_utc": source_end.isoformat(), "desired_start_utc": desired_start.isoformat(),
                "desired_end_utc": desired_end.isoformat(),
                "suspend_until_utc": (max(source_end, desired_end) + timedelta(minutes=180)).isoformat(),
                "source_zones": source_zones, "zones": custom_zones,
                "commanded_relay_ids": [], "confirm_since_utc": None,
            }
            terminal = mark_coordination_terminal(checked, status="SCHEDULED", now_utc=now)
            state = replace(
                terminal, revision=terminal.revision + 1,
                irrigation_schedule_override_json=dump_irrigation_schedule_object(override),
                irrigation_schedule_history_json=append_irrigation_schedule_history(
                    terminal.irrigation_schedule_history_json, now_utc=now,
                    action="COORDINATION_CUSTOM_NEXT", status="REQUESTED",
                    summary="Reservierter Koordinationslauf wird in der bestehenden CUSTOM_NEXT-Transaktion geprüft.",
                ),
            )
            details["coordination_execution"].update(accepted=True, reserved=False, request_id=override["request_id"])
            return _persist_result(
                store=store, original=original, state=state, result=result,
                details=details, settings=settings,
                decision_code="COORDINATION_EXECUTION_SCHEDULED",
                message="Die Koordination wurde ohne Gerätebefehl in die bestehende Sieben-Zonen-Transaktion übernommen.",
            )
        except Exception as exc:
            terminal = mark_coordination_terminal(checked, status="REVALIDATION_FAILED", now_utc=now)
            details["coordination_execution"].update(reason=f"{type(exc).__name__}: {exc}", reserved=False)
            return _persist_result(
                store=store, original=original, state=terminal, result=result,
                details=details, settings=settings,
                decision_code="COORDINATION_EXECUTION_REVALIDATION_FAILED",
                message="Die reservierte Koordination wurde ohne Gerätebefehl endgültig verworfen.",
            )
    schedule_override_status = str(
        (schedule_override or {}).get("status") or ""
    ).strip().upper()
    if (
        operator_action == "START_MOWING"
        and schedule_override_status in START_BLOCKING_SCHEDULE_STATUSES
    ):
        # Defence in depth for requests accepted by an older client/version or
        # in the narrow handover around a deployment.  Never let a start wish
        # silently age out behind the multi-cycle seven-zone transaction.
        state = _finish_operator_request(
            state,
            (
                "Der Mäherstart wurde nicht ausgeführt, weil die Änderung des "
                "Beregnungsplans noch bestätigt wird. Nach Abschluss ist eine "
                "neue Startbestätigung erforderlich."
            ),
            status="REJECTED",
        )
        operator_action = None
        details["operator_start_blocked_by_schedule_change"] = {
            "kind": str((schedule_override or {}).get("kind") or ""),
            "status": schedule_override_status,
            "start_command_sent": False,
        }
    irrigation_failsafe_lead_minutes = _env_int(
        environment,
        "IRRIGATION_FAILSAFE_DOCK_LEAD_MINUTES",
        40,
        minimum=30,
        maximum=120,
    )
    schedule_possible_start = _schedule_possible_irrigation_start(schedule_override)
    schedule_override_parking_due = (
        schedule_possible_start is not None
        and schedule_possible_start - now
        <= timedelta(minutes=irrigation_failsafe_lead_minutes)
    )
    schedule_gate_ready = (
        settings.full_failsafe_write_gate_enabled
        and hydra_safety.get("available") is True
        and hydra_safety.get("fresh") is True
        and relay_allowlist_valid
        and int(hydra_safety.get("selected_zone_count") or 0) == expected_zones
        and not _active_relay_ids(details)
    )
    mower_park_precedence = (
        activity in PARKABLE_ACTIVITIES
        and (
            str(decision.get("hypothetical_command") or "").upper() == "PARK"
            or bool(parking_block)
            or bool(_active_relay_ids(details))
            or result.decision_code.startswith("HYDRAWISE_")
            or schedule_override_parking_due
        )
    )

    if operator_action in SCHEDULE_ACTIONS and not mower_park_precedence:
        if not schedule_gate_ready:
            rejected = _finish_operator_request(
                state,
                "Die Planänderung wurde nicht ausgeführt, weil Hydrawise nicht frisch, vollständig und frei bestätigt ist.",
                status="REJECTED",
            )
            return _persist_result(
                store=store,
                original=original,
                state=rejected,
                result=result,
                details=details,
                settings=settings,
                decision_code="IRRIGATION_SCHEDULE_REQUEST_REJECTED",
                message="Der Beregnungsplan blieb unverändert.",
            )
        try:
            new_override = _new_schedule_override(
                action=operator_action,
                request=_schedule_request(state),
                current=schedule_override,
                details=details,
                state=state,
                now_utc=now,
                expected_zone_count=expected_zones,
                expected_relay_ids=expected_relay_ids,
            )
        except Exception as exc:
            rejected = _finish_operator_request(
                state,
                f"Beregnungsplan-Anpassung abgelehnt: {exc}",
                status="REJECTED",
            )
            return _persist_result(
                store=store,
                original=original,
                state=rejected,
                result=result,
                details=details,
                settings=settings,
                decision_code="IRRIGATION_SCHEDULE_REQUEST_REJECTED",
                message="Der Beregnungsplan blieb unverändert.",
            )
        summary = (
            "Der Hydrawise-Plan ist bereits unverändert aktiv."
            if new_override is None
            else _schedule_override_summary(new_override)
        )
        accepted = replace(
            _finish_operator_request(state, summary),
            revision=state.revision + 2,
            irrigation_schedule_override_json=dump_irrigation_schedule_object(
                new_override
            ),
            irrigation_schedule_history_json=append_irrigation_schedule_history(
                state.irrigation_schedule_history_json,
                now_utc=now,
                action=operator_action,
                status="REQUESTED" if new_override is not None else "NO_CHANGE",
                summary=summary,
            ),
        )
        return _persist_result(
            store=store,
            original=original,
            state=accepted,
            result=result,
            details=details,
            settings=settings,
            decision_code="IRRIGATION_SCHEDULE_REQUEST_ACCEPTED",
            message=summary,
        )

    # Eine Plananpassung wird in demselben Minutenzyklus-Zustandsautomaten wie
    # Mäher und Beregnung verarbeitet. Pro Zyklus wird höchstens ein
    # Hydrawise-Schreibbefehl gesendet; erst zwei frische Bestätigungszyklen
    # machen die Anpassung wirksam.
    if schedule_override is not None and not mower_park_precedence:
        override_kind = str(schedule_override.get("kind") or "")
        override_status = str(schedule_override.get("status") or "")
        suspend_until = _parse_time(schedule_override.get("suspend_until_utc"))
        details["irrigation_schedule_override"] = dict(schedule_override)

        if override_status == "VERIFYING":
            if not schedule_gate_ready:
                return _persist_result(
                    store=store, original=original, state=state, result=result,
                    details=details, settings=settings,
                    decision_code="IRRIGATION_SCHEDULE_VERIFY_WAIT",
                    message="Die Plananpassung wartet auf einen frischen vollständigen Hydrawise-Status.",
                )
            try:
                live_plan_id, _live_zones = _validated_upcoming_plan(
                    details,
                    now_utc=now,
                    expected_zone_count=expected_zones,
                    expected_relay_ids=expected_relay_ids,
                    max_lead_minutes=14 * 24 * 60,
                    preserve_approved_gaps=schedule_override.get("coordination_execution") is True,
                )
            except Exception as exc:
                failed_override = {
                    **schedule_override,
                    "status": "REJECTED",
                    "error": f"Der nächste Lauf ist nicht mehr eindeutig: {exc}",
                }
                failed_state = replace(
                    state,
                    revision=state.revision + 1,
                    irrigation_schedule_override_json=dump_irrigation_schedule_object(
                        failed_override
                    ),
                    irrigation_schedule_history_json=append_irrigation_schedule_history(
                        state.irrigation_schedule_history_json,
                        now_utc=now,
                        action=override_kind,
                        status="REJECTED",
                        summary=str(failed_override["error"]),
                    ),
                )
                return _persist_result(
                    store=store, original=original, state=failed_state, result=result,
                    details=details, settings=settings,
                    decision_code="IRRIGATION_SCHEDULE_TARGET_CHANGED",
                    message="Die Anpassung wurde ohne Hydrawise-Befehl verworfen.",
                )
            if live_plan_id != str(schedule_override.get("source_plan_id") or ""):
                failed_override = {
                    **schedule_override,
                    "status": "REJECTED",
                    "error": "Der nächste Hydrawise-Lauf hat sich seit der Bestätigung geändert.",
                }
                failed_state = replace(
                    state,
                    revision=state.revision + 1,
                    irrigation_schedule_override_json=dump_irrigation_schedule_object(
                        failed_override
                    ),
                    irrigation_schedule_history_json=append_irrigation_schedule_history(
                        state.irrigation_schedule_history_json,
                        now_utc=now,
                        action=override_kind,
                        status="REJECTED",
                        summary=str(failed_override["error"]),
                    ),
                )
                return _persist_result(
                    store=store, original=original, state=failed_state, result=result,
                    details=details, settings=settings,
                    decision_code="IRRIGATION_SCHEDULE_TARGET_CHANGED",
                    message="Die Anpassung wurde ohne Hydrawise-Befehl verworfen.",
                )
            verify_since = _parse_time(schedule_override.get("verify_since_utc"))
            if verify_since is None or now - verify_since < timedelta(minutes=2):
                return _persist_result(
                    store=store, original=original, state=state, result=result,
                    details=details, settings=settings,
                    decision_code="IRRIGATION_SCHEDULE_VERIFYING",
                    message="Der unveränderte nächste Lauf wird über zwei Minutenzyklen bestätigt.",
                )
            schedule_override = {**schedule_override, "status": "APPLYING"}
            state = replace(
                state,
                revision=state.revision + 1,
                irrigation_schedule_override_json=dump_irrigation_schedule_object(
                    schedule_override
                ),
            )
            override_status = "APPLYING"

        if override_kind == "RESUME" and override_status == "PARKING":
            required_park_observations = _env_int(
                environment,
                "MOWER_PARK_CONFIRMATION_CYCLES",
                2,
                minimum=2,
                maximum=10,
            )
            if (
                _park_confirmation_ready(
                    state,
                    now_utc=now,
                    activity=activity,
                    mower_state=mower_state,
                    mower_mode=str(mower.get("mode") or ""),
                    confirmation_minutes=1,
                    required_observations=required_park_observations,
                )
                and mower.get("connected") is True
                and mower_status_fresh
            ):
                schedule_override = {**schedule_override, "status": "APPLYING"}
                state = replace(
                    state,
                    revision=state.revision + 1,
                    irrigation_schedule_override_json=dump_irrigation_schedule_object(
                        schedule_override
                    ),
                )
                override_status = "APPLYING"
            elif activity in PARKED_ACTIVITIES:
                return _persist_result(
                    store=store,
                    original=original,
                    state=state,
                    result=result,
                    details=details,
                    settings=settings,
                    decision_code="IRRIGATION_SCHEDULE_RESUME_PARK_CONFIRMING",
                    message="Der Mäher bleibt geparkt, bis die Parkposition fortlaufend bestätigt ist.",
                )

        if override_status == "APPLYING":
            if not schedule_gate_ready:
                return _persist_result(
                    store=store, original=original, state=state, result=result,
                    details=details, settings=settings,
                    decision_code="IRRIGATION_SCHEDULE_APPLY_WAIT",
                    message="Die Plananpassung wartet auf einen frischen freien Hydrawise-Status.",
                )
            commanded = {
                int(value) for value in schedule_override.get("commanded_relay_ids", [])
            }
            pending_relay = next(
                (relay for relay in sorted(expected_relay_ids) if relay not in commanded),
                None,
            )
            if pending_relay is not None:
                command_until = suspend_until
                if override_kind == "RESUME":
                    command_until = now + timedelta(minutes=1)
                if command_until is None:
                    raise RuntimeError("Der Suspendierungszeitpunkt der Plananpassung fehlt.")
                coordination_transaction = schedule_override.get("coordination_execution") is True
                pending_reservation = schedule_override.get("coordination_command_reservation")
                if coordination_transaction and pending_reservation is not None:
                    # A prior process may have reached the CAS point and then
                    # died or lost the sender response.  It is never safe to
                    # replay that relay command.
                    rejected_override = {
                        **schedule_override, "status": "REJECTED",
                        "error": "Der reservierte Koordinations-Suspendierungsbefehl hat keine bestätigte Antwort.",
                    }
                    rejected = replace(
                        state, revision=state.revision + 1,
                        irrigation_schedule_override_json=dump_irrigation_schedule_object(rejected_override),
                    )
                    return _persist_result(
                        store=store, original=original, state=rejected, result=result,
                        details=details, settings=settings,
                        decision_code="COORDINATION_EXECUTION_COMMAND_UNCERTAIN",
                        message="Eine koordinierte Hydrawise-Antwort ist unklar; der Bedarf wird nicht wiederholt.",
                    )
                if coordination_transaction:
                    reserved_override = {
                        **schedule_override,
                        "coordination_command_reservation": {
                            "relay_id": pending_relay, "until_utc": command_until.isoformat(),
                            "reserved_utc": now.isoformat(),
                        },
                    }
                    reserved_state = replace(
                        state, revision=state.revision + 1,
                        irrigation_schedule_override_json=dump_irrigation_schedule_object(reserved_override),
                    )
                    try:
                        store.save(reserved_state, expected_revision=original.revision)
                    except Exception as exc:
                        return replace(
                            result, decision_code="COORDINATION_EXECUTION_COMMAND_RESERVATION_FAILED",
                            message="Der koordinierte Hydrawise-Befehl wurde ohne CAS-Reservierung nicht gesendet.",
                            command_sent=False,
                            details=_decorate(details, state=state, settings=settings,
                                              persisted=False, command_sent=False,
                                              error=f"{type(exc).__name__}: {exc}"),
                        )
                    # Every subsequent state save in this branch must advance
                    # from the command reservation rather than the stale load.
                    original = reserved_state
                    state = reserved_state
                    schedule_override = reserved_override
                attempts = {
                    str(key): int(value)
                    for key, value in dict(schedule_override.get("attempts") or {}).items()
                }
                try:
                    args = (
                        str(environment.get("HYDRAWISE_API_KEY", "")).strip(),
                        pending_relay,
                        int(command_until.timestamp()),
                        str(environment.get("HYDRAWISE_CONTROLLER_ID", "")).strip() or None,
                    )
                    if settings.enable_manual_sessions:
                        schedule_key = (
                            f"schedule:{schedule_override.get('request_id')}:"
                            f"{override_kind}:{pending_relay}:{command_until.isoformat()}"
                        )

                        def check_schedule_write(latest: AutomationState, at: datetime) -> None:
                            current_override = load_irrigation_schedule_object(
                                latest.irrigation_schedule_override_json
                            )
                            if (
                                latest.maintenance_mode or current_override is None
                                or current_override.get("request_id") != schedule_override.get("request_id")
                                or current_override.get("status") != "APPLYING"
                                or at >= command_until
                            ):
                                raise DeviceSendBlocked("IRRIGATION_SCHEDULE_SEND_REVOKED")
                            if override_kind == "RESUME":
                                observed_at = _parse_time(hydra_safety.get("observed_at_utc"))
                                latest_session = load_manual_session(latest)
                                if (
                                    str(mower.get("mode") or "").strip().upper() != "HOME"
                                    or not _mower_status_is_fresh(
                                        mower, now_utc=at,
                                        max_age_seconds=mower_status_max_age_seconds,
                                    )
                                    or observed_at is None
                                    or not -60 <= (at - observed_at).total_seconds()
                                    <= hydrawise_status_max_age_seconds
                                    or hydra_safety.get("clear_now") is not True
                                    or int(hydra_safety.get("active_zone_count") or 0) != 0
                                    or int(hydra_safety.get("imminent_zone_count") or 0) != 0
                                    or (
                                        latest_session is not None
                                        and latest_session.get("kind") == "START"
                                        and latest_session.get("status") != "ENDED"
                                        and (
                                            latest_session.get("water_choice") != "IRRIGATION"
                                            or latest_session.get("water_conflict_id")
                                            != manual_water_context.get("id")
                                        )
                                    )
                                ):
                                    raise DeviceSendBlocked("NATIVE_RESUME_REVOKED")

                        sent = dispatch_device_send(
                            store=store, original=original, state=state,
                            sender=suspend_zone_sender, args=args,
                            kind=("NATIVE_RESUME" if override_kind == "RESUME" else "SUSPEND"),
                            target=str(pending_relay), intent_key=schedule_key,
                            now_utc=now, clock=command_clock,
                            before_send_check=check_schedule_write,
                            deadline_utc=min(command_until, now + timedelta(seconds=30)),
                        )
                        response = sent.response
                        state = sent.state
                        original = sent.state
                    else:
                        response = suspend_zone_sender(*args)
                except DeviceSendBlocked as exc:
                    blocked_state = exc.state if isinstance(exc.state, AutomationState) else state
                    return replace(
                        result, decision_code=exc.code,
                        message="Die Hydrawise-Planänderung wurde an der persistenten Sendegrenze angehalten.",
                        command_sent=exc.transport_started,
                        details=_decorate(details, state=blocked_state, settings=settings,
                                          persisted=True, command_sent=exc.transport_started),
                    )
                except Exception as exc:
                    key = str(pending_relay)
                    attempts[key] = attempts.get(key, 0) + 1
                    failed = attempts[key] >= 3 or coordination_transaction
                    updated_override = {
                        **schedule_override,
                        "status": "REJECTED" if failed else "APPLYING",
                        "attempts": attempts,
                        "error": f"Hydrawise-Befehl für eine Zone fehlgeschlagen: {exc}",
                    }
                    updated_state = replace(
                        state,
                        revision=state.revision + 1,
                        irrigation_schedule_override_json=dump_irrigation_schedule_object(
                            updated_override
                        ),
                    )
                    return _persist_result(
                        store=store, original=original, state=updated_state,
                        result=result, details=details, settings=settings,
                        decision_code=(
                            "IRRIGATION_SCHEDULE_APPLY_FAILED"
                            if failed else "IRRIGATION_SCHEDULE_APPLY_RETRY"
                        ),
                        message=(
                            "Die Plananpassung wurde nach drei Fehlern sicher angehalten."
                            if failed else "Der Hydrawise-Befehl wird im nächsten Zyklus erneut geprüft."
                        ),
                    )
                commanded.add(pending_relay)
                updated_override = {
                    **schedule_override,
                    "commanded_relay_ids": sorted(commanded),
                    "attempts": attempts,
                    "error": None,
                }
                updated_override.pop("coordination_command_reservation", None)
                if override_kind == "RESUME":
                    updated_override["suspend_until_utc"] = command_until.isoformat()
                if commanded == set(expected_relay_ids):
                    updated_override.update(
                        {
                            "status": "CONFIRMING",
                            "confirm_since_utc": None,
                            "confirm_last_seen_utc": None,
                            "confirm_not_before_utc": now.isoformat(),
                        }
                    )
                updated_state = replace(
                    state,
                    revision=state.revision + 1,
                    irrigation_schedule_override_json=dump_irrigation_schedule_object(
                        updated_override
                    ),
                )
                details["irrigation_schedule_action"] = {
                    "relay_id": pending_relay,
                    "until_utc": command_until.isoformat(),
                    "response": response,
                }
                return _persist_result(
                    store=store, original=original, state=updated_state,
                    result=result, details=details, settings=settings,
                    decision_code="IRRIGATION_SCHEDULE_ZONE_UPDATED",
                    message="Eine weitere der sieben Zonen wurde sicher aktualisiert.",
                    command_sent=True,
                )

        if override_status == "CONFIRMING" or str(schedule_override.get("status")) == "CONFIRMING":
            if not schedule_gate_ready:
                return _persist_result(
                    store=store, original=original, state=state, result=result,
                    details=details, settings=settings,
                    decision_code="IRRIGATION_SCHEDULE_CONFIRM_WAIT",
                    message="Die Plananpassung wartet auf frische Hydrawise-Bestätigungen.",
                )
            now_suspend_until = _parse_time(schedule_override.get("suspend_until_utc"))
            observations = _zone_observation_by_relay(details)
            blocked_relays: list[int] = []
            if override_kind != "RESUME" and now_suspend_until is not None:
                for relay_id, observation in observations.items():
                    start = _parse_time(observation.get("scheduled_start_utc"))
                    if start is not None and start <= now_suspend_until:
                        blocked_relays.append(relay_id)
            if blocked_relays:
                commanded = {
                    int(value)
                    for value in schedule_override.get("commanded_relay_ids", [])
                    if int(value) not in set(blocked_relays)
                }
                retry_override = {
                    **schedule_override,
                    "status": "APPLYING",
                    "commanded_relay_ids": sorted(commanded),
                    "confirm_since_utc": None,
                    "confirm_last_seen_utc": None,
                    "confirm_not_before_utc": None,
                }
                retry_state = replace(
                    state,
                    revision=state.revision + 1,
                    irrigation_schedule_override_json=dump_irrigation_schedule_object(
                        retry_override
                    ),
                )
                return _persist_result(
                    store=store, original=original, state=retry_state, result=result,
                    details=details, settings=settings,
                    decision_code="IRRIGATION_SCHEDULE_CONFIRM_RETRY",
                    message="Mindestens eine Zone hat die Anpassung noch nicht bestätigt und wird erneut aktualisiert.",
                )
            confirm_since = _parse_time(schedule_override.get("confirm_since_utc"))
            confirm_last = _parse_time(schedule_override.get("confirm_last_seen_utc"))
            proof_now = _hydrawise_source_observation(details, now_utc=now)
            confirm_not_before = _parse_time(
                schedule_override.get("confirm_not_before_utc")
            )
            if proof_now is None or (confirm_last is not None and proof_now <= confirm_last):
                return _persist_result(
                    store=store, original=original, state=state, result=result,
                    details=details, settings=settings,
                    decision_code="IRRIGATION_SCHEDULE_CONFIRM_WAIT",
                    message="Die Plananpassung wartet auf eine neue gültige Hydrawise-Quellbeobachtung.",
                )
            if confirm_not_before is not None and proof_now <= confirm_not_before:
                return _persist_result(
                    store=store, original=original, state=state, result=result,
                    details=details, settings=settings,
                    decision_code="IRRIGATION_SCHEDULE_CONFIRMING",
                    message="Die Plananpassung wartet auf eine Quellbeobachtung nach dem letzten Suspendierungsbefehl.",
                )
            continuity = (
                confirm_last is not None
                and timedelta(0) < proof_now - confirm_last <= timedelta(minutes=3)
            )
            if confirm_since is None or not continuity:
                confirming = {
                    **schedule_override,
                    "confirm_since_utc": proof_now.isoformat(),
                    "confirm_last_seen_utc": proof_now.isoformat(),
                }
                confirming_state = replace(
                    state,
                    revision=state.revision + 1,
                    irrigation_schedule_override_json=dump_irrigation_schedule_object(
                        confirming
                    ),
                )
                return _persist_result(
                    store=store, original=original, state=confirming_state,
                    result=result, details=details, settings=settings,
                    decision_code="IRRIGATION_SCHEDULE_CONFIRMING",
                    message="Hydrawise bestätigt die Plananpassung fortlaufend.",
                )
            if proof_now - confirm_since < timedelta(minutes=2):
                confirming = {
                    **schedule_override,
                    "confirm_last_seen_utc": proof_now.isoformat(),
                }
                confirming_state = replace(
                    state,
                    revision=state.revision + 1,
                    irrigation_schedule_override_json=dump_irrigation_schedule_object(
                        confirming
                    ),
                )
                return _persist_result(
                    store=store, original=original, state=confirming_state,
                    result=result, details=details, settings=settings,
                    decision_code="IRRIGATION_SCHEDULE_CONFIRMING",
                    message="Die zweite Hydrawise-Bestätigung steht noch aus.",
                )
            if settings.enable_manual_sessions and now_suspend_until is not None:
                schedule_prefix = (
                    f"schedule:{schedule_override.get('request_id')}:{override_kind}:"
                )
                for entry in unresolved_device_sends(state):
                    dispatched = _parse_time(entry.get("dispatched_at_utc"))
                    if (
                        str(entry.get("intent_key") or "").startswith(schedule_prefix)
                        and dispatched is not None
                        and proof_now > dispatched
                    ):
                        state = reconcile_device_send(
                            state, str(entry["intent_key"]), now,
                            f"relay-{entry.get('target')}-schedule-confirmed",
                        )
            if override_kind == "RESUME":
                completed_state = replace(
                    state,
                    revision=state.revision + 1,
                    irrigation_schedule_override_json=None,
                    irrigation_schedule_history_json=append_irrigation_schedule_history(
                        state.irrigation_schedule_history_json,
                        now_utc=now,
                        action="RESUME_IRRIGATION_SCHEDULE",
                        status="COMPLETED",
                        summary="Der unveränderte Hydrawise-Plan ist wieder freigegeben.",
                    ),
                )
                state = completed_state
                schedule_override = None
            else:
                schedule_override = {
                    **schedule_override,
                    "status": "ACTIVE",
                    "confirmed_utc": now.isoformat(),
                    "confirm_last_seen_utc": proof_now.isoformat(),
                }
                state = replace(
                    state,
                    revision=state.revision + 1,
                    irrigation_schedule_override_json=dump_irrigation_schedule_object(
                        schedule_override
                    ),
                    irrigation_schedule_history_json=append_irrigation_schedule_history(
                        state.irrigation_schedule_history_json,
                        now_utc=now,
                        action=override_kind,
                        status="ACTIVE",
                        summary=_schedule_override_summary(schedule_override),
                    ),
                )

        if schedule_override is not None and str(schedule_override.get("status")) == "ACTIVE":
            override_kind = str(schedule_override.get("kind") or "")
            suspend_until = _parse_time(schedule_override.get("suspend_until_utc"))
            if override_kind in {"PAUSE", "SKIP_NEXT"} and suspend_until is not None:
                # Direkte Änderungen in der Hydrawise-App bleiben ausdrücklich
                # möglich. Meldet Hydrawise trotz unserer bestätigten Pause
                # wieder alle sieben Zonen vor dem gespeicherten Pausenende,
                # wurde die Pause extern aufgehoben. Zwei fortlaufende frische
                # Minutenbeobachtungen verhindern eine Reaktion auf flüchtige
                # oder teilweise API-Antworten. Es wird dabei kein Befehl an
                # Hydrawise gesendet.
                observations = _zone_observation_by_relay(details)
                live_starts = {
                    relay_id: _parse_time(observation.get("scheduled_start_utc"))
                    for relay_id, observation in observations.items()
                    if observation.get("valid") is True
                }
                active_ids = _active_relay_ids(details)
                exact_live_set = (
                    set(observations) == set(expected_relay_ids)
                    and set(live_starts) == set(expected_relay_ids)
                    and relay_allowlist_valid
                    and hydra_safety.get("available") is True
                    and hydra_safety.get("fresh") is True
                )
                external_resume = bool(
                    active_ids
                    or (
                        exact_live_set
                        and all(start is not None for start in live_starts.values())
                        and min(live_starts.values()) <= suspend_until
                    )
                )
                candidate_since = _parse_time(
                    schedule_override.get("external_resume_candidate_since_utc")
                )
                candidate_last = _parse_time(
                    schedule_override.get("external_resume_candidate_last_seen_utc")
                )
                proof_payload = {
                    "active": sorted(active_ids),
                    "starts": {
                        str(relay_id): start.isoformat() if start is not None else None
                        for relay_id, start in sorted(live_starts.items())
                    },
                }
                proof_hash = _plan_change_fingerprint(
                    "EXTERNAL_RESUME", proof_payload
                )
                proof_now = _hydrawise_source_observation(details, now_utc=now)
                continuous = (
                    candidate_last is not None
                    and proof_now is not None
                    and timedelta(0) < proof_now - candidate_last <= timedelta(minutes=3)
                )
                if external_resume:
                    if proof_now is None or (candidate_last is not None and proof_now <= candidate_last):
                        return _persist_result(
                            store=store, original=original, state=state, result=result,
                            details=details, settings=settings,
                            decision_code="IRRIGATION_SCHEDULE_EXTERNAL_RESUME_CONFIRMING",
                            message="Die externe Planänderung wartet auf eine neue gültige Hydrawise-Quellbeobachtung.",
                        )
                    if (
                        candidate_since is None
                        or not continuous
                        or schedule_override.get("external_resume_candidate_hash")
                        != proof_hash
                    ):
                        confirming_override = {
                            **schedule_override,
                            "external_resume_candidate_hash": proof_hash,
                            "external_resume_candidate_since_utc": proof_now.isoformat(),
                            "external_resume_candidate_last_seen_utc": proof_now.isoformat(),
                        }
                        confirming_state = replace(
                            state,
                            revision=state.revision + 1,
                            irrigation_schedule_override_json=(
                                dump_irrigation_schedule_object(confirming_override)
                            ),
                        )
                        return _persist_result(
                            store=store,
                            original=original,
                            state=confirming_state,
                            result=result,
                            details=details,
                            settings=settings,
                            decision_code="IRRIGATION_SCHEDULE_EXTERNAL_RESUME_CONFIRMING",
                            message=(
                                "Eine direkte Hydrawise-Änderung wird über zwei "
                                "Minutenzyklen bestätigt."
                            ),
                        )
                    confirmation_minutes = _env_int(
                        environment,
                        "IRRIGATION_PLAN_CHANGE_CONFIRMATION_MINUTES",
                        2,
                        minimum=1,
                        maximum=15,
                    )
                    if proof_now - candidate_since < timedelta(
                        minutes=confirmation_minutes
                    ):
                        confirming_override = {
                            **schedule_override,
                            "external_resume_candidate_last_seen_utc": proof_now.isoformat(),
                        }
                        confirming_state = replace(
                            state,
                            revision=state.revision + 1,
                            irrigation_schedule_override_json=(
                                dump_irrigation_schedule_object(confirming_override)
                            ),
                        )
                        return _persist_result(
                            store=store,
                            original=original,
                            state=confirming_state,
                            result=result,
                            details=details,
                            settings=settings,
                            decision_code="IRRIGATION_SCHEDULE_EXTERNAL_RESUME_CONFIRMING",
                            message=(
                                "Die zweite Bestätigung der direkten "
                                "Hydrawise-Änderung steht noch aus."
                            ),
                        )
                    state = replace(
                        state,
                        revision=state.revision + 1,
                        irrigation_schedule_override_json=None,
                        irrigation_schedule_history_json=(
                            append_irrigation_schedule_history(
                                state.irrigation_schedule_history_json,
                                now_utc=now,
                                action=override_kind,
                                status="SUPERSEDED_EXTERNALLY",
                                summary=(
                                    "Die Pause wurde direkt in Hydrawise "
                                    "geändert oder aufgehoben."
                                ),
                            )
                        ),
                    )
                    schedule_override = None
                elif candidate_since is not None or candidate_last is not None:
                    cleaned_override = dict(schedule_override)
                    for key in (
                        "external_resume_candidate_hash",
                        "external_resume_candidate_since_utc",
                        "external_resume_candidate_last_seen_utc",
                    ):
                        cleaned_override.pop(key, None)
                    state = replace(
                        state,
                        revision=state.revision + 1,
                        irrigation_schedule_override_json=(
                            dump_irrigation_schedule_object(cleaned_override)
                        ),
                    )
                    schedule_override = cleaned_override
            if schedule_override is None:
                override_kind = ""
                suspend_until = None
            if override_kind == "CUSTOM_NEXT":
                coordination_disabled_hold = (
                    schedule_override.get("coordination_execution") is True
                    and not coordination_gate
                )
                if coordination_disabled_hold:
                    details["coordination_execution"].update(reason="COORDINATION_EXECUTION_DISABLED")
                desired_start = _parse_time(schedule_override.get("desired_start_utc"))
                if desired_start is None:
                    raise RuntimeError("Der angepasste Beregnungsstart fehlt.")
                if now > desired_start + timedelta(minutes=10):
                    failed_override = {
                        **schedule_override,
                        "status": "REJECTED",
                        "error": "Der sichere Startzeitpunkt wurde wegen einer Laufzeitlücke verpasst.",
                    }
                    state = replace(
                        state,
                        revision=state.revision + 1,
                        irrigation_schedule_override_json=dump_irrigation_schedule_object(
                            failed_override
                        ),
                    )
                    schedule_override = failed_override
                elif (not coordination_disabled_hold and desired_start - now <= timedelta(
                    minutes=irrigation_capture_max_lead_minutes
                )):
                    custom_zones = [
                        dict(zone) for zone in schedule_override.get("zones", [])
                        if isinstance(zone, dict)
                    ]
                    if len(custom_zones) != expected_zones:
                        raise RuntimeError("Der bestätigte angepasste Zonenplan ist unvollständig.")
                    canonical = json.dumps(
                        custom_zones,
                        ensure_ascii=False,
                        sort_keys=True,
                        separators=(",", ":"),
                    )
                    executing_override = {
                        **schedule_override,
                        "status": "EXECUTING",
                        "execution_started_utc": now.isoformat(),
                    }
                    state = replace(
                        state,
                        revision=state.revision + 1,
                        irrigation_phase="READY",
                        irrigation_plan_id=hashlib.sha256(
                            canonical.encode("utf-8")
                        ).hexdigest(),
                        irrigation_plan_json=canonical,
                        irrigation_suspended_relay_ids_json=json.dumps(
                            sorted(expected_relay_ids)
                        ),
                        irrigation_suspension_until_utc=schedule_override.get(
                            "suspend_until_utc"
                        ),
                        irrigation_suspension_completed_utc=now.isoformat(),
                        irrigation_completed_relay_ids_json="[]",
                        irrigation_current_relay_id=None,
                        irrigation_zone_start_reserved_utc=None,
                        irrigation_zone_started_utc=None,
                        irrigation_suspension_revalidation_last_seen_utc=None,
                        irrigation_suspension_revalidation_observed_utc=None,
                        irrigation_suspension_revalidation_observations=0,
                        irrigation_zone_clear_since_utc=None,
                        irrigation_completed_utc=None,
                        irrigation_failed_reason=None,
                        irrigation_schedule_override_json=dump_irrigation_schedule_object(
                            executing_override
                        ),
                    )
                    schedule_override = executing_override

        if schedule_override is not None and str(schedule_override.get("status")) == "EXECUTING":
            if state.irrigation_phase == "COMPLETE_HOLD":
                schedule_override = {
                    **schedule_override,
                    "status": "POST_RUN",
                    "completed_utc": state.irrigation_completed_utc or now.isoformat(),
                }
                state = replace(
                    state,
                    revision=state.revision + 1,
                    irrigation_schedule_override_json=dump_irrigation_schedule_object(
                        schedule_override
                    ),
                )
            elif state.irrigation_phase == "FAILED":
                schedule_override = {
                    **schedule_override,
                    "status": "REJECTED",
                    "error": state.irrigation_failed_reason or "Der angepasste Lauf wurde sicher angehalten.",
                }
                state = replace(
                    state,
                    revision=state.revision + 1,
                    irrigation_schedule_override_json=dump_irrigation_schedule_object(
                        schedule_override
                    ),
                )

        if schedule_override is not None:
            final_status = str(schedule_override.get("status") or "")
            final_kind = str(schedule_override.get("kind") or "")
            final_until = _parse_time(schedule_override.get("suspend_until_utc"))
            may_expire = (
                final_status == "ACTIVE" and final_kind in {"PAUSE", "SKIP_NEXT"}
            ) or final_status == "POST_RUN"
            if may_expire and final_until is not None and now >= final_until:
                summary = (
                    "Die zeitweise Beregnungspause ist beendet."
                    if final_kind == "PAUSE"
                    else "Die einmalige Beregnungsplan-Anpassung ist beendet."
                )
                state = replace(
                    state,
                    revision=state.revision + 1,
                    irrigation_schedule_override_json=None,
                    irrigation_schedule_history_json=append_irrigation_schedule_history(
                        state.irrigation_schedule_history_json,
                        now_utc=now,
                        action=final_kind,
                        status="COMPLETED",
                        summary=summary,
                    ),
                )
                schedule_override = None

    next_irrigation_start = raw_next_irrigation_start
    if schedule_override is not None:
        possible_start = _schedule_possible_irrigation_start(schedule_override)
        if possible_start is not None and possible_start > now:
            next_irrigation_start = min(
                [value for value in (next_irrigation_start, possible_start) if value is not None]
            )
    irrigation_capture_due = (
        schedule_override is None
        and
        next_irrigation_start is not None
        and timedelta(0)
        <= next_irrigation_start - now
        <= timedelta(minutes=irrigation_capture_max_lead_minutes)
    )
    irrigation_due = (
        irrigation_due
        or irrigation_capture_due
        or operator_action in {"START_IRRIGATION", "START_IRRIGATION_ZONE"}
    )
    if (
        operator_action in {"START_IRRIGATION", "START_IRRIGATION_ZONE"}
        and state.irrigation_phase in ACTIVE_IRRIGATION_PHASES
    ):
        state = _finish_operator_request(
            state,
            "Der sichere Beregnungsablauf läuft bereits.",
        )
        operator_action = None

    # Maintenance counter changes never take precedence over a safety return. They are only
    # sent in a completely clear cycle; otherwise the pending request waits
    # while the ordinary park/irrigation logic continues unchanged.
    hydra_safety_for_height = _as_dict(
        _as_dict(details.get("hydrawise")).get("safety")
    )
    height_cycle_is_clear = (
        not parking_block
        and not blocked_now
        and state.irrigation_phase is None
        and str(decision.get("hypothetical_command") or "").upper() != "PARK"
        and hydra_safety_for_height.get("available") is True
        and hydra_safety_for_height.get("fresh") is True
        and hydra_safety_for_height.get("clear_now") is True
    )
    blade_reset_cycle_is_clear = (
        state.irrigation_phase is None
        and (
            height_cycle_is_clear
            or activity in PARKED_ACTIVITIES
        )
    )
    if operator_action == "RESET_BLADE_USAGE" and blade_reset_cycle_is_clear:
        safe_to_reset = (
            settings.full_failsafe_write_gate_enabled
            and mower.get("connected") is True
            and error_code == 0
            and bool(mower_id)
        )
        if not safe_to_reset:
            rejected = _finish_operator_request(
                state,
                "Die Klingenlaufzeit wurde nicht zurückgesetzt, weil der Mäher nicht eindeutig verfügbar ist.",
                status="REJECTED",
            )
            return _persist_result(
                store=store, original=original, state=rejected, result=result,
                details=details, settings=settings,
                decision_code="BLADE_USAGE_RESET_REJECTED",
                message="Die Klingenlaufzeit blieb unverändert.",
            )
        try:
            response = blade_usage_reset_sender(
                str(environment.get("HUSQVARNA_CLIENT_ID", "")).strip(),
                str(environment.get("HUSQVARNA_CLIENT_SECRET", "")).strip(),
                mower_id,
            )
        except Exception as exc:
            rejected = _finish_operator_request(
                state,
                f"Klingenlaufzeit konnte nicht zurückgesetzt werden: {exc}",
                status="REJECTED",
            )
            return _persist_result(
                store=store, original=original, state=rejected, result=result,
                details=details, settings=settings,
                decision_code="BLADE_USAGE_RESET_REJECTED",
                message="Die Klingenlaufzeit blieb unverändert.",
            )
        completed = _finish_operator_request(
            state, "Die Klingenlaufzeit wurde zurückgesetzt."
        )
        details["blade_usage_reset"] = {"response": response}
        return _persist_result(
            store=store, original=original, state=completed, result=result,
            details=details, settings=settings,
            decision_code="BLADE_USAGE_RESET",
            message="Die Klingenlaufzeit wurde zurückgesetzt.",
            command_sent=True,
        )
    if operator_action == "SET_CUTTING_HEIGHT" and height_cycle_is_clear:
        if settings.enable_manual_sessions:
            height_result = run_full_height_control(
                store=store, original=original, state=state, mower=mower,
                environment=environment, settings=settings, now_utc=now,
                clock=command_clock, sender=cutting_height_sender,
            )
            details["cutting_height_action"] = {
                "millimetres": height_result.target_mm,
                "percent": height_result.target_percent,
                "area_id": height_result.area_id,
                "status": height_result.status,
                "reason_code": height_result.reason_code,
            }
            if height_result.status == "CONFIRMED":
                completed = _finish_operator_request(
                    height_result.state,
                    f"Die Schnitthöhe wurde auf {height_result.target_mm} mm eingestellt.",
                )
                return _persist_result(
                    store=store, original=original, state=completed,
                    result=result, details=details, settings=settings,
                    decision_code="CUTTING_HEIGHT_CONFIRMED",
                    message="Die neue Schnitthöhe wurde am Gerät frisch bestätigt.",
                )
            if height_result.status in {"SENT_UNCONFIRMED", "UNKNOWN"}:
                return replace(
                    result, decision_code=height_result.reason_code,
                    message="Die Schnitthöhe wartet auf eine frische Gerätebestätigung.",
                    command_sent=height_result.command_sent,
                    details=_decorate(
                        details, state=height_result.state, settings=settings,
                        persisted=True, command_sent=height_result.command_sent,
                    ),
                )
            rejected = _finish_operator_request(
                height_result.state,
                "Die Schnitthöhe wurde wegen fehlender sicherer Gerätebindung nicht geändert.",
                status="REJECTED",
            )
            return _persist_result(
                store=store, original=original, state=rejected, result=result,
                details=details, settings=settings,
                decision_code=height_result.reason_code,
                message="Die Schnitthöhe blieb unverändert.",
            )
        requested_mm = state.operator_request_cutting_height_mm
        area_id = int(target_area.get("id") or 0)
        safe_to_change = (
            settings.full_failsafe_write_gate_enabled
            and mower.get("connected") is True
            and error_code == 0
            and supports_metric_cutting_height(mower.get("model"))
            and area_id > 0
            and target_area.get("enabled") is not False
            and target_area.get("use_global_cutting_height") is False
            and requested_mm is not None
        )
        if not safe_to_change:
            rejected = _finish_operator_request(
                state,
                "Die Schnitthöhe wurde nicht geändert, weil Mäher oder Rasenfläche nicht eindeutig sicher verfügbar sind.",
                status="REJECTED",
            )
            return _persist_result(
                store=store,
                original=original,
                state=rejected,
                result=result,
                details=details,
                settings=settings,
                decision_code="CUTTING_HEIGHT_REJECTED",
                message="Die Schnitthöhe blieb unverändert.",
            )
        try:
            target_percent = cutting_height_mm_to_percent(requested_mm)
            client_id = str(environment.get("HUSQVARNA_CLIENT_ID", "")).strip()
            client_secret = str(environment.get("HUSQVARNA_CLIENT_SECRET", "")).strip()
            response = cutting_height_sender(
                client_id,
                client_secret,
                mower_id,
                area_id,
                target_percent,
            )
        except Exception as exc:
            rejected = _finish_operator_request(
                state,
                f"Schnitthöhe konnte nicht geändert werden: {exc}",
                status="REJECTED",
            )
            return _persist_result(
                store=store,
                original=original,
                state=rejected,
                result=result,
                details=details,
                settings=settings,
                decision_code="CUTTING_HEIGHT_REJECTED",
                message="Die Schnitthöhe blieb unverändert.",
            )
        completed = _finish_operator_request(
            state,
            f"Die Schnitthöhe wurde auf {requested_mm} mm eingestellt.",
        )
        details["cutting_height_action"] = {
            "millimetres": requested_mm,
            "percent": target_percent,
            "response": response,
        }
        return _persist_result(
            store=store,
            original=original,
            state=completed,
            result=result,
            details=details,
            settings=settings,
            decision_code="CUTTING_HEIGHT_SET",
            message=f"Die Schnitthöhe wurde auf {requested_mm} mm eingestellt.",
            command_sent=True,
        )
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
    external_irrigation_observed = (
        operator_action not in {"START_IRRIGATION", "START_IRRIGATION_ZONE"}
        and state.irrigation_phase is None
        and _external_irrigation_is_running_or_changing_zone(
            state,
            details,
            now_utc=now,
            previous_state=original,
        )
    )
    details["external_irrigation_observation"] = {
        "active": external_irrigation_observed,
        "command_sent": False,
        "reason": (
            "Hydrawise-Lauf wurde bereits außerhalb der Automatik gestartet."
            if external_irrigation_observed
            else None
        ),
    }
    if irrigation_due and state.irrigation_phase is None and not external_irrigation_observed:
        try:
            if operator_action == "START_IRRIGATION":
                plan_id, zones = _validated_operator_plan(
                    details,
                    now_utc=now,
                    expected_zone_count=expected_zones,
                    expected_relay_ids=expected_relay_ids,
                )
            elif operator_action == "START_IRRIGATION_ZONE":
                plan_id, zones = _validated_operator_single_zone_plan(
                    details,
                    now_utc=now,
                    expected_zone_count=expected_zones,
                    expected_relay_ids=expected_relay_ids,
                    requested_zone=state.operator_request_zone,
                    requested_run_seconds=state.operator_request_run_seconds,
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
                irrigation_suspension_revalidation_last_seen_utc=None,
                irrigation_suspension_revalidation_observed_utc=None,
                irrigation_suspension_revalidation_observations=0,
                irrigation_cancelled_without_run_utc=None,
            )
            if operator_action in {"START_IRRIGATION", "START_IRRIGATION_ZONE"}:
                if state.irrigation_onsite_dock_proof_json is not None:
                    state = onsite_dock_proof.bind_plan(
                        state,
                        request_id=str(state.operator_request_id or ""),
                        action=operator_action,
                        plan_id=plan_id,
                        plan=zones,
                        now_utc=now,
                        end_confirmation_minutes=_env_int(
                            environment,
                            "IRRIGATION_ZONE_END_CONFIRMATION_MINUTES",
                            2,
                            minimum=1,
                            maximum=10,
                        ),
                    )
                state = _finish_operator_request(
                    state,
                    (
                        "Die einzelne Zone wurde sicher vorbereitet."
                        if operator_action == "START_IRRIGATION_ZONE"
                        else "Der sichere Sieben-Zonen-Ablauf wurde vorbereitet."
                    ),
                )
                operator_action = None
        except Exception as exc:
            if operator_action in {"START_IRRIGATION", "START_IRRIGATION_ZONE"}:
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

    occupancy_only_sources = frozenset({"training", "match", "special"})
    current_occupancy_key = _occupancy_block_key(blocked_now)
    current_manual_block_key = manual_block_key(blocked_now)
    current_occupancy_end = _parse_time(blocked_now.get("end"))
    all_current_block_sources = (
        _source_parts(blocked_now.get("source"))
        | _source_parts(parking_block.get("source"))
    )
    requested_override_key = str(
        state.operator_request_occupancy_override_key or ""
    ).strip()
    occupancy_override_request = (
        operator_action == "START_MOWING" and bool(requested_override_key)
    )
    occupancy_block_is_overrideable = (
        current_occupancy_key is not None
        and current_occupancy_end is not None
        and current_occupancy_end > now
        and bool(all_current_block_sources)
        and all_current_block_sources.issubset(occupancy_only_sources)
        and occupancy_override_allowed(blocked_now)
        and (not parking_block or occupancy_override_allowed(parking_block))
        and state.irrigation_phase is None
    )
    if occupancy_override_request and (
        not occupancy_block_is_overrideable
        or requested_override_key != current_occupancy_key
    ):
        rejected = _finish_operator_request(
            state,
            "Der bestätigte Belegungsblock ist nicht mehr aktuell oder enthält eine nicht überstimmbare Sicherheitssperre.",
            status="REJECTED",
        )
        return _persist_result(
            store=store,
            original=original,
            state=rejected,
            result=result,
            details=details,
            settings=settings,
            decision_code="OPERATOR_OCCUPANCY_OVERRIDE_REJECTED",
            message="Der Mäher blieb geparkt; die Belegungsausnahme war nicht mehr eindeutig gültig.",
        )

    saved_override_until = _parse_time(state.operator_occupancy_override_until_utc)
    saved_override_key = str(state.operator_occupancy_override_key or "").strip()
    saved_override_valid = (
        occupancy_block_is_overrideable
        and saved_override_key == current_occupancy_key
        and saved_override_until is not None
        and saved_override_until == current_occupancy_end
        and now < saved_override_until
    )
    manual_confirmed_keys = (
        set(manual_session.get("confirmed_block_keys", []))
        if settings.enable_manual_sessions
        and manual_session is not None
        and manual_session.get("kind") == "START"
        and manual_session.get("status") in {"PENDING", "ACTIVE"}
        else set()
    )
    manual_occupancy_override_active = (
        manual_permission.get("allowed") is True
        and current_manual_block_key is not None
        and current_manual_block_key in manual_confirmed_keys
    )
    occupancy_override_active = (
        occupancy_override_request
        or saved_override_valid
        or manual_occupancy_override_active
    )
    if occupancy_override_request:
        state = replace(
            state,
            revision=state.revision + 1,
            operator_occupancy_override_key=current_occupancy_key,
            operator_occupancy_override_until_utc=current_occupancy_end.isoformat(),
        )
    elif not saved_override_valid and (
        state.operator_occupancy_override_key is not None
        or state.operator_occupancy_override_until_utc is not None
    ):
        state = replace(
            state,
            revision=state.revision + 1,
            operator_occupancy_override_key=None,
            operator_occupancy_override_until_utc=None,
        )

    effective_blocked_now = {} if occupancy_override_active else blocked_now
    effective_parking_block = {} if occupancy_override_active else parking_block
    details["operator_occupancy_override"] = {
        "active": occupancy_override_active,
        "block_key": current_occupancy_key if occupancy_override_active else None,
        "until_utc": (
            current_occupancy_end.isoformat()
            if occupancy_override_active and current_occupancy_end is not None
            else None
        ),
        "does_not_override_irrigation": True,
        "manual_session": manual_occupancy_override_active,
    }

    # This covers water that the controller did not initiate too.  Do not
    # transmit an unowned stop; freeze automation and surface an on-site
    # controller check when fresh Hydrawise evidence says it is outside the
    # mandatory local operating window.
    operating_bounds_now = operating_bounds(now)
    actual_active_ids = _active_relay_ids(details)
    try:
        active_operator_manual_plan = _operator_manual_water_is_owned(
            state,
            plan=_plan_from_state(state),
            active_relay_ids=actual_active_ids,
            expected_relay_ids=expected_relay_ids,
        )
    except RuntimeError:
        active_operator_manual_plan = False
    if (
        operating_bounds_now is not None
        and actual_active_ids
        and not active_operator_manual_plan
        and hydra_safety.get("available") is True
        and hydra_safety.get("fresh") is True
        and not (operating_bounds_now[0] <= now < operating_bounds_now[1])
    ):
        outside_hold = _failed_irrigation(
            state,
            "Frische Hydrawise-Beobachtung meldet Wasser außerhalb 03:30--08:00 Europe/Berlin.",
        )
        details["irrigation_operating_window"] = {
            "code": "ACTIVE_OUTSIDE_WINDOW",
            "earliest_start_utc": operating_bounds_now[0].isoformat(),
            "latest_end_utc": operating_bounds_now[1].isoformat(),
            "active_relay_ids": sorted(actual_active_ids),
            "automatic_stop_sent": False,
            "required_action": "ON_SITE_CONTROLLER_CHECK",
        }
        return _persist_result(
            store=store, original=original, state=outside_hold, result=result,
            details=details, settings=settings,
            decision_code="IRRIGATION_ACTIVE_OUTSIDE_OPERATING_WINDOW",
            message="Bewässerung bitte beenden und den Controller vor Ort prüfen.",
        )

    occupancy_sources = _source_parts(
        effective_parking_block.get("source")
    ) & occupancy_only_sources
    external_park_evidence = (
        override_action in PARK_OVERRIDE_ACTIONS
        or (
            activity in PARKED_ACTIVITIES
            and not state.continuous_mowing_owned
        )
    )
    if (
        effective_parking_block
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

    wants_park = (
        str(decision.get("hypothetical_command") or "").upper() == "PARK"
        and not occupancy_override_active
    )
    if operator_action == "PARK_MOWER":
        wants_park = True
    schedule_resume_parking = (
        schedule_override is not None
        and str(schedule_override.get("kind") or "") == "RESUME"
        and str(schedule_override.get("status") or "") == "PARKING"
        and activity not in PARKED_ACTIVITIES
    )
    if schedule_resume_parking:
        wants_park = True
    onsite_station_authorized = onsite_dock_proof.valid_for_plan(
        state, mower, environment, now,
    )
    if (
        state.irrigation_phase in ACTIVE_IRRIGATION_PHASES
        and not state.parked_by_automation
        and not onsite_station_authorized
    ):
        wants_park = True
    owns_matching_park = (
        state.parked_by_automation
        and bool(_source_parts(block_source))
        and _source_parts(block_source).issubset(
            _source_parts(state.automation_park_source)
        )
    )
    if effective_parking_block and not owns_matching_park and not onsite_station_authorized:
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
    takeover_deadline = _parse_time(state.continuous_mowing_takeover_deadline_utc)
    takeover_return_due = bool(state.continuous_mowing_owned and state.continuous_mowing_observed_takeover
                               and activity in PARK_COMMAND_ACTIVITIES
                               and (takeover_deadline is None or now >= takeover_deadline))
    if takeover_return_due:
        wants_park = True

    park_guard_required = (
        bool(
            _source_parts(effective_parking_block.get("source"))
            & PARK_GUARD_BLOCK_SOURCES
        )
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
            or ("continuous" if takeover_return_due else "")
            or (
                "irrigation"
                if irrigation_outage_park_due
                or schedule_resume_parking
                or state.irrigation_phase in ACTIVE_IRRIGATION_PHASES
                else ""
            )
            or (str(state.automation_park_source or "").strip().lower() if park_reassert_due else "")
            or "hydrawise_unconfirmed"
        )
        block_end = _park_valid_until(
            now_utc=now,
            state=state,
            parking_block=effective_parking_block,
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
        mower_cannot_receive_park_command = (
            not mower_id
            or error_code != 0
            or mower_state in ERROR_STATES
            or activity not in PARK_COMMAND_ACTIVITIES
        )
        if mower_cannot_receive_park_command:
            details["park_command_unavailable"] = {
                "mower_id_available": bool(mower_id),
                "activity": activity,
                "state": mower_state,
                "error_code": error_code,
                "irrigation_schedule_capture_continues": (
                    state.irrigation_phase in {"PLANNED", "SUSPENDING"}
                ),
            }
            # Ein lokaler Hydrawise-Zeitplan darf nicht allein deshalb anlaufen,
            # weil ein fehlerhafter oder nicht erreichbarer Mäher keinen
            # Parkbefehl annehmen kann. In der Vorbereitungsphase lassen wir
            # deshalb die darunterliegende, separat abgesicherte Suspendierung
            # der sieben Zonen weiterlaufen. Sie darf weiterhin nur mit
            # vollständigem Relay-Allowlist- und Frischenachweis schreiben.
            # Jeder spätere Wasserstart bleibt unverändert durch eigenen
            # Parkbesitz und die fortlaufende Dockbestätigung gesperrt.
            if state.irrigation_phase in {"PLANNED", "SUSPENDING"}:
                pass
            else:
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
        else:
            intent = CommandIntent(
                action="PARK",
                target=mower_id,
                reason=(
                    f"reassert|{park_source}|{effective_parking_block.get('start', '')}|"
                    f"{effective_parking_block.get('end', '')}|{state.irrigation_phase or ''}"
                    if park_reassert_due
                    else f"{park_source}|{effective_parking_block.get('start', '')}|{effective_parking_block.get('end', '')}"
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
            command_state = state.record_command(
                fingerprint=intent.fingerprint,
                sent_utc=now,
                action="PARK",
                park_until_utc=block_end,
                park_source=park_source,
                restart_allowed=_restart_allowed(park_source),
            )
            if operator_action == "PARK_MOWER":
                if settings.enable_manual_sessions and manual_session is not None:
                    active_park = with_session_status(
                        manual_session, "ACTIVE", now_utc=now,
                    )
                    command_state = replace(
                        command_state,
                        manual_session_json=dump_manual_session(active_park),
                    )
                command_state = _finish_operator_request(
                    command_state,
                    "Der sichere Parkbefehl wurde gesendet.",
                )
            if settings.enable_manual_sessions:
                def check_park(latest: AutomationState, at: datetime) -> None:
                    if latest.maintenance_mode or not mower_id or at >= block_end:
                        raise DeviceSendBlocked("PARK_SEND_REVOKED")
                try:
                    sent = dispatch_device_send(
                        store=store, original=original, state=command_state,
                        sender=park_sender, args=(client_id, client_secret, mower_id),
                        kind="PARK", target=mower_id,
                        intent_key=f"park:{intent.fingerprint}", now_utc=now,
                        clock=command_clock, before_send_check=check_park,
                        deadline_utc=min(block_end, now + timedelta(seconds=30)),
                    )
                except DeviceSendBlocked as exc:
                    blocked_state = exc.state if isinstance(exc.state, AutomationState) else state
                    return replace(
                        result, decision_code=exc.code,
                        message="Der Parkbefehl wurde an der persistenten Sendegrenze angehalten.",
                        command_sent=exc.transport_started,
                        details=_decorate(details, state=blocked_state, settings=settings,
                                          persisted=True, command_sent=exc.transport_started),
                    )
                response = sent.response
                command_state = sent.state
                original = sent.state
                command_state = replace(command_state, revision=command_state.revision + 1)
            else:
                response = park_sender(client_id, client_secret, mower_id)
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
        failed_clear_since = _parse_time(state.hydrawise_clear_since_utc)
        failed_release_minutes = _env_int(
            environment,
            "POST_IRRIGATION_DRYING_MINUTES",
            150,
            minimum=150,
            maximum=1440,
        )
        expired_confirmation_minutes = _env_int(
            environment,
            "IRRIGATION_PLAN_CHANGE_CONFIRMATION_MINUTES",
            2,
            minimum=1,
            maximum=10,
        )
        expired_unused_proof = _expired_unused_irrigation_proof(
            state,
            original,
            details,
            now_utc=now,
            expected_relay_ids=expected_relay_ids,
            relay_allowlist_valid=relay_allowlist_valid,
            confirmation_minutes=expired_confirmation_minutes,
        )
        expired_lease_without_run = (
            state.irrigation_failed_reason
            == EXPIRED_IRRIGATION_PLAN_LEASE_FAILURE
            and expired_unused_proof["eligible"] is True
        )
        if expired_lease_without_run:
            fingerprint = _plan_change_fingerprint(
                "FAILED_WINDOW_EXPIRED_WITHOUT_RUN",
                {
                    "plan_id": state.irrigation_plan_id,
                    "stored_plan_end_utc": expired_unused_proof[
                        "stored_plan_end_utc"
                    ],
                    "suspend_until_utc": expired_unused_proof[
                        "suspend_until_utc"
                    ],
                },
            )
            candidate_state, confirmed = _candidate_confirmation(
                state,
                fingerprint=fingerprint,
                now_utc=now,
                observed_utc=_hydrawise_source_observation(details, now_utc=now),
                required_minutes=expired_confirmation_minutes,
            )
            details["irrigation_expired_failed_gate"] = {
                **expired_unused_proof,
                "confirmed": confirmed,
            }
            if not confirmed:
                return _persist_result(
                    store=store,
                    original=original,
                    state=candidate_state,
                    result=result,
                    details=details,
                    settings=settings,
                    decision_code="IRRIGATION_EXPIRED_FAILED_CONFIRMING",
                    message=(
                        "Der ausgefallene Beregnungslauf wird nochmals mit "
                        "allen sieben Hydrawise-Zonen abgeglichen."
                    ),
                )
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
                decision_code="IRRIGATION_EXPIRED_FAILED_RELEASED",
                message=(
                    "Der ausgefallene Lauf hat nachweislich kein Wasser gestartet; "
                    "die Beregnungssperre ist sicher beendet."
                ),
            )
        partial_end_proof: dict[str, Any] = {"eligible": False}
        if state.irrigation_failed_reason == PARTIAL_IRRIGATION_WINDOW_FAILURE:
            try:
                partial_end_proof = _partial_irrigation_end_proof(
                    state,
                    details,
                    expected_relay_ids=expected_relay_ids,
                    relay_allowlist_valid=relay_allowlist_valid,
                )
            except RuntimeError as exc:
                details["irrigation_partial_end_recovery"] = {
                    "eligible": False,
                    "recovered": False,
                    "reason": f"{type(exc).__name__}: {exc}",
                    "command_sent": False,
                }
        safely_ended_partial_run = (
            state.irrigation_failed_reason == PARTIAL_IRRIGATION_WINDOW_FAILURE
            and mower.get("connected") is True
            and activity in PARKED_ACTIVITIES
            and error_code == 0
            and mower_state not in ERROR_STATES
            and partial_end_proof["eligible"] is True
        )
        if safely_ended_partial_run:
            details["irrigation_partial_end_recovery"] = {
                **partial_end_proof,
                "recovered": True,
                "command_sent": False,
                "reason": (
                    "Der teilweise gelaufene Beregnungsplan ist physisch sicher "
                    "beendet; die volle Trocknungssperre bleibt bestehen."
                ),
            }
            state = _complete_partial_irrigation(state, now_utc=now)
        recoverable_external_plan_failure = (
            state.irrigation_failed_reason == RECOVERABLE_EXTERNAL_PLAN_FAILURE
            and mower.get("connected") is True
            and activity in PARKED_ACTIVITIES
            and error_code == 0
            and mower_state not in ERROR_STATES
            and hydra_safety.get("available") is True
            and hydra_safety.get("fresh") is True
            and hydra_safety.get("clear_now") is True
            and int(hydra_safety.get("active_zone_count") or 0) == 0
            and relay_allowlist_valid
            and failed_clear_since is not None
            and state.hydrawise_clear_origin == "IRRIGATION_END"
            and now - failed_clear_since
            >= timedelta(minutes=failed_release_minutes)
        )
        if safely_ended_partial_run:
            pass
        elif recoverable_external_plan_failure:
            details["irrigation_failure_recovery"] = {
                "recovered": True,
                "reason": (
                    "Extern gestarteter Lauf ist seit der konfigurierten "
                    "Trocknungszeit sicher beendet."
                ),
                "clear_since_utc": failed_clear_since.isoformat(),
                "command_sent": False,
            }
            state = _clear_irrigation(state)
        else:
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
        active_override = _schedule_override(state)
        # Do not return here when the pilot is switched off.  The normal active
        # state machine must still run its lease, parking and stop recovery.
        confirmation_minutes = _env_int(
            environment,
            "MOWER_PARK_CONFIRMATION_MINUTES",
            1,
            minimum=1,
            maximum=15,
        )
        required_park_observations = _env_int(
            environment,
            "MOWER_PARK_CONFIRMATION_CYCLES",
            2,
            minimum=2,
            maximum=10,
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
        execution_zones = [zone for zone in zones if zone.get("selected", True) is not False]
        single_zone_plan = (
            len(execution_zones) == 1
            and bool(execution_zones[0].get("operator_single_zone"))
        )
        active_override_for_plan = _schedule_override(state)
        coordination_plan = bool(
            active_override_for_plan
            and active_override_for_plan.get("coordination_execution") is True
        )
        schedule_override_plan = bool(zones) and (
            coordination_plan or all(
                zone.get("operator_schedule_override") is True for zone in zones
            )
        )
        operator_manual_plan = _operator_manual_irrigation(
            zones,
            plan_id=state.irrigation_plan_id,
            expected_relay_ids=expected_relay_ids,
        )
        execution_zone_count = len(execution_zones)
        active_ids = _active_relay_ids(details)
        completed = _json_ints(state.irrigation_completed_relay_ids_json)
        suspended = _json_ints(state.irrigation_suspended_relay_ids_json)
        all_ids = [int(zone["relay_id"]) for zone in zones]
        if (
            len(zones) != expected_zones
            or len(set(all_ids)) != expected_zones
            or set(all_ids) != expected_relay_ids
            or execution_zone_count < 1
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
            operator_action in {"STOP_IRRIGATION_AFTER_ZONE", "STOP_IRRIGATION_NOW"}
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
            if single_zone_plan:
                change_kind, reconciled_plan, change_reason = (
                    "UNCHANGED", zones, "Manuell gewählte Einzelzone bleibt unverändert."
                )
            else:
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
                    observed_utc=_hydrawise_source_observation(details, now_utc=now),
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
                        irrigation_suspension_revalidation_last_seen_utc=None,
                        irrigation_suspension_revalidation_observed_utc=None,
                        irrigation_suspension_revalidation_observations=0,
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
            relay = int(pending["relay_id"])
            suspended.append(relay)
            updated = replace(
                state,
                revision=state.revision + 1,
                irrigation_phase="READY" if len(suspended) == expected_zones else "SUSPENDING",
                irrigation_suspended_relay_ids_json=json.dumps(sorted(set(suspended))),
                irrigation_suspension_until_utc=suspend_until.isoformat(),
                irrigation_suspension_completed_utc=(
                    now.isoformat() if len(set(suspended)) == expected_zones else None
                ),
                irrigation_suspension_revalidation_last_seen_utc=None,
                irrigation_suspension_revalidation_observed_utc=None,
                irrigation_suspension_revalidation_observations=0,
            )
            if settings.enable_manual_sessions:
                suspend_key = (
                    f"irrigation:{state.irrigation_plan_id}:suspend:{relay}:"
                    f"{suspend_until.isoformat()}"
                )

                def check_irrigation_suspend(latest: AutomationState, at: datetime) -> None:
                    if (
                        latest.maintenance_mode
                        or latest.irrigation_plan_id != state.irrigation_plan_id
                        or latest.irrigation_phase not in {"SUSPENDING", "READY"}
                        or relay not in set(_json_ints(latest.irrigation_suspended_relay_ids_json))
                        or at >= suspend_until
                    ):
                        raise DeviceSendBlocked("IRRIGATION_SUSPEND_REVOKED")

                try:
                    sent = dispatch_device_send(
                        store=store, original=original, state=updated,
                        sender=suspend_zone_sender,
                        args=(api_key, relay, int(suspend_until.timestamp()), controller_id),
                        kind="SUSPEND", target=str(relay), intent_key=suspend_key,
                        now_utc=now, clock=command_clock,
                        before_send_check=check_irrigation_suspend,
                        deadline_utc=min(suspend_until, now + timedelta(seconds=30)),
                    )
                except DeviceSendBlocked as exc:
                    blocked_state = exc.state if isinstance(exc.state, AutomationState) else state
                    return replace(
                        result, decision_code=exc.code,
                        message="Der Hydrawise-Suspendierungsbefehl wurde nicht wiederholt.",
                        command_sent=exc.transport_started,
                        details=_decorate(details, state=blocked_state, settings=settings,
                                          persisted=True, command_sent=exc.transport_started),
                    )
                response = sent.response
                updated = sent.state
                original = sent.state
                updated = replace(updated, revision=updated.revision + 1)
            else:
                response = suspend_zone_sender(
                    api_key, relay, int(suspend_until.timestamp()), controller_id,
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

        if state.irrigation_phase == "READY":
            expired_confirmation_minutes = _env_int(
                environment,
                "IRRIGATION_PLAN_CHANGE_CONFIRMATION_MINUTES",
                2,
                minimum=1,
                maximum=10,
            )
            expired_unused_proof = _expired_unused_irrigation_proof(
                state,
                original,
                details,
                now_utc=now,
                expected_relay_ids=expected_relay_ids,
                relay_allowlist_valid=relay_allowlist_valid,
                confirmation_minutes=expired_confirmation_minutes,
            )
            details["irrigation_expired_ready_gate"] = expired_unused_proof
            if expired_unused_proof["eligible"] is True:
                fingerprint = _plan_change_fingerprint(
                    "READY_WINDOW_EXPIRED_WITHOUT_RUN",
                    {
                        "plan_id": state.irrigation_plan_id,
                        "stored_plan_end_utc": expired_unused_proof[
                            "stored_plan_end_utc"
                        ],
                        "suspend_until_utc": expired_unused_proof[
                            "suspend_until_utc"
                        ],
                    },
                )
                candidate_state, confirmed = _candidate_confirmation(
                    state,
                    fingerprint=fingerprint,
                    now_utc=now,
                    observed_utc=_hydrawise_source_observation(details, now_utc=now),
                    required_minutes=expired_confirmation_minutes,
                )
                details["irrigation_expired_ready_gate"]["confirmed"] = confirmed
                if not confirmed:
                    return _persist_result(
                        store=store,
                        original=original,
                        state=candidate_state,
                        result=result,
                        details=details,
                        settings=settings,
                        decision_code="IRRIGATION_EXPIRED_READY_CONFIRMING",
                        message=(
                            "Der verpasste Beregnungslauf wird vor der Freigabe "
                            "nochmals mit Hydrawise abgeglichen."
                        ),
                    )
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
                    decision_code="IRRIGATION_EXPIRED_READY_RELEASED",
                    message=(
                        "Der nicht gestartete Beregnungslauf ist sicher beendet; "
                        "die freie Zeit kann wieder zum Mähen genutzt werden."
                    ),
                )

        onsite_station_authorized = onsite_dock_proof.valid_for_plan(
            state, mower, environment, now,
        )
        if not state.parked_by_automation and not onsite_station_authorized:
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

        # A vendor event may remain unchanged while direct reads keep proving
        # the same owned HOME park. The opt-in durable proof is only for water;
        # ordinary mower starts retain their strict event freshness requirement.
        fresh_park_event_required = state.irrigation_phase in {
            "PLANNED",
            "SUSPENDING",
            "READY",
        }
        fresh_park_event_accepted = (
            not fresh_park_event_required
            or _water_park_authorized(state, mower, now_utc=now, environment=environment,
                                      max_age_seconds=mower_status_max_age_seconds)
        )
        park_gate_required = state.irrigation_phase in {
            "PLANNED", "SUSPENDING", "READY",
        }
        normal_park_ready = _park_confirmation_ready(
                state,
                now_utc=now,
                activity=activity,
                mower_state=mower_state,
                mower_mode=str(mower.get("mode") or ""),
                confirmation_minutes=confirmation_minutes,
                required_observations=required_park_observations,
            )
        if park_gate_required and (
            not (normal_park_ready or onsite_station_authorized)
            or mower.get("connected") is not True
            or not fresh_park_event_accepted
            or error_code != 0
            or mower_state in ERROR_STATES
        ):
            details["irrigation_park_gate"] = {
                "connected": mower.get("connected") is True,
                "status_fresh": mower_status_fresh,
                "fresh_event_required": fresh_park_event_required,
                "fresh_event_accepted": fresh_park_event_accepted,
                "activity": activity,
                "observations": int(state.park_confirmed_observations or 0),
                "required_observations": required_park_observations,
                "confirmed_since_utc": state.park_confirmed_utc,
                "minimum_minutes": confirmation_minutes,
            }
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
        ) - frozenset({"irrigation"})
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
                    "Wasserstart wartet auf das Ende der Rasenbelegung."
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

        if not single_zone_plan and not schedule_override_plan and state.irrigation_phase in {"READY", "RUNNING"} and (
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
                    observed_utc=_hydrawise_source_observation(details, now_utc=now),
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
                    irrigation_suspension_revalidation_last_seen_utc=None,
                    irrigation_suspension_revalidation_observed_utc=None,
                    irrigation_suspension_revalidation_observations=0,
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
            if operator_action in {"STOP_IRRIGATION_AFTER_ZONE", "STOP_IRRIGATION_NOW"}:
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
                        "Keine weitere Zone startet; die konfigurierte "
                        "Trocknungssperre beginnt."
                    ),
                )
            scheduled_override_start = min(
                (_parse_time(zone.get("scheduled_start_utc")) for zone in zones),
                default=None,
            )
            if (
                schedule_override_plan
                and not completed
                and current_id is None
                and scheduled_override_start is not None
                and now < scheduled_override_start
            ):
                return _persist_result(
                    store=store,
                    original=original,
                    state=state,
                    result=result,
                    details=details,
                    settings=settings,
                    decision_code="IRRIGATION_CUSTOM_START_WAIT",
                    message="Der Mäher ist sicher geparkt; der angepasste Beregnungsstart wird abgewartet.",
                )
            if not completed and current_id is None:
                if single_zone_plan or schedule_override_plan:
                    change_kind, reconciled_plan, change_reason = (
                        "UNCHANGED",
                        zones,
                        "Vom Platzwart bestätigter Zonenplan bleibt unverändert.",
                    )
                else:
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
                        observed_utc=_hydrawise_source_observation(details, now_utc=now),
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
                            irrigation_suspension_revalidation_last_seen_utc=None,
                            irrigation_suspension_revalidation_observed_utc=None,
                            irrigation_suspension_revalidation_observations=0,
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
            next_zone = next(
                (zone for zone in execution_zones if int(zone["relay_id"]) not in completed),
                None,
            )
            if coordination_plan and next_zone is not None:
                next_scheduled_start = _parse_time(next_zone.get("scheduled_start_utc"))
                if next_scheduled_start is None:
                    failed = _failed_irrigation(state, "Der koordinierte Zonenabstand ist nicht persistent nachweisbar.")
                    return _persist_result(
                        store=store, original=original, state=failed, result=result,
                        details=details, settings=settings,
                        decision_code="COORDINATION_EXECUTION_GAP_INVALID",
                        message=failed.irrigation_failed_reason or "Koordinierter Zonenabstand unklar.",
                    )
                if now < next_scheduled_start:
                    return _persist_result(
                        store=store, original=original, state=state, result=result,
                        details=details, settings=settings,
                        decision_code="COORDINATION_EXECUTION_ZONE_GAP_WAIT",
                        message="Der bestätigte Abstand bis zur nächsten koordinierten Zone wird eingehalten.",
                    )
            if next_zone is None:
                partial_schedule_override = (
                    schedule_override_plan and execution_zone_count < expected_zones
                )
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
                    decision_code=(
                        "IRRIGATION_SELECTED_ZONES_CONFIRMED_COMPLETE"
                        if partial_schedule_override
                        else "IRRIGATION_ALL_ZONES_CONFIRMED_COMPLETE"
                    ),
                    message=(
                        "Die gewählte Zone ist bestätigt beendet; der konfigurierte Sicherheitsnachlauf beginnt."
                        if single_zone_plan
                        else "Alle ausgewählten Zonen sind bestätigt beendet; der konfigurierte Sicherheitsnachlauf beginnt."
                        if partial_schedule_override
                        else "Alle sieben Zonen sind bestätigt beendet; der konfigurierte Sicherheitsnachlauf beginnt."
                    ),
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
                ordinary_suspension_proof_valid = (
                    set(suspended) == expected_relay_ids
                    and suspension_completed is not None
                    and timedelta(0)
                    <= now - suspension_completed
                    <= timedelta(minutes=plan_lease_minutes)
                    and first_planned_start is not None
                    and now < first_planned_start
                )
                stored_suspend_until = _parse_time(
                    state.irrigation_suspension_until_utc
                )
                observations = _zone_observation_by_relay(details)
                schedule_override_suspension_proof_valid = (
                    schedule_override_plan
                    and set(suspended) == expected_relay_ids
                    and stored_suspend_until is not None
                    and stored_suspend_until > now
                    and set(observations) == expected_relay_ids
                    and not active_ids
                    and all(
                        (
                            (start := _parse_time(observation.get("scheduled_start_utc")))
                            is None
                            or start > stored_suspend_until
                        )
                        for observation in observations.values()
                    )
                )
                ordinary_live_suspension_proof_valid = (
                    not schedule_override_plan
                    and set(suspended) == expected_relay_ids
                    and stored_suspend_until is not None
                    and stored_suspend_until > now
                    and set(observations) == expected_relay_ids
                    and all(
                        observation.get("valid") is True
                        for observation in observations.values()
                    )
                    and not active_ids
                    and all(
                        (
                            (start := _parse_time(observation.get("scheduled_start_utc")))
                            is None
                            or start > stored_suspend_until
                        )
                        for observation in observations.values()
                    )
                )
                if settings.enable_manual_sessions and ordinary_live_suspension_proof_valid:
                    source_observed = _hydrawise_source_observation(details, now_utc=now)
                    for relay in sorted(expected_relay_ids):
                        intent_key = (
                            f"irrigation:{state.irrigation_plan_id}:suspend:{relay}:"
                            f"{stored_suspend_until.isoformat()}"
                        )
                        entry = next(
                            (
                                item for item in unresolved_device_sends(state)
                                if item.get("intent_key") == intent_key
                            ),
                            None,
                        )
                        dispatched = _parse_time(entry.get("dispatched_at_utc")) if entry else None
                        if (
                            source_observed is not None and dispatched is not None
                            and source_observed > dispatched
                        ):
                            state = reconcile_device_send(
                                state, intent_key, now,
                                f"relay-{relay}-scheduled-after-suspension",
                            )
                required_revalidation_observations = _env_int(
                    environment, "IRRIGATION_SUSPENSION_REVALIDATION_CYCLES",
                    2, minimum=2, maximum=10,
                )
                renewed_observed = _parse_time(
                    state.irrigation_suspension_revalidation_observed_utc
                )
                # A confirmed renewal is independent of the original start
                # time, but never independent of current complete zone proof.
                # Keep it short-lived and bound to the completed renewal, not
                # an older observation retained across a plan change.
                renewed_suspension_proof_valid = (
                    ordinary_live_suspension_proof_valid
                    and suspension_completed is not None
                    and timedelta(0) <= now - suspension_completed
                    <= timedelta(minutes=plan_lease_minutes)
                    and renewed_observed is not None
                    and renewed_observed >= suspension_completed
                    and renewed_observed == _parse_time(
                        state.irrigation_suspension_revalidation_last_seen_utc
                    )
                    and int(state.irrigation_suspension_revalidation_observations or 0)
                    >= required_revalidation_observations
                )
                suspension_proof_valid = (
                    ordinary_suspension_proof_valid
                    or schedule_override_suspension_proof_valid
                    or renewed_suspension_proof_valid
                )
                if (
                    not suspension_proof_valid
                    and ordinary_live_suspension_proof_valid
                ):
                    revalidation_gap_seconds = _env_int(
                        environment,
                        "IRRIGATION_SUSPENSION_REVALIDATION_MAX_GAP_SECONDS",
                        90,
                        minimum=60,
                        maximum=300,
                    )
                    observed_state, revalidated = (
                        _record_suspension_revalidation_observation(
                            state,
                            now_utc=now,
                            observed_utc=_hydrawise_source_observation(details, now_utc=now),
                            not_before_utc=_parse_time(
                                state.irrigation_suspension_completed_utc
                            ),
                            max_gap_seconds=revalidation_gap_seconds,
                            required_observations=required_revalidation_observations,
                        )
                    )
                    details["irrigation_suspension_revalidation"] = {
                        "valid_live_proof": True,
                        "observations": (
                            observed_state.irrigation_suspension_revalidation_observations
                        ),
                        "required_observations": required_revalidation_observations,
                        "max_gap_seconds": revalidation_gap_seconds,
                        "suspend_until_utc": stored_suspend_until.isoformat(),
                    }
                    if not revalidated:
                        return _persist_result(
                            store=store,
                            original=original,
                            state=observed_state,
                            result=result,
                            details=details,
                            settings=settings,
                            decision_code="IRRIGATION_SUSPENSION_REVALIDATING",
                            message=(
                                "Der weiterhin gesperrte Hydrawise-Plan wird vor "
                                "einem verspäteten Wasserstart erneut bestätigt."
                            ),
                        )
                    renewed = replace(
                        observed_state,
                        revision=observed_state.revision + 1,
                        # Use the confirmed source time, not later processing
                        # time. Fetch latency must not invalidate this renewal.
                        irrigation_suspension_completed_utc=(
                            observed_state.irrigation_suspension_revalidation_observed_utc
                        ),
                    )
                    details["irrigation_suspension_revalidation"]["renewed"] = True
                    return _persist_result(
                        store=store,
                        original=original,
                        state=renewed,
                        result=result,
                        details=details,
                        settings=settings,
                        decision_code="IRRIGATION_SUSPENSION_REVALIDATED",
                        message=(
                            "Alle sieben Hydrawise-Zonen sind weiterhin sicher "
                            "suspendiert; die kurzlebige Startfreigabe wurde erneuert."
                        ),
                    )
                if not suspension_proof_valid:
                    failed = _failed_irrigation(
                        state,
                        EXPIRED_IRRIGATION_PLAN_LEASE_FAILURE,
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
                plan=execution_zones,
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
                partial_end_proof = _partial_irrigation_end_proof(
                    state,
                    details,
                    expected_relay_ids=expected_relay_ids,
                    relay_allowlist_valid=relay_allowlist_valid,
                )
                if partial_end_proof["eligible"] is True:
                    complete = _complete_partial_irrigation(state, now_utc=now)
                    details["irrigation_partial_end"] = {
                        **partial_end_proof,
                        "projected_end_utc": projected_end.isoformat(),
                        "suspension_until_utc": (
                            suspension_until.isoformat()
                            if suspension_until is not None
                            else None
                        ),
                        "command_sent": False,
                    }
                    return _persist_result(
                        store=store,
                        original=original,
                        state=complete,
                        result=result,
                        details=details,
                        settings=settings,
                        decision_code="IRRIGATION_PARTIAL_RUN_COMPLETE_HOLD",
                        message=(
                            "Die restlichen Zonen passen nicht mehr sicher in das "
                            "Beregnungsfenster. Die Beregnung ist beendet und die "
                            "Trocknungssperre läuft."
                        ),
                    )
                failed = _failed_irrigation(
                    state,
                    PARTIAL_IRRIGATION_WINDOW_FAILURE,
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
            active_override = _schedule_override(state)
            if active_override is not None and active_override.get("coordination_execution") is True:
                remaining_plan = [
                    zone for zone in execution_zones
                    if int(zone["relay_id"]) not in set(completed)
                ]
                if not coordination_gate:
                    coordination_blocker = "COORDINATION_EXECUTION_DISABLED"
                else:
                    coordination_blocker = coordination_start_authorized(
                        override=active_override, cycle=result.to_dict(),
                        execution_input=execution_input, now_utc=now,
                        remaining_plan=remaining_plan, projected_end_utc=projected_end,
                    )
                details["coordination_execution"]["start_revalidation"] = coordination_blocker is None
                if coordination_blocker is not None:
                    return _persist_result(
                        store=store, original=original, state=state, result=result,
                        details=details, settings=settings,
                        decision_code="COORDINATION_EXECUTION_START_BLOCKED",
                        message="Der koordinierte Zonenstart bleibt wegen geänderter Freigabe oder Belegung gesperrt.",
                    )
            operating_window = _irrigation_operating_window(
                plan=execution_zones,
                completed_relay_ids=set(completed),
                now_utc=now,
                end_confirmation_minutes=end_confirmation_minutes,
                automatic=not operator_manual_plan,
            )
            details["irrigation_operating_window"] = {
                "code": operating_window.code,
                "intent": (
                    "MANUAL_OPERATOR" if operator_manual_plan else "AUTOMATIC"
                ),
                "automatic_window_applies": not operator_manual_plan,
                "earliest_start_utc": (
                    operating_window.earliest_start_utc.isoformat()
                    if operating_window.earliest_start_utc else None
                ),
                "latest_end_utc": (
                    operating_window.latest_end_utc.isoformat()
                    if operating_window.latest_end_utc else None
                ),
                "projected_end_utc": (
                    operating_window.projected_end_utc.isoformat()
                    if operating_window.projected_end_utc else None
                ),
                "latest_start_utc": (
                    operating_window.latest_start_utc.isoformat()
                    if operating_window.latest_start_utc else None
                ),
                "reason": operating_window.reason,
            }
            if operating_window.code == "TOO_EARLY":
                return _persist_result(
                    store=store, original=original, state=state, result=result,
                    details=details, settings=settings,
                    decision_code="IRRIGATION_OPERATING_WINDOW",
                    message="Die Beregnung wartet bis 03:30 Europe/Berlin; der gesicherte native Plan bleibt unterdrückt.",
                )
            if operating_window.code != "OK":
                return _persist_result(
                    store=store, original=original, state=state, result=result,
                    details=details, settings=settings,
                    decision_code=(
                        "IRRIGATION_WINDOW_CANNOT_FIT"
                        if operating_window.code == "TOO_LATE"
                        else "IRRIGATION_OPERATING_WINDOW"
                    ),
                    message="Bewässerung ist nur ab 03:30 möglich und muss bis 08:00 beendet sein. Bitte früheren Start wählen.",
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
            # Read persisted ownership before sampling the dispatch clock: a
            # slow remote state read must not make an old clock look current.
            try:
                latest = store.load()
                dispatch_now = command_clock()
                if dispatch_now.tzinfo is None or dispatch_now.utcoffset() is None:
                    raise ValueError("command clock is not timezone-aware")
                dispatch_now = dispatch_now.astimezone(timezone.utc)
            except Exception as exc:
                details["irrigation_action"] = {
                    "type": "StartZone", "outcome": "PRE_SEND_BLOCKED",
                    "reason_code": "IRRIGATION_OPERATING_WINDOW",
                    "error_type": type(exc).__name__,
                }
                return replace(
                    result, decision_code="IRRIGATION_OPERATING_WINDOW", command_sent=False,
                    message="Die Startvorbereitung ist nicht aktuell nachweisbar; es wurde kein Zonenbefehl gesendet.",
                    details=_decorate(details, state=reserved, settings=settings, persisted=True, command_sent=False),
                )
            if (
                latest.revision != reserved.revision
                or latest.irrigation_phase != "START_RESERVED"
                or latest.irrigation_current_relay_id != int(next_zone["relay_id"])
                or latest.irrigation_zone_start_reserved_utc
                != reserved.irrigation_zone_start_reserved_utc
            ):
                details["irrigation_action"] = {
                    "type": "StartZone", "outcome": "PRE_SEND_BLOCKED",
                    "reason_code": "IRRIGATION_START_RESERVATION_CHANGED",
                }
                return replace(
                    result, decision_code="IRRIGATION_OPERATING_WINDOW", command_sent=False,
                    message="Die Zonenstart-Reservierung wurde vor dem Versand verändert; es wurde kein Zonenbefehl gesendet.",
                    details=_decorate(details, state=latest, settings=settings, persisted=True, command_sent=False),
                )
            # The state reread is deliberately the final remote operation.
            # Reassess every proof whose age can expire while it was in
            # flight; a cached cycle result is never a licence to POST later.
            dispatch_window = _irrigation_operating_window(
                plan=execution_zones,
                completed_relay_ids=set(completed),
                now_utc=dispatch_now,
                end_confirmation_minutes=end_confirmation_minutes,
                automatic=not operator_manual_plan,
            )
            dispatch_code: str | None = None
            dispatch_reason: str | None = None
            if dispatch_window.code == "TOO_LATE":
                dispatch_code, dispatch_reason = "IRRIGATION_WINDOW_CANNOT_FIT", "IRRIGATION_WINDOW_CANNOT_FIT"
            elif latest.maintenance_mode:
                dispatch_code, dispatch_reason = "IRRIGATION_OPERATING_WINDOW", "MAINTENANCE_MODE"
            elif _operator_action(latest, dispatch_now) is not None:
                dispatch_code, dispatch_reason = "IRRIGATION_OPERATING_WINDOW", "OPERATOR_ACTION_PENDING"
            elif latest.mower_start_pending_since_utc is not None:
                dispatch_code, dispatch_reason = "IRRIGATION_OPERATING_WINDOW", "MOWER_START_PENDING"
            elif not _water_park_authorized(
                latest, mower, now_utc=dispatch_now, environment=environment,
                max_age_seconds=mower_status_max_age_seconds,
                expected_json=reserved.irrigation_park_hold_json,
                expected_onsite_json=reserved.irrigation_onsite_dock_proof_json,
            ):
                dispatch_code, dispatch_reason = "IRRIGATION_OPERATING_WINDOW", "MOWER_STATUS_STALE"
            else:
                dispatch_observed = _hydrawise_source_observation(
                    details, now_utc=dispatch_now,
                )
                dispatch_hydra_fresh = (
                    dispatch_observed is not None
                    and -60 <= (dispatch_now - dispatch_observed).total_seconds()
                    <= hydrawise_status_max_age_seconds
                )
                if (
                    not dispatch_hydra_fresh
                    or hydra_safety.get("clear_now") is not True
                    or bool(_active_relay_ids(details))
                ):
                    dispatch_code, dispatch_reason = "IRRIGATION_OPERATING_WINDOW", "HYDRAWISE_STATUS_STALE_OR_ACTIVE"
                elif (
                    (_source_parts(effective_blocked_now.get("source"))
                     | _source_parts(effective_parking_block.get("source")))
                    - frozenset({"irrigation"})
                ):
                    # These are the cycle's already-read occupancy proofs.
                    # Do not initiate another source burst here; without a
                    # fresh positive read they remain a conservative block.
                    dispatch_code, dispatch_reason = "IRRIGATION_OPERATING_WINDOW", "OCCUPANCY_OR_PARKING_BLOCK"
                elif dispatch_window.code != "OK":
                    dispatch_code = (
                        "IRRIGATION_WINDOW_CANNOT_FIT"
                        if dispatch_window.code == "TOO_LATE"
                        else "IRRIGATION_OPERATING_WINDOW"
                    )
                    dispatch_reason = dispatch_code
                else:
                    latest_suspension_until = _parse_time(
                        latest.irrigation_suspension_until_utc
                    )
                    if (
                        latest_suspension_until is None
                        or dispatch_window.projected_end_utc is None
                        or dispatch_window.projected_end_utc > latest_suspension_until
                    ):
                        dispatch_code = "IRRIGATION_WINDOW_CANNOT_FIT"
                        dispatch_reason = "IRRIGATION_SUSPENSION_WINDOW_EXPIRED"
            if dispatch_code is not None:
                reopened = replace(
                    reserved,
                    revision=reserved.revision + 1,
                    irrigation_phase="READY",
                    irrigation_current_relay_id=None,
                    irrigation_zone_start_reserved_utc=None,
                    last_decision_code=dispatch_code,
                )
                try:
                    store.save(reopened, expected_revision=reserved.revision)
                except Exception:
                    reopened = reserved
                details["irrigation_action"] = {
                    "type": "StartZone", "outcome": "PRE_SEND_BLOCKED",
                    "reason_code": dispatch_reason,
                }
                return replace(
                    result,
                    decision_code=dispatch_code,
                    command_sent=False,
                    message="Bewässerung ist nur ab 03:30 möglich und muss bis 08:00 beendet sein. Bitte früheren Start wählen.",
                    details=_decorate(details, state=reopened, settings=settings, persisted=True, command_sent=False),
                )
            if settings.enable_manual_sessions:
                relay = int(next_zone["relay_id"])
                start_key = (
                    f"water-start:{state.irrigation_plan_id}:{relay}:"
                    f"{reserved.irrigation_zone_start_reserved_utc}"
                )

                def check_water_start(latest: AutomationState, at: datetime) -> None:
                    latest_session = load_manual_session(latest)
                    observed_at = _parse_time(hydra_safety.get("observed_at_utc"))
                    hydra_still_fresh_clear = (
                        observed_at is not None
                        and -60 <= (at - observed_at).total_seconds()
                        <= hydrawise_status_max_age_seconds
                        and hydra_safety.get("available") is True
                        and hydra_safety.get("fresh") is True
                        and hydra_safety.get("relay_set_valid") is True
                        and hydra_safety.get("clear_now") is True
                        and int(hydra_safety.get("active_zone_count") or 0) == 0
                        and int(hydra_safety.get("imminent_zone_count") or 0) == 0
                    )
                    if (
                        latest.maintenance_mode
                        or latest.irrigation_phase != "START_RESERVED"
                        or latest.irrigation_current_relay_id != relay
                        or not _water_dispatch_station_authorized(
                            latest, mower, now_utc=at, environment=environment,
                            max_age_seconds=mower_status_max_age_seconds,
                            expected_json=reserved.irrigation_park_hold_json,
                            expected_onsite_json=reserved.irrigation_onsite_dock_proof_json,
                            allowed_intent_key=start_key,
                        )
                        or not hydra_still_fresh_clear
                    ):
                        raise DeviceSendBlocked("WATER_START_REVOKED")
                    if (
                        latest_session is not None
                        and latest_session.get("kind") == "START"
                        and latest_session.get("status") != "ENDED"
                        and (
                            latest_session.get("water_choice") != "IRRIGATION"
                            or latest_session.get("water_conflict_id")
                            != manual_water_context.get("id")
                        )
                    ):
                        raise DeviceSendBlocked("MANUAL_WATER_CHOICE_REVOKED")

                try:
                    sent = dispatch_device_send(
                        store=store, original=reserved, state=reserved,
                        sender=start_zone_sender,
                        args=(api_key, relay, int(next_zone["run_seconds"]), controller_id),
                        kind="WATER_START", target=str(relay), intent_key=start_key,
                        now_utc=now, clock=command_clock,
                        before_send_check=check_water_start,
                        deadline_utc=min(
                            dispatch_window.latest_start_utc or dispatch_now + timedelta(seconds=30),
                            dispatch_now + timedelta(seconds=30),
                        ),
                    )
                except DeviceSendBlocked as exc:
                    blocked_state = exc.state if isinstance(exc.state, AutomationState) else reserved
                    return replace(
                        result, decision_code=exc.code,
                        message="Der Zonenstart wurde an der persistenten Sendegrenze angehalten.",
                        command_sent=exc.transport_started,
                        details=_decorate(details, state=blocked_state, settings=settings,
                                          persisted=True, command_sent=exc.transport_started),
                    )
                response = sent.response
                reserved = sent.state
            else:
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

        if (
            operator_action == "STOP_IRRIGATION_NOW"
            and state.irrigation_phase in {"START_RESERVED", "RUNNING"}
        ):
            if current_id is None:
                failed = _finish_operator_request(
                    _failed_irrigation(
                        state,
                        "Die laufende Hydrawise-Zone konnte nicht eindeutig bestimmt werden.",
                    ),
                    "Direktes Beenden abgelehnt: Die laufende Zone ist nicht eindeutig.",
                    status="REJECTED",
                )
                return _persist_result(
                    store=store,
                    original=original,
                    state=failed,
                    result=result,
                    details=details,
                    settings=settings,
                    decision_code="IRRIGATION_OPERATOR_STOP_TARGET_UNCLEAR",
                    message="Die Beregnung bleibt sicher gesperrt; die laufende Zone ist nicht eindeutig.",
                )
            if active_ids and active_ids != {int(current_id)}:
                failed = _finish_operator_request(
                    _failed_irrigation(
                        state,
                        "Der Live-Status passt nicht zur gespeicherten laufenden Zone.",
                    ),
                    "Direktes Beenden abgelehnt: Der Live-Status ist nicht eindeutig.",
                    status="REJECTED",
                )
                return _persist_result(
                    store=store,
                    original=original,
                    state=failed,
                    result=result,
                    details=details,
                    settings=settings,
                    decision_code="IRRIGATION_OPERATOR_STOP_TARGET_MISMATCH",
                    message="Die Beregnung bleibt sicher gesperrt; der Zonenstatus ist widersprüchlich.",
                )
            stopping = replace(
                state,
                revision=state.revision + 1,
                irrigation_phase="STOPPING",
                irrigation_zone_clear_since_utc=None,
                irrigation_zone_clear_observed_utc=None,
                irrigation_zone_stop_requested_utc=now.isoformat(),
            )
            try:
                store.save(stopping, expected_revision=original.revision)
            except Exception as exc:
                return replace(
                    result,
                    decision_code="IRRIGATION_STOP_RESERVATION_FAILED",
                    message="Der direkte Zonenstopp wurde ohne persistente Reservierung nicht gesendet.",
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
            try:
                if settings.enable_manual_sessions:
                    relay = int(current_id)

                    def check_water_stop(latest: AutomationState, at: datetime) -> None:
                        if (
                            latest.irrigation_phase != "STOPPING"
                            or latest.irrigation_current_relay_id != relay
                        ):
                            raise DeviceSendBlocked("WATER_STOP_REVOKED")

                    sent = dispatch_device_send(
                        store=store, original=stopping, state=stopping,
                        sender=stop_zone_sender,
                        args=(api_key, relay, controller_id), kind="WATER_STOP",
                        target=str(relay),
                        intent_key=f"water-stop:{state.irrigation_plan_id}:{relay}:{now.isoformat()}",
                        now_utc=now, clock=command_clock,
                        before_send_check=check_water_stop,
                        deadline_utc=now + timedelta(seconds=30),
                    )
                    response = sent.response
                    stopping = sent.state
                else:
                    response = stop_zone_sender(api_key, int(current_id), controller_id)
            except DeviceSendBlocked as exc:
                blocked_state = exc.state if isinstance(exc.state, AutomationState) else stopping
                return replace(
                    result, decision_code=exc.code,
                    message="Der Zonenstopp wurde an der persistenten Sendegrenze angehalten.",
                    command_sent=exc.transport_started,
                    details=_decorate(details, state=blocked_state, settings=settings,
                                      persisted=True, command_sent=exc.transport_started),
                )
            except Exception as exc:
                failed = _finish_operator_request(
                    _failed_irrigation(
                        stopping,
                        f"Hydrawise-Zonenstopp fehlgeschlagen: {type(exc).__name__}: {exc}",
                    ),
                    "Der direkte Hydrawise-Stopp ist fehlgeschlagen; die Anlage bleibt sicher gesperrt.",
                    status="REJECTED",
                )
                try:
                    store.save(failed, expected_revision=stopping.revision)
                    persisted = True
                    error = None
                except Exception as save_exc:
                    persisted = False
                    error = f"{type(save_exc).__name__}: {save_exc}"
                return replace(
                    result,
                    decision_code="IRRIGATION_ZONE_STOP_FAILED",
                    message="Hydrawise hat den direkten Zonenstopp nicht bestätigt; der Mäher bleibt gesperrt.",
                    command_sent=False,
                    details=_decorate(
                        details,
                        state=failed,
                        settings=settings,
                        persisted=persisted,
                        command_sent=False,
                        error=error or f"{type(exc).__name__}: {exc}",
                    ),
                )
            details["irrigation_action"] = {
                "type": "StopZone",
                "relay_id": int(current_id),
                "response": response,
            }
            return replace(
                result,
                decision_code="IRRIGATION_ZONE_STOP_SENT",
                message="Der direkte Hydrawise-Zonenstopp wurde gesendet; die Live-Bestätigung läuft.",
                command_sent=True,
                details=_decorate(
                    details,
                    state=stopping,
                    settings=settings,
                    persisted=True,
                    command_sent=True,
                ),
            )

        if state.irrigation_phase == "STOPPING":
            if current_id is not None and active_ids == {int(current_id)}:
                return _persist_result(
                    store=store,
                    original=original,
                    state=state,
                    result=result,
                    details=details,
                    settings=settings,
                    decision_code="IRRIGATION_WAIT_FOR_DIRECT_STOP",
                    message="Hydrawise meldet die Zone noch aktiv; der Mäher bleibt geparkt.",
                )
            if active_ids or not hydra_safety.get("clear_now"):
                failed = _failed_irrigation(
                    state,
                    "Das Ende der direkt gestoppten Zone ist nicht eindeutig.",
                )
                return _persist_result(
                    store=store,
                    original=original,
                    state=failed,
                    result=result,
                    details=details,
                    settings=settings,
                    decision_code="IRRIGATION_DIRECT_STOP_END_UNCLEAR",
                    message="Der direkte Zonenstopp ist nicht sicher bestätigt; der Mäher bleibt gesperrt.",
                )
            confirming, advanced_observation = _record_zone_clear_observation(
                state,
                observed_utc=_hydrawise_source_observation(details, now_utc=now),
                not_before_utc=(
                    _parse_time(state.irrigation_zone_stop_requested_utc)
                    or _parse_time(state.irrigation_zone_started_utc)
                ),
            )
            clear_since = _parse_time(confirming.irrigation_zone_clear_since_utc)
            if clear_since is None or not advanced_observation:
                return _persist_result(
                    store=store,
                    original=original, state=confirming,
                    result=result,
                    details=details,
                    settings=settings,
                    decision_code="IRRIGATION_CONFIRM_DIRECT_STOP",
                    message="Hydrawise meldet die Zone erstmals beendet; die Bestätigung läuft.",
                )
            end_confirmation = _env_int(
                environment,
                "IRRIGATION_ZONE_END_CONFIRMATION_MINUTES",
                2,
                minimum=1,
                maximum=10,
            )
            if _hydrawise_source_observation(details, now_utc=now) - clear_since < timedelta(minutes=end_confirmation):
                return _persist_result(
                    store=store,
                    original=original,
                    state=confirming,
                    result=result,
                    details=details,
                    settings=settings,
                    decision_code="IRRIGATION_CONFIRM_DIRECT_STOP",
                    message="Das direkte Zonenende wird fortlaufend bestätigt.",
                )
            confirmed_stop_state = confirming
            if settings.enable_manual_sessions:
                for entry in unresolved_device_sends(confirmed_stop_state):
                    if (
                        entry.get("kind") == "WATER_STOP"
                        and str(entry.get("target")) == str(current_id)
                    ):
                        confirmed_stop_state = reconcile_device_send(
                            confirmed_stop_state, str(entry["intent_key"]), now,
                            f"relay-{current_id}-continuously-inactive",
                        )
            stopped = replace(
                _finish_operator_request(
                    confirmed_stop_state,
                    "Die Zone ist sicher beendet; keine weitere Zone startet.",
                ),
                revision=confirmed_stop_state.revision + 2,
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
                decision_code="IRRIGATION_OPERATOR_STOPPED_NOW",
                message=(
                    "Hydrawise hat das Ende bestätigt; die konfigurierte "
                    "Trocknungssperre beginnt."
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
                    message="Hydrawise meldet die angeforderte Zone als laufend.",
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
            confirming, advanced_observation = _record_zone_clear_observation(
                state,
                observed_utc=_hydrawise_source_observation(details, now_utc=now),
                not_before_utc=_parse_time(state.irrigation_zone_started_utc),
            )
            clear_since = _parse_time(confirming.irrigation_zone_clear_since_utc)
            if clear_since is None or not advanced_observation:
                return _persist_result(
                    store=store,
                    original=original, state=confirming,
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
            if _hydrawise_source_observation(details, now_utc=now) - clear_since < timedelta(minutes=end_confirmation):
                return _persist_result(
                    store=store,
                    original=original,
                    state=confirming,
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
                        "Die Zone ist bestätigt beendet; die konfigurierte "
                        "Trocknungssperre beginnt."
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
                        execution_zone_count - len(set(completed))
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
            all_complete = len(set(completed)) == execution_zone_count
            if coordination_plan and not all_complete:
                try:
                    shifted = _delay_coordinated_remaining_zones(
                        zones, completed=set(completed), current_id=int(current_id),
                        proved_clear_since=clear_since,
                    )
                except (RuntimeError, TypeError, ValueError, StopIteration) as exc:
                    failed = _failed_irrigation(state, str(exc))
                    return _persist_result(
                        store=store, original=original, state=failed, result=result,
                        details=details, settings=settings,
                        decision_code="COORDINATION_EXECUTION_GAP_INVALID",
                        message="Die Pause bis zur nächsten Zone ist nicht sicher nachweisbar.",
                    )
                canonical = json.dumps(shifted, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
                shifted_override = {
                    **active_override_for_plan, "zones": shifted,
                    "last_proved_zone_end_utc": clear_since.isoformat(),
                }
                state = replace(
                    state, irrigation_plan_json=canonical,
                    irrigation_plan_id=hashlib.sha256(canonical.encode("utf-8")).hexdigest(),
                    irrigation_schedule_override_json=dump_irrigation_schedule_object(shifted_override),
                )
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
                    (
                        "Die gewählte Zone ist bestätigt beendet; der konfigurierte Sicherheitsnachlauf beginnt."
                        if single_zone_plan
                        else "Alle sieben Zonen sind bestätigt beendet; der konfigurierte Sicherheitsnachlauf beginnt."
                    )
                    if all_complete
                    else "Zone ist bestätigt beendet; die nächste Planzone wird vorbereitet."
                ),
            )

    if effective_blocked_now or effective_parking_block:
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
    full_release_origins = {
        "IRRIGATION_ACTIVE",
        "IRRIGATION_END",
        "POSSIBLE_IRRIGATION_DURING_GAP",
    }
    release_origin = state.hydrawise_clear_origin or (
        "IRRIGATION_END"
        if state.irrigation_phase == "COMPLETE_HOLD"
        else "DATA_GAP"
    )
    release_minutes = _env_int(
        environment,
        (
            "POST_IRRIGATION_DRYING_MINUTES"
            if release_origin in full_release_origins
            else "HYDRAWISE_DATA_GAP_CONFIRMATION_MINUTES"
        ),
        150 if release_origin in full_release_origins else 2,
        minimum=150 if release_origin in full_release_origins else 1,
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
        drying_since_utc=state.hydrawise_drying_since_utc,
        telemetry_confirmation_minutes=_env_int(
            environment,
            "HYDRAWISE_DATA_GAP_CONFIRMATION_MINUTES",
            2,
            minimum=1,
            maximum=1440,
        ),
    )
    cancelled_without_run_release = (
        state.irrigation_cancelled_without_run_utc is not None
        and state.hydrawise_drying_since_utc is None
        and bool(hydra_safety.get("available"))
        and bool(hydra_safety.get("fresh"))
        and bool(hydra_safety.get("clear_now"))
        and relay_allowlist_valid
        and int(hydra_safety.get("active_zone_count") or 0) == 0
        and int(hydra_safety.get("imminent_zone_count") or 0) == 0
    )
    details["hydrawise_release_gate"] = {
        **release.to_dict(),
        "origin": release_origin,
        "full_post_irrigation_hold": release_origin in full_release_origins,
        "post_irrigation_drying_minutes": (
            release_minutes if release_origin in full_release_origins else None
        ),
        "cancelled_without_run_release": cancelled_without_run_release,
        "manual_dry_override": manual_permission.get("dry_override") is True,
        "effective_allowed": (
            release.allowed or cancelled_without_run_release
            or manual_permission.get("dry_override") is True
        ),
    }
    if (
        not release.allowed
        and not cancelled_without_run_release
        and manual_permission.get("dry_override") is not True
    ):
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
                parked = state.record_command(
                    fingerprint=intent.fingerprint,
                    sent_utc=now,
                    action="PARK",
                    park_until_utc=now + timedelta(days=1),
                    park_source="hydrawise_unconfirmed",
                    restart_allowed=True,
                )
                if settings.enable_manual_sessions:
                    try:
                        sent = dispatch_device_send(
                            store=store, original=original, state=parked,
                            sender=park_sender, args=(client_id, client_secret, mower_id),
                            kind="PARK", target=mower_id,
                            intent_key=f"park:{intent.fingerprint}", now_utc=now,
                            clock=command_clock,
                            before_send_check=lambda latest, at: (
                                None if not latest.maintenance_mode
                                else (_ for _ in ()).throw(DeviceSendBlocked("PARK_SEND_REVOKED"))
                            ),
                            deadline_utc=now + timedelta(seconds=30),
                        )
                    except DeviceSendBlocked as exc:
                        blocked_state = exc.state if isinstance(exc.state, AutomationState) else state
                        return replace(
                            result, decision_code=exc.code,
                            message="Der Schutz-Parkbefehl wurde an der Sendegrenze angehalten.",
                            command_sent=exc.transport_started,
                            details=_decorate(details, state=blocked_state, settings=settings,
                                              persisted=True, command_sent=exc.transport_started),
                        )
                    response = sent.response
                    parked = replace(sent.state, revision=sent.state.revision + 1)
                    original = sent.state
                else:
                    response = park_sender(client_id, client_secret, mower_id)
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

    manual_start_requested = (
        operator_action == "START_MOWING"
        or (
            settings.enable_manual_sessions
            and manual_session is not None
            and manual_session.get("kind") == "START"
            and manual_session.get("status") in {"PENDING", "ACTIVE"}
        )
    )
    if settings.enable_manual_sessions and manual_session is not None:
        if (
            manual_session.get("kind") == "PARK"
            and manual_session.get("status") != "ENDED"
            and operator_action != "PARK_MOWER"
        ):
            return _persist_result(
                store=store, original=original, state=state, result=result,
                details=details, settings=settings,
                decision_code="MANUAL_PARK_SESSION_HOLD",
                message="Die bestätigte Park-Sitzung bleibt bis zur ausdrücklichen Freigabe aktiv.",
            )
        if manual_start_requested and manual_permission.get("allowed") is not True:
            permission_code = str(
                manual_permission.get("code") or "MANUAL_SESSION_BLOCKED"
            )
            return _persist_result(
                store=store, original=original, state=state, result=result,
                details=details, settings=settings,
                decision_code=permission_code,
                message="Die manuelle Start-Sitzung wartet auf alle ausdrücklich bestätigten Sicherheitsnachweise.",
            )

    # This admission cannot grant occupancy/drying/water exceptions. All
    # protection paths above remain reachable before an ownership change.
    takeover_checked_at = now
    takeover_allowed = False
    if _env_enabled(environment, "MOWER_AUTOMATIC_TAKEOVER_ENABLED"):
        try:
            takeover_checked_at = command_clock()
            if (isinstance(takeover_checked_at, datetime) and takeover_checked_at.tzinfo is not None
                and takeover_checked_at.utcoffset() is not None
                and timedelta(0) <= takeover_checked_at - now <= timedelta(seconds=90)):
                takeover_allowed = automatic_takeover.eligible(state, details, environment, takeover_checked_at)
        except (ValueError, TypeError, OverflowError, OSError, RuntimeError, DeviceSendBlocked):
            takeover_allowed = False
    details["automatic_takeover"] = {"eligible": takeover_allowed, "adopted": False}
    manual_lock = activity in MANUAL_ACTIVITIES or mower_state in MANUAL_STATES
    if state.continuous_mowing_takeover_hold_utc and not takeover_allowed and operator_action != "START_MOWING":
        return _persist_result(store=store, original=original, state=state, result=result, details=details,
                               settings=settings, decision_code="OBSERVED_MANUAL_STOP_HOLD",
                               message="Der manuelle Stopp bleibt bis zu einer ausdrücklichen Freigabe bestehen.")
    confirmed_operator_paused_start = (
        manual_start_requested
        and operator_action == "START_MOWING"
        and mower_state == "PAUSED"
        and activity == "NOT_APPLICABLE"
        and (state.parked_by_automation or state.continuous_mowing_owned)
    )
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
    if (
        (manual_lock and not confirmed_operator_paused_start)
        or error_code != 0
        or mower_state in ERROR_STATES
        or (external_override and not manual_start_requested and not takeover_allowed)
    ):
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
        and not manual_start_requested
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
    manual_start_active = bool(
        settings.enable_manual_sessions
        and manual_session is not None
        and manual_session.get("kind") == "START"
        and manual_session.get("status") in {"PENDING", "ACTIVE"}
    )
    turnaround_before_dock = (
        activity == "GOING_HOME"
        and state.continuous_mowing_owned
        and not state.continuous_mowing_observed_takeover
        and not manual_start_active
        and not (
            settings.enable_manual_sessions and manual_session is not None
            and manual_session.get("kind") == "START"
            and manual_session.get("status") == "ENDED"
            # Preserve this return after the manual permission ends. A later
            # ordinary START has passed all automatic checks and owns its own
            # return policy; the retained audit session must not disable that.
            and (
                state.last_start_command_utc is None
                or _parse_time(state.last_start_command_utc)
                <= _parse_time(manual_session["ended_at_utc"])
            )
        )
    )

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
        and not confirmed_operator_paused_start
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
    if occupancy_override_active and current_occupancy_end is not None:
        # Der ausdrücklich bestätigte Start gilt nur bis zum Ende genau dieses
        # Belegungsblocks. Danach übernimmt wieder der normale Planer.
        window = {
            "start": now.isoformat(),
            "end": (
                current_occupancy_end
                + timedelta(minutes=settings.park_lookahead_minutes)
            ).isoformat(),
        }
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
        and not state.continuous_mowing_observed_takeover
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
        "device_deadline_installed_by_automation": not state.continuous_mowing_observed_takeover,
    }
    if takeover_allowed and mowing_now and not failsafe_refresh:
        # Persist ownership only after the normal window and water checks, and
        # never claim that observing a run installed a native timed command.
        if remaining < minimum_window:
            takeover_allowed = False
        else:
            if manual_session is not None and manual_session.get("status") == "ACTIVE":
                manual_session = with_session_status(manual_session, "ENDED", now_utc=takeover_checked_at)
                state = replace(state, manual_session_json=dump_manual_session(manual_session))
                details["manual_session"] = {
                    **details.get("manual_session", {}),
                    "status": "ENDED",
                    "permission_code": "MANUAL_SESSION_INACTIVE",
                }
            if operator_action == "START_MOWING":
                state = _finish_operator_request(state, "Der laufende Einsatz wird automatisch fortgeführt.")
            observed_only = state.continuous_mowing_observed_takeover or not state.continuous_mowing_owned
            state = replace(state, revision=state.revision + 1, continuous_mowing_owned=True,
                            continuous_mowing_observed_takeover=observed_only,
                            continuous_mowing_takeover_deadline_utc=(safe_command_deadline.isoformat() if observed_only else None),
                            continuous_mowing_takeover_hold_utc=None,
                            continuous_mowing_work_area_id=target_area["id"],
                            continuous_mowing_window_end_utc=(None if observed_only else state.continuous_mowing_window_end_utc),
                            parked_by_automation=False, automation_park_source=None,
                            automation_park_until_utc=None)
            details["automatic_takeover"] = {"eligible": True, "adopted": True,
                                              "device_command_sent": False, "planned_return_utc": safe_command_deadline.isoformat()}
            details["mower_outage_guard"]["device_deadline_installed_by_automation"] = not observed_only
            return _persist_result(store=store, original=original, state=state, result=result,
                                   details=details, settings=settings, decision_code="AUTOMATIC_MOWING_TAKEOVER",
                                   message="Der laufende Mäheinsatz wird automatisch fortgeführt.")
    if mowing_now and not failsafe_refresh:
        if manual_start_requested:
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
                    message="Die sichere Automatik ist noch gesperrt.",
                )
            state = _finish_operator_request(
                state,
                "Der bereits fahrende Mäher wurde von der sicheren Automatik übernommen.",
            )
            state = replace(
                state,
                revision=state.revision + 1,
                continuous_mowing_owned=True,
                continuous_mowing_work_area_id=work_area_id,
                continuous_mowing_window_end_utc=safe_command_deadline.isoformat(),
                parked_by_automation=False,
                automation_park_source=None,
                automation_restart_allowed=False,
                automation_park_until_utc=None,
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
    required_window = 1 if occupancy_override_active else minimum_window
    if (
        (not failsafe_refresh and (remaining < required_window or duration < required_window))
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
        owned = state.record_command(
            fingerprint=intent.fingerprint,
            sent_utc=now,
            action="PARK",
            park_until_utc=safe_command_deadline,
            park_source="continuous",
            restart_allowed=True,
        )
        if settings.enable_manual_sessions:
            try:
                sent = dispatch_device_send(
                    store=store, original=original, state=owned,
                    sender=park_sender, args=(client_id, client_secret, mower_id),
                    kind="PARK", target=mower_id,
                    intent_key=f"park:{intent.fingerprint}", now_utc=now,
                    clock=command_clock,
                    before_send_check=lambda latest, at: (
                        None if not latest.maintenance_mode
                        else (_ for _ in ()).throw(DeviceSendBlocked("PARK_SEND_REVOKED"))
                    ),
                    deadline_utc=min(safe_command_deadline, now + timedelta(seconds=30)),
                )
            except DeviceSendBlocked as exc:
                blocked_state = exc.state if isinstance(exc.state, AutomationState) else state
                return replace(
                    result, decision_code=exc.code,
                    message="Der Besitz-Parkbefehl wurde an der Sendegrenze angehalten.",
                    command_sent=exc.transport_started,
                    details=_decorate(details, state=blocked_state, settings=settings,
                                      persisted=True, command_sent=exc.transport_started),
                )
            response = sent.response
            owned = replace(sent.state, revision=sent.state.revision + 1)
            original = sent.state
        else:
            response = park_sender(client_id, client_secret, mower_id)
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

    confirmed_manual_station = bool(
        manual_start_active and irrigation_park_hold.enabled(environment)
        and irrigation_park_hold.start_valid(state, mower, now_utc=now)
    )
    if mower.get("connected") is not True or not (mower_status_fresh or confirmed_manual_station):
        details["start_action"] = {
            "type": "StartInWorkArea",
            "outcome": "PRE_SEND_BLOCKED",
            "reason_code": "MOWER_STATUS_STALE",
            "requested_deadline_utc": safe_command_deadline.isoformat(),
            "failsafe_refresh": failsafe_refresh,
        }
        return _persist_result(
            store=store,
            original=original,
            state=state,
            result=result,
            details=details,
            settings=settings,
            decision_code="MOWER_START_SEND_BLOCKED",
            message="Der Mäherstart wurde wegen veralteter Gerätetelemetrie nicht reserviert.",
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
    if (
        settings.enable_manual_sessions
        and manual_session is not None
        and manual_session.get("kind") == "PARK"
        and manual_session.get("status") == "ENDED"
    ):
        command_state = replace(command_state, automation_restart_allowed=True)
    if manual_start_requested:
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
    # All START variants share one durable reservation. Keep the previously
    # acknowledged interval until the vendor response and following CAS both
    # succeed; a process death or lost response must not invent a shorter one.
    reserved = replace(
        state, revision=state.revision + 1,
        last_command_fingerprint=intent.fingerprint, last_command_utc=now.isoformat(),
        mower_start_pending_since_utc=now.isoformat(),
        mower_start_pending_deadline_utc=command_end.isoformat(),
        mower_start_pending_session_id=(
            str(manual_session["session_id"]) if manual_start_active else None
        ),
        mower_start_pending_session_epoch=(
            int(manual_session["epoch"]) if manual_start_active else None
        ),
    )
    try:
        store.save(reserved, expected_revision=original.revision)
    except Exception as exc:
        return replace(
            result, decision_code="MOWER_START_RESERVATION_FAILED", command_sent=False,
            message="Mäherstart wurde wegen fehlender persistenter Reservierung nicht gesendet.",
            details=_decorate(details, state=state, settings=settings, persisted=False,
                              command_sent=False, error=type(exc).__name__),
        )

    dispatch_guard_provenance = object()

    def before_start_dispatch() -> int:
        return prepare_start_dispatch(
            clock=command_clock,
            store=store,
            reserved=reserved,
            mower=mower,
            hydrawise_safety=hydra_safety,
            safe_command_deadline_utc=safe_command_deadline,
            command_end_utc=command_end,
            requested_duration_minutes=duration,
            mower_status_max_age_seconds=mower_status_max_age_seconds,
            hydrawise_status_max_age_seconds=hydrawise_status_max_age_seconds,
            manual_session_id=(
                str(manual_session["session_id"]) if manual_start_active else None
            ),
            manual_session_epoch=(
                int(manual_session["epoch"]) if manual_start_active else None
            ),
            _guard_provenance=dispatch_guard_provenance,
            allow_confirmed_station_start=irrigation_park_hold.enabled(environment),
        )

    try:
        # Always fence legacy five-argument fakes/adapters immediately before
        # their invocation.  The production sender repeats this exact fence
        # after OAuth and immediately before its HTTP POST.
        guarded_duration = before_start_dispatch()
        if start_sender is start_in_work_area:
            response = start_sender(
                client_id, client_secret, mower_id, work_area_id, guarded_duration,
                before_send=before_start_dispatch,
            )
        else:
            response = start_sender(
                client_id, client_secret, mower_id, work_area_id, guarded_duration
            )
    except Exception as exc:
        if (
            isinstance(exc, StartDispatchBlocked)
            and exc.belongs_to(dispatch_guard_provenance)
        ):
            blocked_state = reserved
            release_error = None
            try:
                blocked_state = release_start_reservation(
                    store=store,
                    reserved=reserved,
                    prior=state,
                )
            except StartReservationReleaseError as release_exc:
                release_error = str(release_exc)
                try:
                    blocked_state = store.load()
                except Exception:
                    pass
            details["start_action"] = {
                "type": "StartInWorkArea", "outcome": "PRE_SEND_BLOCKED",
                "reason_code": exc.code, "requested_deadline_utc": command_end.isoformat(),
                "failsafe_refresh": failsafe_refresh,
                "reservation_released": release_error is None,
            }
            if release_error is not None:
                details["start_action"]["reservation_release_error"] = release_error
            return replace(
                result, decision_code="MOWER_START_SEND_BLOCKED", command_sent=False,
                message=(
                    "Der Mäherstart wurde vor dem HTTP-Versand verworfen; "
                    + (
                        "die eigene Reservierung wurde sicher freigegeben."
                        if release_error is None
                        else "der Schutz-Latch bleibt wegen einer konkurrierenden oder fehlgeschlagenen CAS-Freigabe bestehen."
                    )
                ),
                details=_decorate(
                    details, state=blocked_state, settings=settings,
                    persisted=True, command_sent=False,
                ),
            )
        unknown_state = reserved
        if manual_start_active and manual_session is not None:
            unknown_session = with_session_status(
                manual_session, "UNKNOWN", now_utc=now,
            )
            candidate = replace(
                reserved, revision=reserved.revision + 1,
                manual_session_json=dump_manual_session(unknown_session),
            )
            try:
                store.save(candidate, expected_revision=reserved.revision)
            except Exception:
                pass
            else:
                unknown_state = candidate
        details["start_action"] = {
            "type": "StartInWorkArea", "outcome": "UNCONFIRMED", "error_type": type(exc).__name__,
            "requested_deadline_utc": command_end.isoformat(), "failsafe_refresh": failsafe_refresh,
        }
        return replace(
            result, decision_code="MOWER_START_OUTCOME_UNCONFIRMED", command_sent=True,
            message="Die Startantwort fehlt; weitere Starts bleiben bis zum Abgleich gesperrt.",
            details=_decorate(details, state=unknown_state, settings=settings, persisted=True, command_sent=True),
        )
    command_state = replace(
        command_state, revision=reserved.revision + 1,
        mower_start_pending_since_utc=None, mower_start_pending_deadline_utc=None,
        mower_start_pending_session_id=None, mower_start_pending_session_epoch=None,
    )
    try:
        store.save(command_state, expected_revision=reserved.revision)
    except Exception as exc:
        details["start_action"] = {
            "type": "StartInWorkArea", "response": response,
            "duration_minutes": duration, "work_area_id": work_area_id,
            "continuous_mowing": True, "requested_deadline_utc": command_end.isoformat(),
            "failsafe_refresh": failsafe_refresh, "state_confirmation_error": type(exc).__name__,
        }
        return replace(
            result, decision_code="MOWER_START_OUTCOME_UNCONFIRMED", command_sent=True,
            message="Start angenommen, aber nicht sicher gespeichert; weitere Starts bleiben bis zum Abgleich gesperrt.",
            details=_decorate(details, state=reserved, settings=settings, persisted=False,
                              command_sent=True, error=type(exc).__name__),
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
        "acknowledgement": "VENDOR_QUEUE_ACCEPTED",
        "physical_execution_confirmed": False,
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
            "Die zeitlich begrenzte Laufzeitänderung wurde angenommen; die Geräteausführung ist noch nicht bestätigt."
            if failsafe_refresh
            else (
                "Der begrenzte Startbefehl für die Rückfahrt wurde angenommen; die Geräteausführung ist noch nicht bestätigt."
                if turnaround_before_dock
                else "Der begrenzte Mähstart wurde angenommen; die Geräteausführung ist noch nicht bestätigt."
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
