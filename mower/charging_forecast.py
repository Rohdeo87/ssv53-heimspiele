"""Prospective, session-weighted charging calibration. Display only; no commands.

Predictions are frozen before their outcome exists. The fixed algorithm learns
from earlier completed charges only; revisions start a new validation history.
"""
from copy import deepcopy
from datetime import datetime, timezone
import math
import statistics

VERSION = 1
DAY = 86400
WINDOW = 60


def epoch(value):
    return datetime.fromisoformat(str(value).replace("Z", "+00:00")).timestamp()


def iso(value):
    return datetime.fromtimestamp(value, timezone.utc).isoformat()


def wilson(successes, count):
    if not count:
        return 0.0
    z = 1.6448536269514722  # one-sided 95% lower bound, session-level trials
    p = successes / count
    return (p + z*z/(2*count) - z*math.sqrt(p*(1-p)/count + z*z/(4*count*count))) / (1+z*z/count)


def quality(outcomes, battery, now):
    bucket = min(3, battery // 25)
    rows = [r for r in outcomes if r["bucket"] == bucket and now-90*DAY <= r["end"] <= now][-WINDOW:]
    scored = [r for r in rows if r.get("valid") and r.get("ownError") is not None and r.get("vendorError") is not None]
    good = sum(r["ownError"] <= 300 for r in scored)
    wins = sum(r["ownError"] + 30 < r["vendorError"] for r in scored)
    days = len({int(r["end"] // DAY) for r in scored})
    span = (max(r["end"] for r in scored)-min(r["end"] for r in scored))/DAY if scored else 0
    own = statistics.mean(r["ownError"] for r in scored) if scored else None
    vendor = statistics.mean(r["vendorError"] for r in scored) if scored else None
    trajectory = statistics.mean(r.get("vendorTrajectoryError", r["vendorError"]) for r in scored) if scored else None
    fresh = bool(rows and now - rows[-1]["end"] <= 7*DAY)
    approved = bool(len(rows) == WINDOW and len(scored) == WINDOW and days >= 14 and span >= 14
                    and fresh and wilson(good, WINDOW) >= .95 and wilson(wins, WINDOW) > .5
                    and own + 30 <= vendor and own <= vendor * .8
                    and own + 30 <= trajectory and own <= trajectory * .8)
    return {"approved": approved, "sessions": len(rows), "scoredSessions": len(scored),
            "days": days, "spanDays": round(span,2), "withinFiveMinutesLower95": wilson(good, len(rows)),
            "pairedImprovementLower95": wilson(wins, len(rows)),
            "ownMeanErrorSeconds": own, "vendorMeanErrorSeconds": vendor,
            "vendorTrajectoryMeanErrorSeconds": trajectory,
            "startingBatteryBucket": bucket, "modelVersion": VERSION}


def predict(history, start, battery):
    durations = []
    days = set()
    for ref in history:
        if not start - 90*DAY <= ref["end"] < start:
            continue
        points = ref["points"]
        if not points or points[0][1] > battery or points[-1][1] != 100:
            continue
        for index, (at, level) in enumerate(points):
            if level >= battery:
                crossing = at
                if level > battery and index:
                    prior, low = points[index-1]
                    crossing = prior+(at-prior)*(battery-low)/(level-low)
                durations.append(ref["end"]-crossing)
                days.add(int(ref["end"]//DAY))
                break
    if len(durations) < 5 or len(days) < 3 or min(durations) <= 0:
        return None
    if max(durations)-min(durations) > 600:
        return None
    return math.ceil((start + statistics.median(durations))/60)*60


def valid_report(mower, now):
    try:
        at = round(float(mower["status_timestamp_ms"])/1000, 3)
        battery = mower["battery_percent"]
        if (type(battery) is not int or not 1 <= battery <= 100 or not math.isfinite(at)
                or not 0 <= now-at <= 360 or mower.get("connected") is not True
                or mower.get("state") != "IN_OPERATION" or mower.get("error_code") != 0
                or not mower.get("mower_id")):
            return None
        return at, battery
    except (KeyError, TypeError, ValueError, OverflowError):
        return None


def advance(previous, mower, now):
    """Pure state transition. Duplicate/out-of-order ticks never score twice."""
    state = deepcopy(previous) if previous and previous.get("version") == VERSION and previous.get("mowerId") == mower.get("mower_id") else {
        "version": VERSION, "mowerId": mower.get("mower_id"), "history": [], "outcomes": [], "active": None, "lastTick": 0}
    if now <= state["lastTick"]:
        return state
    state["lastTick"] = now
    state["history"] = [r for r in state["history"] if r["end"] >= now-90*DAY][-9:]
    state["outcomes"] = [r for r in state["outcomes"] if r["end"] >= now-90*DAY][-119:]
    active = state["active"]
    report = valid_report(mower, now)
    charging = mower.get("activity") == "CHARGING"
    departed = mower.get("activity") in {"LEAVING", "MOWING", "GOING_HOME"}
    if active and (now-active["seen"] > 180 or not report):
        active["valid"] = False
    if not active and charging:
        if not report or report[1] >= 100:
            return state
        at, battery = report
        # The preceding observation proves entry; startup midway is censored.
        entered = (state.get("entryArmed", False) and state.get("priorActivity") in {"LEAVING","MOWING","GOING_HOME"}
                   and now-state.get("priorSeen", 0) <= 180)
        candidate = predict(state["history"], at, battery) if entered else None
        if candidate is not None and candidate <= now:
            candidate = None  # A forecast already past at first receipt was never usable.
        active = {"start": at, "seen": now, "lastReport": at, "battery": battery,
                  "points": [[at,battery]], "valid": entered, "vendors": [],
                  "prediction": candidate, "ownDisplay": quality(state["outcomes"], battery, at)["approved"],
                  "bucket": min(3,battery//25), "firstFull": None}
        state["active"] = active
        state["entryArmed"] = False
    if active:
        active["seen"] = now
        if now-active["start"] > 21600:
            active["valid"] = False
        if report:
            at, battery = report
            if at < active["lastReport"] or battery < active["battery"]:
                active["valid"] = False
            if at > active["lastReport"]:
                if at-active["lastReport"] > 360:
                    active["valid"] = False
                if battery != active["battery"] and len(active["points"]) < 102:
                    active["points"].append([at,battery])
                active["lastReport"] = at
                active["battery"] = battery
            # One manufacturer estimate per distinct report, on the same
            # horizon as the frozen shadow prediction. Timer repeats are ignored.
            seconds = mower.get("remaining_charging_seconds")
            if (charging and type(seconds) is int and 0 < seconds <= 21600
                    and (not active["vendors"] or active["vendors"][-1][0] != at)):
                if len(active["vendors"]) < 240:
                    active["vendors"].append([at,at+seconds])
                else:
                    active["valid"] = False
            if battery == 100 and active["firstFull"] is None:
                active["firstFull"] = at
        # PARKED_IN_CS can continue charging. STOP/PAUSE cannot create a new
        # independent training trial when charging resumes in the same dock.
        if not charging and not departed and mower.get("activity") != "PARKED_IN_CS":
            active["valid"] = False
        terminal = departed or (mower.get("activity") == "PARKED_IN_CS" and report and report[1] == 100)
        if terminal or now-active["start"] > 21600:
            full = active["firstFull"]
            complete = bool(active["valid"] and report and report[1] == 100 and full
                            and mower.get("activity") in {"LEAVING","MOWING","PARKED_IN_CS"}
                            and len(active["points"]) >= 6 and 100-active["points"][0][1] >= 10)
            pred = active["prediction"]
            vendors = [value for at,value in active["vendors"] if full and at < full]
            first_vendor = next((value for at,value in active["vendors"] if at == active["start"]), None)
            state["outcomes"].append({"end": round(full if complete else now,3), "bucket": active["bucket"],
                "valid": complete, "ownError": round(abs(pred-full),1) if complete and pred is not None else None,
                "signedError": round(pred-full,1) if complete and pred is not None else None,
                "vendorError": round(abs(first_vendor-full),1) if complete and first_vendor is not None else None,
                "vendorTrajectoryError": round(statistics.mean(abs(value-full) for value in vendors),1) if complete and vendors else None,
                "vendorReports": len(vendors), "prediction": pred})
            if complete:
                state["history"].append({"end": full, "points": [p for p in active["points"] if p[0] <= full]})
            state["active"] = None
    if departed and report and state["active"] is None:
        state["entryArmed"] = True
    state["priorActivity"] = mower.get("activity")
    state["priorSeen"] = now
    return state


def own_display(state, mower, now):
    active = (state or {}).get("active")
    report = valid_report(mower, now)
    if (not active or state.get("version") != VERSION or state.get("mowerId") != mower.get("mower_id") or not report or not active["valid"]
            or not active["ownDisplay"] or mower.get("activity") != "CHARGING"
            or not 0 <= now-active["seen"] <= 180 or not 0 <= now-report[0] <= 180
            or report[0] < active["lastReport"] or report[1] < active["battery"] or report[1] >= 100):
        return None
    at = active["prediction"]
    if at is None or not now < at <= now+21600:
        return None
    return {"at": iso(at), "estimated": True, "displayOnly": True,
            "source": "VALIDATED_CHARGING_FORECAST", "precisionMinutes": 1,
            "modelVersion": VERSION, "anchorAt": iso(active["start"])}
