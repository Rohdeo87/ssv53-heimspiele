"""End-to-end policy admission plus controller cycles with synthetic device I/O."""
from copy import deepcopy
from dataclasses import replace
from datetime import timedelta

from mower.full_failsafe import run_full_failsafe_cycle
from mower.manual_session import load_manual_session
from mower.runtime import RuntimeSettings
from mower.state import AutomationState
from mower.state_store import InMemoryStateStore
from tests.test_full_failsafe import NOW, result
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
