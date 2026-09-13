"""Read-only runtime reconstruction; never infer water from a schedule alone."""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone


def utc(value):
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return parsed.astimezone(timezone.utc) if parsed.tzinfo else None
    except (TypeError, ValueError):
        return None


def decoded(value, default):
    if isinstance(value, type(default)):
        return value
    try:
        result = json.loads(value)
        return result if isinstance(result, type(default)) else default
    except (TypeError, ValueError):
        return default


def ids(value):
    return {int(v) for v in decoded(value, []) if str(v).isdigit()}


def normalize_rows(rows):
    """Deduplicate the same cycle, not distinct observations in one minute.

    Old journal rows have no countdown/action. They must not erase the richer
    trace for the same cycle. Ordering of either source is immaterial.
    """
    by_cycle = {}
    for row in rows:
        stamp = utc(row.get("executed_at_utc")) or utc(row.get("timestamp"))
        if stamp is None:
            continue
        target = by_cycle.setdefault(stamp, {})
        for key, value in row.items():
            if value is not None and value != "":
                # Rich fields are identical in new journal and trace records.
                if key not in target or value not in ("[]", "{}"):
                    target[key] = value
        target["timestamp"] = stamp.isoformat()
    return [by_cycle[key] for key in sorted(by_cycle)]


def union_seconds(intervals, start=None, end=None):
    merged = []
    for a, b in sorted(intervals):
        a = max(a, start) if start else a
        b = min(b, end) if end else b
        if b <= a:
            continue
        if merged and a <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(b, merged[-1][1]))
        else:
            merged.append((a, b))
    return sum((b - a).total_seconds() for a, b in merged)


def runtime_segments(rows, *, period_start=None, period_end=None):
    """Segments (relay, plan, start, end, estimated).

    A full duration needs an accepted start, a continuous countdown with a
    stable end, a fresh clear observation at that end, and subsequent zone
    completion. Command duration only anchors the start after these checks.
    Other samples contribute bounded estimates, never the entire planned run.
    """
    samples = []
    for row in normalize_rows(rows):
        cycle = utc(row["timestamp"])
        observed = utc(row.get("hydrawise_observed_at_utc")) or cycle
        if abs((observed - cycle).total_seconds()) > 180:
            continue
        if any(str(row.get(k)).lower() == "false" for k in
               ("hydrawise_available", "hydrawise_fresh")):
            continue
        samples.append((observed, cycle, row))
    samples.sort(key=lambda item: item[:2])
    pending = {}
    episodes = {}
    finished = []

    def finish(relay, clear=None):
        episode = episodes.pop(relay)
        episode["clear"] = clear
        finished.append(episode)

    for index, (observed, cycle, row) in enumerate(samples):
        active = ids(row.get("active_relay_ids"))
        for relay in list(episodes):
            if relay not in active:
                finish(relay, observed if all(str(row.get(k)).lower() == "true" for k in
                       ("hydrawise_available", "hydrawise_fresh")) else None)
        action = decoded(row.get("irrigation_action"), {})
        if (action.get("type") == "StartZone"
                and row.get("decision_code") == "IRRIGATION_ZONE_START_SENT"
                and str(row.get("command_sent")).lower() == "true"
                and decoded(action.get("response"), {}).get("message_type") == "info"):
            try:
                relay, seconds = int(action["relay_id"]), int(action["run_seconds"])
                if 1 <= seconds <= 7200:
                    pending[relay] = (cycle, seconds)
            except (KeyError, TypeError, ValueError):
                pass
        zones = {int(z["relay_id"]): z for z in decoded(row.get("zone_observations"), [])
                 if isinstance(z, dict) and str(z.get("relay_id")).isdigit()}
        for relay in active:
            if relay in episodes and (observed - episodes[relay]["last"]).total_seconds() > 90:
                finish(relay)  # A data gap must not become watered time.
            if relay not in episodes:
                episodes[relay] = {"relay": relay, "plan": row.get("irrigation_plan_id") or "",
                    "first": observed, "last": observed, "samples": [], "ends": [],
                    "start": pending.pop(relay, None), "consistent": True}
            ep = episodes[relay]
            ep["last"] = observed
            zone = zones.get(relay, {})
            predicted = utc(zone.get("scheduled_end_utc"))
            try:
                remaining = int(zone.get("run_seconds"))
            except (TypeError, ValueError):
                remaining = 0
            valid = (all(str(row.get(k)).lower() == "true" for k in
                         ("hydrawise_available", "hydrawise_fresh"))
                     and zone.get("running") is True and zone.get("valid") is True
                     and predicted is not None and 0 < remaining <= 7200
                     and abs((predicted - observed).total_seconds() - remaining) <= 3)
            ep["consistent"] = ep["consistent"] and valid
            if valid:
                ep["ends"].append(predicted)
            next_time = samples[index + 1][0] if index + 1 < len(samples) else observed + timedelta(seconds=60)
            end = min(next_time, observed + timedelta(seconds=60))
            if valid:
                end = min(end, predicted)
            ep["samples"].append((observed, end))
    for relay in list(episodes):
        finish(relay)

    result = []
    for ep in finished:
        verified = False
        ends, start, clear = ep["ends"], ep["start"], ep["clear"]
        if ep["consistent"] and len(ends) >= 2 and start and clear:
            end = sorted(ends)[len(ends) // 2]
            begin = end - timedelta(seconds=start[1])
            confirmed = any(
                clear <= observed <= clear + timedelta(seconds=180)
                and ep["relay"] in ids(row.get("completed_relay_ids"))
                and row.get("irrigation_plan_id") == ep["plan"]
                for observed, _, row in samples
            )
            verified = (max(ends) - min(ends) <= timedelta(seconds=3)
                        and -3 <= (begin - start[0]).total_seconds() <= 30
                        and 0 <= (ep["first"] - begin).total_seconds() <= 90
                        and 0 <= (end - ep["last"]).total_seconds() <= 90
                        and -3 <= (clear - end).total_seconds() <= 90
                        and confirmed)
        intervals = [(begin, min(end, clear))] if verified else ep["samples"]
        for a, b in intervals:
            a = max(a, period_start) if period_start else a
            b = min(b, period_end) if period_end else b
            if b > a:
                result.append((ep["relay"], ep["plan"], a, b, not verified))
    return result


def duration_summary(segments, *, plan=None, start=None, end=None):
    selected = [s for s in segments if (plan is None or s[1] == plan)
                and (start is None or s[3] > start) and (end is None or s[2] < end)]
    seconds = union_seconds([(s[2], s[3]) for s in selected], start, end)
    # Round once after summing seconds, not once per zone/sample.
    return int(seconds / 60 + 0.5), any(s[4] for s in selected)
