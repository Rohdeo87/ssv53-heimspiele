"""Actual calendar template and FullCalendar, with isolated synthetic data only."""
import hashlib
import json
import re
import subprocess
from pathlib import Path
from urllib.request import urlopen

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "docs/ui-2026-09-11/calendar-polish"
OUT.mkdir(parents=True, exist_ok=True)
asset = OUT / "fullcalendar.js"
if not asset.exists():
    with urlopen("https://cdn.appack.de/modules/fullcalendar-scheduler-6.1.15/dist/index.global.js", timeout=30) as response:
        asset.write_bytes(response.read())

base = subprocess.check_output(["git", "show", "72ed7e6:appack-platzbelegungsplan-azure.html"], cwd=ROOT).decode("utf-8")
fixture = {
    "data_source": "azure", "season": "Sommer",
    "resources": [{"id": "rasen", "title": "Rasen"}, {"id": "kunstrasen", "title": "Kunstrasen"}],
    "training_calendar": {"active": True, "mode": "ACTIVE", "trainingControl": {"available": True, "fail_closed": False, "season": "Sommer"}},
    "events": []
}
for day in (10, 11, 14):
    for pitch, name, start, end in [("rasen", "C-Junioren", "17:00", "18:30"), ("rasen", "1. Herren", "19:00", "20:30"), ("kunstrasen", "E-Junioren", "16:30", "18:00"), ("kunstrasen", "B-Junioren", "18:00", "19:30")]:
        fixture["events"].append({"id": f"example-{day}-{pitch}-{start}", "title": name, "team": name, "source": "training", "resourceId": pitch, "start": f"2026-09-{day}T{start}:00+02:00", "end": f"2026-09-{day}T{end}:00+02:00", "area": "vorne & hinten", "blocking": True})

bootstrap = '''<meta http-equiv="Content-Security-Policy" content="default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; connect-src 'none'">
<script>
window.Workbook={load:async()=>[]};
window.fetch=async function(url,options){
 if(options&&options.method&&options.method!=='GET')throw new Error('Vorschau: Speichern ist gesperrt.');
 if(!String(url).includes('/api/occupancy?'))throw new Error('Vorschau: Fremde Abfrage gesperrt.');
 const mode=new URLSearchParams(location.search).get('mode');
 await new Promise(r=>setTimeout(r,mode==='slow'?4500:150));
 if(mode==='error')throw new Error('Vorschau: Daten nicht erreichbar.');
 const payload=__FIXTURE__;
 if(mode==='legacy')payload.training_calendar={active:false};
 return {ok:true,json:async()=>payload};
};
</script><script src="fullcalendar.js" defer></script>
'''.replace("__FIXTURE__", json.dumps(fixture, ensure_ascii=False))

for name, source in [("before", base), ("after", (ROOT / "appack-platzbelegungsplan-azure.html").read_text(encoding="utf-8"))]:
    html = re.sub(r'<link\b[^>]*>', '', source)
    html = re.sub(r'<script src=[^>]*></script>', '', html)
    html = html.replace('[#assign hauptfarbe="var(--appack-color-main)"]', '')
    html = html.replace('${hauptfarbe}', '#285ea7').replace('${userTitle}', 'Platzbelegung · Vorschau mit Beispielterminen')
    html = html.replace('[#if profile_json?has_content]${profile_json}[#else]{}[/#if]', '{"roles":[{"enumKey":"TR"}]}')
    html = html.replace("${worksheet['Einstellungen']}", 'example-settings')
    html = html.replace('</head>', bootstrap + '</head>')
    (OUT / f'{name}.html').write_text(html, encoding="utf-8", newline="\n")

(OUT / 'provenance.json').write_text(json.dumps({"synthetic_events": True, "original_calendar_logic": True, "external_connections_blocked": True, "native_device_acceptance": False, "source_sha256": hashlib.sha256((ROOT / 'appack-platzbelegungsplan-azure.html').read_bytes()).hexdigest()}, indent=2)+'\n', encoding='utf-8')
print(OUT)
