"""Read existing charging telemetry without device calls or setting changes."""
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from daily_safety_report import _cycle_query, parse_cycle_rows, charging_evidence
from read_grounds_installation import az, GROUP

now = datetime.now(timezone.utc)
query = _cycle_query(now - timedelta(days=7), now).replace(
    "activity=tostring(p.details.mower.activity),",
    "remaining_charging_seconds=toint(p.details.mower.remaining_charging_seconds), activity=tostring(p.details.mower.activity),").replace("\n", " ")
table = az("monitor", "app-insights", "query", "-g", GROUP,
    "--app", "appi-ssv53platzpflege-prod-q7kbw54s", "--analytics-query", query, "--offset", "8d")["tables"][0]
names = [c["name"] for c in table["columns"]]
if "activity" not in names:
    raise RuntimeError("Projected telemetry columns missing; query rejected")
rows = [dict(zip(names, r)) for r in table["rows"]]
evidence = charging_evidence(parse_cycle_rows(rows), now)
target = Path("docs/ui-2026-09-12/charging-calibration")
target.mkdir(parents=True, exist_ok=True)
# Raw selected telemetry is a local audit input, not a public user-data dump.
(target / "telemetry-local.json").write_text(json.dumps(rows), encoding="utf-8")
charging = [r for r in rows if r.get("activity") == "CHARGING"]
report = {"at_utc": now.isoformat(), "read_only": True, "observations": len(rows),
    "charging_observations": len(charging), "valid_completed_sections": evidence["validCompletedSections"],
    "sections_observed": evidence["sectionsObserved"],
    "vendor_positive_observations": sum(isinstance(r.get("remaining_charging_seconds"), int) and r["remaining_charging_seconds"] > 0 for r in charging),
    "recent_charging": [{k:r.get(k) for k in ("timestamp","activity","battery_percent","remaining_charging_seconds","mower_status_timestamp_ms")} for r in charging[-12:]]}
(target / "baseline.json").write_text(json.dumps(report, indent=2)+"\n", encoding="utf-8")
print(json.dumps(report))
