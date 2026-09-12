"""A renewed suspension must be usable after the original schedule time."""
from copy import deepcopy
from dataclasses import replace
from datetime import timedelta
import pytest
from mower.full_failsafe import run_full_failsafe_cycle, _clear_irrigation, _cancel_irrigation_without_run
from mower.irrigation_recovery import _reset_state
from mower.state import AutomationState
from mower.state_store import InMemoryStateStore
from tests.test_full_failsafe import NOW, ENV, irrigation_state, suspended_result, settings

LATE=NOW+timedelta(minutes=40)

def step(store, at, calls, *, mower_at=None, fault=None, water_delay_seconds=0):
    cycle=deepcopy(suspended_result())
    cycle.details['mower']['status_timestamp_ms']=int((mower_at or at).timestamp()*1000)
    cycle.details['hydrawise']['safety']['observed_at_utc']=(at-timedelta(seconds=water_delay_seconds)).isoformat()
    if fault=='water_stale':cycle.details['hydrawise']['safety']['fresh']=False
    if fault=='mower_offline':cycle.details['mower']['connected']=False
    if fault=='mower_leaves':cycle.details['mower'].update(activity='LEAVING',mode='MAIN_AREA')
    if fault=='schedule_reappears':
        cycle.details['hydrawise']['zone_observations'][0]['scheduled_start_utc']=(at+timedelta(minutes=1)).isoformat()
    def unexpected(*args):raise AssertionError('Unexpected non-irrigation command')
    return run_full_failsafe_cycle(now_utc=at,settings=settings(),environment=ENV,past_due=False,source='unit-test',
        read_only_runner=lambda **_:cycle,state_store_factory=lambda _:store,
        park_sender=unexpected,start_sender=unexpected,suspend_zone_sender=unexpected,
        start_zone_sender=lambda *args:calls.append(args) or {'message_type':'info'},
        stop_zone_sender=lambda *_:{'message_type':'info'},command_clock=lambda:at)

def renew(*, water_delay_seconds=0):
    store=InMemoryStateStore(irrigation_state(phase='READY'));calls=[]
    a=step(store,LATE,calls,mower_at=LATE,water_delay_seconds=water_delay_seconds)
    b=step(store,LATE+timedelta(minutes=1),calls,mower_at=LATE,water_delay_seconds=water_delay_seconds)
    assert [a.decision_code,b.decision_code]==['IRRIGATION_SUSPENSION_REVALIDATING','IRRIGATION_SUSPENSION_REVALIDATED']
    assert not calls
    return store,calls

@pytest.mark.parametrize('water_delay_seconds', [0, 10])
def test_late_run_starts_after_revalidation_and_restart_with_same_fresh_dock_event(water_delay_seconds):
    store,calls=renew(water_delay_seconds=water_delay_seconds)
    store=InMemoryStateStore(AutomationState.from_mapping(store.load().to_dict()))
    result=step(store,LATE+timedelta(minutes=2),calls,mower_at=LATE,water_delay_seconds=water_delay_seconds)
    assert result.decision_code=='IRRIGATION_ZONE_START_SENT'
    assert len(calls)==1

@pytest.mark.parametrize('fault',['water_stale','mower_offline','schedule_reappears'])
def test_renewal_never_overrides_missing_live_safety(fault):
    store,calls=renew()
    step(store,LATE+timedelta(minutes=2),calls,mower_at=LATE,fault=fault)
    assert not calls

def test_stale_dock_remains_blocked_after_successful_revalidation():
    store,calls=renew()
    result=step(store,LATE+timedelta(minutes=2),calls,mower_at=LATE-timedelta(minutes=2))
    assert result.decision_code=='IRRIGATION_WAIT_FOR_CONFIRMED_PARK'
    assert not calls

def test_renewal_expires_and_a_long_gap_needs_new_distinct_observations():
    store,calls=renew()
    result=step(store,LATE+timedelta(minutes=6),calls)
    assert result.decision_code=='IRRIGATION_WAIT_FOR_CONFIRMED_PARK'
    result=step(store,LATE+timedelta(minutes=7),calls)
    assert result.decision_code=='IRRIGATION_SUSPENSION_REVALIDATING'
    assert store.load().irrigation_suspension_revalidation_observations==1
    assert not calls

@pytest.mark.parametrize('operation', ['clear', 'cancel', 'recovery'])
def test_finished_plan_cannot_pass_its_renewal_to_another_plan(operation):
    store, _ = renew()
    state = store.load()
    assert state.irrigation_suspension_revalidation_observed_utc
    if operation == 'clear':
        cleared = _clear_irrigation(state)
    elif operation == 'cancel':
        cleared = _cancel_irrigation_without_run(state, now_utc=LATE+timedelta(minutes=2))
    else:
        cleared = _reset_state(state, now_utc=LATE+timedelta(minutes=2),
            hydrawise_observed_utc=LATE.isoformat(),
            confirmed_clear_since_utc=LATE.isoformat(), confirmed_clear_origin='DATA_GAP')
    assert cleared.irrigation_suspension_revalidation_observed_utc is None
    assert cleared.irrigation_suspension_revalidation_last_seen_utc is None
    assert cleared.irrigation_suspension_revalidation_observations == 0
