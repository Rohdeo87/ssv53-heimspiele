"""Reproduce the reported upper dashboard with labelled, isolated fixtures."""
import json
from pathlib import Path
from build_audit_preview import build

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "docs/ui-2026-09-10/parked-status"
payload = {
    "ok": True, "generatedAt": "2026-09-10T18:31:00Z",
    "controlsAvailable": True, "deviceControlsAvailable": True,
    "overall": {"code": "OCCUPANCY_OR_IRRIGATION_HOLD"}, "operationMode": "AUTOMATIC",
    "protection": {"automaticStartEnabled": True, "protectiveParkingEnabled": True},
    "mower": {"activity": "PARKED_IN_CS", "state": "RESTRICTED", "mode": "HOME", "connected": True,
        "telemetryFresh": False, "statusAgeSeconds": 720, "statusTimestamp": 1789064340000,
        "errorCode": 0, "batteryPercent": 100, "restartBatteryPercent": 90, "workAreaProgress": 100,
        "model": "Automower 580 EPOS", "cuttingHeightMm": 25},
    "automation": {"parkedByAutomation": True, "continuousMowingOwned": False, "irrigationPhase": None},
    "manualControl": {"enabled": True, "status": "AUTOMATIC", "canStart": False, "canPark": True,
        "canResume": False, "title": "Automatik", "message": "Der Mäher folgt der Platzpflegeplanung."},
    "irrigation": {"safety": {"available": True, "fresh": True, "clear_now": True, "active_zone_count": 0}, "zones": []},
    "occupancy": {"available": True, "current": {"start": "2026-09-10T14:30:00Z", "end": "2026-09-10T20:00:00Z", "source": "training", "title": "Training E1; Training A; Training Herren"},
        "upcoming": [], "safeWindows": [{"start": "2026-09-10T20:00:00Z", "command_deadline": "2026-09-11T01:56:00Z", "minimum_mowing_minutes": 30}]},
    "coordination": {"explanationOnly": True, "blockers": [{"code": "MOWER_TELEMETRY"}, {"code": "OCCUPANCY", "until": "2026-09-10T20:00:00Z"}],
        "dryUntil": "2026-09-10T16:25:00Z", "releaseNotBefore": "2026-09-10T16:25:00Z", "chargingEndEstimate": None},
    "dataQuality": {"displayOnly": False}, "irrigationSchedule": {}, "statistics": {}, "irrigationStatistics": {}, "clubhouseEvents": [],
}
for name, age in [("training", 720), ("missing-report", 1201)]:
    case = json.loads(json.dumps(payload))
    case["mower"]["statusAgeSeconds"] = age
    # 20:31 Berlin is 18:31 UTC; preserve a matching status-age pair.
    from datetime import datetime
    case["mower"]["statusTimestamp"] = int(datetime.fromisoformat(case["generatedAt"].replace("Z", "+00:00")).timestamp() * 1000) - age * 1000
    build(output=OUT / name, fixture=case)
    (OUT / name / "fixture.json").write_text(json.dumps({"synthetic": True, "payload": case}, indent=2) + "\n", encoding="utf-8")
print("Built isolated parked-status previews")
