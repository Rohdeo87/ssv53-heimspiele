"""Apply one reviewed administrator preview; no retries or device commands."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path

import requests
from read_grounds_installation import APP, GROUP, HOST, az


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--preview', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    raw = args.preview.read_bytes()
    evidence = json.loads(raw)
    now = datetime.now(timezone.utc)
    observed = datetime.fromisoformat(evidence['at_utc'])
    if observed.tzinfo is None or not 0 <= (now - observed).total_seconds() <= 120:
        raise RuntimeError('Preview is not current; inspect again')
    preview = evidence['preview']
    if evidence.get('read_only') is not True or preview.get('dryRun') is not True or preview.get('eligible') is not True:
        raise RuntimeError('Preview does not permit recovery')
    body = {
        'confirmation': 'RECOVER_VERIFIED_UNSENT_MOWER_START',
        'expectedStateRevision': preview['stateRevision'],
        'pendingFingerprint': preview['pendingFingerprint'],
        'proofToken': preview['proofToken'],
    }
    key = az('functionapp', 'keys', 'list', '-g', GROUP, '-n', APP)['masterKey']
    response = requests.post(HOST + '/api/mower/recover-unsent-start', json=body,
                             headers={'x-functions-key': key}, timeout=(5, 55), allow_redirects=False)
    report = {'at_utc': datetime.now(timezone.utc).isoformat(),
              'preview_sha256': hashlib.sha256(raw).hexdigest(),
              'status_code': response.status_code, 'result': response.json(),
              'device_commands_requested': False}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(json.dumps(report, ensure_ascii=False))
    if response.status_code != 200 or report['result'].get('applied') is not True:
        raise RuntimeError('Recovery not confirmed; inspect before any further attempt')


if __name__ == '__main__':
    main()
