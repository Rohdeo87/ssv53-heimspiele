"""Review the actual simplified template with labelled synthetic examples only."""
from pathlib import Path
import re

from build_audit_preview import build

OUT = Path(__file__).resolve().parents[1] / "docs/ui-2026-09-09"


def main():
    fixture = {
        "ok": True, "generatedAt": "2026-09-09T10:00:00Z", "controlsAvailable": True,
        "overall": {"code": "HYDRAWISE_CLEAR_CONFIRMATION"}, "dataQuality": {"displayOnly": False},
        "mower": {
            "activity": "CHARGING", "state": "IN_OPERATION", "connected": True, "errorCode": 0,
            "batteryPercent": 73, "restartBatteryPercent": 90, "model": "Automower 580 EPOS",
            "cuttingHeightMm": 30, "cuttingHeightSupported": True, "workAreaProgress": 62,
        },
        "automation": {"continuousMowingOwned": True, "parkedByAutomation": True, "irrigationPhase": "COMPLETE_HOLD"},
        "irrigation": {
            "safety": {"available": True, "fresh": True, "clear_now": True, "active_zone_count": 0},
            "zones": [{"zone": number, "name": f"Zone {number}", "running": False, "run_seconds": 900} for number in range(1, 8)],
        },
        "occupancy": {
            "current": None,
            "upcoming": [{"start": "2026-09-09T16:30:00+02:00", "end": "2026-09-09T20:00:00+02:00", "title": "Jugendtraining", "source": "training", "details": {"nominal_start": "2026-09-09T17:00:00+02:00", "nominal_end": "2026-09-09T19:30:00+02:00"}}],
            "safeWindows": [{"start": "2026-09-09T10:00:00Z", "command_deadline": "2026-09-09T14:26:00Z", "minimum_mowing_minutes": 30}],
        },
        "coordination": {
            "explanationOnly": True, "dryUntil": "2026-09-09T12:30:00Z", "releaseNotBefore": "2026-09-09T12:30:00Z",
            "chargingEndEstimate": {"at": "2026-09-09T11:45:00Z", "estimated": True},
            "blockers": [{"code": "CHARGING"}, {"code": "DRYING_OR_CONFIRMATION"}],
        },
        "irrigationSchedule": {"available": True, "nextRun": {"start": "2026-09-10T02:00:00Z", "selectedZoneCount": 7, "zones": [{"zone": number, "name": f"Zone {number}", "selected": True, "runSeconds": 900} for number in range(1, 8)]}},
        "statistics": {"mowingMinutesToday": 180, "mowingMinutes7d": 1260, "available": True},
        "irrigationStatistics": {"available": True},
        "clubhouse": {"available": True, "events": []},
    }
    build(output=OUT, fixture=fixture)
    target = OUT / "appack-preview.html"
    preview = target.read_text(encoding="utf-8")
    preview = preview.replace("return {ok:true,status:200,json:async()=>window.auditFixture};", """const data=JSON.parse(JSON.stringify(window.auditFixture));
 if(location.hash==='#unknown')data.coordination.chargingEndEstimate=null;
 if(location.hash==='#error'){data.mower.state='ERROR';data.mower.activity='NOT_APPLICABLE';data.mower.errorCode=93;data.mower.errorActive=true;data.automation.irrigationPhase=null;data.coordination.blockers=[{code:'MOWER_ERROR'}];data.coordination.dryUntil=null;}
 if(location.hash==='#rejected'){data.irrigationSchedule.override={kind:'PAUSE',status:'REJECTED'};data.automation.irrigationPhase=null;}
 if(location.hash==='#manual'){data.overall.code='OPERATOR_PARK_HOLD';data.coordination.blockers.push({code:'MANUAL_STOP'});data.automation.irrigationPhase=null;}
 return {ok:true,status:200,json:async()=>data};""")
    banner = """<body><nav style="background:#172033;color:white;padding:8px;text-align:center;font:14px Arial">Vorschau · Beispieldaten<br>
<a style="color:white" href="#charging" onclick="setTimeout(()=>document.getElementById('refresh').click(),0)">Zeitplan</a> ·
<a style="color:white" href="#unknown" onclick="setTimeout(()=>document.getElementById('refresh').click(),0)">Zeit offen</a> ·
<a style="color:white" href="#error" onclick="setTimeout(()=>document.getElementById('refresh').click(),0)">Fehler</a> ·
<a style="color:white" href="#offline" onclick="setTimeout(()=>document.getElementById('refresh').click(),0)">Keine Verbindung</a></nav>"""
    preview = re.sub(r"<body><div style=\"background:#172033.*?</div>", lambda _: banner, preview, count=1, flags=re.S)
    target.write_text(preview, encoding="utf-8", newline="\n")
    print(f"Synthetic, network-isolated review: {OUT.relative_to(OUT.parents[1])}")


if __name__ == "__main__":
    main()
