"""Verify the six changed UI functions in actual public Appack delivery."""
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
from urllib.request import urlopen

root = Path(__file__).resolve().parents[1]
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

names = ["dashboardMessage", "stationConfirmed", "stationDisplaySafe", "mowerActions", "render", "pfVisibility"]
for name in names:
    assert function(public, name) == function(local, name), name + " differs in actual delivery"
proof = {"at_utc": datetime.now(timezone.utc).isoformat(), "read_only": True, "url": url,
         "http_status": status, "changed_functions_match": names,
         "local_template_sha256_lf": hashlib.sha256(local.encode()).hexdigest(),
         "device_commands_sent": False, "native_device_tested": False}
out = root / "docs/ui-2026-09-12/manual-start-release/publication.json"
out.write_text(json.dumps(proof, indent=2) + "\n", encoding="utf-8")
print(json.dumps(proof))
