from __future__ import annotations

import json
from copy import deepcopy
from dataclasses import replace
from datetime import timedelta

import pytest

from mower.full_failsafe import run_full_failsafe_cycle
from mower.husqvarna_start_actions import start_in_work_area
from mower.manual_session import dump_manual_session, new_session
from mower.start_dispatch_guard import StartDispatchBlocked, prepare_start_dispatch
from mower.state import AutomationState
from mower.state_store import InMemoryStateStore, StateConflictError
from tests.test_full_failsafe import ENV, NOW, observed_cycle, result, settings


class _Response:
    status = 202

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self):
        return b'{"accepted":true}'


def _reserved_store(*, deadline_minutes: int = 10):
    reserved = AutomationState(
        revision=1,
        last_command_fingerprint="start-fingerprint",
        last_command_utc=NOW.isoformat(),
        mower_start_pending_since_utc=NOW.isoformat(),
        mower_start_pending_deadline_utc=(NOW + timedelta(minutes=deadline_minutes)).isoformat(),
    )
    return InMemoryStateStore(reserved), reserved


def _dispatch_callback(store, reserved, clock, *, deadline_minutes=10):
    mower = {"connected": True, "status_timestamp_ms": int(NOW.timestamp() * 1000)}
    water = {
        "available": True, "fresh": True, "relay_set_valid": True,
        "clear_now": True, "active_zone_count": 0, "observed_at_utc": NOW.isoformat(),
    }
    return lambda: prepare_start_dispatch(
        clock=clock, store=store, reserved=reserved, mower=mower,
        hydrawise_safety=water,
        safe_command_deadline_utc=NOW + timedelta(minutes=deadline_minutes),
        command_end_utc=NOW + timedelta(minutes=deadline_minutes),
        requested_duration_minutes=10,
        mower_status_max_age_seconds=180,
        hydrawise_status_max_age_seconds=180,
    )


def _startable_state() -> AutomationState:
    return AutomationState(
        parked_by_automation=True,
        automation_park_source="continuous",
        automation_restart_allowed=True,
        park_command_sent_utc=(NOW - timedelta(hours=1)).isoformat(),
        park_confirmed_utc=(NOW - timedelta(minutes=2)).isoformat(),
        hydrawise_clear_since_utc=(NOW - timedelta(minutes=120)).isoformat(),
        last_hydrawise_success_utc=(NOW - timedelta(minutes=1)).isoformat(),
    )


def _run_start(
    store,
    *,
    clock,
    start_sender=start_in_work_area,
    window_end=None,
    cycle=None,
    now_utc=NOW,
    mower_observed_at=None,
):
    cycle = deepcopy(
        cycle or result(activity="PARKED_IN_CS", battery=100, window_end=window_end)
    )
    cycle.details["mower"]["status_timestamp_ms"] = int(
        (mower_observed_at or now_utc).timestamp() * 1000
    )
    return run_full_failsafe_cycle(
        now_utc=now_utc, settings=settings(), environment=ENV, past_due=False, source="test",
        read_only_runner=lambda **kwargs: observed_cycle(cycle, kwargs["now_utc"]),
        state_store_factory=lambda _environment: store,
        park_sender=lambda *_: pytest.fail("unexpected PARK"),
        start_sender=start_sender,
        suspend_zone_sender=lambda *_: pytest.fail("unexpected water mutation"),
        start_zone_sender=lambda *_: pytest.fail("unexpected water start"),
        stop_zone_sender=lambda *_: pytest.fail("unexpected water stop"),
        command_clock=clock,
    )


def test_auth_delay_past_deadline_does_not_post(monkeypatch):
    store = InMemoryStateStore(_startable_state())
    now = [NOW]
    posts = []

    def token(*_args, **_kwargs):
        now[0] = NOW + timedelta(minutes=31)
        return "token"

    monkeypatch.setattr("mower.husqvarna_start_actions.get_access_token", token)
    monkeypatch.setattr("mower.husqvarna_start_actions.urlopen", lambda request, **_: posts.append(request))

    output = _run_start(store, clock=lambda: now[0], window_end=NOW + timedelta(minutes=40))

    assert output.decision_code == "MOWER_START_SEND_BLOCKED"
    assert output.command_sent is False
    assert output.details["start_action"]["reason_code"] == "START_WINDOW_EXPIRED"
    assert output.details["start_action"]["reservation_released"] is True
    assert posts == []
    assert store.load().mower_start_pending_since_utc is None

    now[0] = NOW
    monkeypatch.setattr(
        "mower.husqvarna_start_actions.get_access_token",
        lambda *_args, **_kwargs: "token",
    )
    monkeypatch.setattr(
        "mower.husqvarna_start_actions.urlopen",
        lambda request, **_kwargs: posts.append(request) or _Response(),
    )
    retry = _run_start(store, clock=lambda: now[0])

    assert retry.decision_code == "CONTINUOUS_MOWING_START_SENT"
    assert retry.command_sent is True
    assert len(posts) == 1


def test_slow_final_state_read_cannot_extend_the_start_window():
    store, reserved = _reserved_store()
    now = [NOW]

    class SlowStore:
        def load(self):
            now[0] = NOW + timedelta(minutes=10)
            return store.load()

    with pytest.raises(StartDispatchBlocked, match="START_WINDOW_EXPIRED"):
        _dispatch_callback(SlowStore(), reserved, lambda: now[0])()


def test_manual_stop_written_during_auth_fences_post(monkeypatch):
    store = InMemoryStateStore(_startable_state())
    posts = []

    def token(*_args, **_kwargs):
        current = store.load()
        stopped = replace(
            current, revision=current.revision + 1,
            operator_request_action="PARK_MOWER", operator_request_status="PENDING",
            operator_request_expires_utc=(NOW + timedelta(minutes=5)).isoformat(),
        )
        store.save(stopped, expected_revision=current.revision)
        return "token"

    monkeypatch.setattr("mower.husqvarna_start_actions.get_access_token", token)
    monkeypatch.setattr("mower.husqvarna_start_actions.urlopen", lambda request, **_: posts.append(request))

    output = _run_start(store, clock=lambda: NOW)

    assert output.decision_code == "MOWER_START_SEND_BLOCKED"
    assert output.command_sent is False
    assert output.details["start_action"]["reason_code"] == "START_RESERVATION_CHANGED"
    assert output.details["start_action"]["reservation_released"] is False
    assert posts == []
    current = store.load()
    assert current.operator_request_action == "PARK_MOWER"
    assert current.mower_start_pending_since_utc == NOW.isoformat()


def test_post_token_guard_reduces_encoded_duration(monkeypatch):
    store, reserved = _reserved_store(deadline_minutes=3)
    sent = []
    monkeypatch.setattr("mower.husqvarna_start_actions.get_access_token", lambda *_args, **_kwargs: "token")
    monkeypatch.setattr(
        "mower.husqvarna_start_actions.urlopen",
        lambda request, **_kwargs: sent.append(request) or _Response(),
    )

    start_in_work_area(
        "client", "secret", "mower", 7, 10,
        before_send=_dispatch_callback(store, reserved, lambda: NOW, deadline_minutes=3),
    )

    assert len(sent) == 1
    assert json.loads(sent[0].data.decode("utf-8"))["data"]["attributes"]["duration"] == 3


def test_normal_fsm_start_uses_production_post_token_hook(monkeypatch):
    store = InMemoryStateStore(_startable_state())
    sent = []
    monkeypatch.setattr("mower.husqvarna_start_actions.get_access_token", lambda *_args, **_kwargs: "token")
    monkeypatch.setattr(
        "mower.husqvarna_start_actions.urlopen",
        lambda request, **_kwargs: sent.append(request) or _Response(),
    )

    output = _run_start(store, clock=lambda: NOW)

    assert output.decision_code == "CONTINUOUS_MOWING_START_SENT"
    assert output.command_sent is True
    assert len(sent) == 1
    assert store.load().mower_start_pending_since_utc is None


def test_lost_response_keeps_pending_latch_after_legacy_wrapper_check():
    store = InMemoryStateStore(_startable_state())

    def lost_response(*_args):
        raise TimeoutError("reply may be lost after vendor receipt")

    output = _run_start(store, clock=lambda: NOW, start_sender=lost_response)

    assert output.decision_code == "MOWER_START_OUTCOME_UNCONFIRMED"
    assert output.command_sent is True
    assert store.load().mower_start_pending_since_utc == NOW.isoformat()


def test_stale_mower_preflight_does_not_reserve_and_fresh_cycle_can_retry():
    prior_time = (NOW - timedelta(hours=2)).isoformat()
    store = InMemoryStateStore(
        replace(
            _startable_state(),
            last_command_fingerprint="acknowledged-park",
            last_command_utc=prior_time,
        )
    )
    calls = []

    stale = _run_start(
        store,
        clock=lambda: NOW,
        start_sender=lambda *_args: calls.append("stale") or {"accepted": True},
        mower_observed_at=NOW - timedelta(seconds=181),
    )

    assert stale.decision_code == "MOWER_START_SEND_BLOCKED"
    assert stale.command_sent is False
    assert stale.details["start_action"]["reason_code"] == "MOWER_STATUS_STALE"
    assert calls == []
    after_stale = store.load()
    assert after_stale.mower_start_pending_since_utc is None
    assert after_stale.last_command_fingerprint == "acknowledged-park"
    assert after_stale.last_command_utc == prior_time

    retry_at = NOW + timedelta(minutes=1)
    fresh = _run_start(
        store,
        now_utc=retry_at,
        clock=lambda: retry_at,
        start_sender=lambda *_args: calls.append("fresh") or {"accepted": True},
    )

    assert fresh.decision_code == "CONTINUOUS_MOWING_START_SENT"
    assert calls == ["fresh"]
    assert store.load().mower_start_pending_since_utc is None


def test_custom_sender_typed_block_is_ambiguous_and_keeps_reservation():
    store = InMemoryStateStore(_startable_state())

    def custom_sender(*_args):
        raise StartDispatchBlocked("MOWER_STATUS_STALE")

    output = _run_start(store, clock=lambda: NOW, start_sender=custom_sender)

    assert output.decision_code == "MOWER_START_OUTCOME_UNCONFIRMED"
    assert output.command_sent is True
    assert output.details["start_action"]["error_type"] == "StartDispatchBlocked"
    assert store.load().mower_start_pending_since_utc == NOW.isoformat()


def test_production_transport_exception_after_final_guard_keeps_reservation(monkeypatch):
    store = InMemoryStateStore(_startable_state())
    monkeypatch.setattr(
        "mower.husqvarna_start_actions.get_access_token",
        lambda *_args, **_kwargs: "token",
    )
    monkeypatch.setattr(
        "mower.husqvarna_start_actions.urlopen",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            TimeoutError("request outcome unknown")
        ),
    )

    output = _run_start(store, clock=lambda: NOW)

    assert output.decision_code == "MOWER_START_OUTCOME_UNCONFIRMED"
    assert output.command_sent is True
    assert store.load().mower_start_pending_since_utc == NOW.isoformat()


def test_release_cas_failure_keeps_reservation_latched(monkeypatch):
    class CleanupConflictStore(InMemoryStateStore):
        saves = 0

        def save(self, state, *, expected_revision):
            self.saves += 1
            if self.saves == 2:
                raise StateConflictError("cleanup raced")
            return super().save(state, expected_revision=expected_revision)

    store = CleanupConflictStore(_startable_state())
    now = [NOW]

    def token(*_args, **_kwargs):
        now[0] = NOW + timedelta(minutes=31)
        return "token"

    monkeypatch.setattr("mower.husqvarna_start_actions.get_access_token", token)

    output = _run_start(
        store,
        clock=lambda: now[0],
        window_end=NOW + timedelta(minutes=40),
    )

    assert output.decision_code == "MOWER_START_SEND_BLOCKED"
    assert output.command_sent is False
    assert output.details["start_action"]["reservation_released"] is False
    assert (
        output.details["start_action"]["reservation_release_error"]
        == "START_RESERVATION_RELEASE_CAS_FAILED"
    )
    assert store.load().mower_start_pending_since_utc == NOW.isoformat()


def test_post_oauth_rejection_releases_reservation_and_preserves_refresh_interval(monkeypatch):
    prior_end = (NOW + timedelta(hours=12)).isoformat()
    prior_command_time = (NOW - timedelta(minutes=10)).isoformat()
    store = InMemoryStateStore(
        AutomationState(
            continuous_mowing_owned=True,
            continuous_mowing_work_area_id=849199,
            continuous_mowing_window_end_utc=prior_end,
            last_command_fingerprint="acknowledged-start",
            last_command_utc=prior_command_time,
            last_start_command_utc=prior_command_time,
            hydrawise_clear_since_utc=(NOW - timedelta(minutes=120)).isoformat(),
            last_hydrawise_success_utc=(NOW - timedelta(minutes=1)).isoformat(),
        )
    )
    now = [NOW]
    cycle = result(activity="MOWING", battery=100, window_end=NOW + timedelta(hours=2))

    def token(*_args, **_kwargs):
        now[0] = NOW + timedelta(minutes=3, seconds=1)
        return "token"

    monkeypatch.setattr("mower.husqvarna_start_actions.get_access_token", token)

    output = _run_start(store, clock=lambda: now[0], cycle=cycle)

    assert output.decision_code == "MOWER_START_SEND_BLOCKED"
    assert output.command_sent is False
    assert output.details["start_action"]["reason_code"] == "MOWER_STATUS_STALE"
    assert output.details["start_action"]["reservation_released"] is True
    current = store.load()
    assert current.mower_start_pending_since_utc is None
    assert current.last_command_fingerprint == "acknowledged-start"
    assert current.last_command_utc == prior_command_time
    assert current.last_start_command_utc == prior_command_time
    assert current.continuous_mowing_owned is True
    assert current.continuous_mowing_window_end_utc == prior_end


def test_guard_rejects_invalid_clock_before_any_sender():
    store, reserved = _reserved_store()
    with pytest.raises(StartDispatchBlocked, match="COMMAND_CLOCK_INVALID"):
        _dispatch_callback(store, reserved, lambda: NOW.replace(tzinfo=None))()


def _manual_reserved(*, session=None, deadline_minutes=20):
    session = session or new_session(
        session_id="session-1", epoch=1, mower_id="mower-1", kind="START", source="APP", now_utc=NOW,
    )
    reserved = AutomationState(
        revision=1, last_command_fingerprint="manual-start", last_command_utc=NOW.isoformat(),
        mower_start_pending_since_utc=NOW.isoformat(),
        mower_start_pending_deadline_utc=(NOW + timedelta(minutes=deadline_minutes)).isoformat(),
        manual_session_json=dump_manual_session(session),
        operator_request_session_id="session-1", operator_request_session_epoch=1,
        mower_start_pending_session_id="session-1", mower_start_pending_session_epoch=1,
    )
    return reserved


def _manual_dispatch(current, reserved, *, now=NOW, safety=None):
    return prepare_start_dispatch(
        clock=lambda: now, store=type("Store", (), {"load": lambda _self: current})(), reserved=reserved,
        mower={"mower_id": "mower-1", "connected": True, "status_timestamp_ms": int(NOW.timestamp() * 1000)},
        hydrawise_safety=safety or {
            "available": True, "fresh": True, "relay_set_valid": True, "clear_now": True,
            "active_zone_count": 0, "observed_at_utc": NOW.isoformat(),
        },
        safe_command_deadline_utc=NOW + timedelta(minutes=20), command_end_utc=NOW + timedelta(minutes=20),
        requested_duration_minutes=10, mower_status_max_age_seconds=180, hydrawise_status_max_age_seconds=180,
        manual_session_id="session-1", manual_session_epoch=1,
    )


def test_manual_start_epoch_fence_blocks_changed_or_replaced_session_before_post():
    reserved = _manual_reserved()
    assert _manual_dispatch(reserved, reserved) == 10
    changed = new_session(
        session_id="session-1", epoch=2, mower_id="mower-1", kind="START", source="APP", now_utc=NOW,
    )
    with pytest.raises(StartDispatchBlocked, match="MANUAL_SESSION_FENCE_CHANGED"):
        _manual_dispatch(replace(reserved, manual_session_json=dump_manual_session(changed)), reserved)
    parked = new_session(
        session_id="session-2", epoch=2, mower_id="mower-1", kind="PARK", source="APP", now_utc=NOW,
    )
    with pytest.raises(StartDispatchBlocked, match="MANUAL_SESSION_FENCE_CHANGED"):
        _manual_dispatch(replace(reserved, manual_session_json=dump_manual_session(parked)), reserved)


def test_manual_start_fence_rejects_expired_preparation_and_never_masks_nonclear_water():
    reserved = _manual_reserved()
    with pytest.raises(StartDispatchBlocked, match="MANUAL_SESSION_EXPIRED"):
        _manual_dispatch(reserved, reserved, now=NOW + timedelta(minutes=11))
    with pytest.raises(StartDispatchBlocked, match="HYDRAWISE_STATUS_STALE"):
        _manual_dispatch(
            reserved, reserved,
            safety={
                "available": True, "fresh": True, "relay_set_valid": True, "clear_now": False,
                "active_zone_count": 0, "active_relay_ids": [], "observed_at_utc": NOW.isoformat(),
            },
        )


@pytest.mark.parametrize("kind", ["PARK", "SUSPEND", "WATER_START", "WATER_STOP", "NATIVE_RESUME"])
def test_any_unresolved_device_send_fences_a_new_start(kind):
    store, reserved = _reserved_store(deadline_minutes=20)
    outstanding = {
        "version": 1, "id": "record-1", "kind": kind, "target": "mower-1",
        "intent_key": "intent-1", "control_binding": "binding-1",
        "reserved_at_utc": NOW.isoformat(), "deadline_utc": (NOW + timedelta(minutes=2)).isoformat(),
        "status": "UNKNOWN",
    }
    current = replace(reserved, device_send_journal_json=json.dumps([outstanding]))
    with pytest.raises(StartDispatchBlocked, match="PREVIOUS_DEVICE_OUTCOME_UNCONFIRMED"):
        _dispatch_callback(type("Store", (), {"load": lambda _self: current})(), current, lambda: NOW, deadline_minutes=20)()
