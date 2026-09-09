"""Replay dated control-cycle copies locally; never contact or command devices.

Input: {"schema_version": 1, "cycles": [CycleResult, ...]}; need: one explicitly
approved occurrence in the contract documented in shadow-integration.md.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from collections import Counter, deque
from datetime import datetime, timedelta, timezone
from pathlib import Path
from time import monotonic

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from daily_safety_report import charging_evidence, estimate_charging_end, parse_cycle_rows
from mower.coordination_shadow import compare_charging_window

MAX_INPUT_BYTES = 64 * 1024 * 1024
MAX_REPLAY_SECONDS = 30
MAX_EVIDENCE_ROWS = 5_000_000


def read_input(path: Path) -> dict:
    if path.stat().st_size > MAX_INPUT_BYTES:
        raise ValueError('Input exceeds the local replay size limit')
    with path.open('rb') as source:
        raw = source.read(MAX_INPUT_BYTES + 1)
    if len(raw) > MAX_INPUT_BYTES:
        raise ValueError('Input grew beyond the local replay size limit')
    return json.loads(raw.decode('utf-8'))


def replay(document: dict, need: dict) -> dict:
    deadline = monotonic() + MAX_REPLAY_SECONDS
    if not isinstance(document, dict) or document.get('schema_version') != 1 or not isinstance(document.get('cycles'), list):
        raise ValueError('A versioned control-cycle export is required')
    if not isinstance(need, dict):
        raise ValueError('A versioned approved need is required')
    cycles = document['cycles']
    if not 1 <= len(cycles) <= 20000:
        raise ValueError('Expected 1 to 20000 dated cycles')
    history, previous, previous_at = deque(), None, None
    evidence_rows = 0
    counts: Counter[str] = Counter()
    first_proposal = None
    stopped = False
    reviewed = []
    for cycle in cycles:
        if monotonic() >= deadline:
            raise ValueError('Local replay time budget exceeded; no complete result')
        now = datetime.fromisoformat(cycle['executed_at_utc'].replace('Z', '+00:00'))
        if now.tzinfo is None or now.utcoffset() is None:
            raise ValueError('Cycle time has no timezone')
        now = now.astimezone(timezone.utc)
        if previous_at is not None and now <= previous_at:
            raise ValueError('Cycles must be unique and strictly chronological')
        mower = cycle['details'].get('mower') or {}
        row = {'timestamp': now.isoformat(), 'activity': mower.get('activity'),
                     'mower_state': mower.get('state'), 'error_code': mower.get('error_code'),
                     'battery_percent': mower.get('battery_percent'), 'mower_id': mower.get('mower_id'),
                     'mower_connected': mower.get('connected'),
                     'mower_status_timestamp_ms': mower.get('status_timestamp_ms')}
        # Parse each dated observation once. Preserve the report parser's latest
        # observation per minute, using only the prefix already seen here.
        observation = parse_cycle_rows([row])[0]
        if history and history[-1].timestamp_utc.replace(second=0, microsecond=0) == now.replace(second=0, microsecond=0):
            history.pop()
        history.append(observation)
        while history and history[0].timestamp_utc < now - timedelta(days=7):
            history.popleft()
        # Do not repeatedly rebuild charging evidence for cycles already ruled
        # out by missing capture, stop, station, water or approval evidence.
        result = compare_charging_window(cycle=cycle, previous_cycle=previous, need=need,
                                         charging_end_estimate=None)
        estimate = None
        if mower.get('activity') == 'CHARGING' and result['blockers'] == ['EMPIRICAL_CHARGING_END_UNKNOWN']:
            evidence_rows += len(history)
            if evidence_rows > MAX_EVIDENCE_ROWS:
                raise ValueError('Local charging evidence budget exceeded; no complete result')
            estimate = estimate_charging_end(charging_evidence(history, now), mower, now)
            result = compare_charging_window(cycle=cycle, previous_cycle=previous, need=need,
                                             charging_end_estimate=estimate)
        frame = cycle['details'].get('coordination_shadow_input') or {}
        if ((frame.get('manual_stop') is True and frame.get('state_available') is True)
                or 'MANUAL_STOP' in result['blockers']):
            stopped = True
        if stopped:
            result.update(status='BLOCKED', selected_start_utc=None)
            result['blockers'].append('MANUAL_STOP_REQUIRES_REVIEW')
        if result['status'] == 'SHADOW_PROPOSAL' and first_proposal is None:
            first_proposal = {'observed_at_utc': now.isoformat(), **result}
        counts.update(result['blockers'] or [result['status']])
        # The immutable first proposal can be compared after a process restart
        # by replaying the same dated inputs. Later observations never rewrite it.
        reviewed.append({'at': now.isoformat(), 'status': result['status'], 'blockers': result['blockers']})
        previous, previous_at = cycle, now
    if monotonic() >= deadline:
        raise ValueError('Local replay time budget exceeded; no complete result')
    report = {
        'schema_version': 1, 'shadow_only': True, 'execution_available': False,
        'input_sha256': hashlib.sha256(json.dumps(document, sort_keys=True).encode()).hexdigest(),
        'need_sha256': hashlib.sha256(json.dumps(need, sort_keys=True).encode()).hexdigest(),
        'cycle_count': len(cycles), 'status_counts': dict(counts), 'first_proposal': first_proposal,
        'manual_stop_latched_in_replay': stopped, 'observations': reviewed,
        'production_actions': [], 'productive_mowing_gain_minutes': None,
    }
    if monotonic() >= deadline:
        raise ValueError('Local replay time budget exceeded; no complete result')
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--cycles', required=True, type=Path)
    parser.add_argument('--approved-need', required=True, type=Path)
    parser.add_argument('--output', required=True, type=Path)
    args = parser.parse_args()
    try:
        result = replay(read_input(args.cycles), read_input(args.approved_need))
        # Preserve earlier evidence and make two concurrent writers fail rather
        # than silently overwrite a previous comparison. No automatic directory.
        with args.output.open('x', encoding='utf-8') as destination:
            json.dump(result, destination, ensure_ascii=False, indent=2)
            destination.write('\n')
    except (OSError, ValueError, KeyError, TypeError, AttributeError) as exc:
        print('Shadow replay failed: ' + type(exc).__name__, file=sys.stderr)
        return 2
    print(json.dumps({'cycle_count': result['cycle_count'], 'proposal': result['first_proposal'] is not None,
                      'execution_available': False}))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
