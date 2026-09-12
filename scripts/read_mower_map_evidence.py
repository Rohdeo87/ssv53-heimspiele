"""Read-only provider/installation check. Never sends device commands."""
from datetime import datetime, timezone
import hashlib
import json
import re
from pathlib import Path
import sys
import xml.etree.ElementTree as ET

import requests

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from read_grounds_installation import APP, GROUP, HOST, az
from mower.husqvarna import fetch_mowers, select_mower

out = Path('docs/ui-2026-09-12/mower-map')
out.mkdir(parents=True, exist_ok=True)
config = {r['name']: r.get('value') for r in az('functionapp', 'config', 'appsettings', 'list', '-g', GROUP, '-n', APP)}
def resolved(name):
    value=config[name]
    match=re.fullmatch(r'@Microsoft.KeyVault\(SecretUri=(https://[^/]+\.vault\.azure\.net/secrets/[^;)]+)\)',value)
    return az('keyvault','secret','show','--id',match.group(1))['value'] if match else value

try:
    item = select_mower(fetch_mowers(resolved('HUSQVARNA_CLIENT_ID'), resolved('HUSQVARNA_CLIENT_SECRET')))
    vendor_read = 'success'
except Exception as exc:
    item = {}
    vendor_read = type(exc).__name__ + ': direct manufacturer read unavailable'
a = item.get('attributes', {})
positions = a.get('positions', [])
if positions:
    # Keep the observed GPS out of the public evidence/commit.
    (out/'live-position-local.json').write_text(json.dumps({'position':positions[0], 'metadata':a.get('metadata',{})}),encoding='utf-8')
key = az('functionapp', 'keys', 'list', '-g', GROUP, '-n', APP)['masterKey']
manifest = requests.get(HOST + '/admin/vfs/home/site/wwwroot/package-manifest.json', headers={'x-functions-key': key}, timeout=(5,30))
manifest.raise_for_status()
endpoint = 'https://isk.geobasis-bb.de/mapproxy/dop20c/service/wms'
r = requests.get(endpoint, params={'SERVICE':'WMS','VERSION':'1.1.1','REQUEST':'GetCapabilities'}, timeout=(5,30))
r.raise_for_status()
root = ET.fromstring(r.content)
layers = [{'name':e.findtext('Name'), 'title':e.findtext('Title'), 'srs':e.findtext('SRS')} for e in root.iter('Layer') if e.findtext('Name')]
report = {'at_utc':datetime.now(timezone.utc).isoformat(), 'read_only':True, 'vendor_read': vendor_read,
          'manifest_sha256':hashlib.sha256(manifest.content).hexdigest(),
          'model':a.get('system',{}).get('model'), 'capability_position':a.get('capabilities',{}).get('position'),
          'position_count':len(positions), 'latest_position_fields':sorted(positions[0]) if positions else [],
          'metadata':{k:a.get('metadata',{}).get(k) for k in ('connected','statusTimestamp')},
          'map_endpoint':endpoint, 'map_layers':layers}
(out/'baseline.json').write_text(json.dumps(report,indent=2)+'\n',encoding='utf-8')
(out/'wms-capabilities.xml').write_bytes(r.content)
print(json.dumps(report))
