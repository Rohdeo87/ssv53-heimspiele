"""Compare changed status functions against public Appack delivery; read only."""
from datetime import datetime, timezone
import argparse
import hashlib
import json
from pathlib import Path
from urllib.request import urlopen

root = Path(__file__).resolve().parents[1]
parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('--output', type=Path, default=root / 'docs/ui-2026-09-12/paused-status/publication.json')
parser.add_argument('--functions', nargs='+', default=['mowerStopNotice', 'simpleStatus', 'nextStartInfo', 'dashboardMessage'])
args = parser.parse_args()
url = "https://appack.de/rest-api/drender/6a86ab6c4b3c829dd60de9b7"
with urlopen(url, timeout=30) as response:
    public = response.read().decode("utf-8").replace("\r\n", "\n")
    status = response.status
local = (root / "appack-platzwart-dashboard.html").read_text(encoding="utf-8")

def function(source, name):
    lines = source[source.index("    function " + name + "("):].splitlines()
    if not lines[0].endswith("{"):
        return lines[0]
    end = next(i for i, line in enumerate(lines) if i > 0 and line == "    }")
    return "\n".join(lines[:end+1])

names = args.functions
for name in names:
    assert function(public, name) == function(local, name), name + " differs in public delivery"
proof = {"at_utc": datetime.now(timezone.utc).isoformat(), "read_only": True,
         "url": url, "http_status": status, "changed_functions_match": names,
         "local_template_sha256_lf": hashlib.sha256(local.encode()).hexdigest(),
         "device_commands_sent": False, "native_device_tested": False}
args.output.write_text(
    json.dumps(proof, indent=2) + "\n", encoding="utf-8")
print(json.dumps(proof))
