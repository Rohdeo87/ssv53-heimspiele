"""Offline view of the real calendar controls; no Appack/device connection."""
import hashlib
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
source = (ROOT / "appack-platzbelegungsplan-azure.html").read_text(encoding="utf-8")
style = re.search(r'<style[^>]*>(.*?)</style>', source, re.S).group(1)
style = re.sub(r'\[#.*?\]', '', style).replace('${hauptfarbe}', '#285ea7')
controls = re.search(r'<section id="booking-controls".*?</section>', source, re.S).group()


def function(name, next_name):
    body = source.split('function ' + name + '(', 1)[1].split('function ' + next_name + '(', 1)[0]
    return 'function ' + name + '(' + body


shared = function('applySharedTrainingCalendar', 'updateViewButtons')
allowed = function('getAllowedCalendarIds', 'getVisibleCalendarIds')
output = ROOT / "docs/ui-2026-09-09/shared-training-preview.html"
html = '''<!doctype html><html lang="de"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"><title>Belegungsplan – Vorschau</title><style>''' + style + '''
body {font-family:Arial,sans-serif!important;padding:18px!important;overflow:auto!important;display:block!important}
html {overflow:auto!important} h1{font-size:24px;margin:6px 0 18px;color:#1f4d8c}
#booking-controls{position:relative!important;inset:auto!important;margin:0 0 18px!important;width:100%!important}
.preview-day{padding:20px;background:white;border:1px solid #dce4ee;border-radius:17px}
.preview-day h2{font-size:18px;margin:0 0 18px}.preview-item{padding:16px 0;border-top:1px solid #dce4ee}
.preview-item strong{display:block;margin-bottom:6px;font-size:19px}.preview-item span{color:#687588}
.preview-note{font-size:12px;color:#687588;margin-top:20px;line-height:1.5}
</style></head><body><h1>Platzbelegung</h1>''' + controls + '''
<main class="preview-day"><h2>Dienstag, 8. September</h2><div id="preview-events"></div></main>
<p class="preview-note">Vorschau mit erfundenen Terminen. Geprüft wird die Saison- und Platzwahl; dies ist keine Aufnahme aus der installierten App.</p>
<script>
const state={activeSeason:'Winter',activeCalendarId:'kunstrasen',resources:[]};
const SUMMER_RESOURCE_IDS=['rasen','kunstrasen'], WINTER_RESOURCE_IDS=['kunstrasen'];
function storageKey(k){return k} function storageSet(){} function ensureActiveCalendarSelection(){} function updateResources(){}
function renderPlaceFilters(){
document.getElementById('place-filters').innerHTML=['all'].concat(getAllowedCalendarIds()).map(id => '<button type="button" class="ssv-segment-button" data-place="'+id+'" aria-pressed="'+(state.activeCalendarId===id)+'">'+({all:'Alle',rasen:'Rasen',kunstrasen:'Kunstrasen'}[id])+'</button>').join('');
document.getElementById('preview-events').innerHTML=state.activeCalendarId==='kunstrasen'?'<p>Keine Belegung eingetragen.</p>':'<div class="preview-item"><strong>17:00–18:30 Uhr</strong><span>Test E1 · Rasenplatz</span></div><div class="preview-item"><strong>18:30–20:00 Uhr</strong><span>Test A · Rasenplatz</span></div>';
document.querySelectorAll('[data-place]').forEach(b => b.onclick=()=>{state.activeCalendarId=b.dataset.place;renderPlaceFilters()});
}
''' + allowed + shared + '''
applySharedTrainingCalendar({training_calendar:{active:true}});
document.querySelector('[data-calendar-view="resourceTimeGridDay"]').setAttribute('aria-pressed','true');
</script></body></html>'''
output.write_text(html, encoding="utf-8", newline="\n")
output.with_suffix('.json').write_text(json.dumps({
    "scope": "real calendar control markup, CSS and shared-source selection function; synthetic list and local DOM harness",
    "appack_source_sha256": hashlib.sha256(source.encode()).hexdigest(),
    "live_data": False, "cms_published": False, "native_device_acceptance": False,
}, indent=2) + '\n', encoding="utf-8", newline="\n")
print(output)
