"""Read-only coordination inputs from the existing config and statistics query.

The observation buffer is only a convenience for two independent dock samples.
A restart deliberately requires new samples. It holds no request or authority;
the controller must reserve and revalidate against its persistent CAS state.
"""
from __future__ import annotations

import copy
import hashlib
import json
import threading
from collections import deque
from datetime import datetime, timezone
from typing import Any, Mapping

from daily_safety_report import estimate_charging_end
from mower.coordination_request import canonical_schedule_id
from mower.coordination_shadow import compare_charging_window
from mower.statistics_cache import get_dashboard_statistics

MAX_NEEDS = 64
MAX_APPROVAL_BYTES = 32768
_OBSERVATION_LOCK = threading.Lock()
_OBSERVATIONS: dict[tuple[str, ...], deque] = {}


def capture_enabled(environment: Mapping[str, str]) -> bool:
    return any(str(environment.get(key, "")).strip().lower() in {"true", "1", "yes", "on"}
               for key in ("COORDINATION_SHADOW_CAPTURE_ENABLED", "COORDINATION_EXECUTION_ENABLED"))


def _time(value: Any) -> datetime:
    if not isinstance(value, str):
        raise ValueError("timestamp required")
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("timezone required")
    return parsed.astimezone(timezone.utc)


def _previous_observation(cycle: Mapping[str, Any], environment: Mapping[str, str]):
    now = _time(cycle["executed_at_utc"])
    mower = cycle["details"]["mower"]
    stamp = mower.get("status_timestamp_ms")
    if not mower.get("mower_id") or type(stamp) is not int:
        return None
    key = tuple(str(environment.get(name, "")) for name in (
        "SSV53_STORAGE_ACCOUNT_URL", "SSV53_STATE_TABLE_NAME", "SSV53_APP_INSIGHTS_APP_ID")) + (str(mower["mower_id"]),)
    sample = {"executed_at_utc": now.isoformat(), "details": {"mower": copy.deepcopy(mower)}}
    with _OBSERVATION_LOCK:
        if key not in _OBSERVATIONS:
            # This is a read-only warm-up buffer, so eviction can only delay a
            # new proposal; it cannot erase persistent deduplication or a stop.
            if len(_OBSERVATIONS) >= 8:
                _OBSERVATIONS.pop(next(iter(_OBSERVATIONS)))
            _OBSERVATIONS[key] = deque(maxlen=32)
        samples = _OBSERVATIONS[key]
        if samples and now < _time(samples[-1]["executed_at_utc"]):
            samples.clear()
        while samples and (now - _time(samples[0]["executed_at_utc"])).total_seconds() > 180:
            samples.popleft()
        previous = next((item for item in reversed(samples)
                         if 60 <= (now - _time(item["executed_at_utc"])).total_seconds() <= 180
                         and item["details"]["mower"]["status_timestamp_ms"] < stamp), None)
        # Repeated cached source samples never acquire a new observation time.
        # Also retain the oldest sample of a ten-second bucket despite UI polls.
        if (not samples or (stamp > samples[-1]["details"]["mower"]["status_timestamp_ms"]
                            and (now - _time(samples[-1]["executed_at_utc"])).total_seconds() >= 10)):
            samples.append(sample)
        return copy.deepcopy(previous)


def prepare_coordination_inputs(*, cycle: Mapping[str, Any], config: Mapping[str, Any],
                                environment: Mapping[str, str], source_fresh: bool,
                                statistics_loader=get_dashboard_statistics) -> dict[str, Any] | None:
    """Return evidence only, without any device, request or control-state write.

    Approval is maintained in the already authenticated, atomic runtime config.
    No browser payload or telemetry log may create a watering need. Invalid or
    missing input blocks this optional optimization, not unrelated occupancy.
    """
    if not capture_enabled(environment):
        return None
    result: dict[str, Any] = {
        "schema_version": 1, "available": False, "needs": [], "need": None,
        "previous_cycle": None, "charging_end_estimate": None,
        "permission_to_start": False, "blockers": [],
    }
    try:
        if source_fresh is not True:
            result["blockers"].append("APPROVAL_SOURCE_NOT_FRESH")
            return result
        document = config.get("coordination")
        if not isinstance(document, dict) or document.get("enabled") is not True:
            result["blockers"].append("APPROVED_COORDINATION_CONFIG_DISABLED")
            return result
        raw = json.dumps(document, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
        needs = document.get("needs")
        if (type(document.get("schema_version")) is not int or document["schema_version"] != 1
                or len(raw) > MAX_APPROVAL_BYTES or not isinstance(needs, list) or len(needs) > MAX_NEEDS):
            raise ValueError("Invalid approval document")
        now = _time(cycle["executed_at_utc"])
        mower = cycle["details"]["mower"]
        identifiers, plans = set(), set()
        for need in needs:
            if (not isinstance(need, dict) or type(need.get("schema_version")) is not int
                    or need["schema_version"] != 1):
                raise ValueError("Invalid approval")
            identifier, plan = need.get("need_id"), need.get("source_plan_id")
            if (not isinstance(identifier, str) or not identifier.strip() or len(identifier) > 128
                    or not isinstance(plan, str) or len(plan) != 64
                    or any(char not in "0123456789abcdef" for char in plan)
                    or identifier in identifiers or plan in plans):
                raise ValueError("Ambiguous approval identity")
            identifiers.add(identifier)
            plans.add(plan)
            _time(need["valid_until_utc"])
        result.update(available=True, needs=copy.deepcopy(needs),
                      approval_document_sha256=hashlib.sha256(raw).hexdigest())
        previous = _previous_observation(cycle, environment)
        result["previous_cycle"] = previous
        actual_zones = cycle["details"].get("hydrawise", {}).get("zones")
        if not isinstance(actual_zones, list) or not actual_zones:
            result["blockers"].append("ORIGINAL_SCHEDULE_MISSING")
            return result
        source_plan_id = canonical_schedule_id(actual_zones)
        matching = [need for need in needs if need.get("source_plan_id") == source_plan_id
                    and need.get("mower_id") == mower.get("mower_id")
                    and _time(need["valid_until_utc"]) >= now]
        if len(matching) != 1:
            result["blockers"].append("NO_UNIQUE_APPROVED_NEED_FOR_SOURCE_PLAN")
            return result
        result["need"] = copy.deepcopy(matching[0])
        # Avoid even the existing statistics query until station, occupancy,
        # water and authorization checks leave only the estimate unresolved.
        preliminary = compare_charging_window(
            cycle=cycle, need=matching[0], previous_cycle=previous,
            charging_end_estimate=None, minimum_lead_minutes=45,
        )
        if preliminary["blockers"] != ["EMPIRICAL_CHARGING_END_UNKNOWN"]:
            result["blockers"].extend(preliminary["blockers"])
            return result
        try:
            statistics = statistics_loader(environment, now)
            estimate = estimate_charging_end(statistics.get("_chargingEvidence"), mower, now)
        except Exception:
            # Analytics is optional. Failure must not stop the primary safety
            # controller or release any existing watering/drying reservation.
            estimate = None
        result["charging_end_estimate"] = estimate
        if estimate is None:
            result["blockers"].append("EMPIRICAL_CHARGING_END_UNKNOWN")
        return result
    except (AttributeError, KeyError, TypeError, ValueError, OverflowError):
        result.update(available=False, needs=[], need=None, charging_end_estimate=None)
        result["blockers"].append("INVALID_COORDINATION_INPUT")
        return result
