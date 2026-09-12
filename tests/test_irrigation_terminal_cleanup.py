from copy import deepcopy
from dataclasses import replace
from datetime import timedelta
import json

import pytest

from mower.full_failsafe import _retire_completed_irrigation, _manual_dry_release_proof, run_full_failsafe_cycle
from mower.state import AutomationState
from mower.state_store import InMemoryStateStore
from tests.test_full_failsafe import ENV, NOW, RELAYS, result, settings
from tests.test_device_send_guard import _entry


def terminal():
    return AutomationState(
        irrigation_phase="COMPLETE_HOLD", irrigation_plan_id="finished",
        irrigation_completed_utc=(NOW-timedelta(minutes=5)).isoformat(),
        hydrawise_clear_since_utc=(NOW-timedelta(minutes=4)).isoformat(),
        hydrawise_drying_since_utc=(NOW-timedelta(minutes=5)).isoformat(),
        parked_by_automation=True, automation_restart_allowed=False,
        last_mower_state="STOPPED", last_mower_activity="NOT_APPLICABLE",
    )


def details():
    return {"hydrawise":{"safety":{
        "available":True,"fresh":True,"relay_set_valid":True,"clear_now":True,
        "active_zone_count":0,"imminent_zone_count":0,"active_relay_ids":[],"imminent_relay_ids":[],
        "observed_relay_ids":RELAYS,"observed_at_utc":NOW.isoformat(),
    }}}


def retire(state=None, payload=None):
    return _retire_completed_irrigation(state or terminal(), payload or details(),
        now_utc=NOW,expected_relay_ids=set(RELAYS),max_age_seconds=180)


def test_terminal_cleanup_preserves_stop_park_and_exact_drying_release_across_restart():
    before=terminal();after=retire(before)
    assert after.irrigation_phase is None
    for key in ["last_mower_state","parked_by_automation","automation_restart_allowed",
                "hydrawise_clear_since_utc","hydrawise_drying_since_utc"]:
        assert getattr(after,key)==getattr(before,key)
    safe=details()["hydrawise"]["safety"]
    old=_manual_dry_release_proof(before,safe,now_utc=NOW,environment=ENV)
    new=_manual_dry_release_proof(AutomationState.from_mapping(after.to_dict()),safe,now_utc=NOW,environment=ENV)
    assert old==new
    assert new["allowed"] is False
    assert new["required_clear_minutes"]==150


@pytest.mark.parametrize("change",[
    {"irrigation_phase":phase} for phase in [None,"RUNNING","START_RESERVED","READY","STOPPING","FAILED","UNKNOWN"]
]+[
    {"maintenance_mode":True},{"operator_request_status":"PENDING"},
    {"mower_start_pending_since_utc":NOW.isoformat()},
    {"irrigation_current_relay_id":RELAYS[0]},
    {"irrigation_zone_start_reserved_utc":NOW.isoformat()},
    {"irrigation_zone_started_utc":NOW.isoformat()},
    {"irrigation_schedule_override_json":"{}"},
    {"irrigation_completed_utc":None},
    {"irrigation_completed_utc":(NOW+timedelta(minutes=1)).isoformat()},
    {"device_send_journal_json":"{}"},
])
def test_no_cleanup_for_nonterminal_or_ambiguous_work(change):
    assert retire(replace(terminal(),**change)) is None


@pytest.mark.parametrize("change",[
    {"available":False},{"fresh":False},{"relay_set_valid":False},{"clear_now":False},
    {"active_zone_count":1},{"imminent_zone_count":1},{"active_relay_ids":[RELAYS[0]]},
    {"active_zone_count":False},{"active_zone_count":0.0},{"imminent_zone_count":False},
    {"imminent_relay_ids":[RELAYS[0]]},{"imminent_relay_ids":None},
    {"observed_relay_ids":RELAYS[:-1]},{"observed_relay_ids":None},
    {"observed_relay_ids":[*RELAYS,RELAYS[0]]},{"observed_relay_ids":[str(r) for r in RELAYS]},
    {"observed_at_utc":(NOW-timedelta(minutes=4)).isoformat()},
    {"observed_at_utc":(NOW+timedelta(seconds=1)).isoformat()},
])
def test_missing_stale_incomplete_or_active_water_never_retires_old_proof(change):
    payload=details();payload["hydrawise"]["safety"].update(change)
    assert retire(payload=payload) is None


@pytest.mark.parametrize("enabled", [False, True])
def test_controller_retires_terminal_state_while_stopped_without_any_device_command(enabled):
    live=result(activity="NOT_APPLICABLE",mower_state="STOPPED",block_source="training")
    live.details["hydrawise"]["safety"].update(details()["hydrawise"]["safety"])
    store=InMemoryStateStore(terminal())
    def forbidden(*args,**kwargs):raise AssertionError("No real or fake command should be needed")
    cycle=run_full_failsafe_cycle(now_utc=NOW,settings=settings(),
        environment={**ENV,**({"IRRIGATION_TERMINAL_CLEANUP_ENABLED":"true"} if enabled else {})},
        past_due=False,source="test",read_only_runner=lambda **_:live,
        state_store_factory=lambda _:store,park_sender=forbidden,start_sender=forbidden,
        suspend_zone_sender=forbidden,start_zone_sender=forbidden,stop_zone_sender=forbidden)
    assert ("irrigation_terminal_cleanup" in cycle.details) is enabled
    assert cycle.command_sent is False
    assert store.load().irrigation_phase == (None if enabled else "COMPLETE_HOLD")
    assert store.load().last_mower_state=="STOPPED"
    assert store.load().automation_restart_allowed is False


def test_cleanup_does_not_delay_protective_park_during_occupancy():
    live=result(activity="MOWING",mower_state="IN_OPERATION",block_source="training")
    live.details["hydrawise"]["safety"].update(details()["hydrawise"]["safety"])
    before=replace(terminal(), parked_by_automation=False, automation_restart_allowed=True)
    store=InMemoryStateStore(before); parks=[]
    def forbidden(*args,**kwargs):raise AssertionError("Only the protective park may be sent")
    cycle=run_full_failsafe_cycle(now_utc=NOW,settings=settings(),
        environment={**ENV,"IRRIGATION_TERMINAL_CLEANUP_ENABLED":"true"},
        past_due=False,source="test",read_only_runner=lambda **_:live,
        state_store_factory=lambda _:store,park_sender=lambda *args:parks.append(args) or {"accepted":True},
        start_sender=forbidden,suspend_zone_sender=forbidden,start_zone_sender=forbidden,
        stop_zone_sender=forbidden,cutting_height_sender=forbidden,blade_usage_reset_sender=forbidden)
    assert cycle.details["irrigation_terminal_cleanup"]["prepared"] is True
    assert len(parks)==1
    assert cycle.command_sent is True
    assert store.load().irrigation_phase is None


def test_phase_cleanup_does_not_make_second_cleanup_or_write_replay_eligible():
    after=retire()
    assert retire(after) is None


@pytest.mark.parametrize("kind", ["WATER_START", "WATER_STOP", "PARK"])
@pytest.mark.parametrize("status", ["RESERVED", "DISPATCHING", "SENT_UNCONFIRMED", "UNKNOWN"])
def test_unconfirmed_commands_are_never_retired_even_when_relays_are_off(kind, status):
    receipt={**_entry(1,status=status),"kind":kind}
    state=replace(terminal(),device_send_journal_json=json.dumps([receipt]))
    assert retire(state) is None


@pytest.mark.parametrize("status", ["CONFIRMED", "REJECTED"])
def test_terminal_receipts_are_preserved_for_audit(status):
    state=replace(terminal(),device_send_journal_json=json.dumps([_entry(1,status=status)]))
    assert retire(state).device_send_journal_json == state.device_send_journal_json
