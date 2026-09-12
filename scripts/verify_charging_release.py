"""Read-only verification of the charging UI, flags and prospective collector."""
import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import requests
from activate_onsite_dock_release import PUBLIC, settings
from read_grounds_installation import APP, GROUP, HOST, az

parser=argparse.ArgumentParser(description=__doc__)
parser.add_argument("--manifest-sha256",required=True)
parser.add_argument("--template-sha256",required=True)
parser.add_argument("--output",type=Path,required=True)
args=parser.parse_args()
source=Path("appack-platzwart-dashboard.html").read_text(encoding="utf-8")
assert hashlib.sha256(source.encode()).hexdigest()==args.template_sha256
response=requests.get(PUBLIC,timeout=(5,40),headers={"Cache-Control":"no-cache"})
response.raise_for_status()
public=response.content.decode("utf-8").replace("\r\n","\n")
lines=source.splitlines();verified=[]
for name in ("chargingEnd","renderCoordination","mergeDisplayDetails","pfChargingInfo"):
    start=next(i for i,line in enumerate(lines) if line.startswith("    function "+name+"("))
    end=start
    if lines[start].rstrip().endswith("{"):
        end=next(i for i in range(start+1,len(lines)) if lines[i].rstrip()=="    }")
    assert "\n".join(lines[start:end+1]) in public, name
    verified.append(name)
key=az("functionapp","keys","list","-g",GROUP,"-n",APP)["masterKey"]
manifest=requests.get(HOST+"/admin/vfs/home/site/wwwroot/package-manifest.json",headers={"x-functions-key":key},timeout=(5,30))
manifest.raise_for_status()
assert hashlib.sha256(manifest.content).hexdigest()==args.manifest_sha256
config=settings()
assert config["MOWER_AUTOMATIC_TAKEOVER_ENABLED"]["value"]=="true"
assert config.get("CHARGING_LEARNER_ENABLED",{}).get("value","true").lower()=="true"
query=('traces | where timestamp > ago(10m) and message startswith "SSV53_CONTROL_CYCLE " '
       '| extend p=parse_json(substring(message,20)) '
       '| project timestamp, manifest=tostring(p.build_provenance.package_manifest_sha256), '
       'calibration=p.charging_calibration, decision=tostring(p.decision_code), command_sent=tobool(p.command_sent) '
       '| order by timestamp desc | take 5')
table=az("monitor","app-insights","query","-g",GROUP,"--app","appi-ssv53platzpflege-prod-q7kbw54s",
         "--analytics-query",query,"--offset","1h")["tables"][0]
rows=[dict(zip([c["name"] for c in table["columns"]],r)) for r in table["rows"]]
for row in rows:
    if isinstance(row.get("calibration"),str) and row["calibration"]:
        row["calibration"]=json.loads(row["calibration"])
report={"at_utc":datetime.now(timezone.utc).isoformat(),"read_only":True,
        "manifest_sha256":args.manifest_sha256,"template_sha256":args.template_sha256,
        "public_functions_match":verified,"settings_changed":False,
        "automatic_takeover_still_enabled":True,"charging_collector_enabled":True,
        "rows":rows,"collector_live_verified":any(r["manifest"]==args.manifest_sha256 and isinstance(r.get("calibration"),dict) and r["calibration"].get("recorded") for r in rows)}
args.output.write_text(json.dumps(report,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
print(json.dumps(report))
