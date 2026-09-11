"""Offline fixtures of the shipping template, never a device simulation endpoint."""
from copy import deepcopy
import json
import os
from pathlib import Path
from scripts.build_audit_preview import build
from scripts.build_manual_control_preview import fixture

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / os.environ.get("SSV53_UI_OUTPUT", "docs/ui-2026-09-11/wunschdesign")


def fixtures():
    base = deepcopy(fixture)
    base.update(generatedAt="2026-09-11T08:15:00Z", operationMode="AUTOMATIC")
    base["mower"].update(activity="CHARGING", mode="HOME", operationMode="AUTOMATIC", batteryPercent=72)
    base["manualControl"].update(status="AUTOMATIC", title="Automatik", message="Der Mäher folgt der Platzpflegeplanung.", occupancyOverrideActive=False)
    base["manualControl"]["confirmations"].update(occupancyRequired=False, occupancyTitles=[], dryingRequired=True, dryUntil="2026-09-11T09:40:00Z")
    base["coordination"].update(dryUntil="2026-09-11T09:40:00Z", releaseNotBefore="2026-09-11T09:40:00Z", chargingEndEstimate={"at":"2026-09-11T08:55:00Z","estimated":True}, blockers=[{"code":"CHARGING"},{"code":"DRYING_OR_CONFIRMATION"}])
    base["overall"]={"code":"HYDRAWISE_CLEAR_CONFIRMATION"}
    base["trainingControl"]={"available":True,"active":False,"pending":None,"effectiveAt":None,"nextEffectiveAt":"2026-09-11T22:00:00Z","trainingRevision":"a"*64}
    base["occupancy"]={"available":True,"current":None,"upcoming":[{"title":"Training","source":"training","start":"2026-09-11T14:00:00Z","end":"2026-09-11T17:30:00Z"}],"safeWindows":[{"start":"2026-09-11T08:15:00Z","command_deadline":"2026-09-11T13:56:00Z","minimum_mowing_minutes":30}]}
    base["irrigation"]["zones"]=[{"zone":i,"name":f"Zone {i}","running":False,"run_seconds":1800} for i in range(1,8)]
    base["irrigationSchedule"]={"available":True,"nextRun":{"start":"2026-09-12T01:30:00Z","selectedZoneCount":7,"zones":[{"zone":i,"name":f"Zone {i}","durationMinutes":30,"run_seconds":1800,"selected":True} for i in range(1,8)]},"history":[]}
    base["statistics"]={"available":True,"mownAreaEquivalents7d":4.2,"mowingMinutes7d":1860,"averageDailyMowingMinutes7d":266,"mowingMinutesToday":220,"currentAreaProgress":43,"lastCompletedAreaUtc":"2026-09-10T19:10:00Z","bladeUsageSeconds":136800,"averageReturnMinutes7d":2}
    base["irrigationStatistics"]={"available":True,"wateringMinutes7d":660,"completedRuns7d":3,"lastCompletedAt":"2026-09-11T05:10:00Z","lastCompletedDurationMinutes":220,"zoneMinutes7d":[{"name":f"Zone {i}","minutes":90} for i in range(1,8)],"planChanges7d":1}
    for name in ["PARK_MOWER","START_MOWING","START_IRRIGATION","START_IRRIGATION_ZONE","STOP_IRRIGATION_NOW","STOP_IRRIGATION_AFTER_ZONE","CUSTOMIZE_NEXT_IRRIGATION","PAUSE_IRRIGATION_UNTIL","RESUME_IRRIGATION_SCHEDULE","SKIP_NEXT_IRRIGATION","RESET_BLADE_USAGE"]:
        base["actionCapabilities"][name]={"available":True}
    base["mower"].update(cuttingHeightMinimumMm=20,cuttingHeightMaximumMm=60,cuttingHeightMm=27,statusTimestamp=1789114500000)
    output={"charging":base}
    charging_unknown=deepcopy(base)
    charging_unknown["mower"]["batteryPercent"]=29
    charging_unknown["overall"]={"code":"MOWER_BATTERY_CHARGING"}
    charging_unknown["automation"]["irrigationPhase"]=None
    charging_unknown["coordination"].update(dryUntil=None,releaseNotBefore=None,chargingEndEstimate=None,blockers=[{"code":"CHARGING"}])
    charging_unknown["manualControl"]["confirmations"]["dryingRequired"]=False
    output["charging-unknown"]=charging_unknown
    parked=deepcopy(base);parked["manualControl"].update(status="MANUAL_PARKED",message="Die Automatik wartet auf deine Freigabe.");parked["mower"].update(activity="PARKED_IN_CS",batteryPercent=100);output["parked"]=parked
    moving=deepcopy(base);moving["mower"].update(activity="MOWING",mode="MAIN");moving["coordination"].update(blockers=[],dryUntil=None,releaseNotBefore=None);moving["manualControl"]["confirmations"].update(dryingRequired=False);output["mowing"]=moving
    stale=deepcopy(base);stale["mower"].update(telemetryFresh=False,statusAgeSeconds=500);stale["coordination"]["blockers"]=[{"code":"MOWER_TELEMETRY"}];stale["manualControl"]["canStart"]=False;output["stale"]=stale
    wet=deepcopy(parked);wet["automation"].update(irrigationPhase="RUNNING");wet["irrigation"]["intent"]={"source":"MANUAL_OPERATOR","verified":True,"controllerManaged":True,"automaticWindowApplies":False};wet["irrigation"]["safety"].update(active_zone_count=1,clear_now=False);wet["irrigation"]["zones"][0]["running"]=True;wet["manualControl"]["confirmations"].update(waterChoiceRequired=True);output["watering"]=wet
    failed=deepcopy(stale);failed["automation"]["mowerStartOutcomeUnconfirmed"]=True;output["unconfirmed"]=failed
    return output


if __name__ == "__main__":
    OUT.mkdir(parents=True, exist_ok=True)
    payloads=fixtures()
    build(output=OUT,fixture=payloads["charging"])
    page=OUT/"appack-preview.html"
    html=page.read_text(encoding="utf-8")
    html=html.replace("window.auditFixture=", "window.pfFixtures="+json.dumps(payloads,ensure_ascii=False)+";\nwindow.auditFixture=",1)
    html=html.replace("return {ok:true,status:200,json:async()=>window.auditFixture};", "return {ok:true,status:200,json:async()=>window.pfFixtures[location.hash.slice(1)]||window.auditFixture};")
    page.write_text(html,encoding="utf-8")
    print(OUT)
