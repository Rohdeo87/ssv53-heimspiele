"""Read a bounded controller trace window; never command a device."""
from datetime import datetime, timezone
import argparse
import json
from pathlib import Path

from read_grounds_installation import GROUP, az


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    query = ('traces | where timestamp > ago(20m) and message startswith "SSV53_CONTROL_CYCLE " '
             '| extend p=parse_json(substring(message,20)) '
             '| project timestamp,p | order by timestamp desc | take 20')
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
        rows.append({"at": at, "decision": raw.get("decision_code"),
            "command_sent": raw.get("command_sent"),
            "manifest": (raw.get("build_provenance") or {}).get("package_manifest_sha256"),
            "mower": {k: mower.get(k) for k in
                      ("state", "activity", "mode", "connected", "error_code", "status_timestamp_ms")},
            "takeover": details.get("automatic_takeover"),
            "manual_session": details.get("manual_session"),
            "outage_guard": details.get("mower_outage_guard"),
            "automation": {k: state.get(k) for k in
                           ("continuous_mowing_owned", "continuous_mowing_observed_takeover",
                            "continuous_mowing_takeover_deadline_utc", "continuous_mowing_takeover_hold_utc",
                            "continuous_mowing_window_end_utc", "parked_by_automation",
                            "automation_restart_allowed", "irrigation_phase", "operator_request_status")},
            "water": water.get("safety"), "drying": water.get("release_confirmation")})
    report = {"retrieved_at_utc": datetime.now(timezone.utc).isoformat(),
              "read_only": True, "query": query, "rows": rows}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"observations": len(rows), "latest": rows[0] if rows else None}, ensure_ascii=False))


if __name__ == "__main__":
    main()
