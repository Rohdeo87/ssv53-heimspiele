"""Read existing admin preview and selected device-action traces, never mutate."""
from datetime import datetime, timezone
import json
import argparse
from pathlib import Path
import requests
from read_grounds_installation import az, APP, GROUP, HOST

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('--output', type=Path, default=Path('docs/ui-2026-09-12/manual-start-release'))
args = parser.parse_args()
out = args.output
out.mkdir(parents=True, exist_ok=True)
key = az('functionapp', 'keys', 'list', '-g', GROUP, '-n', APP)['masterKey']
r = requests.get(HOST + '/api/mower/recover-unsent-start', headers={'x-functions-key':key},
                 timeout=(5,40), allow_redirects=False)
r.raise_for_status()
preview = r.json()
(out/'existing-admin-preview.json').write_text(json.dumps({'at_utc':datetime.now(timezone.utc).isoformat(),
    'read_only':True,'preview':preview},indent=2)+'\n',encoding='utf-8')
print(json.dumps({'unresolved':preview.get('journal', {}).get('unresolvedDeviceSendCount'),
                 'journal':preview.get('journal'), 'legacy_recovery_eligible':preview.get('eligible')}))
query = ('traces | where timestamp > datetime(2026-09-11T12:00:00Z) and message startswith "SSV53_CONTROL_CYCLE " '
    '| extend p=parse_json(substring(message,20)) '
    '| where p.command_sent == true or p.decision_code has_any ("FAILED", "REJECTED") '
    '| project timestamp,p | order by timestamp desc | take 150')
data = az('monitor','app-insights','query','-g',GROUP,'--app','appi-ssv53platzpflege-prod-q7kbw54s',
          '--analytics-query',query,'--offset','2d')['tables'][0]['rows']
rows=[]
for at,p in data:
    p=json.loads(p) if isinstance(p,str) else p
    d=p.get('details') or {}
    actions={k:v for k,v in d.items() if k.endswith('_action') or k in ('operator_request','operatorAction','cutting_height')}
    rows.append({'at':at,'decision':p.get('decision_code'),'sent':p.get('command_sent'),
                 'message':p.get('message'),'actions':actions})
(out/'device-action-traces.json').write_text(json.dumps({'at_utc':datetime.now(timezone.utc).isoformat(),
    'read_only':True,'query':query,'rows':rows},indent=2)+'\n',encoding='utf-8')
print(json.dumps({'action_traces':len(rows),'latest':rows[:2]}))
query = ('traces | where timestamp > ago(15m) and message startswith "SSV53_CONTROL_CYCLE " '
    '| extend p=parse_json(substring(message,20)) | project timestamp,p | order by timestamp asc | take 30')
data = az('monitor','app-insights','query','-g',GROUP,'--app','appi-ssv53platzpflege-prod-q7kbw54s',
          '--analytics-query',query,'--offset','1h')['tables'][0]['rows']
recent=[]
for at,p in data:
    p=json.loads(p) if isinstance(p,str) else p
    d=p.get('details') or {}
    water=d.get('hydrawise') or {}
    recent.append({'at':at,'decision':p.get('decision_code'),'sent':p.get('command_sent'),
        'manifest':(p.get('build_provenance') or {}).get('package_manifest_sha256'),
        'mower':{k:(d.get('mower') or {}).get(k) for k in ('mode','activity','state','connected','status_timestamp_ms','error_code')},
        'water':water.get('safety'), 'release':water.get('release_confirmation'),
        'park_hold':d.get('irrigation_park_hold'), 'start':d.get('start_action'),
        'device_write_reconciliation':d.get('device_write_reconciliation'),
        'manual':d.get('manual_session'), 'automation':d.get('automation_state')})
(out/'recent-cycles.json').write_text(json.dumps({'at_utc':datetime.now(timezone.utc).isoformat(),
    'read_only':True,'query':query,'rows':recent},indent=2)+'\n',encoding='utf-8')
print(json.dumps({'recent_cycles':len(recent), 'latest':recent[-1] if recent else None}))
