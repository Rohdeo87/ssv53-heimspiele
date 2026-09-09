"""Offline interval analysis of the sanitized Application Insights audit export.

No device imports, credentials or network calls. Values are telemetry estimates,
not proof of blade contact or physical occupancy. Gaps are never interpolated.
"""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path


def utc(value):
    parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("Timezone required")
    return parsed.astimezone(timezone.utc)


def read_export(payload):
    table = payload["tables"][0]
    columns = [column["name"] for column in table["columns"]]
    return [dict(zip(columns, row, strict=True)) for row in table["rows"]]


def summarize(rows, start, end, *, max_interval_seconds=90):
    if start.tzinfo is None or end.tzinfo is None:
        raise ValueError("An aware, positive evaluation interval is required")
    start, end = start.astimezone(timezone.utc), end.astimezone(timezone.utc)
    if start >= end:
        raise ValueError("An aware, positive evaluation interval is required")
    observations = {utc(row["timestamp"]): row for row in rows}
    ordered = sorted((when, row) for when, row in observations.items() if start <= when < end)
    activities, idle_reasons, decisions, errors = Counter(), Counter(), Counter(), Counter()
    transitions = 0
    observed = 0.0
    irrigation = 0.0
    sent = 0
    last_activity = None
    last_time = None
    for index, (when, row) in enumerate(ordered):
        following = ordered[index + 1][0] if index + 1 < len(ordered) else end
        gap = (following - when).total_seconds()
        # One sample describes at most a nominal minute. If the next observation
        # is late, all time beyond that is unknown, not silently counted as work.
        duration = min(gap, 60.0) if gap > max_interval_seconds else gap
        duration = max(0.0, min(duration, (end - when).total_seconds()))
        activity = str(row.get("activity") or "UNKNOWN")
        error = int(row.get("error") or 0)
        if error or row.get("state") in {"ERROR", "FATAL_ERROR"}:
            classification = "FAULT"
        elif activity == "MOWING":
            classification = "PRODUCTIVE_MOWING_ESTIMATE"
        elif activity == "LEAVING":
            classification = "TRAVEL_TO_PITCH"
        elif activity == "GOING_HOME":
            classification = "RETURN_HOME"
        elif activity == "CHARGING":
            classification = "CHARGING"
        elif activity == "PARKED_IN_CS":
            classification = "PARKED"
        elif activity == "STOPPED_IN_GARDEN":
            classification = "PAUSED"
        else:
            classification = "UNKNOWN_STATE"
        activities[classification] += duration / 60
        observed += duration / 60
        code = str(row.get("code") or "UNKNOWN")
        decisions[code] += 1
        if error:
            errors[str(error)] += duration / 60
        if classification not in {"PRODUCTIVE_MOWING_ESTIMATE", "TRAVEL_TO_PITCH", "RETURN_HOME", "CHARGING"}:
            # Exactly one primary reason per interval; concurrent blockers are
            # diagnostic, not additional idle minutes.
            idle_reasons["DEVICE_ERROR" if error else code] += duration / 60
        if row.get("hydraAvailable") is True and row.get("hydraFresh") is True and int(row.get("activeZones") or 0) > 0:
            irrigation += duration / 60
        if last_activity is not None and last_time is not None and (when - last_time).total_seconds() <= max_interval_seconds and activity != last_activity:
            transitions += 1
        last_activity, last_time = activity, when
        sent += row.get("sent") is True
    total = (end - start).total_seconds() / 60
    return {
        "schema_version": 1, "basis": "telemetry_interval_estimate_not_physical_proof",
        "from_utc": start.isoformat(), "until_utc": end.isoformat(),
        "rows": len(ordered), "period_minutes": round(total, 2),
        "observed_minutes": round(observed, 2), "unobserved_minutes": round(total - observed, 2),
        "coverage_percent": round(observed / total * 100, 2),
        "minutes_by_activity": {k: round(v, 2) for k, v in sorted(activities.items())},
        "idle_minutes_by_primary_reason": {k: round(v, 2) for k, v in idle_reasons.most_common()},
        "minutes_by_error_code": {k: round(v, 2) for k, v in errors.most_common()},
        "decisions": dict(decisions.most_common()), "commands_reported_sent": sent,
        "observed_activity_transitions": transitions,
        "irrigation_active_minutes_estimate": round(irrigation, 2),
        "water_volume_litres": None, "avoidable_idle_minutes": None,
        "suitable_window_utilization": None, "missed_water_requirements": None,
        "limitations": [
            "MOWING is an API state; blade contact/area quality was not independently measured.",
            "No extrapolation across missing samples; interval boundaries have polling uncertainty.",
            "Concurrent irrigation and charging overlap intentionally; do not add these totals.",
            "No flow sensor, validated water requirement or complete blocker history in this export.",
            "Command sent does not prove receipt, execution or absence of late commands."
        ],
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", type=Path)
    parser.add_argument("--start", required=True)
    parser.add_argument("--end", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = summarize(read_export(json.loads(args.input.read_text(encoding="utf-8-sig"))), utc(args.start), utc(args.end))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(json.dumps({k: result[k] for k in ("rows", "coverage_percent", "minutes_by_activity")}))


if __name__ == "__main__":
    main()
