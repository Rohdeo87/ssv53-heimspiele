"""Command-free input capture and comparison of an approved watering occurrence.

No sender, network client, schedule mutation or control-state writer belongs in
this module. A comparison estimates freed field time, never productive mowing.
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable, Mapping

from mower.adaptive_planner import _occupancy_blocks


UTC = timezone.utc


def _instant(value: Any) -> datetime:
    if not isinstance(value, str):
        raise ValueError("A dated input is required")
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("An explicit timezone is required")
    return parsed.astimezone(UTC)


def _union(intervals: Iterable[tuple[datetime, datetime]]) -> float:
    ordered = sorted(intervals)
    if not ordered:
        return 0.0
    start, end = ordered[0]
    seconds = 0.0
    for begin, finish in ordered[1:]:
        if begin <= end:
            end = max(end, finish)
        else:
            seconds += (end - start).total_seconds()
            start, end = begin, finish
    return (seconds + (end - start).total_seconds()) / 60


def capture_planning_inputs(*, now_utc: datetime, blocks, runtime_inputs,
                            complete_from: datetime, complete_until: datetime,
                            special_available: bool, state) -> dict[str, Any]:
    """Copy an already resolved full occupancy horizon; never poll a source.

    Old logs only contained six upcoming blocks. They cannot prove completeness
    for this comparison. Missing state/source evidence is explicitly unknown.
    """
    valid_source = (
        runtime_inputs.source_kind in {"azure_blob", "azure_blob_cache"}
        and bool(runtime_inputs.manifest_etag and runtime_inputs.published_at_utc)
        and not runtime_inputs.fallback_used
    )
    occupancy = []
    for block in blocks:
        # Reuse the existing complete-provenance check; never trust a partial
        # child list to remove a binding parent's interval. Unknown sources hold.
        parts = set(block.source.split("+"))
        leaves = _occupancy_blocks(block) if parts <= {"match", "training", "special", "irrigation"} else [block]
        occupancy.extend({"start": leaf.start.isoformat(), "end": leaf.end.isoformat(), "source": leaf.source}
                         for leaf in leaves)
    return {
        "schema_version": 1, "captured_at_utc": now_utc.isoformat(),
        "complete_from_utc": complete_from.isoformat(), "complete_until_utc": complete_until.isoformat(),
        "occupancy_complete": valid_source and special_available,
        "source_published_at_utc": runtime_inputs.published_at_utc,
        "source_manifest_etag": runtime_inputs.manifest_etag,
        "occupancy": occupancy,
        "state_available": state is not None,
        "manual_stop": (state is None or state.maintenance_mode
                        or getattr(state, "last_decision_code", None) == "OPERATOR_PARK_HOLD"
                        or (getattr(state, "parked_by_automation", False)
                            and not getattr(state, "automation_restart_allowed", False))),
        "uncertain_start": state is None or bool(state.mower_start_pending_since_utc),
        "execution_available": False,
    }


def compare_charging_window(*, cycle: Mapping[str, Any], need: Mapping[str, Any],
                            charging_end_estimate: Mapping[str, Any] | None,
                            previous_cycle: Mapping[str, Any] | None = None,
                            minimum_gain_minutes: int = 10, drying_minutes: int = 150,
                            minimum_lead_minutes: int = 0,
                            fixed_start_utc: str | None = None) -> dict[str, Any]:
    """Compare the unchanged occurrence with one charging-event proposal.

    The charge estimate must come from the existing empirical estimator. Fresh
    consecutive station observations and approved need/geometry are required.
    Holding the mower and suppressing the original program remain execution
    prerequisites even when a useful proposal exists.
    """
    result: dict[str, Any] = {
        "schema_version": 1, "shadow_only": True, "execution_available": False,
        "permission_to_start": False, "status": "BLOCKED", "blockers": [],
        "potential_freed_field_minutes": None, "productive_mowing_gain_minutes": None,
        "selected_start_utc": None, "water_minutes_unchanged": None,
    }
    blockers = result["blockers"]
    try:
        if (type(drying_minutes) is not int or drying_minutes < 150
                or type(minimum_gain_minutes) is not int or minimum_gain_minutes < 1
                or type(minimum_lead_minutes) is not int or not 0 <= minimum_lead_minutes <= 120):
            raise ValueError("Conservative timing required")
        now = _instant(cycle["executed_at_utc"])
        details = cycle["details"]
        captured = details.get("coordination_shadow_input") or {}
        if captured.get("schema_version") != 1:
            blockers.append("COMPLETE_INPUT_CAPTURE_MISSING")
            return result
        if not 0 <= (now - _instant(captured["captured_at_utc"])).total_seconds() <= 180:
            blockers.append("CAPTURE_NOT_CURRENT")
        if (captured.get("occupancy_complete") is not True or captured.get("state_available") is not True):
            blockers.append("OCCUPANCY_OR_STATE_UNKNOWN")
        if captured.get("manual_stop") is not False:
            blockers.append("MANUAL_STOP_OR_UNKNOWN")
        runtime_state = details.get("automation_state") or {}
        if (runtime_state.get("maintenance_mode") is True or cycle.get("decision_code") == "OPERATOR_PARK_HOLD"
                or ("operator" in str(runtime_state.get("automation_park_source") or "").split("+")
                    and runtime_state.get("automation_restart_allowed") is not True)):
            blockers.append("MANUAL_STOP")
        if captured.get("uncertain_start") is not False or cycle.get("command_sent") is not False:
            blockers.append("DEVICE_ACTION_PENDING_OR_UNCERTAIN")
        mower = details.get("mower") or {}
        previous = (previous_cycle or {}).get("details", {}).get("mower", {})
        prior_at = _instant((previous_cycle or {}).get("executed_at_utc")) if previous_cycle else None
        good_dock = prior_at is not None and 60 <= (now - prior_at).total_seconds() <= 180
        for item, seen in ((mower, now), (previous, prior_at)):
            stamp = item.get("status_timestamp_ms")
            good_dock = good_dock and (
                item.get("activity") == "CHARGING" and item.get("connected") is True
                and type(item.get("error_code")) is int and item["error_code"] == 0
                and item.get("state") == "IN_OPERATION" and type(stamp) is int
                and 0 <= seen.timestamp() - stamp / 1000 <= 180
                and bool(item.get("mower_id")) and item.get("mower_id") == mower.get("mower_id")
            )
        if (not good_dock or mower.get("status_timestamp_ms") <= previous.get("status_timestamp_ms")):
            blockers.append("TWO_FRESH_CHARGING_OBSERVATIONS_REQUIRED")
        water = details.get("hydrawise") or {}
        safety = water.get("safety") or {}
        if (safety.get("available") is not True or safety.get("fresh") is not True
                or safety.get("relay_set_valid") is not True or safety.get("active_zone_count") != 0
                or type(safety.get("active_zone_count")) is not int
                or not 0 <= (now - _instant(safety.get("observed_at_utc"))).total_seconds() <= 180):
            blockers.append("WATER_STATE_UNKNOWN_OR_ACTIVE")
        if (need.get("schema_version") != 1 or need.get("required") is not True
                or need.get("timing_window_approved") is not True or need.get("station_and_paths_checked") is not True
                or not isinstance(need.get("need_id"), str) or not need["need_id"].strip()
                or not isinstance(need.get("demand_reference"), str) or not need["demand_reference"].strip()
                or need.get("mower_id") != mower.get("mower_id")):
            blockers.append("APPROVED_NEED_AND_GEOMETRY_REQUIRED")
        original = _instant(need["original_start_utc"])
        earliest, latest = _instant(need["earliest_start_utc"]), _instant(need["latest_start_utc"])
        if not earliest <= original <= latest or _instant(need["valid_until_utc"]) < latest:
            blockers.append("APPROVAL_WINDOW_INVALID")
        zones = need["zones"]
        if (not isinstance(zones, list) or not zones or len(zones) > 16
                or any(type(z.get("relay_id")) is not int or z["relay_id"] <= 0
                       or type(z.get("run_seconds")) is not int or not 1 <= z["run_seconds"] <= 7200 for z in zones)
                or len({z["relay_id"] for z in zones}) != len(zones)):
            raise ValueError("Invalid approved zone need")
        actual = water.get("zones") or []
        # Bind the authorization to the actual schedule, including order and
        # duration. A move, rain reduction or changed zone must be reviewed.
        expected = [(z["relay_id"], z["run_seconds"], _instant(z["scheduled_start_utc"])) for z in zones]
        observed = [(z.get("relay_id"), z.get("run_seconds"), _instant(z["scheduled_start_utc"])) for z in actual]
        if expected != observed or min(start for _, _, start in expected) != original:
            blockers.append("ORIGINAL_SCHEDULE_CHANGED")
        estimate = charging_end_estimate or {}
        if (estimate.get("estimated") is not True or estimate.get("source") != "OBSERVED_COMPLETED_CHARGING_SECTIONS"
                or type(estimate.get("sampleCount")) is not int or estimate["sampleCount"] < 3
                or type(estimate.get("daysCovered")) is not int or estimate["daysCovered"] < 2):
            blockers.append("EMPIRICAL_CHARGING_END_UNKNOWN")
        if blockers:
            return result
        charge_end = _instant(estimate["at"])
        available_start = max(now + timedelta(minutes=minimum_lead_minutes), earliest)
        if fixed_start_utc is None:
            candidate = available_start.replace(second=0, microsecond=0)
            if candidate < available_start:
                candidate += timedelta(minutes=1)
        else:
            # A persisted reservation must be checked at its exact timestamp;
            # validating a newly shifted slot cannot release the old one.
            candidate = _instant(fixed_start_utc)
        if (not now < charge_end or candidate < available_start or candidate >= charge_end
                or candidate >= original or not earliest <= candidate <= latest):
            blockers.append("NO_UPCOMING_WINDOW_DURING_CHARGING")
            return result
        if original - candidate > timedelta(minutes=120):
            blockers.append("ADVANCE_LIMIT_EXCEEDED")
            return result
        # Keep the source's sequential program span as well as every zone's
        # duration; no invented back-to-back water program is substituted.
        ordered = sorted((start, start + timedelta(seconds=seconds)) for _, seconds, start in expected)
        if any(right[0] < left[1] for left, right in zip(ordered, ordered[1:])):
            blockers.append("UNEXPECTED_ZONE_OVERLAP")
            return result
        span = max(end for _, end in ordered) - original
        old_release = original + span + timedelta(minutes=drying_minutes)
        new_release = candidate + span + timedelta(minutes=drying_minutes)
        blocks = [(_instant(b["start"]), _instant(b["end"])) for b in captured["occupancy"]]
        if any(start >= end for start, end in blocks):
            raise ValueError("Invalid occupancy interval")
        if (_instant(captured["complete_from_utc"]) > candidate
                or _instant(captured["complete_until_utc"]) < max(old_release, charge_end)):
            blockers.append("OCCUPANCY_HORIZON_TOO_SHORT")
            return result
        if any(start < new_release and end > candidate for start, end in blocks):
            blockers.append("PROPOSED_WATER_OR_DRYING_OVERLAPS_SPORT")
            return result
        end = max(old_release, charge_end)
        common = [(now, charge_end)] + [(max(start, now), min(finish, end)) for start, finish in blocks
                                      if start < end and finish > now]
        baseline = _union(common + [(original, old_release)])
        proposed = _union(common + [(candidate, new_release)])
        gain = max(0.0, baseline - proposed)
        result.update({
            "need_id": need["need_id"], "input_sha256": hashlib.sha256(json.dumps(
                {"cycle": cycle, "need": need, "estimate": estimate,
                 "minimum_lead_minutes": minimum_lead_minutes,
                 "fixed_start_utc": fixed_start_utc}, sort_keys=True).encode()).hexdigest(),
            "potential_freed_field_minutes": round(gain, 2),
            "water_minutes_unchanged": sum(z["run_seconds"] for z in zones) / 60,
            "baseline_start_utc": original.isoformat(), "baseline_dry_until_utc": old_release.isoformat(),
            "proposed_dry_until_utc": new_release.isoformat(), "charging_end_estimated_utc": charge_end.isoformat(),
            "estimated": True,
            "interpretation": "Upper bound on freed field time; additional energy, travel and minimum mowing windows are not deducted.",
            "execution_prerequisites": ["MOWER_RESTART_INHIBITION_CONFIRMED", "ORIGINAL_SCHEDULE_SUPPRESSION_CONFIRMED",
                                         "REVALIDATE_IMMEDIATELY_BEFORE_START", "SUPERVISED_PILOT_ACCEPTED"],
        })
        if gain >= minimum_gain_minutes:
            result.update(status="SHADOW_PROPOSAL", selected_start_utc=candidate.isoformat())
        else:
            blockers.append("GAIN_BELOW_THRESHOLD")
    except (KeyError, TypeError, ValueError, AttributeError, OverflowError):
        result.update(status="BLOCKED", selected_start_utc=None, potential_freed_field_minutes=None)
        blockers.append("INVALID_OR_INCOMPLETE_INPUT")
    return result
