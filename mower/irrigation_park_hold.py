"""Continue an owned, confirmed HOME park across unchanged vendor events.

The operator must end/confirm irrigation before starting in the vendor app or
at the mower. This is not a hardware interlock. A confirmed manual APP start
may consume a held park proof once, through a short-lived request-bound handoff.
Automatic starts and unconfirmed device actions still require fresh events.
"""
from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone
import hashlib
import json
from typing import Any, Mapping

from mower.device_send_guard import DeviceSendBlocked, unresolved_device_sends
from mower.state import AutomationState

FLAG = "IRRIGATION_CONFIRMED_PARK_HOLD_ENABLED"
MAX_READ_GAP_SECONDS = 90


def enabled(environment: Mapping[str, str]) -> bool:
    return str(environment.get(FLAG, "false")).strip().lower() == "true"


def _time(raw: Any) -> datetime:
    parsed = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("Missing timezone")
    return parsed.astimezone(timezone.utc)


def _load(state: AutomationState) -> dict:
    raw = json.loads(state.irrigation_park_hold_json or "null")
    if raw is None or raw == {}:
        return {}
    if not isinstance(raw, dict) or type(raw.get("version")) is not int or raw["version"] != 1:
        raise ValueError("Invalid park proof version")
    if raw.get("status") == "INVALID":
        _time(raw.get("not_before_utc"))
    elif raw.get("status") in {"CANDIDATE", "HELD", "START_READY"}:
        since, checked = _time(raw.get("since_utc")), _time(raw.get("checked_at_utc"))
        _time(raw.get("source_at_utc"))
        count = raw.get("observations")
        binding = raw.get("binding")
        if (type(count) is not int or count < 1 or checked < since
            or not isinstance(binding, str) or len(binding) != 64
            or any(c not in "0123456789abcdef" for c in binding)
            or (raw["status"] in {"HELD", "START_READY"} and (count < 2 or checked - since < timedelta(minutes=1)))):
            raise ValueError("Invalid park proof observations")
        if raw["status"] == "START_READY":
            issued, expires = _time(raw["issued_at_utc"]), _time(raw["expires_at_utc"])
            if (not checked <= issued < expires <= issued + timedelta(seconds=120)
                or not isinstance(raw.get("request_id"), str)
                or type(raw.get("request_epoch")) is not int):
                raise ValueError("Invalid start handoff")
    else:
        raise ValueError("Invalid park proof status")
    return raw


def _dump(proof: dict) -> str:
    return json.dumps(proof, sort_keys=True, separators=(",", ":"))


def _binding(state: AutomationState, mower: Mapping[str, Any]) -> str:
    values = [mower.get("mower_id"), state.park_command_sent_utc,
              state.last_start_command_utc, state.manual_session_json,
              state.automation_park_source, state.automation_restart_allowed,
              state.operator_request_id, state.operator_request_action,
              state.operator_request_status,
              state.operator_request_session_id, state.operator_request_session_epoch]
    return hashlib.sha256(json.dumps(values, separators=(",", ":")).encode()).hexdigest()


def invalidate(state: AutomationState, *, now_utc: datetime, reason: str) -> AutomationState:
    return replace(state, irrigation_park_hold_json=_dump({
        "version": 1, "status": "INVALID", "not_before_utc": now_utc.isoformat(), "reason": reason,
    }))


def _station(state: AutomationState, mower: Mapping[str, Any], now: datetime) -> bool:
    try:
        observed = datetime.fromtimestamp(float(mower["status_timestamp_ms"]) / 1000, timezone.utc)
        park_sent = _time(state.park_command_sent_utc)
        session = json.loads(state.manual_session_json or "null")
        session_safe = session is None or (
            isinstance(session, dict) and (session.get("status") == "ENDED"
                or (session.get("kind") == "PARK" and session.get("status") == "ACTIVE"))
        )
        return bool(
            state.parked_by_automation
            and state.park_confirmed_utc and state.park_confirmed_observations >= 2
            and not state.maintenance_mode and not state.continuous_mowing_owned
            and not state.mower_start_pending_since_utc
            and state.operator_request_status != "PENDING" and session_safe
            and mower.get("mower_id") and mower.get("connected") is True
            and mower.get("mode") == "HOME"
            and mower.get("activity") in {"PARKED_IN_CS", "CHARGING"}
            and mower.get("state") in {"RESTRICTED", "IN_OPERATION", "PAUSED"}
            and type(mower.get("error_code")) is int and mower["error_code"] == 0
            and park_sent <= observed <= now + timedelta(seconds=60)
            and not any(entry["kind"] in {"PARK", "NATIVE_RESUME"}
                        for entry in unresolved_device_sends(state))
        )
    except (KeyError, ValueError, TypeError, OverflowError, OSError, DeviceSendBlocked):
        return False


def valid(state: AutomationState, mower: Mapping[str, Any], *, now_utc: datetime,
          expected_json: str | None = None) -> bool:
    """Recheck the persisted identity and current successful read at dispatch."""
    try:
        proof = _load(state)
        observed = datetime.fromtimestamp(float(mower["status_timestamp_ms"]) / 1000, timezone.utc)
        return bool(
            (expected_json is None or state.irrigation_park_hold_json == expected_json)
            and _station(state, mower, now_utc)
            and proof.get("status") == "HELD"
            and proof.get("binding") == _binding(state, mower)
            and timedelta(0) <= now_utc - _time(proof["checked_at_utc"])
            <= timedelta(seconds=MAX_READ_GAP_SECONDS)
            and observed >= _time(proof["source_at_utc"])
        )
    except (KeyError, TypeError, ValueError, OverflowError, OSError):
        return False


def prepare_manual_start(previous: AutomationState, requested: AutomationState,
                         mower: Mapping[str, Any], *, now_utc: datetime) -> AutomationState:
    """Consume server-held park evidence in the same CAS as one APP request."""
    if not valid(previous, mower, now_utc=now_utc) or unresolved_device_sends(previous):
        return requested
    proof = _load(previous)
    proof.update(status="START_READY", binding=_binding(requested, mower),
                 issued_at_utc=now_utc.isoformat(),
                 expires_at_utc=(now_utc + timedelta(seconds=120)).isoformat(),
                 request_id=requested.operator_request_session_id,
                 request_epoch=requested.operator_request_session_epoch)
    candidate = replace(requested, irrigation_park_hold_json=_dump(proof))
    return candidate if start_valid(candidate, mower, now_utc=now_utc) else requested


def start_valid(state: AutomationState, mower: Mapping[str, Any], *, now_utc: datetime) -> bool:
    """Only the admitted APP start may use this proof; never a water release.

    The proof is not renewed. A changed request, failed read, device departure,
    unresolved write or expired handoff revokes it, including after OAuth.
    """
    try:
        proof = _load(state)
        session = json.loads(state.manual_session_json or "null")
        observed = datetime.fromtimestamp(float(mower["status_timestamp_ms"]) / 1000, timezone.utc)
        issued = _time(proof["issued_at_utc"])
        pending = state.mower_start_pending_since_utc
        return bool(
            proof.get("status") == "START_READY" and issued <= now_utc < _time(proof["expires_at_utc"])
            and proof["binding"] == _binding(state, mower)
            and isinstance(session, dict) and session.get("kind") == "START"
            and session.get("status") == "PENDING" and session.get("source") == "APP"
            and session.get("session_id") == proof["request_id"] == state.operator_request_session_id
            and session.get("epoch") == proof["request_epoch"] == state.operator_request_session_epoch
            and session.get("mower_id") == mower.get("mower_id")
            and state.operator_request_id == proof["request_id"]
            and state.operator_request_action == "START_MOWING" and state.operator_request_status == "PENDING"
            and state.parked_by_automation and state.park_confirmed_utc
            and not state.maintenance_mode and not state.continuous_mowing_owned
            and not unresolved_device_sends(state)
            and (pending is None or (
                issued <= _time(pending) <= now_utc
                and state.mower_start_pending_session_id == proof["request_id"]
                and state.mower_start_pending_session_epoch == proof["request_epoch"]))
            and mower.get("connected") is True and mower.get("mode") == "HOME"
            and mower.get("activity") in {"PARKED_IN_CS", "CHARGING"}
            and mower.get("state") in {"RESTRICTED", "IN_OPERATION", "PAUSED"}
            and type(mower.get("error_code")) is int and mower["error_code"] == 0
            and _time(proof["source_at_utc"]) <= observed <= now_utc + timedelta(seconds=30)
        )
    except (KeyError, TypeError, ValueError, OverflowError, OSError, DeviceSendBlocked):
        return False


def observe(state: AutomationState, mower: Mapping[str, Any], *, now_utc: datetime,
            event_fresh: bool, confirmation_minutes: int = 1,
            required_observations: int = 2) -> tuple[AutomationState, dict]:
    """Project a proof over direct reads; caller persists it with its normal CAS.

    Invalidation leaves a watermark: the same old vendor event cannot revive
    a proof after an outage or intervening manual action.
    """
    now = now_utc
    if start_valid(state, mower, now_utc=now):
        return state, {"status": "START_READY", "held": False, "reason": "MANUAL_START_PENDING"}
    reason = None
    try:
        proof = _load(state)
    except (TypeError, ValueError):
        proof = {}
        reason = "PROOF_INVALID"
    if reason is None and not _station(state, mower, now):
        reason = "STATION_OR_CONTROL_CHANGED"
    elif reason is None and proof.get("status") in {"CANDIDATE", "HELD"}:
        try:
            gap = now - _time(proof["checked_at_utc"])
            event = datetime.fromtimestamp(float(mower["status_timestamp_ms"]) / 1000, timezone.utc)
            if not timedelta(0) < gap <= timedelta(seconds=MAX_READ_GAP_SECONDS):
                reason = "READ_CONTINUITY_LOST"
            elif proof.get("binding") != _binding(state, mower):
                reason = "CONTROL_GENERATION_CHANGED"
            elif event < _time(proof["source_at_utc"]):
                reason = "STATUS_MOVED_BACKWARDS"
        except (KeyError, TypeError, ValueError, OverflowError, OSError):
            reason = "PROOF_INVALID"
    if reason:
        proof = {"version": 1, "status": "INVALID", "not_before_utc": now.isoformat(), "reason": reason}
    else:
        event = datetime.fromtimestamp(float(mower["status_timestamp_ms"]) / 1000, timezone.utc)
        if proof.get("status") == "HELD":
            proof.update(checked_at_utc=now.isoformat(), source_at_utc=event.isoformat())
        elif proof.get("status") == "CANDIDATE":
            observations = int(proof.get("observations", 0)) + int(event_fresh)
            proof.update(checked_at_utc=now.isoformat(), source_at_utc=event.isoformat(), observations=observations)
            if (event_fresh and observations >= required_observations
                and now - _time(proof["since_utc"]) >= timedelta(minutes=confirmation_minutes)):
                proof["status"] = "HELD"
        elif event_fresh and (not proof.get("not_before_utc") or event >= _time(proof["not_before_utc"])):
            proof = {"version": 1, "status": "CANDIDATE", "binding": _binding(state, mower),
                     "since_utc": now.isoformat(), "checked_at_utc": now.isoformat(),
                     "source_at_utc": event.isoformat(), "observations": 1}
    projected = replace(state, irrigation_park_hold_json=_dump(proof))
    return projected, {"status": proof.get("status", "WAITING_FOR_FRESH_EVENT"),
                       "reason": proof.get("reason"), "read_max_gap_seconds": MAX_READ_GAP_SECONDS,
                       "confirmed_since_utc": proof.get("since_utc"),
                       "current_read_utc": proof.get("checked_at_utc"),
                       "held": valid(projected, mower, now_utc=now)}
