"""Build an isolated synthetic preview of the actual Appack template (no network)."""
from pathlib import Path
import hashlib
import json
import re

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "docs/audit-2026-09-09"


def build():
    source = (ROOT / "appack-platzwart-dashboard.html").read_text(encoding="utf-8")
    payload = {
        "ok": True, "generatedAt": "2026-09-09T10:00:00Z", "controlsAvailable": True,
        "overall": {"code": "HYDRAWISE_CLEAR_CONFIRMATION", "title": "Freigabe wird geprüft", "message": "Synthetischer Prüffall: Laden und Trocknung überlappen."},
        "mower": {"activity": "CHARGING", "state": "IN_OPERATION", "connected": True, "errorCode": 0, "batteryPercent": 73, "model": "Automower 580 EPOS", "cuttingHeightMm": 30},
        "automation": {"continuousMowingOwned": True, "irrigationPhase": "COMPLETE_HOLD", "lastOperatorAction": "START_IRRIGATION", "lastOperatorStatus": "COMPLETE"},
        "irrigation": {"safety": {"available": True, "fresh": True, "clear_now": True}, "zones": []},
        "occupancy": {"current": None, "upcoming": [{"start": "2026-09-09T14:30:00+02:00", "end": "2026-09-09T17:00:00+02:00", "title": "Training Jugend (Beispiel)", "source": "training"}], "suitableWindows": []},
        "coordination": {
            "explanationOnly": True, "dryingMinutes": 150, "dryUntil": "2026-09-09T10:40:00Z", "releaseNotBefore": "2026-09-09T10:42:00Z", "telemetryConfirmed": False,
            "blockers": [
                {"code": "DRYING_OR_CONFIRMATION", "label": "Trocknung oder erneute Datenbestätigung", "resolution": "Physische Trocknungsfrist und aktuelle Datenbestätigung müssen beide erfüllt sein.", "until": "2026-09-09T10:42:00Z"},
                {"code": "CHARGING", "label": "Akku wird geladen", "resolution": "Ladeende unbekannt; Gerätemeldung abwarten."}],
            "dataAgeSeconds": {"mower": 26, "irrigation": 8, "controller": 34, "safetyBundle": 420},
            "lastAction": {"action": "START_IRRIGATION", "label": "Dienstverarbeitung abgeschlossen", "deviceExecutionConfirmed": False}},
        "dataQuality": {"displayOnly": False}, "irrigationSchedule": {}, "statistics": {}, "irrigationStatistics": {}, "clubhouseEvents": []}
    preview = re.sub(r'\[#if profile_json\?has_content\]\$\{profile_json\}\[#else\]\{\}\[/#if\]', '{"roleKeys":["Platzwart"]}', source)
    preview = preview.replace('https://func-ssv53platzpflege-prod-q7kbw54s.azurewebsites.net/api/platzwart', '/synthetic/platzwart')
    preview = re.sub(r'<img class="logo"[^>]*>', '<div class="logo" style="margin:auto;font-weight:800;color:#285ea7;padding:20px 0">SSV53</div>', preview)
    setup = '''<meta http-equiv="Content-Security-Policy" content="default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; img-src data:; connect-src 'none'; base-uri 'none'; form-action 'none'">
<script>
window.auditFixture=PAYLOAD;
localStorage.setItem('ssv53_platzwart_device_v1',JSON.stringify({deviceId:'synthetic',deviceToken:'not-a-credential'}));
sessionStorage.setItem('ssv53_platzwart_session_v1',JSON.stringify({token:'synthetic-not-signed',expiresAt:'2099-01-01T00:00:00Z'}));
window.fetch=async function(url,options){
 if(options&&options.method==='POST')return {ok:false,status:409,json:async()=>({error:'Vorschau: Es wird kein Gerätebefehl gesendet.'})};
 if(location.hash==='#offline')throw new TypeError('Failed to fetch');
 return {ok:true,status:200,json:async()=>window.auditFixture};
};
</script>'''.replace("PAYLOAD", json.dumps(payload, ensure_ascii=False))
    preview = preview.replace("<head>", "<head>\n" + setup, 1)
    preview = preview.replace("<body>", '<body><div style="background:#172033;color:white;padding:12px;text-align:center">SIMULATION · Beispielwerte · Netzwerk gesperrt · keine Gerätebefehle<br><a style="color:white" href="#offline" onclick="location.hash=\'offline\';document.getElementById(\'refresh\').click()">Verbindungsausfall zeigen</a></div>', 1)
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "appack-preview.html").write_text(preview, encoding="utf-8")
    (OUT / "appack-preview-mobile.html").write_text('''<!doctype html><html lang="de"><meta charset="utf-8"><title>Mobile Prüfung – 390 px</title><style>body{margin:0;background:#e8edf3;font-family:Arial;text-align:center}iframe{width:390px;height:1600px;border:1px solid #657084;background:white;max-width:100%}</style><h1>Appack-Vorschau · 390 px</h1><p>Browserreferenz mit synthetischen Daten; keine Geräteabnahme.</p><iframe title="Platzpflege Mobilvorschau" src="appack-preview.html"></iframe></html>''', encoding="utf-8")
    (OUT / "appack-preview-provenance.json").write_text(json.dumps({"source": "appack-platzwart-dashboard.html", "sha256": hashlib.sha256((ROOT / "appack-platzwart-dashboard.html").read_bytes()).hexdigest(), "synthetic": True, "network": "blocked by CSP", "device_acceptance": False}, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    build()
