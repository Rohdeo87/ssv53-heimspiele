"""Recorded native rescheduling caused by our own prefix suspensions."""
from copy import deepcopy
from datetime import datetime, timedelta
import json
from pathlib import Path
import pytest
from mower.full_failsafe import _reconcile_prestart_plan

ROOT=Path(__file__).resolve().parents[1]
ROWS=json.loads((ROOT/'docs/ui-2026-09-12/irrigation-reliability/incident-evidence.json').read_text(encoding='utf-8'))['observations']
PLAN=next(row['source_zones'] for row in ROWS if row['decision']=='PARK_COMMAND_SENT')
CHANGES=[row for row in ROWS if row['decision']=='IRRIGATION_PLAN_UPDATED']

def reconcile(plan,row,safety=None):
    live=deepcopy(row['source_zones'])
    return _reconcile_prestart_plan(plan=deepcopy(plan),suspended_relay_ids=set(row['reconciliation']['suspended_relay_ids']),
        details={'hydrawise':{'safety':safety or {'available':True,'fresh':True,'active_zone_count':0,'active_relay_ids':[]},'zones':live,'zone_observations':[{**z,'valid':True,'running':False} for z in live]}},
        capture_max_lead_minutes=60,expected_relay_ids=frozenset(z['relay_id'] for z in plan),now_utc=datetime.fromisoformat(row['at']))

@pytest.mark.parametrize('row',CHANGES,ids=lambda r:r['at'])
def test_own_suspension_preserves_original_nonoverlapping_plan(row):
    kind,updated,reason=reconcile(PLAN,row)
    assert kind=='UNCHANGED'
    assert updated==PLAN

def test_unrelated_shift_is_not_mistaken_for_own_suspension():
    row=deepcopy(CHANGES[0])
    for z in row['source_zones']:
        if z['relay_id'] not in row['reconciliation']['suspended_relay_ids']:
            z['scheduled_start_utc']=(datetime.fromisoformat(z['scheduled_start_utc'])+timedelta(hours=1)).isoformat()
    kind,updated,_=reconcile(PLAN,row)
    assert kind=='UPDATED'
    assert updated!=PLAN

@pytest.mark.parametrize('delta', [-60, 60])
def test_duration_change_is_not_silently_ignored_or_saved_as_overlapping(delta):
    row=deepcopy(CHANGES[0]);row['source_zones'][0]['run_seconds']+=delta
    kind,updated,_=reconcile(PLAN,row)
    assert kind=='INVALID'
    assert updated is None

@pytest.mark.parametrize('fault',[{'active_zone_count':1},{'active_relay_ids':[PLAN[0]['relay_id']]},{'fresh':False},{'available':False}])
def test_inconsistent_or_old_aggregate_water_state_cannot_explain_compaction(fault):
    kind,updated,_=reconcile(PLAN,CHANGES[0],{'available':True,'fresh':True,'active_zone_count':0,'active_relay_ids':[],**fault})
    assert kind=='INVALID'
    assert updated is None
