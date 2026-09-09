"""Offline, command-free draft for one approved coordination proposal.

This module deliberately does not know the runtime state store, vendor APIs, or
the operator request validator.  It converts a freshly rechecked shadow result
into an auditable draft only.
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta, timezone
from typing import Any, Mapping

from mower.coordination_shadow import compare_charging_window


def _time(value: Any) -> datetime:
    if not isinstance(value, str):
        raise ValueError("timestamp required")
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("timestamp must include timezone")
    return parsed.astimezone(timezone.utc)


def _request_id(need_id: str, source_plan_id: str, selected: str,
                zones: list[dict[str, Any]]) -> str:
    payload = {
        "need_id": need_id, "source_plan_id": source_plan_id,
        "selected_start_utc": selected,
        "zones": zones,
    }
    return hashlib.sha256(json.dumps(payload, sort_keys=True,
                                     separators=(",", ":")).encode()).hexdigest()


def canonical_schedule_id(zones: list[Mapping[str, Any]]) -> str:
    """Stable identity for the exact source schedule, excluding annotations."""
    identity = [
        {"relay_id": z["relay_id"], "run_seconds": z["run_seconds"],
         "scheduled_start_utc": z["scheduled_start_utc"]}
        for z in zones
    ]
    return hashlib.sha256(json.dumps(identity, sort_keys=True,
                                     separators=(",", ":")).encode()).hexdigest()


def build_coordination_request(*, proposal: Mapping[str, Any], cycle: Mapping[str, Any],
                               need: Mapping[str, Any],
                               charging_end_estimate: Mapping[str, Any] | None,
                               previous_cycle: Mapping[str, Any] | None = None,
                               drying_minutes: int = 150,
                               minimum_lead_minutes: int = 0) -> dict[str, Any]:
    """Return a deterministic draft, or a blocked result with reasons.

    The supplied proposal is checked against a fresh comparison of the exact
    same inputs.  The output contains relative offsets, so a consumer cannot
    accidentally collapse gaps between the original zones.
    """
    blocked = {
        "schema_version": 1, "status": "BLOCKED", "permission_to_start": False,
        "execution_available": False, "queued_device_action": None, "blockers": [],
    }
    try:
        fresh = compare_charging_window(
            cycle=cycle, need=need, charging_end_estimate=charging_end_estimate,
            previous_cycle=previous_cycle, drying_minutes=drying_minutes,
            minimum_lead_minutes=minimum_lead_minutes,
        )
        if fresh.get("status") != "SHADOW_PROPOSAL":
            blocked["blockers"].extend(fresh.get("blockers") or ["SHADOW_PROPOSAL_REQUIRED"])
            return blocked
        if proposal.get("input_sha256") != fresh.get("input_sha256"):
            blocked["blockers"].append("PROPOSAL_INPUT_HASH_MISMATCH")
            return blocked
        selected = proposal.get("selected_start_utc")
        if selected != fresh.get("selected_start_utc"):
            blocked["blockers"].append("PROPOSAL_START_MISMATCH")
            return blocked
        if _time(selected) < _time(cycle["executed_at_utc"]) + timedelta(minutes=45):
            blocked["blockers"].append("EXISTING_CONSUMER_MINIMUM_LEAD")
            return blocked
        if _time(selected) > _time(need["valid_until_utc"]):
            blocked["blockers"].append("APPROVAL_EXPIRED")
            return blocked
        source_zones = (cycle.get("details") or {}).get("hydrawise", {}).get("zones")
        if not isinstance(source_zones, list) or not source_zones:
            blocked["blockers"].append("ORIGINAL_SCHEDULE_MISSING")
            return blocked
        original_start = min(_time(z["scheduled_start_utc"]) for z in source_zones)
        zones = []
        for zone in source_zones:
            start = _time(zone["scheduled_start_utc"])
            seconds = zone.get("run_seconds")
            relay = zone.get("relay_id")
            if type(relay) is not int or type(seconds) is not int or seconds < 1:
                raise ValueError("invalid original zone")
            zones.append({
                "relay_id": relay, "run_seconds": seconds,
                "offset_seconds": int((start - original_start).total_seconds()),
                "scheduled_start_utc": (_time(selected) + (start - original_start)).isoformat(),
            })
        need_id = need.get("need_id")
        source_plan_id = canonical_schedule_id(source_zones)
        if need.get("source_plan_id") != source_plan_id:
            blocked["blockers"].append("SOURCE_PLAN_ID_MISMATCH")
            return blocked
        if not isinstance(need_id, str) or not need_id.strip():
            blocked["blockers"].append("NEED_ID_REQUIRED")
            return blocked
        rid = _request_id(need_id, source_plan_id, selected, zones)
        return {
            "schema_version": 1, "status": "DRAFT", "permission_to_start": False,
            "execution_available": False, "queued_device_action": None,
            "request_id": rid, "need_id": need_id, "source_plan_id": source_plan_id,
            "selected_start_utc": selected,
            "valid_until_utc": need.get("valid_until_utc"),
            "drying_minutes": drying_minutes, "water_minutes_unchanged": fresh["water_minutes_unchanged"],
            "zones": zones,
            "required_consumer_gates": ["ORIGINAL_SCHEDULE_SUPPRESSION_CONFIRMED",
                                         "MOWER_RESTART_INHIBITION_CONFIRMED",
                                         "REVALIDATE_IMMEDIATELY_BEFORE_START"],
        }
    except (AttributeError, KeyError, TypeError, ValueError, OverflowError):
        blocked["blockers"].append("INVALID_OR_INCOMPLETE_INPUT")
        return blocked
