from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest

from mower.operator_controls import (
    OperatorControlError,
    action_capabilities,
    operator_commands_payload,
    queue_operator_action as _queue_operator_action,
    run_operator_cycle,
)
from mower.runtime import CycleResult, RuntimeSettings
from mower.state import AutomationState
from mower.state_store import InMemoryStateStore, JsonFileStateStore


NOW = datetime(2026, 9, 9, 20, tzinfo=timezone.utc)


def queue_operator_action(store, settings_value, action, request_id, now_utc, **kwargs):
    """Test-only wrapper keeps every test request explicitly target-bound."""
    return _queue_operator_action(
        store, settings_value, action, request_id, now_utc,
        mower_id="mower-1", **kwargs,
    )


def settings(**overrides: str) -> RuntimeSettings:
    values = {
        "CONTROL_MODE": "OPERATOR_ONLY", "ENABLE_LIVE_READS": "true",
        "ENABLE_PARK_COMMANDS": "true", "ENABLE_START_COMMANDS": "false",
        "ENABLE_IRRIGATION_COMMANDS": "false",
        "ENABLE_OPERATOR_CUTTING_HEIGHT_COMMANDS": "true",
        "OPERATOR_CONTROL_CONFIRMATION": "SSV53-OPERATOR-PARK-HEIGHT-V1",
    }
    values.update(overrides)
    return RuntimeSettings.from_mapping(values)


def mower(*, now: datetime = NOW, activity="MOWING", height=12) -> dict:
    return {
        "mower_id": "mower-1", "connected": True, "activity": activity,
        "state": "IN_OPERATION", "error_code": 0, "model": "Automower 580 EPOS",
        "override_action": "NOT_ACTIVE", "status_timestamp_ms": int(now.timestamp() * 1000),
        "target_work_area": {"id": 1, "name": "Rasenfläche", "enabled": True,
                             "use_global_cutting_height": False, "cutting_height_percent": height},
    }


def reader(snapshot: dict):
    def run(**_kwargs):
        return CycleResult(2, NOW.isoformat(), "test", "DRY_RUN", False, "READ", False, "READ", {"mower": snapshot})
    return run


def run(store, snapshot, *, moment=NOW, environment=None, state_store_factory=None, command_clock=None, **kwargs):
    return run_operator_cycle(
        now_utc=moment, settings=settings(), environment=environment or {"HUSQVARNA_MOWER_ID": "mower-1"}, past_due=False, source="test",
        read_only_runner=reader(snapshot), state_store_factory=state_store_factory or (lambda _: store),
        command_clock=command_clock or (lambda: moment), **kwargs,
    )


def test_capabilities_and_queue_are_operator_only_and_do_not_touch_legacy_requests():
    store = InMemoryStateStore(AutomationState(operator_request_action="PARK_MOWER", operator_request_status="PENDING"))
    assert action_capabilities(settings())["PARK_MOWER"]["available"] is True
    queued = queue_operator_action(store, settings(), "PARK_MOWER", "park-1", NOW)
    assert queued["status"] == "QUEUED"
    state = store.load()
    assert state.operator_request_action == "PARK_MOWER"
    assert operator_commands_payload(state)["PARK_MOWER"]["requestId"] == "park-1"


@pytest.mark.parametrize("overrides", [
    {"CONTROL_MODE": "DRY_RUN"}, {"ENABLE_LIVE_READS": "false"},
    {"ENABLE_PARK_COMMANDS": "false"}, {"OPERATOR_CONTROL_CONFIRMATION": "LOCKED"},
])
def test_missing_mode_or_gate_never_writes_queue(overrides):
    store = InMemoryStateStore()
    before = store.load()
    with pytest.raises(OperatorControlError):
        queue_operator_action(store, settings(**overrides), "PARK_MOWER", "no-write", NOW)
    assert store.load() == before


def test_only_queued_operator_park_is_sent_and_it_latches_manual_no_restart():
    store = InMemoryStateStore()
    queue_operator_action(store, settings(), "PARK_MOWER", "park-1", NOW)
    sent = []
    output = run(store, mower(), park_sender=lambda *_a, before_send=None: before_send() or sent.append("park") or {"ok": True})
    state = store.load()
    assert output.command_sent and sent == ["park"]
    assert state.parked_by_automation and state.automation_park_source == "operator"
    assert not state.automation_restart_allowed and not state.continuous_mowing_owned
    assert operator_commands_payload(state)["PARK_MOWER"]["status"] == "SENT_UNCONFIRMED"


def test_height_25_to_26_is_sent_then_requires_new_matching_snapshot():
    store = InMemoryStateStore()
    queue_operator_action(store, settings(), "SET_CUTTING_HEIGHT", "height-1", NOW, cutting_height_mm=26)
    sent = []
    run(store, mower(height=12), cutting_height_sender=lambda *_a, before_send=None: before_send() or sent.append(_a[-1]) or {"accepted": True})
    assert sent == [15]
    assert operator_commands_payload(store.load())["SET_CUTTING_HEIGHT"]["status"] == "SENT_UNCONFIRMED"
    run(store, mower(now=NOW + timedelta(seconds=30), height=15), moment=NOW + timedelta(seconds=30))
    assert operator_commands_payload(store.load())["SET_CUTTING_HEIGHT"]["status"] == "CONFIRMED"


def test_park_has_priority_over_pending_height_and_two_cycles_cannot_resend():
    store = InMemoryStateStore()
    queue_operator_action(store, settings(), "SET_CUTTING_HEIGHT", "height-1", NOW, cutting_height_mm=26)
    queue_operator_action(store, settings(), "PARK_MOWER", "park-1", NOW)
    sent = []
    sender = lambda *_a, before_send=None: before_send() or sent.append("park") or {"ok": True}
    run(store, mower(), park_sender=sender)
    run(store, mower(now=NOW - timedelta(minutes=4)), park_sender=sender)
    status = operator_commands_payload(store.load())
    assert sent == ["park"]
    assert status["PARK_MOWER"]["status"] == "SENT_UNCONFIRMED"
    assert status["SET_CUTTING_HEIGHT"]["status"] == "QUEUED"


def test_lost_response_or_crash_after_reservation_is_unknown_without_retry():
    store = InMemoryStateStore()
    queue_operator_action(store, settings(), "PARK_MOWER", "park-1", NOW)
    calls = []
    def lose(*_args, before_send=None):
        before_send(); calls.append("sent"); raise TimeoutError()
    run(store, mower(), park_sender=lose)
    assert operator_commands_payload(store.load())["PARK_MOWER"]["status"] == "RESERVED"
    run(store, mower(now=NOW + timedelta(minutes=11)), moment=NOW + timedelta(minutes=11), park_sender=lambda *_a, **_k: pytest.fail("must not retry"))
    assert calls == ["sent"]
    assert operator_commands_payload(store.load())["PARK_MOWER"]["status"] == "UNKNOWN"


def test_stale_or_wrong_height_confirmation_never_completes_and_legacy_request_is_ignored():
    legacy = AutomationState(operator_request_action="SET_CUTTING_HEIGHT", operator_request_status="PENDING")
    store = InMemoryStateStore(legacy)
    run(store, mower(), cutting_height_sender=lambda *_a, **_k: pytest.fail("legacy request must not run"))
    assert store.load().operator_request_status == "PENDING"
    queue_operator_action(store, settings(), "SET_CUTTING_HEIGHT", "height-1", NOW, cutting_height_mm=26)
    run(store, mower(height=12), cutting_height_sender=lambda *_a, before_send=None: before_send() or {"accepted": True})
    run(store, mower(now=NOW + timedelta(seconds=30), height=14), moment=NOW + timedelta(seconds=30))
    assert operator_commands_payload(store.load())["SET_CUTTING_HEIGHT"]["status"] == "SENT_UNCONFIRMED"


def test_request_id_reuse_with_a_different_height_is_rejected():
    store = InMemoryStateStore()
    queue_operator_action(store, settings(), "SET_CUTTING_HEIGHT", "height-1", NOW, cutting_height_mm=25)
    with pytest.raises(OperatorControlError, match="REQUEST_ID_REUSED"):
        queue_operator_action(store, settings(), "SET_CUTTING_HEIGHT", "height-1", NOW, cutting_height_mm=26)


def test_unknown_is_preserved_and_a_new_explicit_request_can_be_queued_without_replay():
    store = InMemoryStateStore()
    queue_operator_action(store, settings(), "PARK_MOWER", "park-1", NOW)
    def lose(*_args, before_send=None):
        before_send()
        raise TimeoutError()
    run(store, mower(), park_sender=lose)
    later = NOW + timedelta(minutes=11)
    run(store, mower(now=later), moment=later, park_sender=lambda *_a, **_k: pytest.fail("unknown must not resend"))
    assert operator_commands_payload(store.load())["PARK_MOWER"]["status"] == "UNKNOWN"
    queued = queue_operator_action(store, settings(), "PARK_MOWER", "park-2", later)
    assert queued["status"] == "QUEUED"
    records = json.loads(store.load().operator_commands_json)
    assert {record["request_id"] for record in records} == {"park-1", "park-2"}


def test_journal_compacts_only_certain_terminal_history_and_never_unknown():
    def entry(index, status):
        return {
            "version": 1, "action": "PARK_MOWER", "request_id": f"old-{index}",
            "mode": "OPERATOR_ONLY", "generation": "operator-only-v1", "status": status,
            "requested_at": (NOW - timedelta(minutes=64 - index)).isoformat(),
            "expires_at": (NOW - timedelta(minutes=54 - index)).isoformat(),
            "message_code": status, "mower_id": "mower-1",
        }
    certain = [entry(index, "EXPIRED") for index in range(32)]
    store = InMemoryStateStore(AutomationState(operator_commands_json=json.dumps(certain)))
    assert queue_operator_action(store, settings(), "PARK_MOWER", "new", NOW)["status"] == "QUEUED"
    assert len(json.loads(store.load().operator_commands_json)) == 32
    uncertain = [entry(index, "UNKNOWN") for index in range(32)]
    blocked = InMemoryStateStore(AutomationState(operator_commands_json=json.dumps(uncertain)))
    with pytest.raises(OperatorControlError, match="JOURNAL_FULL"):
        queue_operator_action(blocked, settings(), "PARK_MOWER", "new", NOW)


def test_full_capabilities_still_require_live_reads():
    full = settings(
        CONTROL_MODE="FULL_FAILSAFE", ENABLE_LIVE_READS="false", ENABLE_START_COMMANDS="true",
        ENABLE_IRRIGATION_COMMANDS="true",
        FULL_MOWER_CONFIRMATION="SSV53-TRAINING-MATCH-PARK-START",
        FULL_FAILSAFE_CONFIRMATION="SSV53-MOWER-HYDRAWISE-7-ZONES-150-MINUTES-ADAPTIVE-V1",
    )
    assert action_capabilities(full)["PARK_MOWER"] == {"available": False, "reason": "LIVE_READS"}


def test_reserved_lost_height_response_can_later_confirm_from_a_fresh_snapshot():
    store = InMemoryStateStore()
    queue_operator_action(store, settings(), "SET_CUTTING_HEIGHT", "height-1", NOW, cutting_height_mm=26)
    def lose(*_args, before_send=None):
        before_send()
        raise TimeoutError()
    run(store, mower(height=12), cutting_height_sender=lose)
    assert operator_commands_payload(store.load())["SET_CUTTING_HEIGHT"]["status"] == "RESERVED"
    later = NOW + timedelta(seconds=30)
    run(store, mower(now=later, height=15), moment=later)
    assert operator_commands_payload(store.load())["SET_CUTTING_HEIGHT"]["status"] == "CONFIRMED"


def test_park_requires_configured_exact_target_but_not_a_zero_error_code():
    store = InMemoryStateStore()
    queue_operator_action(store, settings(), "PARK_MOWER", "park-1", NOW)
    sent = []
    broken = mower()
    broken["error_code"] = 42
    run(store, broken, environment={"HUSQVARNA_MOWER_ID": "other"}, park_sender=lambda *_a, **_k: sent.append(True))
    assert not sent
    run(store, broken, environment={"HUSQVARNA_MOWER_ID": "mower-1"}, park_sender=lambda *_a, before_send=None: before_send() or sent.append(True) or {})
    assert sent == [True]


def test_unknown_height_blocks_a_different_target_until_a_fresh_snapshot_resolves_it():
    store = InMemoryStateStore()
    queue_operator_action(store, settings(), "SET_CUTTING_HEIGHT", "height-1", NOW, cutting_height_mm=26)
    def lose(*_args, before_send=None):
        before_send()
        raise TimeoutError()
    run(store, mower(height=12), cutting_height_sender=lose)
    later = NOW + timedelta(minutes=11)
    run(store, mower(now=later, height=12), moment=later)
    assert operator_commands_payload(store.load())["SET_CUTTING_HEIGHT"]["status"] == "UNKNOWN"
    with pytest.raises(OperatorControlError, match="OPERATOR_ACTION_UNCONFIRMED"):
        queue_operator_action(store, settings(), "SET_CUTTING_HEIGHT", "height-2", later, cutting_height_mm=28)
    resolved = later + timedelta(seconds=30)
    run(store, mower(now=resolved, height=15), moment=resolved)
    assert operator_commands_payload(store.load())["SET_CUTTING_HEIGHT"]["status"] == "CONFIRMED"
    assert queue_operator_action(store, settings(), "SET_CUTTING_HEIGHT", "height-2", resolved, cutting_height_mm=28)["status"] == "QUEUED"


def test_unknown_park_blocks_height_but_an_explicit_new_park_can_run():
    store = InMemoryStateStore()
    queue_operator_action(store, settings(), "PARK_MOWER", "park-1", NOW)
    def lose(*_args, before_send=None):
        before_send()
        raise TimeoutError()
    run(store, mower(), park_sender=lose)
    later = NOW + timedelta(minutes=11)
    run(store, mower(now=later), moment=later)
    queue_operator_action(store, settings(), "SET_CUTTING_HEIGHT", "height-1", later, cutting_height_mm=26)
    run(store, mower(now=later), moment=later, cutting_height_sender=lambda *_a, **_k: pytest.fail("height follows unresolved park"))
    queue_operator_action(store, settings(), "PARK_MOWER", "park-2", later)
    sent = []
    run(store, mower(now=later), moment=later, park_sender=lambda *_a, before_send=None: before_send() or sent.append(True) or {})
    assert sent == [True]


def test_before_send_rechecks_expiry_after_reservation():
    store = InMemoryStateStore()
    queue_operator_action(store, settings(), "PARK_MOWER", "park-1", NOW)
    ticks = iter((NOW, NOW + timedelta(minutes=11)))
    sent = []
    def sender(*_args, before_send=None):
        before_send()
        sent.append(True)
    run(store, mower(), command_clock=lambda: next(ticks, NOW + timedelta(minutes=11)), park_sender=sender)
    assert not sent
    assert operator_commands_payload(store.load())["PARK_MOWER"]["status"] == "EXPIRED"


def test_concurrent_new_park_cas_blocks_height_before_transport_then_runs_park():
    store = InMemoryStateStore()
    queue_operator_action(store, settings(), "SET_CUTTING_HEIGHT", "height-1", NOW, cutting_height_mm=26)
    height_calls = []
    def height_sender(*_args, before_send=None):
        queue_operator_action(store, settings(), "PARK_MOWER", "park-1", NOW)
        before_send()
        height_calls.append(True)
    run(store, mower(), cutting_height_sender=height_sender)
    assert not height_calls
    assert operator_commands_payload(store.load())["SET_CUTTING_HEIGHT"]["status"] == "REJECTED"
    sent = []
    run(store, mower(now=NOW + timedelta(seconds=1)), moment=NOW + timedelta(seconds=1), park_sender=lambda *_a, before_send=None: before_send() or sent.append(True) or {})
    assert sent == [True]


def test_persisted_reservation_is_not_replayed_by_a_new_store_instance(tmp_path):
    path = tmp_path / "state.json"
    first = JsonFileStateStore(path)
    queue_operator_action(first, settings(), "PARK_MOWER", "park-1", NOW)
    factory = lambda _environment: JsonFileStateStore(path)
    calls = []
    def lose(*_args, before_send=None):
        before_send()
        calls.append(True)
        raise TimeoutError()
    run(first, mower(), state_store_factory=factory, park_sender=lose)
    second = JsonFileStateStore(path)
    run(second, mower(now=NOW + timedelta(minutes=1)), moment=NOW + timedelta(minutes=1), state_store_factory=factory, park_sender=lambda *_a, **_k: pytest.fail("new instance must not replay"))
    assert calls == [True]
    assert operator_commands_payload(second.load())["PARK_MOWER"]["status"] == "RESERVED"


def test_production_queue_requires_an_explicit_target_id():
    with pytest.raises(OperatorControlError, match="MOWER_TARGET_UNAVAILABLE"):
        _queue_operator_action(InMemoryStateStore(), settings(), "PARK_MOWER", "park-1", NOW, mower_id="")


def test_changed_configured_target_cannot_dispatch_a_queued_request():
    store = InMemoryStateStore()
    queue_operator_action(store, settings(), "PARK_MOWER", "park-1", NOW)
    sent = []
    run(store, mower(), environment={"HUSQVARNA_MOWER_ID": "different"},
        park_sender=lambda *_a, **_k: sent.append(True))
    assert not sent
    assert operator_commands_payload(store.load())["PARK_MOWER"]["status"] == "QUEUED"


def test_token_failure_before_callback_is_rejected_but_post_callback_loss_stays_uncertain():
    store = InMemoryStateStore()
    queue_operator_action(store, settings(), "PARK_MOWER", "park-1", NOW)
    run(store, mower(), park_sender=lambda *_a, **_k: (_ for _ in ()).throw(RuntimeError("token failure")))
    assert operator_commands_payload(store.load())["PARK_MOWER"]["status"] == "REJECTED"
    queue_operator_action(store, settings(), "PARK_MOWER", "park-2", NOW + timedelta(seconds=1))
    def after_callback(*_args, before_send=None):
        before_send()
        raise TimeoutError()
    run(store, mower(now=NOW + timedelta(seconds=1)), moment=NOW + timedelta(seconds=1), park_sender=after_callback)
    assert operator_commands_payload(store.load())["PARK_MOWER"]["status"] == "RESERVED"
