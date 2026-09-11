"""Compare public calendar CSS and logic without login or device requests."""
import argparse
import hashlib
import json
import re
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from urllib.request import urlopen

root = Path(__file__).resolve().parents[1]
parser = argparse.ArgumentParser()
parser.add_argument('--before', action='store_true')
args = parser.parse_args()
expected = subprocess.check_output(['git', 'show', '72ed7e6:appack-platzbelegungsplan-azure.html'], cwd=root).decode() if args.before else (root / 'appack-platzbelegungsplan-azure.html').read_text(encoding='utf-8')
url = 'https://appack.de/rest-api/drender/6a6242dfccdd23e7a9553567'
with urlopen(url, timeout=30) as response:
    status = response.status
    actual = response.read().decode('utf-8')

def blocks(html):
    css = re.search(r'<style[^>]*>(.*?)</style>', html, re.S).group(1)
    css = re.sub(r'\[#assign[^\n]*\]', '', css).replace('${hauptfarbe}', 'var(--appack-color-main)')
    js = next(s for s in re.findall(r'<script[^>]*>(.*?)</script>', html, re.S) if 'function applySharedTrainingCalendar(' in s)
    js = re.sub(r'var profileJSON = .*?;', 'var profileJSON = {};', js, count=1)
    js = re.sub(r'workbook: "[^"\n]*"', 'workbook: "WORKBOOK"', js)
    return [b.replace('\r\n', '\n').strip() for b in (css, js)]

local_blocks, served_blocks = blocks(expected), blocks(actual)
matches = dict(zip(('css', 'javascript'), (a == b for a, b in zip(local_blocks, served_blocks))))
proof = {'at_utc': datetime.now(timezone.utc).isoformat(), 'template_id': '6a6242dfccdd23e7a9553567', 'url': url, 'http_status': status, 'phase': 'before' if args.before else 'published', 'matches': matches, 'profile_and_workbook_normalized': True, 'source_sha256_lf': hashlib.sha256(expected.replace('\r\n', '\n').encode()).hexdigest(), 'device_commands_sent': False}
out = root / 'docs/ui-2026-09-11/calendar-polish'
out.mkdir(parents=True, exist_ok=True)
(out / ('before-publication.json' if args.before else 'publication.json')).write_text(json.dumps(proof, indent=2)+'\n', encoding='utf-8')
print(json.dumps(proof, indent=2))
assert all(matches.values()), 'Current deployed source differs from expected source'
