"""Build synthetic, network-isolated dashboard previews for visual review."""
from pathlib import Path
import json
from build_audit_preview import build

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "docs/ui-2026-09-10/trainer-dashboard"

BASE = {
    "ok": True, "generatedAt": "2026-09-10T14:30:00Z", "controlsAvailable": True, "deviceControlsAvailable": True,
    "overall": {}, "mower": {"activity": "PARKED_IN_CS", "state": "IN_OPERATION", "connected": True, "telemetryFresh": True, "statusAgeSeconds": 0, "errorCode": 0, "batteryPercent": 100, "restartBatteryPercent": 90, "model": "Automower 580 EPOS", "cuttingHeightMm": 30},
    "automation": {"continuousMowingOwned": True, "parkedByAutomation": True, "irrigationPhase": None},
    "irrigation": {"safety": {"available": True, "fresh": True, "clear_now": True, "active_zone_count": 0}, "zones": []},
    "occupancy": {"current": None, "upcoming": [], "safeWindows": []},
    "coordination": {"explanationOnly": True, "dryUntil": "2026-09-10T16:25:00Z", "releaseNotBefore": "2026-09-10T16:25:00Z", "blockers": [], "chargingEndEstimate": None},
    "dataQuality": {"displayOnly": False}, "irrigationSchedule": {}, "statistics": {}, "irrigationStatistics": {}, "clubhouseEvents": [],
}

CASES = {
    "possible-gap": {"coordination": {"dryingReason": "POSSIBLE_IRRIGATION_DURING_GAP"}},
    "irrigation-end": {"coordination": {"dryingReason": "IRRIGATION_END"}},
    "charging": {"mower": {"activity": "CHARGING", "batteryPercent": 73}, "coordination": {"dryUntil": None, "releaseNotBefore": None, "chargingEndEstimate": {"at": "2026-09-10T15:10:00Z", "estimated": True}}},
    "water-unknown": {"controlsAvailable": False, "dataQuality": {"code": "IRRIGATION_STATUS_UNAVAILABLE", "displayOnly": True}, "irrigation": {"safety": {"available": False, "fresh": False, "clear_now": False, "active_zone_count": 0}}},
}

for name, overrides in CASES.items():
    payload = json.loads(json.dumps(BASE))
    for key, values in overrides.items():
        payload[key].update(values) if isinstance(values, dict) else payload.__setitem__(key, values)
    build(output=OUT / name, fixture=payload)
    (OUT / name / "case.json").write_text(json.dumps({"synthetic": True, "case": name, "payload": payload}, indent=2) + "\n", encoding="utf-8")
print(f"Built synthetic previews in {OUT}")
