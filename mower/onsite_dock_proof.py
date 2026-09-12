"""One-shot, persisted on-site dock proof for one irrigation program.

This opt-in proof never changes mower state and never sends a mower command.
It only lets an already STOPPED mower satisfy the station predicate for the
single irrigation request and immutable plan recorded below.
"""
from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone
import hashlib
import hmac
import json
from typing import Any, Mapping, Sequence

from mower.decision import NO_OVERRIDE, PARK_OVERRIDE_ACTIONS
from mower.device_send_guard import DeviceSendBlocked, unresolved_device_sends
from mower.state import AutomationState


FLAG = "IRRIGATION_ONSITE_DOCK_CONFIRMATION_ENABLED"
OPERATION = "CONFIRM_DOCK_FOR_IRRIGATION"
VERSION = 1
ADMISSION_SECONDS = 120
MAX_PROGRAM_SECONDS = 8 * 60 * 60
MAX_READ_GAP_SECONDS = 90
ACTIVE_PHASES = {"PLANNED", "SUSPENDING", "READY", "START_RESERVED", "RUNNING", "STOPPING"}


class OnsiteDockProofError(ValueError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def enabled(environment: Mapping[str, str]) -> bool:
    return str(environment.get(FLAG, "false")).strip().lower() == "true"


def _dispatch_guard_enabled(environment: Mapping[str, str]) -> bool:
    return (
        enabled(environment)
        and str(environment.get("ENABLE_MANUAL_SESSIONS", "false")).strip().lower()
        == "true"
    )


def _time(value: Any) -> datetime:
    parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("Zeitangabe ohne Zeitzone")
    return parsed.astimezone(timezone.utc)


def _observed(mower: Mapping[str, Any]) -> datetime:
    return datetime.fromtimestamp(
        float(mower["status_timestamp_ms"]) / 1000, timezone.utc,
    )


def _status_values(mower: Mapping[str, Any]) -> list[Any]:
    return [
        mower.get("mower_id"),
        mower.get("status_timestamp_ms"),
        mower.get("connected"),
        str(mower.get("state") or "").strip().upper(),
        str(mower.get("activity") or "").strip().upper(),
        str(mower.get("mode") or "").strip().upper(),
        mower.get("error_code"),
        mower.get("override_action"),
    ]


def _status_digest(mower: Mapping[str, Any]) -> str:
    return hashlib.sha256(
        json.dumps(_status_values(mower), separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def _token(state: AutomationState, mower: Mapping[str, Any]) -> str:
    # Routine observation cycles advance ``revision``. CAS protects the write;
    # the token binds only control facts whose change revokes this consent.
    values = [state.irrigation_phase, state.operator_request_id,
              state.operator_request_action, state.operator_request_status,
              state.mower_start_pending_since_utc, state.maintenance_mode,
              state.continuous_mowing_owned, _status_digest(mower)]
    return hashlib.sha256(
        json.dumps(values, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def _pending_sends(state: AutomationState, allowed_intent_key: str | None = None) -> bool:
    try:
        pending = unresolved_device_sends(state)
    except DeviceSendBlocked:
        return True
    return any(entry.get("intent_key") != allowed_intent_key for entry in pending)


def _stopped_at_dock(
    state: AutomationState,
    mower: Mapping[str, Any],
    environment: Mapping[str, str],
    now: datetime,
    *,
    allowed_intent_key: str | None = None,
    continuous_read: bool = False,
) -> tuple[bool, str]:
    configured = str(environment.get("HUSQVARNA_MOWER_ID") or "").strip()
    if not configured or str(mower.get("mower_id") or "") != configured:
        return False, "MOWER_TARGET_CHANGED"
    if mower.get("connected") is not True:
        return False, "MOWER_OFFLINE"
    try:
        observed = _observed(mower)
    except (KeyError, TypeError, ValueError, OSError, OverflowError):
        return False, "MOWER_STATUS_INVALID"
    try:
        max_age = int(str(environment.get("MOWER_STATUS_MAX_AGE_SECONDS", "180")).strip())
    except ValueError:
        return False, "MOWER_STATUS_INVALID"
    if not 30 <= max_age <= 900 or (
        not continuous_read
        and not -30 <= (now - observed).total_seconds() <= max_age
    ):
        return False, "MOWER_STATUS_STALE"
    if observed > now + timedelta(seconds=30):
        return False, "MOWER_STATUS_INVALID"
    if type(mower.get("error_code")) is not int or mower.get("error_code") != 0:
        return False, "MOWER_ERROR"
    mower_state = str(mower.get("state") or "").strip().upper()
    activity = str(mower.get("activity") or "").strip().upper()
    mode = str(mower.get("mode") or "").strip().upper()
    if mower_state in {"ERROR", "FATAL_ERROR", "ERROR_AT_POWER_UP", "OFF", "WAIT_UPDATING", "WAIT_POWER_UP"}:
        return False, "MOWER_ERROR_OR_OFF"
    if (mower_state, activity, mode) != ("STOPPED", "NOT_APPLICABLE", "HOME"):
        return False, "MOWER_NOT_STOPPED_AT_DOCK"
    override_action = str(mower.get("override_action") or "").strip().upper()
    if override_action not in NO_OVERRIDE | PARK_OVERRIDE_ACTIONS:
        return False, "MOWER_OVERRIDE_UNSAFE"
    if state.maintenance_mode or state.mower_start_pending_since_utc is not None:
        return False, "CONTROL_STATE_UNSAFE"
    if state.continuous_mowing_owned:
        return False, "MOWER_MOVEMENT_OWNED"
    if _pending_sends(state, allowed_intent_key):
        return False, "DEVICE_SEND_UNCONFIRMED"
    return True, "AVAILABLE"


def context(
    state: AutomationState,
    mower: Mapping[str, Any],
    environment: Mapping[str, str],
    now_utc: datetime,
) -> dict[str, Any]:
    now = now_utc.astimezone(timezone.utc)
    available, reason = _stopped_at_dock(state, mower, environment, now)
    required = (
        str(mower.get("activity") or "").strip().upper() == "NOT_APPLICABLE"
        and str(mower.get("mode") or "").strip().upper() == "HOME"
        and str(mower.get("state") or "").strip().upper() in {"STOPPED", "ERROR", "FATAL_ERROR", "OFF"}
    )
    feature = enabled(environment)
    guarded = _dispatch_guard_enabled(environment)
    can_confirm = bool(
        guarded and required and available
        and state.irrigation_phase is None
        and state.operator_request_status != "PENDING"
    )
    if not feature:
        reason = "FEATURE_DISABLED"
    elif not guarded:
        reason = "PERSISTENT_SEND_GUARD_DISABLED"
    elif state.irrigation_phase is not None:
        reason = "IRRIGATION_ALREADY_ACTIVE"
    elif state.operator_request_status == "PENDING":
        reason = "ACTION_PENDING"
    return {
        "enabled": feature,
        "required": required,
        "canConfirm": can_confirm,
        "contextToken": _token(state, mower) if can_confirm else None,
        "expiresInSeconds": ADMISSION_SECONDS,
        "reason": reason,
    }


def admit(
    state: AutomationState,
    mower: Mapping[str, Any],
    environment: Mapping[str, str],
    now_utc: datetime,
    *,
    request_id: str,
    action: str,
    zone: int | None,
    run_seconds: int | None,
    payload: Mapping[str, Any] | None,
) -> str:
    public = context(state, mower, environment, now_utc)
    if not public["canConfirm"]:
        raise OnsiteDockProofError(
            str(public["reason"]),
            "Die Stationsbestätigung ist für die aktuelle Mähermeldung nicht zulässig.",
        )
    expected_keys = {"operation", "confirmed", "contextToken"}
    if not isinstance(payload, Mapping) or set(payload) != expected_keys:
        raise OnsiteDockProofError(
            "ONSITE_DOCK_CONFIRMATION_INVALID",
            "Bitte die Stationsbestätigung neu öffnen.",
        )
    if payload.get("operation") != OPERATION or payload.get("confirmed") is not True:
        raise OnsiteDockProofError(
            "ONSITE_DOCK_CONFIRMATION_REQUIRED",
            "Bitte bestätigen: Ich sehe den Mäher in der Station.",
        )
    supplied = payload.get("contextToken")
    if not isinstance(supplied, str) or not hmac.compare_digest(supplied, public["contextToken"]):
        raise OnsiteDockProofError(
            "ONSITE_DOCK_CONTEXT_CHANGED",
            "Die Mähermeldung hat sich geändert. Bitte aktualisieren und erneut bestätigen.",
        )
    now = now_utc.astimezone(timezone.utc)
    proof = {
        "version": VERSION,
        "status": "REQUESTED",
        "request_id": request_id,
        "action": action,
        "zone": zone,
        "run_seconds": run_seconds,
        "mower_id": mower.get("mower_id"),
        "override_action": str(mower.get("override_action") or "").strip().upper(),
        "status_digest": _status_digest(mower),
        "source_at_utc": _observed(mower).isoformat(),
        "checked_at_utc": now.isoformat(),
        "confirmed_at_utc": now.isoformat(),
        "admission_expires_at_utc": (now + timedelta(seconds=ADMISSION_SECONDS)).isoformat(),
        "plan_id": None,
        "program_not_after_utc": None,
    }
    return json.dumps(proof, sort_keys=True, separators=(",", ":"))


def _load(state: AutomationState) -> dict[str, Any]:
    raw = json.loads(state.irrigation_onsite_dock_proof_json or "null")
    if not isinstance(raw, dict) or raw.get("version") != VERSION:
        raise ValueError("Ungültiger Vor-Ort-Nachweis")
    if raw.get("status") == "INVALID":
        _time(raw["invalidated_at_utc"])
        return raw
    if raw.get("status") not in {"REQUESTED", "BOUND"}:
        raise ValueError("Ungültiger Vor-Ort-Status")
    if not isinstance(raw.get("request_id"), str) or not raw["request_id"]:
        raise ValueError("Ungültiger Vor-Ort-Auftrag")
    if raw.get("override_action") not in NO_OVERRIDE | PARK_OVERRIDE_ACTIONS:
        raise ValueError("Ungültige Override-Bindung")
    for field in ("status_digest",):
        value = raw.get(field)
        if (not isinstance(value, str) or len(value) != 64
                or any(character not in "0123456789abcdef" for character in value)):
            raise ValueError("Ungültige Statusbindung")
    _time(raw["source_at_utc"])
    _time(raw["checked_at_utc"])
    _time(raw["confirmed_at_utc"])
    _time(raw["admission_expires_at_utc"])
    if raw["status"] == "BOUND":
        if not isinstance(raw.get("plan_id"), str) or not raw["plan_id"]:
            raise ValueError("Ungültige Planbindung")
        fingerprint = raw.get("plan_fingerprint")
        if (not isinstance(fingerprint, str) or len(fingerprint) != 64
                or any(character not in "0123456789abcdef" for character in fingerprint)):
            raise ValueError("Ungültige Planbindung")
        if _time(raw["program_not_after_utc"]) <= _time(raw["confirmed_at_utc"]):
            raise ValueError("Ungültiges Programmende")
    return raw


def bind_plan(
    state: AutomationState,
    *,
    request_id: str,
    action: str,
    plan_id: str,
    plan: Sequence[Mapping[str, Any]],
    now_utc: datetime,
    end_confirmation_minutes: int,
) -> AutomationState:
    proof = _load(state)
    now = now_utc.astimezone(timezone.utc)
    if (
        proof.get("status") != "REQUESTED"
        or proof.get("request_id") != request_id
        or proof.get("action") != action
        or state.operator_request_id != request_id
        or state.operator_request_action != action
        or state.operator_request_status != "PENDING"
        or proof.get("zone") != state.operator_request_zone
        or proof.get("run_seconds") != state.operator_request_run_seconds
        or now >= _time(proof["admission_expires_at_utc"])
    ):
        raise OnsiteDockProofError(
            "ONSITE_DOCK_BINDING_EXPIRED",
            "Die Vor-Ort-Bestätigung konnte nicht mehr eindeutig an den Beregnungsauftrag gebunden werden.",
        )
    selected = [item for item in plan if item.get("selected", True) is not False]
    try:
        total_seconds = sum(int(item["run_seconds"]) for item in selected)
    except (KeyError, TypeError, ValueError) as exc:
        raise OnsiteDockProofError("ONSITE_DOCK_PLAN_INVALID", "Der Beregnungsplan ist ungültig.") from exc
    allowance = total_seconds + len(selected) * end_confirmation_minutes * 60 + 10 * 60
    if not selected or not 0 < allowance <= MAX_PROGRAM_SECONDS:
        raise OnsiteDockProofError("ONSITE_DOCK_PLAN_INVALID", "Der Beregnungsplan ist zu lang oder leer.")
    plan_fingerprint = hashlib.sha256(
        json.dumps(
            list(plan), ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode("utf-8")
    ).hexdigest()
    proof.update(
        status="BOUND",
        plan_id=plan_id,
        plan_fingerprint=plan_fingerprint,
        bound_at_utc=now.isoformat(),
        program_not_after_utc=(now + timedelta(seconds=allowance)).isoformat(),
    )
    return replace(
        state,
        irrigation_onsite_dock_proof_json=json.dumps(
            proof, sort_keys=True, separators=(",", ":")
        ),
    )


def valid_for_plan(
    state: AutomationState,
    mower: Mapping[str, Any],
    environment: Mapping[str, str],
    now_utc: datetime,
    *,
    expected_json: str | None = None,
    allowed_intent_key: str | None = None,
) -> bool:
    if not _dispatch_guard_enabled(environment):
        return False
    try:
        proof = _load(state)
        now = now_utc.astimezone(timezone.utc)
        stored_plan = json.loads(state.irrigation_plan_json or "null")
        stored_fingerprint = hashlib.sha256(
            json.dumps(
                stored_plan, ensure_ascii=False, sort_keys=True, separators=(",", ":")
            ).encode("utf-8")
        ).hexdigest()
        safe, _ = _stopped_at_dock(
            state, mower, environment, now,
            allowed_intent_key=allowed_intent_key,
            continuous_read=True,
        )
        return bool(
            safe
            and proof.get("status") == "BOUND"
            and state.irrigation_phase in ACTIVE_PHASES
            and state.irrigation_plan_id == proof.get("plan_id")
            and isinstance(stored_plan, list)
            and stored_fingerprint == proof.get("plan_fingerprint")
            and proof.get("mower_id") == mower.get("mower_id")
            and proof.get("override_action")
            == str(mower.get("override_action") or "").strip().upper()
            and state.operator_request_id == proof.get("request_id")
            and state.operator_request_action == proof.get("action")
            and state.operator_request_status in {"PENDING", "COMPLETED"}
            and (expected_json is None or state.irrigation_onsite_dock_proof_json == expected_json)
            and _observed(mower) >= _time(proof["source_at_utc"])
            and timedelta(0) <= now - _time(proof["checked_at_utc"])
            <= timedelta(seconds=MAX_READ_GAP_SECONDS)
            and now < _time(proof["program_not_after_utc"])
        )
    except (KeyError, TypeError, ValueError, OSError, OverflowError):
        return False


def invalidated_for_active_plan(state: AutomationState) -> bool:
    """Return whether a durable invalidation belongs to this exact water plan.

    The binding survives invalidation so a later successful input cycle can
    finish the protective WATER_STOP after an outage or process restart.  It
    grants no authority: only an INVALID record can satisfy this predicate.
    """
    try:
        proof = _load(state)
        return bool(
            proof.get("status") == "INVALID"
            and state.irrigation_phase in {"START_RESERVED", "RUNNING", "STOPPING"}
            and state.irrigation_plan_id == proof.get("plan_id")
            and type(proof.get("stop_relay_id")) is int
            and state.irrigation_current_relay_id == proof.get("stop_relay_id")
        )
    except (TypeError, ValueError, KeyError):
        return False


def invalidate(state: AutomationState, *, now_utc: datetime, reason: str) -> AutomationState:
    retained: dict[str, Any] = {}
    try:
        previous = _load(state)
        if previous.get("status") in {"BOUND", "INVALID"}:
            for key in (
                "request_id", "action", "mower_id", "plan_id",
                "plan_fingerprint", "program_not_after_utc", "override_action",
                "stop_relay_id",
            ):
                retained[key] = previous.get(key)
            if (
                previous.get("status") == "BOUND"
                and state.irrigation_phase in {"START_RESERVED", "RUNNING", "STOPPING"}
                and type(state.irrigation_current_relay_id) is int
            ):
                retained["stop_relay_id"] = state.irrigation_current_relay_id
    except (TypeError, ValueError, KeyError):
        pass
    return replace(
        state,
        irrigation_onsite_dock_proof_json=json.dumps(
            {
                "version": VERSION,
                "status": "INVALID",
                "invalidated_at_utc": now_utc.astimezone(timezone.utc).isoformat(),
                "reason": reason,
                **retained,
            },
            sort_keys=True,
            separators=(",", ":"),
        ),
    )


def observe(
    state: AutomationState,
    mower: Mapping[str, Any],
    environment: Mapping[str, str],
    now_utc: datetime,
) -> AutomationState:
    if state.irrigation_onsite_dock_proof_json is None:
        return state
    if not enabled(environment):
        try:
            proof = _load(state)
        except (TypeError, ValueError, KeyError):
            proof = None
        if (
            isinstance(proof, dict)
            and proof.get("status") == "BOUND"
            and state.irrigation_phase in {"START_RESERVED", "RUNNING", "STOPPING"}
        ):
            return invalidate(state, now_utc=now_utc, reason="FEATURE_DISABLED")
        if (
            isinstance(proof, dict)
            and proof.get("status") == "INVALID"
            and type(proof.get("stop_relay_id")) is int
            and state.irrigation_phase in {"START_RESERVED", "RUNNING", "STOPPING"}
        ):
            return state
        return replace(state, irrigation_onsite_dock_proof_json=None)
    try:
        proof = _load(state)
    except (TypeError, ValueError, KeyError):
        return invalidate(state, now_utc=now_utc, reason="PROOF_INVALID")
    if proof.get("status") == "INVALID":
        return state
    now = now_utc.astimezone(timezone.utc)
    try:
        gap = now - _time(proof["checked_at_utc"])
        observed = _observed(mower)
        source = _time(proof["source_at_utc"])
    except (KeyError, TypeError, ValueError, OSError, OverflowError):
        return invalidate(state, now_utc=now, reason="PROOF_INVALID")
    if not timedelta(0) <= gap <= timedelta(seconds=MAX_READ_GAP_SECONDS):
        return invalidate(state, now_utc=now, reason="READ_CONTINUITY_LOST")
    if observed < source:
        return invalidate(state, now_utc=now, reason="STATUS_MOVED_BACKWARDS")
    safe, reason = _stopped_at_dock(
        state, mower, environment, now, continuous_read=True,
    )
    if not safe:
        return invalidate(state, now_utc=now_utc, reason=reason)
    if proof.get("status") == "REQUESTED":
        if (state.operator_request_id != proof.get("request_id")
                or state.operator_request_action != proof.get("action")
                or state.operator_request_status != "PENDING"):
            return invalidate(state, now_utc=now_utc, reason="REQUEST_CHANGED")
    elif not valid_for_plan(state, mower, environment, now_utc):
        return invalidate(state, now_utc=now_utc, reason="PLAN_OR_PROGRAM_CHANGED")
    proof.update(
        checked_at_utc=now.isoformat(),
        source_at_utc=max(source, observed).isoformat(),
    )
    return replace(
        state,
        irrigation_onsite_dock_proof_json=json.dumps(
            proof, sort_keys=True, separators=(",", ":")
        ),
    )
