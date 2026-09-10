"""End-to-end policy admission plus controller cycles with synthetic device I/O."""
from copy import deepcopy
from dataclasses import replace
from datetime import timedelta

import pytest

from mower.full_failsafe import run_full_failsafe_cycle
from mower.manual_session import load_manual_session
from mower.runtime import RuntimeSettings
from mower.state import AutomationState
from mower.state_store import InMemoryStateStore
from tests.test_full_failsafe import NOW, RELAYS, result
from tests.test_manual_control_api import ENV, details, payload, submit


def ready():
    return AutomationState(parked_by_automation=True, automation_park_source="continuous", automation_restart_allowed=True,
        park_command_sent_utc=(NOW-timedelta(minutes=8)).isoformat(), park_confirmed_utc=(NOW-timedelta(minutes=5)).isoformat(),
        hydrawise_clear_since_utc=(NOW-timedelta(minutes=10)).isoformat(), hydrawise_clear_origin="DATA_GAP",
        last_hydrawise_success_utc=(NOW-timedelta(minutes=1)).isoformat())


def tick(store, data, now, calls, height_sender=None):
    data = deepcopy(data)
    data["mower"]["status_timestamp_ms"] = int(now.timestamp()*1000)
    data["hydrawise"]["safety"]["observed_at_utc"] = now.isoformat()
    cycle = replace(result(), details=data, executed_at_utc=now.isoformat())
    def send(kind):
        return lambda *args: calls.append(kind) or {"accepted": True, "message_type": "info"}
    return run_full_failsafe_cycle(now_utc=now, settings=RuntimeSettings.from_mapping(ENV), environment=ENV,
        past_due=False, source="manual-policy-test", read_only_runner=lambda **_:cycle, state_store_factory=lambda _:store,
        park_sender=send("PARK"), start_sender=send("START"), suspend_zone_sender=send("SUSPEND"),
        start_zone_sender=send("WATER_START"), stop_zone_sender=send("WATER_STOP"), cutting_height_sender=height_sender, command_clock=lambda:now)


def test_admitted_start_waits_for_departure_then_ends_on_next_return():
    store=InMemoryStateStore(ready()); data=details(); calls=[]
    submit(store,data,payload(store.load(),data))
    output=tick(store,data,NOW,calls)
    assert "START" in calls, output.decision_code
    assert load_manual_session(store.load())["status"] == "PENDING"
    data["mower"].update(activity="MOWING",mode="MAIN",override_action="FORCE_MOW")
    tick(store,data,NOW+timedelta(minutes=1),calls)
    assert load_manual_session(store.load())["status"] == "ACTIVE"
    data["mower"].update(activity="GOING_HOME",battery_percent=20)
    tick(store,data,NOW+timedelta(minutes=2),calls)
    assert load_manual_session(store.load())["status"] == "ENDED"
    assert calls.count("START") == 1

    # The ended manual permission must not freeze the ordinary controller.
    # Continue from persisted state in a fresh store, as after a process restart.
    store = InMemoryStateStore(store.load())
    data["mower"].update(activity="CHARGING", battery_percent=40)
    for minute in range(3, 12):
        tick(store, data, NOW + timedelta(minutes=minute), calls)
        assert calls.count("START") == 1
    data["mower"].update(activity="PARKED_IN_CS", battery_percent=100)
    output = tick(store, data, NOW + timedelta(minutes=12), calls)
    assert calls.count("START") == 2, output.decision_code
    assert load_manual_session(store.load())["status"] == "ENDED"

    # The historical manual record must not change later automatic return rules.
    data["mower"].update(activity="MOWING", mode="MAIN_AREA", override_action="NO_SOURCE")
    for minute in range(13, 24):
        tick(store, data, NOW + timedelta(minutes=minute), calls)
    data["mower"].update(activity="GOING_HOME", battery_percent=65)
    output = tick(store, data, NOW + timedelta(minutes=24), calls)
    assert calls.count("START") == 3, output.decision_code
    assert output.details["start_action"]["turnaround_before_dock"] is True


def test_ended_start_still_parks_moving_mower_for_new_occupancy():
    from mower.manual_session import new_session, with_session_status, dump_manual_session
    session = new_session(session_id="ended-start", epoch=1, mower_id="mower-1",
                          kind="START", source="APP", now_utc=NOW-timedelta(hours=1))
    session = with_session_status(session, "ENDED", now_utc=NOW-timedelta(minutes=5))
    store = InMemoryStateStore(replace(ready(), manual_session_json=dump_manual_session(session),
        parked_by_automation=False, continuous_mowing_owned=True, automation_park_source=None))
    data = details(block_source="training", activity="MOWING"); calls = []
    data["mower"].update(activity="MOWING", mode="MAIN_AREA", override_action="NO_SOURCE")
    data["current_plan"]["blocked_now"] = {
        "start": NOW.isoformat(), "end": (NOW+timedelta(hours=1)).isoformat(),
        "source": "training", "title": "Training", "resource_id": "rasen",
    }
    output = tick(store, data, NOW, calls)
    assert "PARK" in calls, output.decision_code
    assert "START" not in calls


@pytest.mark.parametrize("active", [True, False])
def test_ended_start_keeps_protection_for_active_or_unknown_water(active):
    from mower.manual_session import new_session, with_session_status, dump_manual_session
    session = new_session(session_id="ended-water", epoch=1, mower_id="mower-1",
                          kind="START", source="APP", now_utc=NOW-timedelta(hours=1))
    session = with_session_status(session, "ENDED", now_utc=NOW-timedelta(minutes=5))
    store = InMemoryStateStore(replace(ready(), manual_session_json=dump_manual_session(session),
        parked_by_automation=False, continuous_mowing_owned=True, automation_park_source=None))
    data = details(activity="MOWING", active_ids=[RELAYS[0]] if active else [], clear=False)
    data["mower"].update(mode="MAIN_AREA", override_action="NO_SOURCE")
    if not active:
        data["hydrawise"]["safety"].update(available=False, fresh=False)
    calls = []
    output = tick(store, data, NOW, calls)
    assert "PARK" in calls, output.decision_code
    assert "START" not in calls
    assert "WATER_START" not in calls


def test_manual_park_survives_cycles_then_resume_restores_safe_automation():
    store=InMemoryStateStore(ready()); data=details(); calls=[]
    submit(store,data,payload(store.load(),data,"PARK"),request_id="park")
    tick(store,data,NOW,calls)
    tick(store,data,NOW+timedelta(minutes=1),calls)
    assert "START" not in calls
    assert load_manual_session(store.load())["kind"] == "PARK"
    submit(store,data,payload(store.load(),data,"RESUME"),request_id="resume")
    output=tick(store,data,NOW+timedelta(minutes=2),calls)
    assert store.load().automation_restart_allowed is True, output.decision_code
    assert "START" in calls, output.decision_code


def test_unconfirmed_external_start_does_not_release_manual_park():
    store=InMemoryStateStore(ready()); data=details(); calls=[]
    submit(store,data,payload(store.load(),data,"PARK"),request_id="park")
    tick(store,data,NOW,calls)
    tick(store,data,NOW+timedelta(minutes=1),calls)
    parked_calls=calls.count("PARK")
    data["mower"].update(activity="MOWING",mode="MAIN",override_action="FORCE_MOW")
    output=tick(store,data,NOW+timedelta(minutes=6),calls)
    assert load_manual_session(store.load())["kind"] == "PARK"
    assert calls.count("PARK") > parked_calls, output.decision_code
    assert "START" not in calls


def test_full_mode_height_is_confirmed_only_by_later_device_value():
    state=replace(ready(),operator_request_id="height",operator_request_action="SET_CUTTING_HEIGHT",
         operator_request_status="PENDING",operator_request_cutting_height_mm=26,
         operator_requested_utc=NOW.isoformat(),operator_request_expires_utc=(NOW+timedelta(minutes=10)).isoformat())
    store=InMemoryStateStore(state); data=details(); calls=[]
    data["mower"]["work_areas"]=[deepcopy(data["mower"]["target_work_area"])]
    def sender(*args,before_send):
        before_send();calls.append("HEIGHT");return {"accepted":True}
    output=tick(store,data,NOW,calls,height_sender=sender)
    assert calls == ["HEIGHT"], output.decision_code
    assert store.load().operator_request_status == "PENDING"
    data["mower"]["target_work_area"]["cutting_height_percent"]=15
    data["mower"]["work_areas"][0]["cutting_height_percent"]=15
    output=tick(store,data,NOW+timedelta(minutes=1),calls,height_sender=sender)
    assert output.decision_code == "CUTTING_HEIGHT_CONFIRMED", output.decision_code
    assert store.load().operator_request_status == "COMPLETED"
    assert calls == ["HEIGHT"]


def test_park_during_height_auth_keeps_the_new_protective_request():
    state=replace(ready(),operator_request_id="height",operator_request_action="SET_CUTTING_HEIGHT",
         operator_request_status="PENDING",operator_request_cutting_height_mm=26,
         operator_requested_utc=NOW.isoformat(),operator_request_expires_utc=(NOW+timedelta(minutes=10)).isoformat())
    store=InMemoryStateStore(state); data=details(); calls=[]
    data["mower"]["work_areas"]=[deepcopy(data["mower"]["target_work_area"])]
    def sender(*args,before_send):
        submit(store,data,payload(store.load(),data,"PARK"),request_id="priority-park")
        before_send();calls.append("HEIGHT");return {"accepted":True}
    tick(store,data,NOW,calls,height_sender=sender)
    assert store.load().operator_request_id == "priority-park"
    assert store.load().operator_request_status == "PENDING"
    assert load_manual_session(store.load())["kind"] == "PARK"
    assert "HEIGHT" not in calls
