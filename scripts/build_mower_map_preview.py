"""Synthetic UI preview. Only public map assets/images may access the network."""
from pathlib import Path
import json
from datetime import datetime, timezone
from build_audit_preview import build

out=Path('docs/ui-2026-09-12/mower-map/preview')
old=Path('docs/ui-2026-09-12/contextual-actions/preview/mowing/appack-preview.html').read_text(encoding='utf-8')
fixture=json.JSONDecoder().raw_decode(old.split('window.auditFixture=',1)[1])[0]
fixture['generatedAt']=datetime.now(timezone.utc).isoformat()
fixture['mower']['position']={'latitude':52.594709,'longitude':13.130208}
fixture['mower']['statusTimestamp']=int(datetime.now(timezone.utc).timestamp()*1000)
fixture['mower']['connected']=True
build(output=out,fixture=fixture)
page=out/'appack-preview.html'
html=page.read_text(encoding='utf-8').replace("script-src 'unsafe-inline'; style-src 'unsafe-inline'; img-src data:;", "script-src 'unsafe-inline' https://unpkg.com; style-src 'unsafe-inline' https://unpkg.com; img-src data: https://isk.geobasis-bb.de;")
html=html.replace('SIMULATION · Beispielwerte · Netzwerk gesperrt · keine Gerätebefehle','VORSCHAU · Beispielposition · keine Gerätebefehle')
page.write_text(html,encoding='utf-8')
(out/'appack-preview-provenance.json').write_text(json.dumps({'synthetic':True,'position':'example only, not live', 'network':'only public Leaflet assets and LGB imagery; device calls disabled'},indent=2)+'\n',encoding='utf-8')
