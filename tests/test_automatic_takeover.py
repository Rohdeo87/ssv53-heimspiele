from copy import deepcopy
from dataclasses import replace
from datetime import timedelta
import json

import pytest

from mower.automatic_takeover import eligible
from mower.full_failsafe import run_full_failsafe_cycle
from mower.manual_session import load_manual_session, new_session, dump_manual_session
from mower.runtime import RuntimeSettings
from mower.state import AutomationState
from mower.state_store import InMemoryStateStore, StateConflictError
from tests.test_manual_control_api import ENV, details, payload, submit
from tests.test_manual_control_cycle import ready
from tests.test_full_failsafe import NOW, RELAYS, result
from tests.test_device_send_guard import _entry

LIVE = {**ENV, "MOWER_AUTOMATIC_TAKEOVER_ENABLED": "true"}


def moving():
    return details(activity="MOWING", override_action="FORCE_MOW", external_reason_id=0)


def tick(store, data, now=NOW, environment=None, conflict=False):
    data = deepcopy(data)
    data["mower"].setdefault("status_timestamp_ms", int(now.timestamp() * 1000))
    data["hydrawise"]["safety"]["observed_at_utc"] = now.isoformat()
    cycle = replace(result(), details=data, executed_at_utc=now.isoformat())
    calls = []
    def sender(kind):
        return lambda *args: calls.append(kind) or {"accepted": True, "message_type": "info"}
    env = LIVE if environment is None else environment
    output = run_full_failsafe_cycle(now_utc=now, settings=RuntimeSettings.from_mapping(env),
        environment=env, past_due=False, source="takeover-test", read_only_runner=lambda **_:cycle,
        state_store_factory=lambda _:store, command_clock=lambda:now,
        park_sender=sender("PARK"), start_sender=sender("START"),
        start_zone_sender=sender("WATER_START"), stop_zone_sender=sender("WATER_STOP"),
        suspend_zone_sender=sender("SUSPEND"))
    return output, calls


def test_external_start_adopts_without_command_or_fabricated_native_deadline():
    before = ready(); store = InMemoryStateStore(before)
    output, calls = tick(store, moving())
    assert output.decision_code == "AUTOMATIC_MOWING_TAKEOVER"
    after = store.load()
    assert calls == [] and output.command_sent is False
    assert after.continuous_mowing_owned and after.continuous_mowing_observed_takeover
    assert after.continuous_mowing_window_end_utc is None
    assert after.last_start_command_utc == before.last_start_command_utc
    assert after.device_send_journal_json == before.device_send_journal_json
    store = InMemoryStateStore(AutomationState.from_mapping(after.to_dict()))
    data = moving(); data["mower"]["status_timestamp_ms"] += 60000
    output, calls = tick(store, data, NOW + timedelta(minutes=1))
    assert calls == [] and store.load().continuous_mowing_owned
    assert output.details["automatic_takeover"]["adopted"] is False


def test_flag_off_keeps_existing_external_start_policy():
    store = InMemoryStateStore(ready())
    output, calls = tick(store, moving(), environment=ENV)
    assert not store.load().continuous_mowing_owned and calls == []
    assert output.decision_code == "MANUAL_OR_ERROR_HOLD"


@pytest.mark.parametrize("change", [
    {"activity":"GOING_HOME"}, {"activity":"CHARGING"}, {"activity":"PARKED_IN_CS"},
    {"state":"STOPPED"}, {"state":"PAUSED"}, {"state":"ERROR"},
    {"connected":False}, {"error_code":9}, {"error_code":None}, {"error_code":False},
    {"mode":"HOME"}, {"mode":"DEMO"}, {"override_action":"FORCE_PARK"},
    {"override_action":"UNRECOGNIZED"}, {"mower_id":"other"},
    {"status_timestamp_ms":int((NOW-timedelta(minutes=4)).timestamp()*1000)},
    {"target_work_area":{"id":849199,"enabled":False}},
])
def test_no_adoption_for_unreliable_or_stopped_mower(change):
    data = moving(); data["mower"].update(change)
    assert eligible(ready(), data, LIVE, NOW) is False


@pytest.mark.parametrize("change", [
    {"maintenance_mode":True}, {"automation_restart_allowed":False},
    {"mower_start_pending_since_utc":NOW.isoformat()},
    {"irrigation_phase":"RUNNING"}, {"operator_request_status":"PENDING","operator_request_action":"PARK_MOWER"},
    {"device_send_journal_json":json.dumps([_entry(1,status="UNKNOWN")])},
])
def test_no_adoption_over_existing_hold_or_unconfirmed_command(change):
    assert eligible(replace(ready(), **change), moving(), LIVE, NOW) is False


@pytest.mark.parametrize("kind", ["training","match","special","water","dry","unknown","empty-import"])
def test_conflicts_or_incomplete_sources_never_acquire_ownership(kind):
    data = moving()
    if kind in {"training","match","special"}:
        data = details(activity="MOWING", override_action="FORCE_MOW", block_source=kind)
    elif kind == "water":
        data = details(activity="MOWING", active_ids=[RELAYS[0]], clear=False)
    elif kind == "dry":
        data["hydrawise"]["release_confirmation"] = {"dry_until_utc":(NOW+timedelta(minutes=60)).isoformat()}
    elif kind == "unknown":
        data["hydrawise"]["safety"]["available"] = False
    else:
        data["input_files"] = {"matches_found":False,"fallback_used":True}
    assert eligible(ready(), data, LIVE, NOW) is False


@pytest.mark.parametrize("source", ["APP","HUSQVARNA"])
def test_confirmed_start_without_exceptions_ends_manual_session_on_departure(source):
    store = InMemoryStateStore(ready()); data = details()
    submit(store, data, payload(store.load(), data, source=source))
    if source == "APP":
        output, calls = tick(store, data)
        assert calls == ["START"]
    data = moving(); data["mower"]["status_timestamp_ms"] += 60000
    output, calls = tick(store, data, NOW+timedelta(minutes=1))
    assert calls == []
    assert load_manual_session(store.load())["status"] == "ENDED"
    assert output.details["manual_session"]["status"] == "ENDED"
    assert output.details["manual_session"]["permission_code"] == "MANUAL_SESSION_INACTIVE"
    assert store.load().continuous_mowing_owned


@pytest.mark.parametrize("exception", ["occupancy","drying","water"])
def test_confirmed_exceptions_are_not_ended_by_takeover(exception):
    kwargs = ({"confirmed_block_keys":["confirmed-training"]} if exception == "occupancy" else
              {"confirmed_dry_until_utc":NOW+timedelta(minutes=60)} if exception == "drying" else
              {"water_conflict_id":"water-1","water_choice":"MOWER"})
    session = new_session(session_id="exception",epoch=1,mower_id="mower-1",kind="START",source="APP",
                          now_utc=NOW-timedelta(minutes=1), **kwargs)
    session.update(status="ACTIVE", departure_observed_utc=NOW.isoformat())
    before = replace(ready(),manual_session_json=dump_manual_session(session))
    assert eligible(before, moving(), LIVE, NOW) is False


def test_stop_and_external_park_revoke_observed_ownership():
    for state_name, activity, override in [("STOPPED","NOT_APPLICABLE","FORCE_MOW"),
                                           ("IN_OPERATION","GOING_HOME","FORCE_PARK")]:
        store = InMemoryStateStore(ready()); tick(store,moving())
        data = moving(); data["mower"].update(state=state_name,activity=activity,override_action=override)
        output, calls = tick(store,data)
        assert calls == []
        assert not store.load().continuous_mowing_owned


def test_manual_park_latch_is_never_adopted_even_if_device_moves_again():
    store = InMemoryStateStore(ready()); data = details()
    submit(store,data,payload(store.load(),data,operation="PARK",source="HUSQVARNA"))
    output, calls = tick(store,moving())
    assert "START" not in calls
    assert load_manual_session(store.load())["kind"] == "PARK"
    assert not store.load().continuous_mowing_owned


def test_charge_return_is_not_reversed_and_later_restart_is_ordinary():
    store = InMemoryStateStore(ready()); tick(store,moving())
    data = moving(); data["mower"].update(activity="GOING_HOME",battery_percent=95)
    output, calls = tick(store,data)
    assert calls == [] and output.decision_code == "WAIT_FOR_MOWER_AT_STATION"
    data["mower"].update(activity="CHARGING",battery_percent=40)
    assert tick(store,data)[1] == []
    data["mower"].update(activity="PARKED_IN_CS",battery_percent=100)
    output, calls = tick(store,data)
    assert calls == ["START"], output.decision_code
    assert store.load().continuous_mowing_observed_takeover is False
    assert store.load().continuous_mowing_window_end_utc is not None


@pytest.mark.parametrize("kind", ["training","water","unknown"])
def test_protections_remain_after_takeover(kind):
    store = InMemoryStateStore(ready()); tick(store,moving())
    data = moving()
    if kind == "training":
        data = details(activity="MOWING", block_source="training", command="PARK")
    else:
        data = details(activity="MOWING",active_ids=[RELAYS[0]] if kind=="water" else [],clear=False)
        if kind == "unknown": data["hydrawise"]["safety"].update(available=False,fresh=False)
    output, calls = tick(store,data)
    assert "PARK" in calls, output.decision_code
    assert "START" not in calls and "WATER_START" not in calls


def test_stored_deadline_parks_even_if_current_plan_would_allow_longer():
    store = InMemoryStateStore(ready()); tick(store, moving())
    deadline = store.load().continuous_mowing_takeover_deadline_utc
    assert deadline is not None
    now = NOW + timedelta(hours=12)
    data = details(activity="MOWING", override_action="FORCE_MOW", window_end=now+timedelta(hours=2))
    data["mower"]["status_timestamp_ms"] = int(now.timestamp()*1000)
    output, calls = tick(store, data, now)
    assert calls == ["PARK"], output.decision_code
    assert store.load().automation_park_source == "continuous"
    assert store.load().continuous_mowing_observed_takeover is False


@pytest.mark.parametrize("override", ["FORCE_PARK","PARK_UNTIL_FURTHER_NOTICE","PARK_UNTIL_NEXT_SCHEDULE"])
def test_external_park_hold_survives_restart_and_cannot_restart_from_idle(override):
    store = InMemoryStateStore(ready()); tick(store,moving())
    data = moving(); data["mower"].update(activity="GOING_HOME",override_action=override,external_reason_id=0)
    assert tick(store,data)[1] == []
    store = InMemoryStateStore(AutomationState.from_mapping(store.load().to_dict()))
    data["mower"].update(activity="CHARGING",override_action="NOT_ACTIVE",battery_percent=100)
    output, calls = tick(store,data)
    assert calls == [] and output.decision_code == "OBSERVED_MANUAL_STOP_HOLD"
    data = moving(); data["mower"]["status_timestamp_ms"] += 60000
    output, calls = tick(store,data,NOW+timedelta(minutes=1))
    assert calls == [] and output.decision_code == "AUTOMATIC_MOWING_TAKEOVER"


def test_stop_during_water_conflict_is_latched_before_safety_early_return():
    store = InMemoryStateStore(ready()); tick(store,moving())
    data = details(activity="NOT_APPLICABLE",mower_state="STOPPED",clear=False,active_ids=[RELAYS[0]])
    tick(store,data)
    assert store.load().continuous_mowing_takeover_hold_utc is not None
    data = moving(); data["mower"].update(activity="CHARGING",override_action="NOT_ACTIVE")
    assert tick(store,data)[1] == []
    assert not store.load().continuous_mowing_owned


def test_save_conflict_never_claims_persisted_takeover_or_sends_start():
    class ConflictingStore(InMemoryStateStore):
        def save(self, state, *, expected_revision):
            raise StateConflictError("Concurrent PARK")
    store = ConflictingStore(ready())
    output, calls = tick(store,moving())
    assert calls == [] and not store.load().continuous_mowing_owned
    assert output.details["automation_state"]["persisted"] is False
