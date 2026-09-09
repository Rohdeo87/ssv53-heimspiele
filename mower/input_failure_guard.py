"""Bounded protective handling when fresh runtime inputs are unavailable.

This path deliberately knows nothing about occupancy, training, matches, or
irrigation schedules.  It may read current mower telemetry and issue PARK, but
it can never infer that the field is free or authorize START/water commands.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from typing import Any, Mapping

from mower.config_source import InputUnavailable
from mower.decision import PARK_OVERRIDE_ACTIONS
from mower.husqvarna import HusqvarnaError, fetch_mowers, parse_snapshot
from mower.husqvarna_actions import park_until_further_notice
from mower.runtime import CycleResult, RuntimeSettings
from mower.safety import CommandIntent, evaluate_command_gate
from mower.state_store import AzureTableStateStore, StateStore


StateStoreFactory = Callable[[Mapping[str, str]], StateStore]
MowerFetcher = Callable[[str, str], list[dict[str, Any]]]
ParkSender = Callable[[str, str, str], dict[str, Any]]
Clock = Callable[[], datetime]

_PARKABLE_ACTIVITIES = frozenset(
    {"MOWING", "LEAVING", "PARKED_IN_CS", "CHARGING"}
)
_MANUAL_HOLD_ACTIVITIES = frozenset(
    {"STOPPED_IN_GARDEN", "NOT_APPLICABLE", "PAUSED"}
)
_INDEFINITE_PARK_OVERRIDES = frozenset(
    {"FORCE_PARK", "PARK_UNTIL_FURTHER_NOTICE"}
)


class ParkSendFenceError(RuntimeError):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def _select_protective_mower(
    items: list[dict[str, Any]],
) -> tuple[dict[str, Any], str]:
    """Use only the already established single-mower-account rule."""

    if len(items) == 1:
        return items[0], "single_account_mower"
    raise HusqvarnaError(
        "Mäheridentität für den Eingabeausfall ist nicht eindeutig: "
        f"{len(items)} Geräte und keine explizite Bindung."
    )


def _explicit_zero_next_start(item: Mapping[str, Any]) -> bool:
    attributes = item.get("attributes")
    if not isinstance(attributes, Mapping):
        return False
    planner = attributes.get("planner")
    if not isinstance(planner, Mapping) or "nextStartTimestamp" not in planner:
        return False
    raw = planner.get("nextStartTimestamp")
    if isinstance(raw, bool):
        return False
    if isinstance(raw, int):
        return raw == 0
    return isinstance(raw, str) and raw.strip() == "0"


def _reservation_owned(state: Any, *, reserved: Any, fingerprint: str) -> bool:
    return (
        state.revision == reserved.revision
        and state.last_command_fingerprint == fingerprint
        and state.last_command_utc == reserved.last_command_utc
        and state.parked_by_automation
        and state.automation_park_source == "input_unavailable"
        and not state.automation_restart_allowed
        and not state.maintenance_mode
        and state.mower_start_pending_since_utc is None
    )


def _fresh(snapshot: Any, *, now_utc: datetime, max_age_seconds: int) -> bool:
    try:
        observed = datetime.fromtimestamp(
            float(snapshot.status_timestamp_ms) / 1000,
            tz=timezone.utc,
        )
    except (TypeError, ValueError, OSError):
        return False
    age_seconds = (now_utc - observed).total_seconds()
    return -60 <= age_seconds <= max_age_seconds


def _clock_utc(clock: Clock) -> datetime | None:
    value = clock()
    if value.tzinfo is None or value.utcoffset() is None:
        return None
    return value.astimezone(timezone.utc)


def _result(
    *,
    now_utc: datetime,
    settings: RuntimeSettings,
    past_due: bool,
    source: str,
    cause: InputUnavailable,
    decision_code: str,
    message: str,
    command_sent: bool = False,
    mower: Mapping[str, Any] | None = None,
    extra: Mapping[str, Any] | None = None,
) -> CycleResult:
    details: dict[str, Any] = {
        "runtime_config": {
            "status": "UNAVAILABLE",
            "error_type": type(cause).__name__,
            "reason_code": "FRESH_RUNTIME_INPUTS_UNAVAILABLE",
            "fresh_validated_inputs": False,
        },
        "occupancy": {
            "status": "UNKNOWN",
            "fail_closed": True,
            "release_allowed": False,
        },
        "irrigation": {
            "status": "UNKNOWN",
            "commands_allowed": False,
        },
        "mower": dict(mower or {}),
        "safety": {
            "status": "FAIL_CLOSED",
            "command_sent": command_sent,
            "park_command_function_present": True,
            "start_command_functions_used": False,
            "irrigation_command_functions_used": False,
            "stale_occupancy_release_allowed": False,
        },
    }
    if extra:
        details.update(extra)
    return CycleResult(
        schema_version=2,
        executed_at_utc=now_utc.isoformat(),
        source=source,
        control_mode=settings.control_mode.value,
        past_due=bool(past_due),
        decision_code=decision_code,
        command_sent=command_sent,
        message=message,
        details=details,
    )


def run_input_failure_guard(
    *,
    now_utc: datetime,
    settings: RuntimeSettings,
    environment: Mapping[str, str],
    past_due: bool,
    source: str,
    cause: InputUnavailable,
    state_store_factory: StateStoreFactory = AzureTableStateStore.from_environment,
    park_sender: ParkSender,
    mower_fetcher: MowerFetcher = fetch_mowers,
    clock: Clock = lambda: datetime.now(timezone.utc),
) -> CycleResult:
    """Emit a diagnostic cycle and, where fully proven, reserve one PARK."""

    now = now_utc.astimezone(timezone.utc)
    client_id = str(environment.get("HUSQVARNA_CLIENT_ID") or "").strip()
    client_secret = str(environment.get("HUSQVARNA_CLIENT_SECRET") or "").strip()
    if not client_id or not client_secret:
        return _result(
            now_utc=now, settings=settings, past_due=past_due, source=source,
            cause=cause, decision_code="INPUT_UNAVAILABLE_ESCALATION_REQUIRED",
            message="Laufzeitdaten und Mäheridentität sind nicht sicher verfügbar; manuelle Prüfung erforderlich.",
            extra={"escalation": {"required": True, "reason": "MOWER_CREDENTIALS_UNAVAILABLE"}},
        )

    try:
        item, identity_strategy = _select_protective_mower(
            mower_fetcher(client_id, client_secret),
        )
        snapshot = parse_snapshot(item)
    except (HusqvarnaError, ValueError, TypeError, KeyError) as exc:
        return _result(
            now_utc=now, settings=settings, past_due=past_due, source=source,
            cause=cause, decision_code="INPUT_UNAVAILABLE_ESCALATION_REQUIRED",
            message="Aktuelle Mähertelemetrie ist nicht eindeutig verfügbar; manuelle Prüfung erforderlich.",
            extra={"escalation": {
                "required": True, "reason": "MOWER_TELEMETRY_UNAVAILABLE",
                "error_type": type(exc).__name__,
            }},
        )

    try:
        max_age_seconds = int(
            str(environment.get("MOWER_STATUS_MAX_AGE_SECONDS") or "180").strip()
        )
    except ValueError:
        max_age_seconds = 0
    observed_now = _clock_utc(clock)
    if observed_now is None:
        return _result(
            now_utc=now, settings=settings, past_due=past_due, source=source,
            cause=cause, decision_code="INPUT_UNAVAILABLE_ESCALATION_REQUIRED",
            message="Die aktuelle Uhrzeit für den Telemetrie-Nachweis ist ungültig; manuelle Prüfung erforderlich.",
            extra={"escalation": {"required": True, "reason": "COMMAND_CLOCK_INVALID"}},
        )
    telemetry_fresh = 30 <= max_age_seconds <= 900 and _fresh(
        snapshot, now_utc=observed_now, max_age_seconds=max_age_seconds
    )
    explicit_zero_next_start = _explicit_zero_next_start(item)
    indefinite_native_hold = (
        snapshot.override_action.strip().upper() in _INDEFINITE_PARK_OVERRIDES
        and explicit_zero_next_start
    )
    mower = {
        **snapshot.to_dict(),
        "identity_strategy": identity_strategy,
        "telemetry_fresh": telemetry_fresh,
        "status": "CURRENT" if telemetry_fresh else "UNKNOWN",
        "explicit_zero_next_start": explicit_zero_next_start,
        "indefinite_native_hold": indefinite_native_hold,
    }
    activity = snapshot.activity.strip().upper()
    mower_state = snapshot.state.strip().upper()
    override_action = snapshot.override_action.strip().upper()

    if not telemetry_fresh or snapshot.connected is not True:
        return _result(
            now_utc=now, settings=settings, past_due=past_due, source=source,
            cause=cause, decision_code="INPUT_UNAVAILABLE_ESCALATION_REQUIRED",
            message="Die Mähertelemetrie ist nicht frisch und verbunden; manuelle Prüfung erforderlich.",
            mower=mower, extra={"escalation": {
                "required": True, "reason": "MOWER_STATUS_NOT_CURRENT",
                "max_age_seconds": max_age_seconds,
            }},
        )

    # Load the authoritative state after all potentially slow input/vendor
    # reads. It is the only state snapshot that may be used for a following
    # state transition or command reservation.
    try:
        store = state_store_factory(environment)
        original = store.load()
    except Exception as exc:
        return _result(
            now_utc=now, settings=settings, past_due=past_due, source=source,
            cause=cause, decision_code="INPUT_UNAVAILABLE_ESCALATION_REQUIRED",
            message="Der persistente Schutzstatus ist nicht verfügbar; kein Gerätebefehl wurde gesendet.",
            mower=mower, extra={"escalation": {
                "required": True, "reason": "STATE_UNAVAILABLE",
                "error_type": type(exc).__name__,
            }},
        )

    operator_action = (
        str(original.operator_request_action or "").strip().upper()
        if original.operator_request_status == "PENDING"
        else ""
    )
    if operator_action in {
        "START_MOWING", "START_IRRIGATION", "START_IRRIGATION_ZONE"
    }:
        rejected = replace(
            original,
            revision=original.revision + 1,
            operator_request_status="REJECTED",
            operator_request_result=(
                "Laufzeitdaten nicht verfügbar; Startanforderung fail-closed verworfen."
            ),
        )
        try:
            store.save(rejected, expected_revision=original.revision)
        except Exception as exc:
            return _result(
                now_utc=now, settings=settings, past_due=past_due, source=source,
                cause=cause, decision_code="INPUT_UNAVAILABLE_OPERATOR_REJECTION_FAILED",
                message="Die unsichere Startanforderung konnte nicht persistent verworfen werden; manuelle Prüfung erforderlich.",
                mower=mower, extra={"escalation": {
                    "required": True, "reason": "OPERATOR_REJECTION_CAS_FAILED",
                    "error_type": type(exc).__name__,
                }},
            )
        return _result(
            now_utc=now, settings=settings, past_due=past_due, source=source,
            cause=cause, decision_code="INPUT_UNAVAILABLE_OPERATOR_START_REJECTED",
            message="Die Startanforderung wurde wegen unbekannter Platz- und Beregnungslage persistent verworfen.",
            mower=mower, extra={
                "operator_request": {
                    "action": operator_action,
                    "status": "REJECTED",
                    "persisted": True,
                },
                "automation_state": {"revision": rejected.revision},
            },
        )

    if original.mower_start_pending_since_utc is not None:
        return _result(
            now_utc=now, settings=settings, past_due=past_due, source=source,
            cause=cause, decision_code="INPUT_UNAVAILABLE_START_OUTCOME_UNKNOWN",
            message="Ein früherer Startausgang ist ungeklärt; der Latch bleibt bestehen und erfordert manuelle Prüfung.",
            mower=mower, extra={
                "start_latch": {
                    "pending_since_utc": original.mower_start_pending_since_utc,
                    "deadline_utc": original.mower_start_pending_deadline_utc,
                    "preserved": True,
                },
                "escalation": {"required": True, "reason": "MOWER_START_OUTCOME_UNCONFIRMED"},
            },
        )

    irrigation_active = original.irrigation_phase is not None

    if activity in {"PARKED_IN_CS", "CHARGING"} and indefinite_native_hold:
        if irrigation_active:
            return _result(
                now_utc=now, settings=settings, past_due=past_due, source=source,
                cause=cause, decision_code="INPUT_UNAVAILABLE_IRRIGATION_UNKNOWN_HOLD",
                message="Der Mäher ist mit Park-Override an der Station; die aktive Beregnungslage bleibt ohne Laufzeitdaten unbekannt.",
                mower=mower, extra={
                    "irrigation": {
                        "status": "UNKNOWN", "phase": original.irrigation_phase,
                        "journal_preserved": True, "commands_allowed": False,
                    },
                    "escalation": {"required": True, "reason": "IRRIGATION_STATE_UNKNOWN"},
                },
            )
        return _result(
            now_utc=now, settings=settings, past_due=past_due, source=source,
            cause=cause, decision_code="INPUT_UNAVAILABLE_SAFE_HOLD",
            message="Die Eingabedaten fehlen; der aktuelle Stationszustand mit bestätigtem Park-Override bleibt bestehen.",
            mower=mower,
        )

    if activity in {"PARKED_IN_CS", "CHARGING"} and override_action in PARK_OVERRIDE_ACTIONS:
        return _result(
            now_utc=now, settings=settings, past_due=past_due, source=source,
            cause=cause, decision_code="INPUT_UNAVAILABLE_PARK_HOLD_UNKNOWN",
            message="Der Stationszustand besitzt keinen nachgewiesenen unbefristeten Park-Override; manuelle Prüfung erforderlich.",
            mower=mower, extra={
                "escalation": {"required": True, "reason": "PARK_HOLD_NOT_INDEFINITE"},
            },
        )

    if activity == "GOING_HOME":
        protected_return = indefinite_native_hold
        return _result(
            now_utc=now, settings=settings, past_due=past_due, source=source,
            cause=cause, decision_code="INPUT_UNAVAILABLE_RETURN_PENDING",
            message=(
                "Die Eingabedaten fehlen; die bestätigte Heimfahrt läuft, der Stationszustand ist noch nicht bewiesen."
                if protected_return else
                "Die Eingabedaten fehlen und die Heimfahrt besitzt keinen sicheren Park-Override; manuelle Prüfung erforderlich."
            ),
            mower=mower, extra={"escalation": {
                "required": irrigation_active or not protected_return,
                "reason": (
                    "IRRIGATION_STATE_UNKNOWN" if irrigation_active
                    else "RETURN_TO_STATION_PENDING" if protected_return
                    else "RETURN_WITHOUT_PARK_OVERRIDE"
                ),
            }, "irrigation": {
                "status": "UNKNOWN", "phase": original.irrigation_phase,
                "journal_preserved": True, "commands_allowed": False,
            }},
        )

    if activity in _MANUAL_HOLD_ACTIVITIES or mower_state == "PAUSED":
        if irrigation_active:
            return _result(
                now_utc=now, settings=settings, past_due=past_due, source=source,
                cause=cause, decision_code="INPUT_UNAVAILABLE_MANUAL_IRRIGATION_UNKNOWN_HOLD",
                message="Der manuelle Mäherstopp bleibt bestehen; die aktive Beregnungslage ist unbekannt und erfordert Prüfung.",
                mower=mower, extra={
                    "irrigation": {
                        "status": "UNKNOWN", "phase": original.irrigation_phase,
                        "journal_preserved": True, "commands_allowed": False,
                    },
                    "escalation": {"required": True, "reason": "IRRIGATION_STATE_UNKNOWN"},
                },
            )
        return _result(
            now_utc=now, settings=settings, past_due=past_due, source=source,
            cause=cause, decision_code="INPUT_UNAVAILABLE_MANUAL_HOLD",
            message="Die Eingabedaten fehlen; der manuelle Stopp bleibt unangetastet.",
            mower=mower,
        )

    if activity in {"MOWING", "LEAVING"} and override_action in PARK_OVERRIDE_ACTIONS:
        return _result(
            now_utc=now, settings=settings, past_due=past_due, source=source,
            cause=cause, decision_code="INPUT_UNAVAILABLE_RETURN_PENDING",
            message="Die Eingabedaten fehlen; ein aktueller Park-Override ist bereits aktiv, seine Wirkung bleibt noch unbestätigt.",
            mower=mower, extra={"escalation": {
                "required": True, "reason": "PARK_OVERRIDE_NOT_YET_AT_STATION",
            }},
        )

    if (
        activity not in _PARKABLE_ACTIVITIES
        or mower_state != "IN_OPERATION"
        or snapshot.error_code != 0
        or not snapshot.mower_id
    ):
        return _result(
            now_utc=now, settings=settings, past_due=past_due, source=source,
            cause=cause, decision_code="INPUT_UNAVAILABLE_ESCALATION_REQUIRED",
            message="Der aktuelle Mäherzustand erlaubt keinen eindeutig sicheren Parkbefehl; manuelle Prüfung erforderlich.",
            mower=mower, extra={"escalation": {
                "required": True, "reason": "MOWER_NOT_SAFE_FOR_PARK_COMMAND",
            }},
        )

    if not settings.enable_park_commands:
        return _result(
            now_utc=now, settings=settings, past_due=past_due, source=source,
            cause=cause, decision_code="INPUT_UNAVAILABLE_PARK_LOCKED",
            message="Ein Schutzparken wäre erforderlich, bleibt aber durch ENABLE_PARK_COMMANDS gesperrt.",
            mower=mower, extra={"escalation": {
                "required": True, "reason": "PARK_WRITE_GATE_LOCKED",
            }},
        )

    reservation_time = _clock_utc(clock)
    if reservation_time is None or not _fresh(
        snapshot, now_utc=reservation_time or observed_now,
        max_age_seconds=max_age_seconds,
    ):
        return _result(
            now_utc=now, settings=settings, past_due=past_due, source=source,
            cause=cause, decision_code="INPUT_UNAVAILABLE_ESCALATION_REQUIRED",
            message="Die Mähertelemetrie ist vor der Schutzreservierung abgelaufen; kein Gerätebefehl wurde gesendet.",
            mower=mower, extra={"escalation": {
                "required": True, "reason": "MOWER_STATUS_EXPIRED_BEFORE_RESERVATION",
            }},
        )

    intent = CommandIntent(
        action="PARK",
        target=snapshot.mower_id,
        reason="runtime-input-unavailable",
    )
    if (
        original.parked_by_automation
        and str(original.automation_park_source or "").strip().lower()
        == "input_unavailable"
    ):
        return _result(
            now_utc=now, settings=settings, past_due=past_due, source=source,
            cause=cause, decision_code="INPUT_UNAVAILABLE_PARK_OUTCOME_UNKNOWN",
            message="Das Schutzparken wurde in diesem Ausfall bereits persistent reserviert; es wird nicht blind wiederholt.",
            mower=mower, extra={
                "park_gate": {
                    "allowed": False, "code": "PRIOR_RESERVATION",
                    "fingerprint": intent.fingerprint,
                    "reserved_at_utc": original.last_command_utc,
                },
                "escalation": {
                    "required": True,
                    "reason": "PRIOR_PARK_OUTCOME_UNCONFIRMED",
                },
            },
        )

    if original.parked_by_automation:
        return _result(
            now_utc=now, settings=settings, past_due=past_due, source=source,
            cause=cause, decision_code="INPUT_UNAVAILABLE_ACTIVE_RESERVATION_HOLD",
            message="Eine bestehende Schutzparkierung bleibt unverändert; ihre Reservierung wird nicht überschrieben.",
            mower=mower, extra={
                "park_gate": {
                    "allowed": False, "code": "ACTIVE_PROTECTIVE_PARK",
                    "existing_source": original.automation_park_source,
                    "existing_command_utc": original.park_command_sent_utc,
                },
                "escalation": {"required": True, "reason": "ACTIVE_PROTECTIVE_PARK"},
            },
        )

    recent_command = None
    if original.last_command_utc:
        try:
            recent_command = datetime.fromisoformat(
                original.last_command_utc.replace("Z", "+00:00")
            ).astimezone(timezone.utc)
        except ValueError:
            recent_command = reservation_time
    if (
        original.mower_start_pending_since_utc is not None
        or (
            recent_command is not None
            and -timedelta(seconds=30) <= reservation_time - recent_command < timedelta(minutes=10)
        )
    ):
        return _result(
            now_utc=now, settings=settings, past_due=past_due, source=source,
            cause=cause, decision_code="INPUT_UNAVAILABLE_ACTIVE_RESERVATION_HOLD",
            message="Eine andere aktuelle Befehlsreservierung bleibt unverändert; Schutzparken wird nicht darübergeschrieben.",
            mower=mower, extra={
                "park_gate": {
                    "allowed": False, "code": "ACTIVE_COMMAND_RESERVATION",
                    "existing_fingerprint": original.last_command_fingerprint,
                    "existing_command_utc": original.last_command_utc,
                    "mower_start_pending": original.mower_start_pending_since_utc is not None,
                },
                "escalation": {"required": True, "reason": "ACTIVE_COMMAND_RESERVATION"},
            },
        )

    gate = evaluate_command_gate(
        state=original, intent=intent, now_utc=reservation_time
    )
    if not gate.allowed:
        return _result(
            now_utc=now, settings=settings, past_due=past_due, source=source,
            cause=cause, decision_code="INPUT_UNAVAILABLE_PARK_GATE_BLOCKED",
            message=f"Die Laufzeitdaten fehlen; Schutzparken ist gesperrt: {gate.reason}",
            mower=mower, extra={
                "park_gate": {
                    "allowed": False, "code": gate.code, "reason": gate.reason,
                    "fingerprint": intent.fingerprint,
                },
                "escalation": {"required": True, "reason": gate.code},
            },
        )

    reserved = original.record_command(
        fingerprint=intent.fingerprint,
        sent_utc=reservation_time,
        action="PARK",
        park_source="input_unavailable",
        restart_allowed=False,
    )
    try:
        store.save(reserved, expected_revision=original.revision)
    except Exception as exc:
        return _result(
            now_utc=now, settings=settings, past_due=past_due, source=source,
            cause=cause, decision_code="INPUT_UNAVAILABLE_STATE_RESERVATION_FAILED",
            message="Schutzparken wurde wegen fehlender persistenter CAS-Reservierung nicht gesendet.",
            mower=mower, extra={
                "park_gate": {
                    "allowed": False, "code": "STATE_RESERVATION_FAILED",
                    "fingerprint": intent.fingerprint,
                    "error_type": type(exc).__name__,
                },
                "escalation": {"required": True, "reason": "STATE_RESERVATION_FAILED"},
            },
        )

    park_gate = {
        "allowed": True,
        "code": gate.code,
        "fingerprint": intent.fingerprint,
        "reserved_revision": reserved.revision,
        "reserved_before_command": True,
        "automatic_retry_allowed": False,
    }
    try:
        confirmed = store.load()
    except Exception as exc:
        return _result(
            now_utc=now, settings=settings, past_due=past_due, source=source,
            cause=cause, decision_code="INPUT_UNAVAILABLE_PARK_OUTCOME_UNKNOWN",
            message="Die Schutzpark-Reservierung kann vor dem Senden nicht erneut bestätigt werden; es wurde kein Befehl gesendet.",
            mower=mower, extra={
                "park_gate": {
                    **park_gate, "allowed": False,
                    "code": "POST_CAS_STATE_UNAVAILABLE",
                    "error_type": type(exc).__name__,
                },
                "escalation": {"required": True, "reason": "POST_CAS_STATE_UNAVAILABLE"},
            },
        )
    reservation_still_owned = _reservation_owned(
        confirmed, reserved=reserved, fingerprint=intent.fingerprint
    )
    if not reservation_still_owned:
        return _result(
            now_utc=now, settings=settings, past_due=past_due, source=source,
            cause=cause, decision_code="INPUT_UNAVAILABLE_PARK_OUTCOME_UNKNOWN",
            message="Die Schutzpark-Reservierung ist vor dem Senden nicht mehr exklusiv gültig; es wurde kein Befehl gesendet.",
            mower=mower, extra={
                "park_gate": {
                    **park_gate, "allowed": False,
                    "code": "POST_CAS_OWNERSHIP_LOST",
                    "confirmed_revision": confirmed.revision,
                },
                "escalation": {"required": True, "reason": "POST_CAS_OWNERSHIP_LOST"},
            },
        )

    command_now = _clock_utc(clock)
    if command_now is None:
        return _result(
            now_utc=now, settings=settings, past_due=past_due, source=source,
            cause=cause, decision_code="INPUT_UNAVAILABLE_PARK_OUTCOME_UNKNOWN",
            message="Die Schutzpark-Reservierung besitzt keine verlässliche Sendezeit; es wurde kein Befehl gesendet.",
            mower=mower, extra={
                "park_gate": {**park_gate, "allowed": False, "code": "COMMAND_CLOCK_INVALID"},
                "escalation": {"required": True, "reason": "COMMAND_CLOCK_INVALID"},
            },
        )
    reservation_age = (command_now - reservation_time).total_seconds()
    if not -5 <= reservation_age <= 30:
        return _result(
            now_utc=now, settings=settings, past_due=past_due, source=source,
            cause=cause, decision_code="INPUT_UNAVAILABLE_PARK_OUTCOME_UNKNOWN",
            message="Die Schutzpark-Reservierung ist vor dem Senden abgelaufen; es erfolgt keine verspätete Geräteaktion.",
            mower=mower, extra={
                "park_gate": {
                    **park_gate, "allowed": False, "code": "RESERVATION_EXPIRED_BEFORE_SEND",
                    "reservation_age_seconds": reservation_age,
                },
                "escalation": {"required": True, "reason": "RESERVATION_EXPIRED_BEFORE_SEND"},
            },
        )
    if not _fresh(snapshot, now_utc=command_now, max_age_seconds=max_age_seconds):
        return _result(
            now_utc=now, settings=settings, past_due=past_due, source=source,
            cause=cause, decision_code="INPUT_UNAVAILABLE_PARK_OUTCOME_UNKNOWN",
            message="Die Mähertelemetrie ist nach der Schutzpark-Reservierung abgelaufen; es wurde kein Befehl gesendet.",
            mower=mower, extra={
                "park_gate": {
                    **park_gate, "allowed": False,
                    "code": "MOWER_STATUS_EXPIRED_BEFORE_SEND",
                },
                "escalation": {"required": True, "reason": "MOWER_STATUS_EXPIRED_BEFORE_SEND"},
            },
        )
    send_authorized = False

    def before_send_fence() -> None:
        nonlocal send_authorized
        callback_now = _clock_utc(clock)
        if callback_now is None:
            raise ParkSendFenceError("COMMAND_CLOCK_INVALID")
        if not -5 <= (callback_now - reservation_time).total_seconds() <= 30:
            raise ParkSendFenceError("RESERVATION_EXPIRED_DURING_AUTH")
        if not _fresh(
            snapshot, now_utc=callback_now, max_age_seconds=max_age_seconds
        ):
            raise ParkSendFenceError("MOWER_STATUS_EXPIRED_DURING_AUTH")
        try:
            latest = store.load()
        except Exception as exc:
            raise ParkSendFenceError("PRE_POST_STATE_UNAVAILABLE") from exc
        if not _reservation_owned(
            latest, reserved=reserved, fingerprint=intent.fingerprint
        ):
            raise ParkSendFenceError("PRE_POST_OWNERSHIP_LOST")
        send_authorized = True

    try:
        if park_sender is park_until_further_notice:
            response = park_sender(
                client_id,
                client_secret,
                snapshot.mower_id,
                before_send=before_send_fence,
            )
        else:
            # Injected senders preserve their existing three-argument contract.
            # Their tests observe the already completed pre-send fence above.
            send_authorized = True
            response = park_sender(client_id, client_secret, snapshot.mower_id)
    except ParkSendFenceError as exc:
        return _result(
            now_utc=now, settings=settings, past_due=past_due, source=source,
            cause=cause, decision_code="INPUT_UNAVAILABLE_PARK_OUTCOME_UNKNOWN",
            command_sent=False,
            message="Die Schutzpark-Reservierung verlor vor dem HTTP-Aufruf ihre Freigabe; es wurde kein Befehl gesendet.",
            mower=mower, extra={
                "park_gate": {
                    **park_gate, "allowed": False, "code": exc.code,
                },
                "escalation": {"required": True, "reason": exc.code},
            },
        )
    except Exception as exc:
        return _result(
            now_utc=now, settings=settings, past_due=past_due, source=source,
            cause=cause, decision_code="INPUT_UNAVAILABLE_PARK_OUTCOME_UNKNOWN",
            command_sent=send_authorized,
            message=(
                "Die Antwort auf das einmalig reservierte Schutzparken fehlt; keine automatische Wiederholung."
                if send_authorized else
                "Die Schutzpark-Anmeldung scheiterte vor dem HTTP-Aufruf; die Reservierung bleibt terminal und wird nicht wiederholt."
            ),
            mower=mower, extra={
                "park_gate": park_gate,
                "park_action": {
                    "type": "ParkUntilFurtherNotice", "outcome": "UNKNOWN",
                    "error_type": type(exc).__name__,
                },
                "escalation": {
                    "required": True,
                    "reason": (
                        "PARK_RESPONSE_LOST" if send_authorized
                        else "PARK_AUTHENTICATION_FAILED"
                    ),
                },
            },
        )

    return _result(
        now_utc=now, settings=settings, past_due=past_due, source=source,
        cause=cause, decision_code="INPUT_UNAVAILABLE_PARK_COMMAND_SENT",
        command_sent=True,
        message="Die Laufzeitdaten fehlen; Schutzparken wurde einmalig reserviert und vom Anbieter angenommen.",
        mower=mower, extra={
            "park_gate": park_gate,
            "park_action": {
                "type": "ParkUntilFurtherNotice", "outcome": "VENDOR_ACCEPTED",
                "response": response,
            },
            "automation_state": {
                "revision": reserved.revision,
                "parked_by_automation": reserved.parked_by_automation,
                "automation_park_source": reserved.automation_park_source,
                "automatic_restart_allowed": reserved.automation_restart_allowed,
                "persisted": True,
            },
        },
    )
