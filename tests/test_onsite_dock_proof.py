from copy import deepcopy
from dataclasses import replace
from datetime import datetime, timedelta, timezone
import json
from unittest.mock import patch

import pytest

from mower import onsite_dock_proof
from mower.config_source import InputUnavailable
from mower.device_send_guard import load_device_send_journal
from mower.full_failsafe import (
    _water_dispatch_station_authorized,
    run_full_failsafe_cycle,
)
from mower.runtime import CycleResult, RuntimeSettings
from mower.state import AutomationState
from mower.state_store import InMemoryStateStore, StateConflictError
from platzwart_console import PlatzwartError, request_action
from tests.test_platzwart_console import FULL_DEVICE_CONTROL_ENV
from tests.test_full_failsafe import RELAYS, result as full_result


NOW = datetime(2026, 9, 12, 12, 0, tzinfo=timezone.utc)
ENV = {
    **FULL_DEVICE_CONTROL_ENV,
    "HUSQVARNA_MOWER_ID": "mower-1",
    "ENABLE_MANUAL_SESSIONS": "true",
    onsite_dock_proof.FLAG: "true",
}


def mower(**changes):
    value = {
        "mower_id": "mower-1",
        "connected": True,
        "state": "STOPPED",
        "activity": "NOT_APPLICABLE",
        "mode": "HOME",
        "error_code": 0,
        "override_action": "NOT_ACTIVE",
        "status_timestamp_ms": int(NOW.timestamp() * 1000),
    }
    value.update(changes)
    return value


def read_result(snapshot):
    return CycleResult(
        2, NOW.isoformat(), "test", "FULL_FAILSAFE", False, "TEST", False,
        "test", {"mower": snapshot},
    )


def confirmation(state, snapshot):
    return {
        "operation": onsite_dock_proof.OPERATION,
        "confirmed": True,
        "contextToken": onsite_dock_proof.context(state, snapshot, ENV, NOW)["contextToken"],
    }


def queued_proof(snapshot=None):
    snapshot = snapshot or mower()
    store = InMemoryStateStore()
    payload = confirmation(store.load(), snapshot)
    with patch("platzwart_console.run_read_only_cycle", return_value=read_result(snapshot)), patch(
        "platzwart_console.AzureTableStateStore.from_environment", return_value=store,
    ), patch(
        "platzwart_console.ConsoleTableStore.from_environment"
    ):
        response = request_action(
            "START_IRRIGATION", "water-1", "START_IRRIGATION", ENV, NOW,
            client_contract_version=4,
            manual_control=payload,
            state_store_factory=lambda _environment: store,
        )
    return store, response


def bound_state():
    store, _ = queued_proof()
    plan = [{"relay_id": RELAYS[0], "run_seconds": 600, "selected": True}]
    state = replace(
        store.load(),
        revision=store.load().revision + 1,
        irrigation_phase="PLANNED",
        irrigation_plan_id="plan-1",
        irrigation_plan_json=json.dumps(plan, sort_keys=True, separators=(",", ":")),
    )
    return onsite_dock_proof.bind_plan(
        state,
        request_id="water-1",
        action="START_IRRIGATION",
        plan_id="plan-1",
        plan=plan,
        now_utc=NOW + timedelta(seconds=30),
        end_confirmation_minutes=2,
    )


def proof_loss_cycle(snapshot, *, active, clear, at):
    cycle = deepcopy(full_result(active_ids=list(active), clear=clear))
    cycle.details["mower"].update(snapshot)
    cycle.details["mower"]["status_timestamp_ms"] = int(at.timestamp() * 1000)
    safety = cycle.details["hydrawise"]["safety"]
    safety["observed_at_utc"] = at.isoformat()
    safety["active_relay_ids"] = list(active)
    safety["active_zone_count"] = len(active)
    safety["clear_now"] = clear
    return cycle


def running_proof_store():
    state = replace(
        bound_state(),
        operator_request_status="COMPLETED",
        irrigation_phase="RUNNING",
        irrigation_current_relay_id=RELAYS[0],
        irrigation_zone_started_utc=(NOW + timedelta(seconds=30)).isoformat(),
    )
    return InMemoryStateStore(state)


def run_proof_loss(
    store, snapshot, *, active=(RELAYS[0],), clear=False, at=NOW + timedelta(minutes=1),
    stop_sender=None, environment=ENV,
):
    stop_sender = stop_sender or (lambda *_args: {"message_type": "info"})
    return run_full_failsafe_cycle(
        now_utc=at,
        settings=RuntimeSettings.from_mapping(environment),
        environment=environment,
        past_due=False,
        source="test-onsite-proof-loss",
        read_only_runner=lambda **_kwargs: proof_loss_cycle(
            snapshot, active=active, clear=clear, at=at,
        ),
        state_store_factory=lambda _environment: store,
        park_sender=lambda *_args: {"ok": True},
        suspend_zone_sender=lambda *_args: {"message_type": "info"},
        start_zone_sender=lambda *_args: {"message_type": "info"},
        stop_zone_sender=stop_sender,
        command_clock=lambda: at,
    )


def test_feature_is_default_off_and_error_9_is_never_confirmable():
    disabled = onsite_dock_proof.context(
        AutomationState(), mower(), {k: v for k, v in ENV.items() if k != onsite_dock_proof.FLAG}, NOW,
    )
    assert disabled["enabled"] is False
    assert disabled["canConfirm"] is False

    trapped = onsite_dock_proof.context(
        AutomationState(), mower(state="ERROR", error_code=9), ENV, NOW,
    )
    assert trapped["required"] is True
    assert trapped["canConfirm"] is False
    assert trapped["reason"] == "MOWER_ERROR"


def test_action_admission_persists_exact_request_and_status_binding():
    store, response = queued_proof()
    assert response == {"accepted": True, "requestId": "water-1", "status": "PENDING"}
    state = store.load()
    proof = json.loads(state.irrigation_onsite_dock_proof_json)
    assert proof["status"] == "REQUESTED"
    assert proof["request_id"] == state.operator_request_id == "water-1"
    assert proof["action"] == state.operator_request_action == "START_IRRIGATION"
    assert proof["mower_id"] == "mower-1"
    assert proof["source_at_utc"] == NOW.isoformat()


def test_changed_status_token_and_parallel_state_change_are_rejected():
    store = InMemoryStateStore()
    original = store.load()
    body = confirmation(original, mower())
    changed = mower(status_timestamp_ms=int((NOW + timedelta(seconds=1)).timestamp() * 1000))
    with patch("platzwart_console.run_read_only_cycle", return_value=read_result(changed)), patch(
        "platzwart_console.AzureTableStateStore.from_environment", return_value=store,
    ), patch(
        "platzwart_console.ConsoleTableStore.from_environment"
    ), pytest.raises(PlatzwartError) as error:
        request_action(
            "START_IRRIGATION", "water-1", "START_IRRIGATION", ENV, NOW,
            client_contract_version=4, manual_control=body,
            state_store_factory=lambda _environment: store,
        )
    assert error.value.code == "ONSITE_DOCK_CONTEXT_CHANGED"

    stale = store.load()
    store.save(replace(stale, revision=stale.revision + 1), expected_revision=stale.revision)
    with pytest.raises(StateConflictError):
        store.save(replace(stale, revision=stale.revision + 1), expected_revision=stale.revision)


def test_routine_revision_does_not_expire_visible_confirmation_token():
    state = AutomationState()
    token = onsite_dock_proof.context(state, mower(), ENV, NOW)["contextToken"]
    assert onsite_dock_proof.context(
        replace(state, revision=state.revision + 1), mower(), ENV, NOW,
    )["contextToken"] == token


@pytest.mark.parametrize("unsafe_override", ["START", "FORCE_START", "UNKNOWN_ACTION"])
def test_initial_non_park_override_cannot_be_confirmed(unsafe_override):
    status = onsite_dock_proof.context(
        AutomationState(), mower(override_action=unsafe_override), ENV, NOW,
    )
    assert status["canConfirm"] is False
    assert status["reason"] == "MOWER_OVERRIDE_UNSAFE"


@pytest.mark.parametrize("timestamp_delta", [0, 1])
def test_any_override_change_revokes_bound_proof(timestamp_delta):
    state = bound_state()
    changed = mower(
        override_action="PARK_UNTIL_FURTHER_NOTICE",
        status_timestamp_ms=int((NOW + timedelta(seconds=timestamp_delta)).timestamp() * 1000),
    )
    projected = onsite_dock_proof.observe(
        state, changed, ENV, NOW + timedelta(minutes=1),
    )
    assert json.loads(projected.irrigation_onsite_dock_proof_json)["status"] == "INVALID"


@pytest.mark.parametrize(
    "change",
    [
        {"connected": False},
        {"state": "OFF"},
        {"state": "ERROR", "error_code": 9},
        {"activity": "LEAVING", "mode": "MAIN"},
        {"status_timestamp_ms": int((NOW - timedelta(minutes=4)).timestamp() * 1000)},
    ],
)
def test_offline_error_movement_and_stale_status_revoke_bound_proof(change):
    state = bound_state()
    projected = onsite_dock_proof.observe(state, mower(**change), ENV, NOW + timedelta(minutes=1))
    proof = json.loads(projected.irrigation_onsite_dock_proof_json)
    assert proof["status"] == "INVALID"
    assert not onsite_dock_proof.valid_for_plan(projected, mower(**change), ENV, NOW + timedelta(minutes=1))


def test_repeated_invalidation_and_operator_change_preserve_exact_stop_binding():
    state = replace(
        bound_state(), operator_request_status="COMPLETED",
        irrigation_phase="RUNNING", irrigation_current_relay_id=RELAYS[0],
    )
    first = onsite_dock_proof.invalidate(
        state, now_utc=NOW + timedelta(minutes=1), reason="MOWER_OFFLINE",
    )
    second = onsite_dock_proof.invalidate(
        first, now_utc=NOW + timedelta(minutes=2), reason="INPUT_UNAVAILABLE",
    )
    changed_request = replace(
        second, operator_request_id="later-request",
        operator_request_action="PARK_MOWER", operator_request_status="PENDING",
    )
    assert onsite_dock_proof.invalidated_for_active_plan(first)
    assert onsite_dock_proof.invalidated_for_active_plan(second)
    assert onsite_dock_proof.invalidated_for_active_plan(changed_request)

    failed_cycle = changed_request.record_cycle(
        started_utc=NOW + timedelta(minutes=3), success=False,
        decision_code="INPUT_UNAVAILABLE", mower_activity=None,
        mower_state=None, error_code=None,
    )
    restarted = AutomationState.from_mapping(failed_cycle.to_dict())
    assert onsite_dock_proof.invalidated_for_active_plan(restarted)


@pytest.mark.parametrize(
    "lost_snapshot",
    [
        mower(activity="LEAVING", mode="MAIN_AREA"),
        mower(state="ERROR", error_code=9),
        mower(connected=False),
    ],
)
def test_bound_proof_loss_stops_exact_running_relay_and_waits_for_physical_end(
    lost_snapshot,
):
    store = running_proof_store()
    stop_calls = []
    output = run_proof_loss(
        store, lost_snapshot,
        stop_sender=lambda *_args: stop_calls.append(_args) or {"message_type": "info"},
    )
    assert output.decision_code == "ONSITE_DOCK_PROOF_LOST_STOP_SENT"
    assert [call[1] for call in stop_calls] == [RELAYS[0]]
    stopped = store.load()
    assert stopped.irrigation_phase == "STOPPING"
    assert stopped.irrigation_current_relay_id == RELAYS[0]
    records = load_device_send_journal(stopped)
    assert [(entry["kind"], entry["target"], entry["status"]) for entry in records] == [
        ("WATER_STOP", str(RELAYS[0]), "SENT_UNCONFIRMED")
    ]

    # A restart cannot replay the deterministic stop intent.
    restarted = InMemoryStateStore(AutomationState.from_mapping(stopped.to_dict()))
    waiting = run_proof_loss(
        restarted, mower(), at=NOW + timedelta(minutes=2),
        stop_sender=lambda *_args: stop_calls.append(_args) or {"message_type": "info"},
    )
    assert waiting.decision_code == "ONSITE_DOCK_PROOF_LOST_WAIT_FOR_STOP"
    assert [call[1] for call in stop_calls] == [RELAYS[0]]

    first_clear = run_proof_loss(
        restarted, mower(), active=(), clear=True,
        at=NOW + timedelta(minutes=3),
        stop_sender=lambda *_args: stop_calls.append(_args) or {"message_type": "info"},
    )
    assert first_clear.decision_code == "ONSITE_DOCK_PROOF_LOST_CONFIRM_STOP"
    confirmed = run_proof_loss(
        restarted, mower(), active=(), clear=True,
        at=NOW + timedelta(minutes=5),
        stop_sender=lambda *_args: stop_calls.append(_args) or {"message_type": "info"},
    )
    assert confirmed.decision_code == "ONSITE_DOCK_PROOF_LOST_STOP_CONFIRMED"
    final = restarted.load()
    assert final.irrigation_phase == "COMPLETE_HOLD"
    assert final.irrigation_current_relay_id is None
    assert final.hydrawise_clear_origin == "IRRIGATION_END"
    assert final.hydrawise_drying_since_utc == (NOW + timedelta(minutes=3)).isoformat()
    assert final.operator_request_id == "water-1"
    assert [call[1] for call in stop_calls] == [RELAYS[0]]


def test_unknown_water_stop_still_runs_persistent_protective_park_next_cycle():
    store = running_proof_store()
    stop_calls = []
    park_calls = []

    def unknown_stop(*args, before_send):
        stop_calls.append(args)
        before_send()
        raise TimeoutError("unknown stop response")

    first = run_proof_loss(
        store, mower(activity="LEAVING", mode="MAIN_AREA"),
        stop_sender=unknown_stop,
    )
    assert first.command_sent is True
    second_at = NOW + timedelta(minutes=2)
    second = run_full_failsafe_cycle(
        now_utc=second_at,
        settings=RuntimeSettings.from_mapping(ENV), environment=ENV,
        past_due=False, source="test-proof-loss-park",
        read_only_runner=lambda **_kwargs: proof_loss_cycle(
            mower(activity="LEAVING", mode="MAIN_AREA"),
            active=(RELAYS[0],), clear=False, at=second_at,
        ),
        state_store_factory=lambda _environment: store,
        park_sender=lambda *_args: park_calls.append(_args) or {"ok": True},
        stop_zone_sender=unknown_stop,
        command_clock=lambda: second_at,
    )
    assert second.command_sent is True
    assert len(park_calls) == 1
    assert len(stop_calls) == 1
    persisted = store.load()
    assert persisted.parked_by_automation is True
    assert persisted.irrigation_phase == "STOPPING"
    assert onsite_dock_proof.invalidated_for_active_plan(persisted)


def test_read_continuity_loss_stops_start_reserved_relay_that_may_have_started():
    initial = replace(
        bound_state(), operator_request_status="COMPLETED",
        irrigation_phase="START_RESERVED",
        irrigation_current_relay_id=RELAYS[0],
        irrigation_zone_start_reserved_utc=(NOW + timedelta(seconds=30)).isoformat(),
    )
    store = InMemoryStateStore(initial)
    calls = []
    output = run_proof_loss(
        store, mower(), active=(), clear=True,
        at=NOW + timedelta(minutes=2),
        stop_sender=lambda *_args: calls.append(_args) or {"message_type": "info"},
    )
    assert output.decision_code == "ONSITE_DOCK_PROOF_LOST_STOP_SENT"
    assert [call[1] for call in calls] == [RELAYS[0]]
    persisted = store.load()
    assert persisted.irrigation_phase == "STOPPING"
    assert json.loads(persisted.irrigation_onsite_dock_proof_json)["reason"] == "READ_CONTINUITY_LOST"


def test_protective_park_never_targets_a_changed_or_unbound_mower():
    store = running_proof_store()
    calls = []
    moved = mower(mower_id="different-mower", activity="LEAVING", mode="MAIN_AREA")
    first = run_proof_loss(store, moved, stop_sender=lambda *_args: {"message_type": "info"})
    assert first.decision_code == "ONSITE_DOCK_PROOF_LOST_STOP_SENT"
    at = NOW + timedelta(minutes=2)
    for configured in ("mower-1", "different-mower"):
        result = run_full_failsafe_cycle(
            now_utc=at, settings=RuntimeSettings.from_mapping(ENV),
            environment={**ENV, "HUSQVARNA_MOWER_ID": configured},
            past_due=False, source="test-bound-park-target",
            read_only_runner=lambda **_kwargs: proof_loss_cycle(
                moved, active=(RELAYS[0],), clear=False, at=at,
            ),
            state_store_factory=lambda _environment: store,
            park_sender=lambda *_args: calls.append(_args) or {"ok": True},
            stop_zone_sender=lambda *_args: {"message_type": "info"},
            command_clock=lambda: at,
        )
        assert result.decision_code == "ONSITE_DOCK_PROOF_LOST_WAIT_FOR_STOP"
        assert calls == []


def test_foreign_active_relay_is_never_targeted_and_inconsistent_clear_never_completes():
    store = running_proof_store()
    calls = []
    output = run_proof_loss(
        store, mower(activity="LEAVING", mode="MAIN_AREA"),
        active=(RELAYS[0], RELAYS[1]), clear=False,
        stop_sender=lambda *_args: calls.append(_args) or {"message_type": "info"},
    )
    assert output.decision_code == "ONSITE_DOCK_PROOF_LOST_STOP_SENT"
    assert [call[1] for call in calls] == [RELAYS[0]]

    inconsistent = proof_loss_cycle(
        mower(), active=(), clear=True,
        at=NOW + timedelta(minutes=3),
    )
    inconsistent.details["hydrawise"]["safety"]["active_zone_count"] = 1
    output = run_full_failsafe_cycle(
        now_utc=NOW + timedelta(minutes=3),
        settings=RuntimeSettings.from_mapping(ENV), environment=ENV,
        past_due=False, source="test-inconsistent-clear",
        read_only_runner=lambda **_kwargs: inconsistent,
        state_store_factory=lambda _environment: store,
        park_sender=lambda *_args: {"ok": True},
        stop_zone_sender=lambda *_args: calls.append(_args) or {"message_type": "info"},
        command_clock=lambda: NOW + timedelta(minutes=3),
    )
    assert output.decision_code == "ONSITE_DOCK_PROOF_LOST_STOP_END_UNCLEAR"
    assert store.load().irrigation_phase == "STOPPING"
    assert [call[1] for call in calls] == [RELAYS[0]]


def test_interrupted_clear_confirmation_restarts_from_new_observation():
    store = running_proof_store()
    lost = mower(state="ERROR", error_code=9)
    run_proof_loss(store, lost, at=NOW + timedelta(minutes=1))
    first = run_proof_loss(
        store, lost, active=(), clear=True, at=NOW + timedelta(minutes=3),
    )
    assert first.decision_code == "ONSITE_DOCK_PROOF_LOST_CONFIRM_STOP"
    assert store.load().irrigation_zone_clear_since_utc == (NOW + timedelta(minutes=3)).isoformat()

    interrupted = run_proof_loss(
        store, lost, active=(RELAYS[0],), clear=False,
        at=NOW + timedelta(minutes=4),
    )
    assert interrupted.decision_code == "ONSITE_DOCK_PROOF_LOST_WAIT_FOR_STOP"
    assert store.load().irrigation_zone_clear_since_utc is None
    restarted = run_proof_loss(
        store, lost, active=(), clear=True, at=NOW + timedelta(minutes=5),
    )
    assert restarted.decision_code == "ONSITE_DOCK_PROOF_LOST_CONFIRM_STOP"
    assert store.load().irrigation_zone_clear_since_utc == (NOW + timedelta(minutes=5)).isoformat()
    confirmed = run_proof_loss(
        store, lost, active=(), clear=True, at=NOW + timedelta(minutes=7),
    )
    assert confirmed.decision_code == "ONSITE_DOCK_PROOF_LOST_STOP_CONFIRMED"


def test_lost_stop_response_is_not_replayed_after_restart():
    store = running_proof_store()
    calls = []

    def lost_response(*args, before_send):
        calls.append(args)
        before_send()
        raise TimeoutError("response lost")

    output = run_proof_loss(
        store, mower(activity="LEAVING", mode="MAIN_AREA"),
        stop_sender=lost_response,
    )
    assert output.command_sent is True
    assert load_device_send_journal(store.load())[0]["status"] == "UNKNOWN"
    restarted = InMemoryStateStore(store.load())
    output = run_proof_loss(
        restarted, mower(),
        at=NOW + timedelta(minutes=2), stop_sender=lost_response,
    )
    assert output.decision_code == "ONSITE_DOCK_PROOF_LOST_WAIT_FOR_STOP"
    assert len(calls) == 1


def test_parallel_change_revokes_stop_before_transport():
    store = running_proof_store()
    calls = []

    def concurrent_change(*args, before_send):
        calls.append(args)
        latest = store.load()
        store.save(
            replace(
                latest, revision=latest.revision + 1,
                operator_request_id="parallel-request",
            ),
            expected_revision=latest.revision,
        )
        before_send()

    output = run_proof_loss(
        store, mower(activity="LEAVING", mode="MAIN_AREA"),
        stop_sender=concurrent_change,
    )
    assert output.decision_code == "DEVICE_RESERVATION_REVOKED"
    assert output.command_sent is False
    assert len(calls) == 1


def test_default_off_running_path_never_acquires_proof_loss_stop_authority():
    environment = {k: v for k, v in ENV.items() if k != onsite_dock_proof.FLAG}
    initial = replace(
        running_proof_store().load(), irrigation_onsite_dock_proof_json=None,
        parked_by_automation=True, automation_park_source="irrigation",
    )
    store = InMemoryStateStore(initial)
    calls = []
    output = run_proof_loss(
        store, mower(activity="PARKED_IN_CS", state="IN_OPERATION", mode="HOME",
                     override_action="FORCE_PARK"),
        active=(RELAYS[0],), clear=False, environment=environment,
        stop_sender=lambda *_args: calls.append(_args) or {"message_type": "info"},
    )
    assert not output.decision_code.startswith("ONSITE_DOCK_PROOF_LOST")
    assert calls == []


def test_input_unavailable_still_stops_exact_bound_water_relay_and_never_confirms_end():
    store = running_proof_store()
    calls = []

    def unavailable(**_kwargs):
        raise InputUnavailable("injected")

    guarded = CycleResult(
        2, NOW.isoformat(), "input-failure", "FULL_FAILSAFE", False,
        "INPUT_UNAVAILABLE", False, "inputs unavailable", {},
    )

    def protective_park_transition(**_kwargs):
        original = store.load()
        failed = original.record_cycle(
            started_utc=NOW + timedelta(minutes=1), success=False,
            decision_code="INPUT_UNAVAILABLE", mower_activity="LEAVING",
            mower_state="IN_OPERATION", error_code=0,
        )
        parked = failed.record_command(
            fingerprint="input-failure-park",
            sent_utc=NOW + timedelta(minutes=1), action="PARK",
            park_source="input_unavailable", restart_allowed=False,
        )
        store.save(parked, expected_revision=original.revision)
        return guarded

    output = run_full_failsafe_cycle(
        now_utc=NOW + timedelta(minutes=1),
        settings=RuntimeSettings.from_mapping(ENV), environment=ENV,
        past_due=False, source="test-input-failure",
        read_only_runner=unavailable,
        input_failure_runner=protective_park_transition,
        state_store_factory=lambda _environment: store,
        stop_zone_sender=lambda *_args: calls.append(_args) or {"message_type": "info"},
        command_clock=lambda: NOW + timedelta(minutes=1),
    )
    assert output.decision_code == "ONSITE_DOCK_PROOF_LOST_STOP_SENT"
    assert [call[1] for call in calls] == [RELAYS[0]]
    assert store.load().irrigation_phase == "STOPPING"
    assert store.load().irrigation_completed_utc is None

    # A second outage retains the durable stop binding and cannot replay or
    # claim a physical end without complete Hydrawise telemetry.
    output = run_full_failsafe_cycle(
        now_utc=NOW + timedelta(minutes=2),
        settings=RuntimeSettings.from_mapping(ENV), environment=ENV,
        past_due=False, source="test-second-input-failure",
        read_only_runner=unavailable,
        input_failure_runner=lambda **_kwargs: guarded,
        state_store_factory=lambda _environment: store,
        stop_zone_sender=lambda *_args: calls.append(_args) or {"message_type": "info"},
        command_clock=lambda: NOW + timedelta(minutes=2),
    )
    assert output.decision_code == "ONSITE_DOCK_PROOF_LOST_STOP_INPUTS_UNSAFE"
    assert [call[1] for call in calls] == [RELAYS[0]]
    assert onsite_dock_proof.invalidated_for_active_plan(store.load())


def test_flag_off_invalidation_survives_locked_cycles_until_exact_stop_can_be_sent():
    locked = {
        **ENV,
        onsite_dock_proof.FLAG: "false",
        "ENABLE_IRRIGATION_COMMANDS": "false",
    }
    store = running_proof_store()
    calls = []
    first = run_proof_loss(
        store, mower(), environment=locked,
        stop_sender=lambda *_args: calls.append(_args) or {"message_type": "info"},
    )
    assert first.decision_code == "ONSITE_DOCK_PROOF_LOST_STOP_LOCKED"
    assert onsite_dock_proof.invalidated_for_active_plan(store.load())
    assert calls == []

    still_locked = run_proof_loss(
        store, mower(), at=NOW + timedelta(minutes=2), environment=locked,
        stop_sender=lambda *_args: calls.append(_args) or {"message_type": "info"},
    )
    assert still_locked.decision_code == "ONSITE_DOCK_PROOF_LOST_STOP_LOCKED"
    assert onsite_dock_proof.invalidated_for_active_plan(store.load())
    assert calls == []

    unlocked = {**ENV, onsite_dock_proof.FLAG: "false"}
    sent = run_proof_loss(
        store, mower(), at=NOW + timedelta(minutes=3), environment=unlocked,
        stop_sender=lambda *_args: calls.append(_args) or {"message_type": "info"},
    )
    assert sent.decision_code == "ONSITE_DOCK_PROOF_LOST_STOP_SENT"
    assert [call[1] for call in calls] == [RELAYS[0]]


def test_short_admission_does_not_cut_off_program_and_restart_never_extends_end():
    state = bound_state()
    proof = json.loads(state.irrigation_onsite_dock_proof_json)
    admission_end = datetime.fromisoformat(proof["admission_expires_at_utc"])
    program_end = datetime.fromisoformat(proof["program_not_after_utc"])
    assert program_end > admission_end
    restarted = AutomationState.from_mapping(state.to_dict())
    restarted = onsite_dock_proof.observe(
        restarted, mower(), ENV, NOW + timedelta(seconds=60),
    )
    restarted = onsite_dock_proof.observe(
        restarted, mower(), ENV, admission_end + timedelta(seconds=1),
    )
    assert onsite_dock_proof.valid_for_plan(restarted, mower(), ENV, admission_end + timedelta(seconds=1))
    assert json.loads(restarted.irrigation_onsite_dock_proof_json)["program_not_after_utc"] == proof["program_not_after_utc"]
    assert not onsite_dock_proof.valid_for_plan(
        restarted, mower(status_timestamp_ms=int(program_end.timestamp() * 1000)), ENV, program_end,
    )


def test_unchanged_stopped_vendor_event_can_be_rechecked_for_two_hours_without_extending_program():
    store, _ = queued_proof()
    plan = [{"relay_id": relay, "run_seconds": 1200, "selected": True} for relay in range(1, 8)]
    state = replace(
        store.load(), revision=store.load().revision + 1,
        irrigation_phase="PLANNED", irrigation_plan_id="long-plan",
        irrigation_plan_json=json.dumps(plan, sort_keys=True, separators=(",", ":")),
    )
    state = onsite_dock_proof.bind_plan(
        state, request_id="water-1", action="START_IRRIGATION",
        plan_id="long-plan", plan=plan, now_utc=NOW + timedelta(seconds=30),
        end_confirmation_minutes=2,
    )
    immutable_end = json.loads(state.irrigation_onsite_dock_proof_json)["program_not_after_utc"]
    state = replace(state, operator_request_status="COMPLETED")
    for minute in range(1, 121):
        at = NOW + timedelta(minutes=minute)
        state = AutomationState.from_mapping(state.to_dict())
        state = onsite_dock_proof.observe(state, mower(), ENV, at)
        assert onsite_dock_proof.valid_for_plan(state, mower(), ENV, at)
        assert json.loads(state.irrigation_onsite_dock_proof_json)["program_not_after_utc"] == immutable_end


def test_new_operator_request_invalidates_bound_plan_proof():
    state = replace(
        bound_state(), operator_request_id="another-request",
        operator_request_action="PARK_MOWER", operator_request_status="PENDING",
    )
    state = onsite_dock_proof.observe(state, mower(), ENV, NOW + timedelta(minutes=1))
    assert json.loads(state.irrigation_onsite_dock_proof_json)["status"] == "INVALID"


def test_plan_payload_change_with_same_plan_id_invalidates_bound_proof():
    state = bound_state()
    changed = replace(state, irrigation_plan_json=json.dumps([
        {"relay_id": 1, "run_seconds": 601, "selected": True},
    ]))
    assert not onsite_dock_proof.valid_for_plan(changed, mower(), ENV, NOW + timedelta(minutes=1))


def test_unconfirmed_device_send_revokes_proof_but_current_water_reservation_can_be_checked():
    state = bound_state()
    intent = "water-start:plan-1:1:reserved"
    pending = [{
        "version": 1,
        "id": "send-1",
        "kind": "WATER_START",
        "target": "1",
        "intent_key": intent,
        "control_binding": "binding",
        "reserved_at_utc": NOW.isoformat(),
        "deadline_utc": (NOW + timedelta(minutes=1)).isoformat(),
        "status": "RESERVED",
    }]
    pending_state = replace(state, device_send_journal_json=json.dumps(pending))
    assert not onsite_dock_proof.valid_for_plan(pending_state, mower(), ENV, NOW)
    assert onsite_dock_proof.valid_for_plan(
        pending_state, mower(), ENV, NOW, allowed_intent_key=intent,
    )


def test_requested_proof_can_only_bind_once_within_120_seconds():
    store, _ = queued_proof()
    state = replace(
        store.load(), revision=store.load().revision + 1,
        irrigation_phase="PLANNED", irrigation_plan_id="plan-1",
        irrigation_plan_json=json.dumps([{"run_seconds": 600}], sort_keys=True, separators=(",", ":")),
    )
    with pytest.raises(onsite_dock_proof.OnsiteDockProofError) as error:
        onsite_dock_proof.bind_plan(
            state, request_id="water-1", action="START_IRRIGATION", plan_id="plan-1",
            plan=[{"run_seconds": 600}], now_utc=NOW + timedelta(seconds=120),
            end_confirmation_minutes=2,
        )
    assert error.value.code == "ONSITE_DOCK_BINDING_EXPIRED"


def test_flag_off_error_status_cannot_pass_the_dispatch_station_guard():
    environment = {k: v for k, v in ENV.items() if k != onsite_dock_proof.FLAG}
    assert not _water_dispatch_station_authorized(
        AutomationState(), mower(state="ERROR", error_code=9),
        now_utc=NOW, environment=environment, max_age_seconds=180,
    )
    assert not _water_dispatch_station_authorized(
        bound_state(), mower(state="ERROR", error_code=9),
        now_utc=NOW + timedelta(minutes=1), environment=ENV,
        max_age_seconds=180,
    )
    assert not _water_dispatch_station_authorized(
        bound_state(), mower(activity="LEAVING", mode="MAIN_AREA"),
        now_utc=NOW + timedelta(minutes=1), environment=ENV,
        max_age_seconds=180,
    )


def test_post_to_plan_to_first_and_second_zone_keeps_stop_and_rechecks_same_vendor_event():
    store, _ = queued_proof()
    cycle = deepcopy(full_result())
    fixed_mower = mower()
    cycle.details["mower"].update(fixed_mower)
    cycle.details["mower"]["mode"] = "HOME"
    cycle.details["mower"]["override_action"] = "NOT_ACTIVE"
    cycle.details["mower"]["external_reason_id"] = None
    park_calls = []
    zone_calls = []
    decision_codes = []

    def run(at, *, active=(), suspended=False):
        current = deepcopy(cycle)
        current.details["mower"]["status_timestamp_ms"] = fixed_mower["status_timestamp_ms"]
        safety = current.details["hydrawise"]["safety"]
        safety["observed_at_utc"] = at.isoformat()
        safety["active_relay_ids"] = list(active)
        safety["active_zone_count"] = len(active)
        safety["clear_now"] = not active
        for item in current.details["hydrawise"]["zone_observations"]:
            item["running"] = item["relay_id"] in active
            if suspended:
                item["scheduled"] = False
                item["scheduled_start_utc"] = None
                item["scheduled_end_utc"] = None
        if suspended:
            current.details["hydrawise"]["zones"] = []
        outcome = run_full_failsafe_cycle(
            now_utc=at,
            settings=__import__("mower.runtime", fromlist=["RuntimeSettings"]).RuntimeSettings.from_mapping(ENV),
            environment=ENV,
            past_due=False,
            source="test-onsite-e2e",
            read_only_runner=lambda **_kwargs: current,
            state_store_factory=lambda _environment: store,
            park_sender=lambda *_args: park_calls.append(_args) or {"ok": True},
            suspend_zone_sender=lambda *_args: {"message_type": "info"},
            start_zone_sender=lambda *_args: zone_calls.append(_args) or {"message_type": "info"},
            stop_zone_sender=lambda *_args: {"message_type": "info"},
            command_clock=lambda: at,
        )
        decision_codes.append((at.isoformat(), outcome.decision_code, store.load().irrigation_phase))
        return outcome

    output = run(NOW + timedelta(seconds=30))
    assert output.decision_code == "IRRIGATION_ZONE_SUSPENDED"
    assert json.loads(store.load().irrigation_onsite_dock_proof_json)["status"] == "BOUND"
    assert park_calls == []

    for minute in range(1, 9):
        output = run(NOW + timedelta(minutes=minute), suspended=True)
        if output.decision_code == "IRRIGATION_ZONE_START_SENT":
            break
    assert output.decision_code == "IRRIGATION_ZONE_START_SENT"
    assert [call[1] for call in zone_calls] == [RELAYS[0]]
    assert park_calls == []

    # Keep polling the same vendor STOP event. Each successful direct read is
    # persisted, while the immutable source event and program deadline remain.
    for minute in range(8, 29):
        active = (RELAYS[0],) if minute < 28 else ()
        run(NOW + timedelta(minutes=minute), active=active, suspended=True)
    for minute in range(29, 34):
        run(NOW + timedelta(minutes=minute), active=(), suspended=True)
        if len(zone_calls) >= 2:
            break
    assert [call[1] for call in zone_calls[:2]] == RELAYS[:2], decision_codes
    assert park_calls == []
    assert store.load().last_mower_state == "STOPPED"
