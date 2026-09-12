"""Replay the observed status with synthetic permissions; no device requests."""
from copy import deepcopy
import json
from pathlib import Path
from scripts.build_audit_preview import build
from scripts.build_wunschdesign_preview import fixtures

root = Path(__file__).resolve().parents[1]
output = root / "docs/ui-2026-09-12/irrigation-status"
evidence = json.loads((output / "controller-observations.json").read_text(encoding="utf-8"))
row = next(row for row in evidence["observations"] if row["at"].startswith("2026-09-12T04:56:"))
s = fixtures()["stale"]
s["generatedAt"] = row["at"]
s["overall"]["code"] = row["decision"]
s["mower"].update(activity=row["mower"]["activity"], statusTimestamp=row["mower"]["status_timestamp_ms"], batteryPercent=row["mower"]["battery_percent"])
s["automation"].update(irrigationPhase=row["automation"]["irrigation_phase"], pendingAction=None)
s["irrigation"]["safety"] = row["water"]
s["coordination"].update(dryUntil=None, releaseNotBefore=None)
for name in ["waiting", "running", "unknown"]:
    example=deepcopy(s)
    if name != "waiting":
        example["automation"]["irrigationPhase"]="RUNNING"
        example["irrigation"]["safety"].update(active_zone_count=1, clear_now=False, fresh=name=="running")
    build(output=output / name, fixture=example)
print("Built waiting (recorded fields), running and unknown (synthetic) previews. No device requests.")
