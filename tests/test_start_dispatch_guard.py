from __future__ import annotations

import json
from dataclasses import replace
from datetime import timedelta

import pytest

from mower.full_failsafe import run_full_failsafe_cycle
from mower.husqvarna_start_actions import start_in_work_area
from mower.start_dispatch_guard import StartDispatchBlocked, prepare_start_dispatch
from mower.state import AutomationState
from mower.state_store import InMemoryStateStore
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


def _run_start(store, *, clock, start_sender=start_in_work_area, window_end=None):
    cycle = result(activity="PARKED_IN_CS", battery=100, window_end=window_end)
    return run_full_failsafe_cycle(
        now_utc=NOW, settings=settings(), environment=ENV, past_due=False, source="test",
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
    assert posts == []
    assert store.load().mower_start_pending_since_utc == NOW.isoformat()


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
    assert posts == []
    assert store.load().operator_request_action == "PARK_MOWER"


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


def test_guard_rejects_invalid_clock_before_any_sender():
    store, reserved = _reserved_store()
    with pytest.raises(StartDispatchBlocked, match="COMMAND_CLOCK_INVALID"):
        _dispatch_callback(store, reserved, lambda: NOW.replace(tzinfo=None))()
