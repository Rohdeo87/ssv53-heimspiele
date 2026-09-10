"""Generate isolated review fixtures for the real template; no device access."""
from pathlib import Path
from scripts.build_audit_preview import build

OUT = Path(__file__).resolve().parents[1] / "docs/audit-2026-09-10/manual-control-preview"
fixture = {
 "ok": True, "generatedAt": "2026-09-10T14:00:00Z", "controlsAvailable": True, "deviceControlsAvailable": True,
 "dataQuality": {"code": "LIVE", "displayOnly": False}, "overall": {"code": "MANUAL_SESSION_ACTIVE"},
 "protection": {"automaticStartEnabled": True, "protectiveParkingEnabled": True},
 "mower": {"activity":"MOWING", "state":"IN_OPERATION", "mode":"MAIN", "operationMode":"MANUAL",
   "connected":True, "telemetryFresh":True, "statusAgeSeconds":20, "statusTimestamp":1789048780000,
   "errorCode":0, "batteryPercent":78, "restartBatteryPercent":90, "cuttingHeightMm":26,
   "cuttingHeightSupported":True, "model":"Automower 580 EPOS", "workAreaProgress":43},
 "automation": {"continuousMowingOwned":True},
 "manualControl": {"enabled":True,"status":"MANUAL_MOWING","title":"Manuell gestartet","message":"Gilt bis zur nächsten Ladefahrt.",
   "source":"APP","until":None,"canStart":True,"canPark":True,"canResume":True,"sessionId":"example-confirmed-start","epoch":1,
   "contextToken":"example-only","occupancyOverrideActive":True,"dryingOverrideActive":False,
   "confirmations":{"occupancyRequired":True,"occupancyTitles":["Training"],"dryingRequired":False,"dryUntil":None,"waterChoiceRequired":False,"waterConflictId":None}},
 "irrigation": {"safety":{"available":True,"fresh":True,"clear_now":True,"active_zone_count":0},"zones":[]},
 "occupancy": {"available":True,"current":{"source":"training","title":"Training","start":"2026-09-10T13:30:00Z","end":"2026-09-10T16:00:00Z"},"upcoming":[],"safeWindows":[]},
 "coordination":{"explanationOnly":True,"blockers":[],"dryUntil":None,"releaseNotBefore":None},
 "actionCapabilities":{"MANUAL_CONTROL":{"available":True},"PARK_MOWER":{"available":True},"SET_CUTTING_HEIGHT":{"available":True}},
 "trainingControl":{"available":True,"active":False,"pending":None,"nextEffectiveAt":"2026-09-10T22:00:00Z","trainingRevision":"example"},
 "irrigationSchedule":{},"statistics":{},"irrigationStatistics":{},"clubhouse":{"available":True,"events":[]}
}
if __name__ == "__main__":
 build(output=OUT, fixture=fixture)
 p=OUT/"appack-preview.html";s=p.read_text(encoding="utf-8")
 s=s.replace("return {ok:true,status:200,json:async()=>window.auditFixture};", """const data=JSON.parse(JSON.stringify(window.auditFixture));
 if(location.hash==='#park'){data.mower.activity='PARKED_IN_CS';data.mower.mode='HOME';data.manualControl.status='MANUAL_PARKED';data.manualControl.title='Manuell geparkt';data.manualControl.message='Die Automatik wartet auf deine Freigabe.';}
 if(location.hash==='#conflict'){data.manualControl.status='WAITING_WATER';data.manualControl.title='Bewässerung und Mähen abstimmen';data.manualControl.message='Bitte für diesen Lauf wählen: Mähen oder Bewässern.';data.manualControl.confirmations.waterChoiceRequired=true;data.manualControl.confirmations.dryingRequired=true;data.manualControl.confirmations.dryUntil='2026-09-10T17:30:00Z';data.irrigation.safety.active_zone_count=1;data.irrigation.safety.clear_now=false;}
 return {ok:true,status:200,json:async()=>data};""")
 p.write_text(s,encoding="utf-8")
 print(OUT)
