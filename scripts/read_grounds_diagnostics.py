"""Read-only, bounded production trace export; no settings or device writes."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import subprocess
import sys


def compact_plan(plan):
    # The full plan repeats weeks of appointments in every minute. This export
    # needs only the current/next blocking interval and no participant details.
    plan = plan or {}
    return {
        key: {field: block.get(field) for field in ("start", "end", "source", "title", "resource_id")}
        if isinstance(block, dict) else None
        for key in ("blocked_now", "parking_block", "next_block")
        for block in (plan.get(key),)
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--minutes", type=int, default=120)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--recovery-proof", action="store_true")
    args = parser.parse_args()
    if not 1 <= args.minutes <= 1440:
        parser.error("minutes must be between 1 and 1440")
    az = r"C:\Program Files\Microsoft SDKs\Azure\CLI2\wbin\az.cmd"
    query = (
        f'traces | where timestamp > ago({args.minutes}m) '
        'and message startswith "SSV53_CONTROL_CYCLE " '
        '| project timestamp,message | order by timestamp asc | take 2000'
    )
    if args.recovery_proof:
        sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
        from mower.start_recovery import _trace_query
        query = _trace_query("2026-09-10T20:00:00.004203+00:00")
        # az.cmd on Windows must receive the Kusto expression on one line.
        query = " ".join(query.splitlines())
    result = subprocess.run([
        az, "monitor", "app-insights", "query", "-g", "rg-ssv53-platzpflege-prod",
        "--app", "appi-ssv53platzpflege-prod-q7kbw54s", "--analytics-query", query,
        "--offset", "1d", "-o", "json", "--only-show-errors",
    ], capture_output=True, check=True, timeout=90)
    try:
        text = result.stdout.decode("utf-8")
    except UnicodeDecodeError:
        text = result.stdout.decode("cp1252")
    if args.recovery_proof:
        table = json.loads(text)["tables"][0]
        columns = [column["name"] for column in table["columns"]]
        rows = [dict(zip(columns, row)) for row in table["rows"]]
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(rows, indent=2) + "\n", encoding="utf-8")
        print(json.dumps({"count": len(rows), "first": rows[:1]}, ensure_ascii=False))
        return
    rows = []
    for timestamp, message in json.loads(text)["tables"][0]["rows"]:
        payload = json.loads(message.split(" ", 1)[1])
        details = payload.get("details") or {}
        mower = details.get("mower") or {}
        water = details.get("hydrawise") or {}
        manual = details.get("manual_session") or {}
        rows.append({
            "at": timestamp, "mode": payload.get("control_mode"),
            "decision": payload.get("decision_code"), "command_sent": payload.get("command_sent"),
            "manifest": (payload.get("build_provenance") or {}).get("package_manifest_sha256"),
            "mower": {k: mower.get(k) for k in (
                "activity", "mode", "state", "connected", "battery_percent", "status_timestamp_ms", "error_code")},
            "water": {k: (water.get("safety") or {}).get(k) for k in (
                "available", "fresh", "clear_now", "active_zone_count", "observed_at_utc")},
            "release": water.get("release_confirmation"),
            "start_action": {k: (details.get("start_action") or {}).get(k) for k in (
                "type", "outcome", "reason_code", "requested_deadline_utc", "failsafe_refresh")},
            "automation": details.get("automation_state"),
            "park_hold": details.get("irrigation_park_hold"),
            "irrigation_action": details.get("irrigation_action"),
            "manual": {k: manual.get(k) for k in ("enabled", "kind", "status", "permission_code")},
            "plan": compact_plan(details.get("current_plan")),
        })
    evidence = {"retrieved_at_utc": datetime.now(timezone.utc).isoformat(),
                "read_only": True, "observations": rows}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(evidence, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    transitions = []
    signature = None
    for row in rows:
        current = (row["decision"], row["mower"]["activity"], row["water"]["active_zone_count"],
                   row["water"]["fresh"], (row["automation"] or {}).get("hydrawise_drying_since_utc"))
        if current != signature:
            transitions.append({"at": row["at"], "state": current})
            signature = current
    print(json.dumps({"observations": len(rows), "transitions": transitions}, ensure_ascii=False))


if __name__ == "__main__":
    main()
