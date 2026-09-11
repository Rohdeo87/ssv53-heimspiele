"""Read-only preview of the fixed production unsent-start recovery."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path

import requests
from read_grounds_installation import APP, GROUP, HOST, az


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    key = az('functionapp', 'keys', 'list', '-g', GROUP, '-n', APP)['masterKey']
    response = requests.get(HOST + '/api/mower/recover-unsent-start',
                            headers={'x-functions-key': key}, timeout=(5, 55), allow_redirects=False)
    response.raise_for_status()
    if response.status_code != 200:
        raise RuntimeError('Unexpected preview response')
    report = {'at_utc': datetime.now(timezone.utc).isoformat(), 'read_only': True,
              'preview': response.json()}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(json.dumps(report, ensure_ascii=False))


if __name__ == '__main__':
    main()
