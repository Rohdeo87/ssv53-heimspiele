"""Admission for an already moving mower; never sends or invents a device command."""
from datetime import datetime, timezone
from mower.manual_control_api import manual_context
from mower.manual_session import load_manual_session
from mower.device_send_guard import unresolved_device_sends


def eligible(state, details, environment, now_utc):
    if str(environment.get("MOWER_AUTOMATIC_TAKEOVER_ENABLED", "false")).lower() != "true":
        return False
    mower = details.get("mower") or {}
    area = mower.get("target_work_area") or {}
    timestamp = mower.get("status_timestamp_ms")
    if type(timestamp) not in {int, float}:
        return False
    observed = datetime.fromtimestamp(timestamp / 1000, timezone.utc)
    if observed > now_utc:
        return False
    if state.continuous_mowing_takeover_hold_utc and observed <= datetime.fromisoformat(state.continuous_mowing_takeover_hold_utc.replace("Z", "+00:00")):
        return False
    if (mower.get("activity") not in {"MOWING", "LEAVING"}
        or mower.get("state") != "IN_OPERATION"
        or mower.get("mode") not in {"MAIN", "MAIN_AREA"}
        or mower.get("override_action") not in {"NOT_ACTIVE", "FORCE_MOW", "NO_SOURCE", "NONE", ""}
        or type(area.get("id")) is not int or area["id"] <= 0 or area.get("enabled") is not True
        or state.maintenance_mode or state.mower_start_pending_since_utc
        or state.irrigation_phase is not None
        or state.operator_occupancy_override_key or state.operator_occupancy_override_until_utc):
        return False
    # A permission granting any exception remains manual until its return.
    session = load_manual_session(state)
    active = session if session and session["status"] != "ENDED" else None
    if state.continuous_mowing_owned and active is None:
        return False
    if active and (active["kind"] != "START" or active["status"] != "ACTIVE"
                   or active["confirmed_block_keys"] or active["confirmed_dry_until_utc"]
                   or active["water_conflict_id"] or active["water_choice"]):
        return False
    if state.parked_by_automation and not state.automation_restart_allowed:
        return False
    if state.operator_request_status == "PENDING" and not (
        active and state.operator_request_action == "START_MOWING"
        and state.operator_request_session_id == active["session_id"]
        and state.operator_request_session_epoch == active["epoch"]
    ):
        return False
    if unresolved_device_sends(state):
        return False
    public, context, conflict = manual_context(state, details, environment, now_utc)
    return bool(public["canStart"] and not context["forbidden"]
                and not context["block_keys"] and not context["dry_until_utc"]
                and not conflict.get("required"))
