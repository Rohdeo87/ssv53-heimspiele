"""Offline replay of selected telemetry; does not seed live validation scores."""
import hashlib
import json
from pathlib import Path
from mower.charging_forecast import advance, epoch, quality

folder=Path("docs/ui-2026-09-12/charging-calibration")
raw=(folder/"telemetry-local.json").read_bytes()
rows=json.loads(raw)
state=None
maximum_jump=0
last_vendor=None
example=None
for row in rows:
    at=epoch(row["timestamp"])
    mower={"mower_id":row.get("mower_id"),"activity":row.get("activity"),"state":row.get("mower_state"),
           "connected":str(row.get("mower_connected")).lower()=="true","error_code":row.get("error_code"),
           "battery_percent":row.get("battery_percent"),"status_timestamp_ms":row.get("mower_status_timestamp_ms"),
           "remaining_charging_seconds":row.get("remaining_charging_seconds")}
    state=advance(state,mower,at)
    seconds=mower["remaining_charging_seconds"]
    if mower["activity"]=="CHARGING" and type(seconds) is int and seconds>0 and mower["status_timestamp_ms"]:
        end=float(mower["status_timestamp_ms"])/1000+seconds
        if last_vendor and end-last_vendor[0]>maximum_jump:
            maximum_jump=end-last_vendor[0]
            example={"before":last_vendor[1],"after":{k:row.get(k) for k in ("timestamp","battery_percent","remaining_charging_seconds")}}
        last_vendor=(end,{k:row.get(k) for k in ("timestamp","battery_percent","remaining_charging_seconds")})
    elif mower["activity"]!="CHARGING":
        last_vendor=None
report={"offline_only":True,"live_validation_seeded":False,"telemetry_sha256":hashlib.sha256(raw).hexdigest(),
        "rows":len(rows),"reference_sessions":len(state["history"]),"outcomes":len(state["outcomes"]),
        "valid_completed":sum(r["valid"] for r in state["outcomes"]),
        "predictions_scored":sum(r.get("ownError") is not None for r in state["outcomes"]),
        "quality_low_battery":quality(state["outcomes"],9,state["lastTick"]),
        "largest_vendor_jump_minutes":round(maximum_jump/60,1),"vendor_jump_example":example}
(folder/"replay.json").write_text(json.dumps(report,indent=2)+"\n",encoding="utf-8")
print(json.dumps(report))
