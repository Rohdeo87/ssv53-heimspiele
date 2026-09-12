"""Verify actual public delivery of the corrected UI functions; read only."""
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
from urllib.request import urlopen

root=Path(__file__).resolve().parents[1]
url="https://appack.de/rest-api/drender/6a86ab6c4b3c829dd60de9b7"
with urlopen(url,timeout=30) as response:
    published=response.read().decode("utf-8").replace("\r\n","\n")
    http_status=response.status
local=(root / "appack-platzwart-dashboard.html").read_text(encoding="utf-8")

def function(source,name):
    start=source.index("    function "+name+"(")
    lines=source[start:].splitlines()
    if not lines[0].endswith("{"):
        return lines[0]
    end=next(i for i,line in enumerate(lines) if i>0 and line=="    }")
    return "\n".join(lines[:end+1])

names=["irrigationAwaitingStart","dashboardMessage","pfUpdate"]
for name in names:
    assert function(local,name)==function(published,name), name+" differs in public response"
handler='document.getElementById("irrigation-stop").onclick=function(){'
def stop_handler(source):
    start=source.index(handler)
    return source[start:source.index('document.getElementById("stop-cancel")',start)]
assert stop_handler(local)==stop_handler(published),"Public stop handler differs"
proof={"at_utc":datetime.now(timezone.utc).isoformat(),"url":url,"http_status":http_status,
       "template_sha256_lf":hashlib.sha256(local.encode()).hexdigest(),
       "public_functions_match":names,"stop_handler_matches":True,"device_commands_sent":False}
(root / "docs/ui-2026-09-12/irrigation-status/publication.json").write_text(json.dumps(proof,indent=2)+"\n",encoding="utf-8")
print(json.dumps(proof,indent=2))
