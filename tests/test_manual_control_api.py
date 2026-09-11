"""Authenticated admission tests; all device networking is prohibited by conftest."""
from copy import deepcopy
from dataclasses import replace
from datetime import timedelta
from unittest.mock import patch

import pytest

from mower.manual_control_api import ManualControlError, manual_context, request_manual_control
from mower.manual_session import load_manual_session
from mower.state import AutomationState
from mower.state_store import InMemoryStateStore, StateConflictError
from platzwart_console import PlatzwartError, request_action
from tests.test_full_failsafe import NOW, RELAYS, result
from tests.test_platzwart_console import FULL_DEVICE_CONTROL_ENV

ENV = {**FULL_DEVICE_CONTROL_ENV, "ENABLE_MANUAL_SESSIONS": "true", "HUSQVARNA_MOWER_ID": "mower-1"}


def details(**kw):
    value = deepcopy(result(**kw).details)
    value["input_files"] = {"matches_found": True, "fallback_used": False}
    value["hydrawise"]["safety"].update(imminent_zone_count=0, imminent_relay_ids=[])
    return value


def payload(state, data, operation="START", **kw):
    return {"operation": operation, "source": "APP", "contextToken": manual_context(state, data, ENV, NOW)[0]["contextToken"],
            "approveOccupancy": False, "approveDrying": False, "waterChoice": None,
            "sessionId": None, **kw}


def submit(store, data, body, request_id="request-1", now=NOW):
    return request_manual_control(store=store, state=store.load(), details=data, environment=ENV,
                                  now_utc=now, request_id=request_id, payload=body)


def test_manual_start_is_persisted_with_same_transport_fence():
    store = InMemoryStateStore(AutomationState())
    data = details()
    state, response = submit(store, data, payload(store.load(), data))
    session = load_manual_session(state)
    assert response["status"] == "PENDING"
    assert session["status"] == "PENDING"
    assert state.operator_request_action == "START_MOWING"
    assert (state.operator_request_session_id, state.operator_request_session_epoch) == (session["session_id"], session["epoch"])
    assert session["prepared_until_utc"] is not None


@pytest.mark.parametrize("source", ["match", "training", "match+training"])
def test_occupancy_requires_explicit_checkbox(source):
    store = InMemoryStateStore(AutomationState()); data = details(block_source=source)
    body = payload(store.load(), data)
    with pytest.raises(ManualControlError, match="tatsächlich frei"):
        submit(store, data, body)
    body["approveOccupancy"] = True
    state, _ = submit(store, data, body)
    assert load_manual_session(state)["confirmed_block_keys"]


@pytest.mark.parametrize("change", ["binding", "new_game", "dry_end"])
def test_changed_context_or_fixed_closure_cannot_inherit_approval(change):
    store = InMemoryStateStore(AutomationState()); data = details(block_source="match")
    body = payload(store.load(), data, approveOccupancy=True, approveDrying=True)
    if change == "binding":
        data["current_plan"]["parking_block"]["bindingClosure"] = True
    elif change == "new_game":
        data["current_plan"]["parking_block"]["title"] = "Anderes Spiel"
    else:
        data["hydrawise"]["release_confirmation"] = {"dry_until_utc": (NOW + timedelta(minutes=30)).isoformat()}
    with pytest.raises(ManualControlError, match="geändert"):
        submit(store, data, body)
    assert store.load().manual_session_json is None


def test_dry_exception_is_exact_and_requires_checkbox():
    store = InMemoryStateStore(AutomationState()); data = details()
    end = (NOW + timedelta(minutes=80)).isoformat()
    data["hydrawise"]["release_confirmation"] = {"dry_until_utc": end}
    body = payload(store.load(), data)
    with pytest.raises(ManualControlError, match="ausreichend trocken"):
        submit(store, data, body)
    body["approveDrying"] = True
    state, _ = submit(store, data, body)
    assert load_manual_session(state)["confirmed_dry_until_utc"] == end


def test_each_active_water_conflict_requires_choice():
    store = InMemoryStateStore(AutomationState()); data = details(active_ids=[RELAYS[0]], clear=False)
    body = payload(store.load(), data)
    assert manual_context(store.load(), data, ENV, NOW)[0]["confirmations"]["waterChoiceRequired"]
    with pytest.raises(ManualControlError, match="zwischen Mähen und Bewässern"):
        submit(store, data, body)
    body["waterChoice"] = "MOWER"
    state, _ = submit(store, data, body)
    assert load_manual_session(state)["water_choice"] == "MOWER"
    assert state.operator_request_status == "PENDING"  # admission is not physical water clearance


def test_park_survives_water_failure_stale_context_and_unknown_start():
    initial = AutomationState(mower_start_pending_since_utc=NOW.isoformat())
    store = InMemoryStateStore(initial); data = details(clear=False)
    data["hydrawise"] = {}; data["manual_sources_available"] = False
    state, _ = submit(store, data, payload(initial, data, "PARK", contextToken="old-screen"))
    assert load_manual_session(state)["kind"] == "PARK"
    assert load_manual_session(state)["prepared_until_utc"] is None
    assert state.automation_restart_allowed is False
    assert state.mower_start_pending_since_utc == initial.mower_start_pending_since_utc


def test_husqvarna_preparation_records_confirmation_without_command():
    store = InMemoryStateStore(AutomationState()); data = details()
    state, _ = submit(store, data, payload(store.load(), data, source="HUSQVARNA"))
    assert state.operator_request_action is None
    assert load_manual_session(state)["status"] == "PREPARED"


@pytest.mark.parametrize("operation", ["START", "PARK", "RESUME", "DECIDE"])
def test_lost_response_retries_are_idempotent_and_changed_payload_rejected(operation):
    store = InMemoryStateStore(AutomationState()); data = details()
    if operation in {"RESUME", "DECIDE"}:
        submit(store, data, payload(store.load(), data, "PARK" if operation == "RESUME" else "START"), request_id="old")
    body = payload(store.load(), data, operation,
                   sessionId=load_manual_session(store.load())["session_id"] if store.load().manual_session_json else None)
    state, _ = submit(store, data, body)
    revision = state.revision
    again, _ = submit(store, data, body)
    assert again.revision == revision
    with pytest.raises(ManualControlError, match="anderen Aktion"):
        submit(store, data, {**body, "approveDrying": True})


def test_decide_keeps_unsent_start_fenced_to_new_epoch():
    store = InMemoryStateStore(AutomationState()); data = details()
    state, _ = submit(store, data, payload(store.load(), data), request_id="start")
    state, _ = submit(store, data, payload(state, data, "DECIDE", sessionId="start"), request_id="choice")
    assert state.operator_request_action == "START_MOWING"
    assert state.operator_request_session_epoch == load_manual_session(state)["epoch"] == 2


def test_resume_never_erases_unconfirmed_device_outcome():
    store = InMemoryStateStore(AutomationState(mower_start_pending_since_utc=NOW.isoformat()))
    data = details()
    state, _ = submit(store, data, payload(store.load(), data, "PARK"), request_id="park")
    with pytest.raises(ManualControlError, match="aufzuheben"):
        submit(store, data, payload(state, data, "RESUME"), request_id="release")
    assert load_manual_session(store.load())["kind"] == "PARK"
    assert store.load().mower_start_pending_since_utc == NOW.isoformat()


def test_unknown_start_is_also_disabled_in_display_with_fresh_telemetry():
    state = AutomationState(mower_start_pending_since_utc=NOW.isoformat())
    public = manual_context(state, details(), ENV, NOW)[0]
    assert public["canStart"] is False
    assert public["canPark"] is True
    assert "letzte Start ist ungeklärt" in public["message"]


def test_stale_report_explains_disabled_start_and_fresh_report_restores_it():
    data = details()
    data["mower"]["status_timestamp_ms"] = int((NOW - timedelta(minutes=11)).timestamp() * 1000)
    state = AutomationState()
    public = manual_context(state, data, ENV, NOW)[0]
    assert public["canStart"] is False
    assert public["canPark"] is True
    assert "neue Mähermeldung abwarten" in public["message"]
    data["mower"]["status_timestamp_ms"] = int(NOW.timestamp() * 1000)
    refreshed = manual_context(state, data, ENV, NOW)[0]
    assert refreshed["canStart"] is True
    assert "neue Mähermeldung abwarten" not in refreshed["message"]


@pytest.mark.parametrize("field,value", [("available", False), ("fresh", False), ("relay_set_valid", False)])
def test_unknown_water_never_admits_start(field, value):
    store = InMemoryStateStore(AutomationState()); data = details()
    data["hydrawise"]["safety"][field] = value
    with pytest.raises(ManualControlError, match="Start noch nicht möglich"):
        submit(store, data, payload(store.load(), data, approveDrying=True))


def test_concurrent_action_uses_compare_and_swap():
    store = InMemoryStateStore(AutomationState()); data = details()
    old = store.load(); body = payload(old, data)
    submit(store, data, payload(old, data, "PARK"), request_id="other")
    with pytest.raises(StateConflictError):
        request_manual_control(store=store, state=old, details=data, environment=ENV,
                               now_utc=NOW, request_id="start", payload=body)
    assert load_manual_session(store.load())["kind"] == "PARK"


def test_console_requires_contract3_and_authoritative_read():
    store = InMemoryStateStore(AutomationState()); data = details()
    body = payload(store.load(), data)
    with pytest.raises(PlatzwartError, match="neu öffnen"):
        request_action("MANUAL_CONTROL", "x", "MANUAL_CONTROL", ENV, NOW,
                       manual_control=body, client_contract_version=2, state_store_factory=lambda _:store)
    with patch("platzwart_console.run_read_only_cycle", side_effect=RuntimeError("source missing")):
        with pytest.raises(PlatzwartError, match="Start noch nicht möglich"):
            request_action("MANUAL_CONTROL", "x", "MANUAL_CONTROL", ENV, NOW,
                           manual_control=body, client_contract_version=3, state_store_factory=lambda _:store)
    assert store.load().manual_session_json is None


def test_console_admits_using_fresh_read_and_same_state():
    store = InMemoryStateStore(AutomationState()); data = details()
    cycle = replace(result(), details=data)
    with patch("platzwart_console.run_read_only_cycle", return_value=cycle) as read, patch("platzwart_console.ConsoleTableStore.from_environment"):
        response = request_action("MANUAL_CONTROL", "x", "MANUAL_CONTROL", ENV, NOW,
                         manual_control=payload(store.load(), data), client_contract_version=3, state_store_factory=lambda _:store)
    assert response["accepted"]
    assert read.call_args.kwargs["persist_observations"] is False
    assert load_manual_session(store.load())["session_id"] == "x"


def test_display_override_is_bound_to_approved_current_occupancy():
    from mower.manual_session import dump_manual_session
    store=InMemoryStateStore(AutomationState()); data=details(block_source="training")
    state,_=submit(store,data,payload(store.load(),data,approveOccupancy=True))
    session=load_manual_session(state); session["status"]="ACTIVE"
    state=replace(state,manual_session_json=dump_manual_session(session))
    assert manual_context(state,data,ENV,NOW)[0]["occupancyOverrideActive"] is True
    data["current_plan"]["parking_block"]["title"]="Neues Training"
    assert manual_context(state,data,ENV,NOW)[0]["occupancyOverrideActive"] is False


def test_choosing_water_does_not_require_or_grant_a_dry_exception():
    store=InMemoryStateStore(AutomationState()); data=details(active_ids=[RELAYS[0]],clear=False)
    data["hydrawise"]["release_confirmation"]={"dry_until_utc":(NOW+timedelta(minutes=150)).isoformat()}
    state,_=submit(store,data,payload(store.load(),data,waterChoice="IRRIGATION"))
    assert load_manual_session(state)["confirmed_dry_until_utc"] is None


def test_cached_legacy_template_can_park_but_cannot_skip_new_start_confirmation():
    store=InMemoryStateStore(AutomationState()); cycle=replace(result(),details=details())
    with patch("platzwart_console.run_read_only_cycle",return_value=cycle), patch("platzwart_console.ConsoleTableStore.from_environment"):
        response=request_action("PARK_MOWER","old-park","PARK_MOWER",ENV,NOW,
             client_contract_version=2,state_store_factory=lambda _:store)
    assert response["accepted"]
    assert load_manual_session(store.load())["kind"] == "PARK"
    with pytest.raises(PlatzwartError, match="neu öffnen"):
        request_action("START_MOWING","old-start","START_MOWING",ENV,NOW,client_contract_version=2,state_store_factory=lambda _:store)
