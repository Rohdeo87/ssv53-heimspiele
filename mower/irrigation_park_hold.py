"""Continue an owned, confirmed HOME park across unchanged vendor events.

The operator must end/confirm irrigation before starting in the vendor app or
at the mower. This is not a hardware interlock. Only the irrigation gate may
use this proof; mower starts still require fresh device events.
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
    elif raw.get("status") in {"CANDIDATE", "HELD"}:
        since, checked = _time(raw.get("since_utc")), _time(raw.get("checked_at_utc"))
        _time(raw.get("source_at_utc"))
        count = raw.get("observations")
        binding = raw.get("binding")
        if (type(count) is not int or count < 1 or checked < since
            or not isinstance(binding, str) or len(binding) != 64
            or any(c not in "0123456789abcdef" for c in binding)
            or (raw["status"] == "HELD" and (count < 2 or checked - since < timedelta(minutes=1)))):
            raise ValueError("Invalid park proof observations")
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


def observe(state: AutomationState, mower: Mapping[str, Any], *, now_utc: datetime,
            event_fresh: bool, confirmation_minutes: int = 1,
            required_observations: int = 2) -> tuple[AutomationState, dict]:
    """Project a proof over direct reads; caller persists it with its normal CAS.

    Invalidation leaves a watermark: the same old vendor event cannot revive
    a proof after an outage or intervening manual action.
    """
    now = now_utc
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
