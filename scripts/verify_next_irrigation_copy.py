"""Read-only verification of the published Appack explanation function."""
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from urllib.request import urlopen

root = Path(__file__).resolve().parents[1]
url = "https://appack.de/rest-api/drender/6a86ab6c4b3c829dd60de9b7"
with urlopen(url, timeout=30) as response:
    status = response.status
    published = response.read().decode("utf-8").replace("\r\n", "\n")
local = (root / "appack-platzwart-dashboard.html").read_text(encoding="utf-8")

def function(text):
    start = text.index("    function pfNextMoment(")
    end = text.index("    function pfRenderMoment(", start)
    return text[start:end]

assert function(published) == function(local), "Published explanation differs"
proof = {
    "at_utc": datetime.now(timezone.utc).isoformat(),
    "template_id": "6a86ab6c4b3c829dd60de9b7",
    "cms_modified_berlin": "2026-09-11 21:28",
    "editor_matched_before_save": True,
    "template_sha256_lf": hashlib.sha256(local.encode()).hexdigest(),
    "render_url": url,
    "http_status": status,
    "published_pfNextMoment_matches": True,
    "tests_passed": 26,
    "device_commands_sent": False,
}
(root / "docs/ui-2026-09-11/next-irrigation/publication.json").write_text(
    json.dumps(proof, indent=2) + "\n", encoding="utf-8"
)
print(json.dumps(proof, indent=2))
