"""Winter switch safety checks. All providers are fakes; network is prohibited."""
from copy import deepcopy
from dataclasses import replace
from datetime import timedelta
import json
from unittest.mock import Mock, patch

import azure.functions as func
import pytest

import function_app
from mower.device_send_guard import DeviceSendBlocked, dispatch_device_send, load_device_send_journal
from mower.full_failsafe import run_full_failsafe_cycle
from mower.husqvarna import HusqvarnaError
from mower.manual_control_api import ManualControlError, manual_context, request_manual_control
from mower.service_mode import MowerServiceError, change_service_mode, fence_mower_sender
from mower.state import AutomationState
from mower.state_store import InMemoryStateStore, StateConflictError
from platzwart_console import PlatzwartError, live_status, request_action
from tests.test_device_send_guard import _entry
from tests.test_full_failsafe import ENV, NOW, RELAYS, irrigation_state, observed_cycle, result, settings
from tests.test_manual_control_api import details, payload as manual_payload
from tests.test_platzwart_console import FULL_DEVICE_CONTROL_ENV


API_ENV = {**FULL_DEVICE_CONTROL_ENV, "ENABLE_MANUAL_SESSIONS": "true", "HUSQVARNA_MOWER_ID": "mower-1"}


def switch(store, *, enabled=True, revision=None, reason="WINTER", request_id="service-1", mower=None, state=None):
    state = state or store.load()
    return change_service_mode(store=store, state=state, payload={"enabled": enabled, "reason": reason,
        "revision": state.mower_service_revision if revision is None else revision}, request_id=request_id,
        now_utc=NOW, mower=mower, environment=API_ENV)


def test_old_state_defaults_to_existing_operation_and_invalid_bool_fails_closed():
    assert AutomationState.from_mapping({}).mower_service_active is False
    with pytest.raises(ValueError):
        AutomationState.from_mapping({"mower_service_active": "false"})


@pytest.mark.parametrize("reason", ["WINTER", "MAINTENANCE", "DEFECT"])
def test_switch_is_persistent_default_off_and_preserves_water_and_other_holds(reason):
    old = replace(irrigation_state(phase="COMPLETE_HOLD"), maintenance_mode=True,
                  winter_training_enabled=True, continuous_mowing_owned=True,
                  operator_request_action="SKIP_NEXT_IRRIGATION", operator_request_status="PENDING")
    store = InMemoryStateStore(old)
    updated = switch(store, reason=reason)
    saved = AutomationState.from_mapping(updated.to_dict())
    assert saved.mower_service_active is True
    assert saved.mower_service_reason == reason
    assert saved.mower_service_since_utc == NOW.isoformat()
    assert saved.continuous_mowing_owned is False
    assert saved.maintenance_mode is True
    assert saved.winter_training_enabled is True
    for field, value in old.to_dict().items():
        if field.startswith(("irrigation_", "hydrawise_", "last_hydrawise_", "operator_request_")):
            assert saved.to_dict()[field] == value


def test_switch_cancels_unsent_start_ends_start_session_and_changes_old_confirmation():
    store = InMemoryStateStore()
    data = details()
    state, _ = request_manual_control(store=store, state=store.load(), details=data, environment=API_ENV,
        now_utc=NOW, request_id="manual-1", payload=manual_payload(store.load(), data))
    old_token = manual_context(state, data, API_ENV, NOW)[0]["contextToken"]
    saved = switch(store)
    assert saved.operator_request_status == "REJECTED"
    assert json.loads(saved.manual_session_json)["status"] == "ENDED"
    assert manual_context(saved, data, API_ENV, NOW)[0]["contextToken"] != old_token
    switch(store, enabled=False, request_id="return", mower=result().details["mower"])
    assert store.load().operator_request_status == "REJECTED"
    assert store.load().continuous_mowing_owned is False
    assert manual_context(store.load(), data, API_ENV, NOW)[0]["contextToken"] != old_token


def test_idempotent_retry_and_stale_switch_cannot_overwrite_newer_choice():
    store = InMemoryStateStore()
    saved = switch(store)
    assert switch(store, revision=0) == saved
    with pytest.raises(MowerServiceError, match="zwischenzeitlich"):
        switch(store, enabled=False, revision=0, request_id="stale")
    assert store.load() == saved
    with pytest.raises(MowerServiceError):
        switch(store, revision=0, reason="DEFECT")


def test_compare_and_swap_preserves_parallel_water_update():
    store = InMemoryStateStore()
    stale = store.load()
    water = replace(stale, revision=1, irrigation_phase="RUNNING")
    store.save(water, expected_revision=0)
    with pytest.raises(StateConflictError):
        switch(store, state=stale)
    assert store.load() == water


@pytest.mark.parametrize("phase", ["PLANNED", "SUSPENDING", "READY", "START_RESERVED", "RUNNING", "STOPPING"])
def test_switch_refuses_to_interrupt_water_handover(phase):
    state = irrigation_state(phase=phase)
    store = InMemoryStateStore(state)
    with pytest.raises(MowerServiceError) as exc:
        switch(store)
    assert exc.value.code == "MOWER_SERVICE_WATER_ACTIVE"
    assert store.load() == state


@pytest.mark.parametrize("kind,status", [("PARK", "UNKNOWN"), ("PARK", "DISPATCHING"), ("HEIGHT", "SENT_UNCONFIRMED")])
def test_uncertain_device_writes_cannot_be_hidden_by_switch(kind, status):
    entry = {**_entry(0, status=status), "kind": kind}
    state = AutomationState(device_send_journal_json=json.dumps([entry]))
    store = InMemoryStateStore(state)
    with pytest.raises(MowerServiceError) as exc:
        switch(store)
    assert exc.value.code == "MOWER_SERVICE_PENDING"
    assert store.load() == state


def test_unconfirmed_start_keeps_its_original_safety_latch():
    state = AutomationState(mower_start_pending_since_utc=NOW.isoformat())
    store = InMemoryStateStore(state)
    with pytest.raises(MowerServiceError):
        switch(store)
    assert store.load() == state


@pytest.mark.parametrize("change", [{"connected": False}, {"status_timestamp_ms": 1}, {"error_code": 42},
    {"mower_id": "other"}, {"mode": "MAIN_AREA"}, {"activity": "MOWING"}, {"state": "ERROR"},
    {"status_timestamp_ms": int((NOW + timedelta(minutes=10)).timestamp() * 1000)}])
def test_return_requires_exact_fresh_fault_free_station_report(change):
    store = InMemoryStateStore()
    state = switch(store)
    mower = {**result().details["mower"], **change}
    with pytest.raises(MowerServiceError) as exc:
        switch(store, enabled=False, request_id="return", mower=mower)
    assert exc.value.code == "MOWER_SERVICE_RETURN_UNSAFE"
    assert store.load() == state


def test_pending_park_is_revoked_during_slow_authentication_without_sending():
    store = InMemoryStateStore()
    sent = []

    def sender(*, before_send):
        switch(store)
        before_send()
        sent.append(True)

    with pytest.raises(DeviceSendBlocked):
        dispatch_device_send(store=store, original=store.load(), state=store.load(), sender=sender, args=(),
            kind="PARK", target="mower-1", intent_key="old-park", now_utc=NOW, clock=lambda: NOW,
            before_send_check=lambda *_: None)
    assert sent == []
    assert store.load().mower_service_active is True
    assert load_device_send_journal(store.load())[0]["status"] == "REJECTED"


def test_separate_operator_queue_is_cancelled_and_cannot_replay_after_return():
    from mower.operator_controls import operator_commands_payload, queue_operator_action, run_operator_cycle
    from tests.test_operator_controls import settings as operator_settings
    store = InMemoryStateStore()
    queue_operator_action(store, operator_settings(), "PARK_MOWER", "old-park", NOW, mower_id="mower-1")
    switch(store)
    assert operator_commands_payload(store.load())["PARK_MOWER"]["status"] == "EXPIRED"
    forbidden = Mock(side_effect=AssertionError("old operator action replayed"))
    output = run_operator_cycle(now_utc=NOW, settings=operator_settings(), environment=ENV, past_due=False,
        source="test", read_only_runner=lambda **_: result(), state_store_factory=lambda _: store, park_sender=forbidden)
    assert output.decision_code == "MOWER_SERVICE_ACTIVE"
    forbidden.assert_not_called()
    switch(store, enabled=False, request_id="return", mower=result().details["mower"])
    assert operator_commands_payload(store.load())["PARK_MOWER"]["status"] == "EXPIRED"


@pytest.mark.parametrize("kind", ["PARK", "HEIGHT"])
def test_shared_journal_rejects_new_mower_work_in_service_mode(kind):
    state = AutomationState(mower_service_active=True)
    store = InMemoryStateStore(state)
    forbidden = Mock(side_effect=AssertionError("mower command"))
    with pytest.raises(DeviceSendBlocked, match="MOWER_SERVICE_ACTIVE"):
        dispatch_device_send(store=store, original=state, state=state, sender=forbidden, args=(),
            kind=kind, target="mower-1", intent_key="new", now_utc=NOW, clock=lambda: NOW,
            before_send_check=lambda *_: None)
    forbidden.assert_not_called()
    assert store.load() == state


def test_final_sender_fence_rejects_a_complete_on_off_cycle_during_oauth():
    store = InMemoryStateStore()
    sent = []

    def sender(*, before_send):
        switch(store)
        switch(store, enabled=False, request_id="return", mower=result().details["mower"])
        before_send()
        sent.append(True)

    with pytest.raises(HusqvarnaError, match="MOWER_SERVICE_CHANGED"):
        fence_mower_sender(sender, store, 0)()
    assert sent == []


@pytest.mark.parametrize("activity,connected,block", [("MOWING", True, "match"), ("PARKED_IN_CS", False, None), ("CHARGING", True, None)])
def test_service_cycle_never_sends_mower_commands(activity, connected, block):
    state = AutomationState(mower_service_active=True, mower_service_reason="WINTER")
    store = InMemoryStateStore(state)
    cycle = result(activity=activity, block_source=block, command="PARK" if block else None)
    cycle.details["mower"].update(connected=connected, status_timestamp_ms=1 if not connected else int(NOW.timestamp() * 1000))
    forbidden = Mock(side_effect=AssertionError("mower command in service mode"))
    output = run_full_failsafe_cycle(now_utc=NOW, settings=settings(manual=True), environment=ENV,
        past_due=False, source="test", read_only_runner=lambda **_: cycle, state_store_factory=lambda _: store,
        park_sender=forbidden, start_sender=forbidden, cutting_height_sender=forbidden, blade_usage_reset_sender=forbidden,
        command_clock=lambda: NOW)
    forbidden.assert_not_called()
    assert output.command_sent is False
    assert store.load().mower_service_active is True


def test_irrigation_stop_and_confirmation_still_work_with_offline_service_mower():
    state = replace(irrigation_state(phase="RUNNING", current=RELAYS[0]), mower_service_active=True,
        operator_request_action="STOP_IRRIGATION_NOW", operator_request_id="stop-now",
        operator_requested_utc=NOW.isoformat(), operator_request_expires_utc=(NOW + timedelta(minutes=10)).isoformat(),
        operator_request_status="PENDING")
    store = InMemoryStateStore(state)
    stops = []

    def run(at, active):
        cycle = result(block_source="irrigation", active_ids=[RELAYS[0]] if active else [], clear=not active)
        cycle.details["mower"].update(connected=False, status_timestamp_ms=1)
        return run_full_failsafe_cycle(now_utc=at, settings=settings(), environment=ENV, past_due=False,
            source="test", read_only_runner=lambda **_: observed_cycle(cycle, at), state_store_factory=lambda _: store,
            stop_zone_sender=lambda *args: stops.append(args) or {"message_type": "info"}, command_clock=lambda: at)

    assert run(NOW, True).decision_code == "IRRIGATION_ZONE_STOP_SENT"
    run(NOW + timedelta(minutes=1), False)
    assert run(NOW + timedelta(minutes=3), False).decision_code == "IRRIGATION_OPERATOR_STOPPED_NOW"
    assert len(stops) == 1
    assert store.load().mower_service_active is True
    assert store.load().irrigation_phase == "COMPLETE_HOLD"


def test_service_mower_absence_never_counts_as_irrigation_start_clearance():
    state = replace(irrigation_state(phase="READY", current=RELAYS[0]), mower_service_active=True)
    store = InMemoryStateStore(state)
    cycle = result(block_source="irrigation", irrigation_start=NOW)
    cycle.details["mower"].update(connected=False, status_timestamp_ms=1)
    forbidden = Mock(side_effect=AssertionError("no physical mower clearance"))
    output = run_full_failsafe_cycle(now_utc=NOW, settings=settings(), environment=ENV, past_due=False,
        source="test", read_only_runner=lambda **_: cycle, state_store_factory=lambda _: store,
        start_zone_sender=forbidden, park_sender=forbidden, command_clock=lambda: NOW)
    forbidden.assert_not_called()
    assert output.command_sent is False


def test_manual_start_park_resume_and_husqvarna_preparation_remain_locked():
    state = AutomationState(mower_service_active=True)
    data = details()
    view = manual_context(state, data, API_ENV, NOW)[0]
    assert not any(view[k] for k in ("canStart", "canPark", "canResume"))
    store = InMemoryStateStore(state)
    for operation in ("START", "PARK", "RESUME", "DECIDE"):
        with pytest.raises(ManualControlError) as exc:
            request_manual_control(store=store, state=state, details=data, environment=API_ENV,
                now_utc=NOW, request_id="manual", payload={"operation": operation})
        assert exc.value.code == "MOWER_SERVICE_ACTIVE"


def test_authenticated_api_can_enable_offline_and_audits_without_device_read():
    store = InMemoryStateStore()
    with patch("platzwart_console.ConsoleTableStore.from_environment") as audit, patch("platzwart_console.run_read_only_cycle") as read:
        response = request_action("SET_MOWER_SERVICE", "api-switch", "SET_MOWER_SERVICE", API_ENV, NOW,
            mower_service={"enabled": True, "reason": "WINTER", "revision": 0}, state_store_factory=lambda _: store)
    assert response["mowerService"]["active"] is True
    read.assert_not_called()
    audit.return_value.audit.assert_called_once_with(NOW, "SET_MOWER_SERVICE", "ACCEPTED", "api-switch")


def test_api_reactivation_fails_closed_without_live_data():
    store = InMemoryStateStore()
    state = switch(store)
    with patch("platzwart_console.run_read_only_cycle", side_effect=RuntimeError("offline")):
        with pytest.raises(PlatzwartError) as exc:
            request_action("SET_MOWER_SERVICE", "return", "SET_MOWER_SERVICE", API_ENV, NOW,
                mower_service={"enabled": False, "reason": "WINTER", "revision": 1}, state_store_factory=lambda _: store)
    assert exc.value.code == "MOWER_SERVICE_RETURN_UNSAFE"
    assert store.load() == state


def test_api_return_rechecks_age_after_slow_live_read_without_starting():
    store = InMemoryStateStore()
    state = switch(store)
    with patch("platzwart_console.run_read_only_cycle", return_value=result()), \
         patch("platzwart_console.monotonic", side_effect=[0, 190]):
        with pytest.raises(PlatzwartError) as exc:
            request_action("SET_MOWER_SERVICE", "return", "SET_MOWER_SERVICE", API_ENV, NOW,
                mower_service={"enabled": False, "reason": "WINTER", "revision": 1}, state_store_factory=lambda _: store)
    assert exc.value.code == "MOWER_SERVICE_RETURN_UNSAFE"
    assert store.load() == state


def test_api_verified_return_only_clears_service_and_preserves_prior_manual_park():
    from mower.manual_session import dump_manual_session, new_session
    session = new_session(mower_id="mower-1", kind="PARK", source="APP", now_utc=NOW,
                          session_id="park", epoch=1)
    original = AutomationState(manual_session_json=dump_manual_session(session))
    store = InMemoryStateStore(original)
    switch(store)
    with patch("platzwart_console.run_read_only_cycle", return_value=result()) as read, \
         patch("platzwart_console.ConsoleTableStore.from_environment"), \
         patch("platzwart_console.monotonic", side_effect=[0, 0]):
        response = request_action("SET_MOWER_SERVICE", "return", "SET_MOWER_SERVICE", API_ENV, NOW,
            mower_service={"enabled": False, "reason": "WINTER", "revision": 1}, state_store_factory=lambda _: store)
    assert response["status"] == "APPLIED"
    assert store.load().mower_service_active is False
    assert store.load().manual_session_json == original.manual_session_json
    assert store.load().last_command_utc is None
    assert read.call_args.kwargs["persist_observations"] is False


def test_api_requires_session_before_any_switch_processing():
    req = func.HttpRequest(method="POST", url="https://example.test/api/platzwart/action", headers={},
        body=json.dumps({"action": "SET_MOWER_SERVICE", "requestId": "no-auth",
                         "confirmation": "SET_MOWER_SERVICE", "mowerService": {"enabled": True, "reason": "WINTER", "revision": 0}}).encode())
    with patch.object(function_app, "require_platzwart_session", side_effect=PlatzwartError("SESSION_INVALID", "Anmeldung erforderlich", 401)), patch.object(function_app, "platzwart_request_action") as action:
        response = function_app.ssv53_platzwart_action(req)
    assert response.status_code == 401
    action.assert_not_called()


def test_live_status_retains_service_switch_when_device_reads_fail():
    store = InMemoryStateStore(AutomationState(mower_service_active=True, mower_service_reason="WINTER"))
    with patch("platzwart_console.AzureTableStateStore.from_environment", return_value=store), \
         patch("platzwart_console.run_read_only_cycle", side_effect=RuntimeError("offline")), \
         patch("platzwart_console._dashboard_statistics", return_value={"available": False}), \
         patch("platzwart_console._clubhouse_events", return_value={"available": False}):
        response = live_status(API_ENV, NOW, include_details=False)
    assert response["mowerService"]["active"] is True
    assert response["controlsAvailable"] is False
    assert response["actionCapabilities"]["SET_MOWER_SERVICE"]["available"] is True
    assert response["dataQuality"]["code"] == "IRRIGATION_STATUS_UNAVAILABLE"
