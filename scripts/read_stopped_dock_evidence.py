"""Read selected, recent controller traces; never contact or command a device."""
from datetime import datetime, timezone
import argparse
import json
from pathlib import Path

from read_grounds_installation import GROUP, az


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    query = ('traces | where timestamp > ago(15m) and message startswith "SSV53_CONTROL_CYCLE " '
             '| extend p=parse_json(substring(message,20)) '
             '| project timestamp,p | order by timestamp desc | take 3')
    data = az("monitor", "app-insights", "query", "-g", GROUP,
              "--app", "appi-ssv53platzpflege-prod-q7kbw54s",
              "--analytics-query", query, "--offset", "1h")["tables"][0]["rows"]
    rows = []
    for at, raw in data:
        raw = json.loads(raw) if isinstance(raw, str) else raw
        details = raw.get("details") or {}
        mower = details.get("mower") or {}
        state = details.get("automation_state") or {}
        water = details.get("hydrawise") or {}
        rows.append({
            "at": at, "decision": raw.get("decision_code"),
            "command_sent": raw.get("command_sent"),
            "manifest": (raw.get("build_provenance") or {}).get("package_manifest_sha256"),
            "mower": {k: mower.get(k) for k in
                      ("state", "activity", "mode", "connected", "error_code", "status_timestamp_ms")},
            "park_hold": details.get("irrigation_park_hold"),
            "automation": {k: state.get(k) for k in
                           ("parked_by_automation", "automation_restart_allowed", "park_confirmed_utc",
                            "park_confirmed_observations", "irrigation_phase", "irrigation_completed_utc",
                            "irrigation_current_relay_id", "irrigation_zone_start_reserved_utc",
                            "irrigation_zone_started_utc", "operator_request_status")},
            "water": water.get("safety"), "drying": water.get("release_confirmation"),
        })
    report = {"retrieved_at_utc": datetime.now(timezone.utc).isoformat(),
              "read_only": True, "query": query, "rows": rows}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"observations": len(rows), "latest": rows[0] if rows else None}, ensure_ascii=False))


if __name__ == "__main__":
    main()
