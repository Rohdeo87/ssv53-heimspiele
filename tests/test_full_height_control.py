from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone

from mower.device_send_guard import load_device_send_journal
from mower.full_height_control import reconcile_full_height_sends, run_full_height_control
from mower.runtime import ControlMode
from mower.state import AutomationState
from mower.state_store import InMemoryStateStore


NOW = datetime(2026, 9, 10, 10, 0, tzinfo=timezone.utc)
ENV = {
    "HUSQVARNA_MOWER_ID": "mower-1",
    "HUSQVARNA_CLIENT_ID": "client",
    "HUSQVARNA_CLIENT_SECRET": "secret",
}


class Settings:
    control_mode = ControlMode.FULL_FAILSAFE
    enable_live_reads = True
    full_failsafe_write_gate_enabled = True
    enable_manual_sessions = True


def state(*, request_id="height-1", target=26):
    return AutomationState(
        operator_request_id=request_id,
        operator_request_action="SET_CUTTING_HEIGHT",
        operator_request_status="PENDING",
        operator_request_cutting_height_mm=target,
        operator_requested_utc=NOW.isoformat(),
        operator_request_expires_utc=(NOW + timedelta(minutes=10)).isoformat(),
    )


def mower(*, observed=NOW, percent=13):
    return {
        "mower_id": "mower-1", "connected": True, "model": "580 EPOS", "error_code": 0,
        "status_timestamp_ms": int(observed.timestamp() * 1000),
        "target_work_area": {"id": 849199, "name": "Rasenfläche"},
        "work_areas": [{
            "id": 849199, "name": "Rasenfläche", "enabled": True,
            "use_global_cutting_height": False, "cutting_height_percent": percent,
        }],
    }


def run(store, current, snapshot, sender, *, now=NOW, clock=lambda: NOW):
    return run_full_height_control(
        store=store, original=current, state=current, mower=snapshot, environment=ENV,
        settings=Settings(), now_utc=now, clock=clock, sender=sender,
    )


def test_http_acceptance_is_only_pending_until_later_matching_mower_observation():
    current = state()
    store = InMemoryStateStore(current)
    calls = []

    def sender(*args, before_send):
        before_send()
        calls.append(args)
        return {"accepted": True}

    sent = run(store, current, mower(percent=13), sender)
    assert sent.status == "SENT_UNCONFIRMED"
    assert sent.command_sent is True
    assert calls and calls[0][3:] == (849199, 15)

    confirmed = run(
        store, store.load(), mower(observed=NOW + timedelta(seconds=1), percent=15),
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("must not resend")),
        now=NOW + timedelta(seconds=1), clock=lambda: NOW + timedelta(seconds=1),
    )
    assert confirmed.status == "CONFIRMED"
    assert confirmed.command_sent is False


def test_old_matching_height_cannot_confirm_before_dispatch_observation():
    current = state()
    store = InMemoryStateStore(current)
    sent = run(store, current, mower(percent=13), lambda *_args, before_send: before_send())

    pending = run(store, store.load(), mower(percent=15), lambda *_args, **_kwargs: None)

    assert sent.status == "SENT_UNCONFIRMED"
    assert pending.status == "SENT_UNCONFIRMED"


def test_lost_response_is_unknown_and_different_height_does_not_replay():
    current = state(target=26)
    store = InMemoryStateStore(current)
    calls = []

    def lost(*_args, before_send):
        before_send()
        calls.append(True)
        raise TimeoutError("response lost")

    uncertain = run(store, current, mower(), lost)
    assert uncertain.status == "UNKNOWN"
    changed = replace(
        store.load(), revision=store.load().revision + 1,
        operator_request_id="height-2", operator_request_cutting_height_mm=27,
    )
    previous = store.load()
    store.save(changed, expected_revision=previous.revision)
    blocked = run(store, store.load(), mower(), lambda *_args, **_kwargs: calls.append("retry"))

    assert blocked.status == "UNKNOWN"
    assert blocked.reason_code == "HEIGHT_OUTCOME_UNCONFIRMED"
    assert calls == [True]


def test_auth_delay_rejects_before_patch_and_new_manual_park_revokes_reservation():
    current = state()
    store = InMemoryStateStore(current)
    now = [NOW]
    patched = []

    def delayed(*_args, before_send):
        now[0] = NOW + timedelta(seconds=121)
        before_send()
        patched.append(True)

    expired = run(store, current, mower(), delayed, clock=lambda: now[0])
    assert expired.status == "REJECTED"
    assert expired.reason_code == "DEVICE_RESERVATION_EXPIRED"
    assert patched == []

    current = state(request_id="height-3")
    store = InMemoryStateStore(current)

    def park_during_auth(*_args, before_send):
        latest = store.load()
        parked = replace(
            latest, revision=latest.revision + 1,
            operator_request_id="park-1", operator_request_action="PARK_MOWER",
            operator_request_status="PENDING",
        )
        store.save(parked, expected_revision=latest.revision)
        before_send()
        patched.append(True)

    revoked = run(store, current, mower(), park_during_auth)
    assert revoked.status == "REJECTED"
    assert revoked.reason_code == "DEVICE_RESERVATION_REVOKED"
    assert patched == []


def test_confirmation_persists_against_original_not_unsaved_cycle_projection():
    """A full-failsafe cycle projects a revision before the height helper runs."""
    initial = state()
    store = InMemoryStateStore(initial)

    def sender(*_args, before_send):
        before_send()
        return {"accepted": True}

    sent = run(store, initial, mower(percent=13), sender)
    stored = store.load()
    # This represents _cycle_state's unsaved regular-cycle projection.
    projection = replace(stored, revision=stored.revision + 1, last_decision_code="CYCLE_PROJECTED")
    confirmed = run_full_height_control(
        store=store,
        original=stored,
        state=projection,
        mower=mower(observed=NOW + timedelta(seconds=1), percent=15),
        environment=ENV,
        settings=Settings(),
        now_utc=NOW + timedelta(seconds=1),
        clock=lambda: NOW + timedelta(seconds=1),
        sender=lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("must not resend")),
    )

    assert sent.status == "SENT_UNCONFIRMED"
    assert confirmed.status == "CONFIRMED"
    assert store.load().revision == projection.revision + 1


def test_reconcile_existing_height_after_legacy_request_changed_to_park():
    current = state()
    store = InMemoryStateStore(current)
    sent = run(store, current, mower(percent=13), lambda *_args, before_send: (before_send(), {"accepted": True})[1])
    previous = store.load()
    parked = replace(
        previous,
        revision=previous.revision + 1,
        operator_request_id="park-1",
        operator_request_action="PARK_MOWER",
        operator_request_status="PENDING",
        operator_request_cutting_height_mm=None,
    )
    store.save(parked, expected_revision=previous.revision)

    reconciled = reconcile_full_height_sends(
        parked, mower(observed=NOW + timedelta(seconds=1), percent=15), ENV, NOW + timedelta(seconds=1),
    )

    assert sent.status == "SENT_UNCONFIRMED"
    entry = load_device_send_journal(reconciled)[0]
    assert entry["kind"] == "HEIGHT"
    assert entry["status"] == "CONFIRMED"
    assert entry["evidence"].startswith("mower:mower-1:area:849199:height:15:")


def test_reconcile_marks_unconfirmed_height_unknown_after_ten_minutes():
    current = state()
    store = InMemoryStateStore(current)
    sent = run(store, current, mower(percent=13), lambda *_args, before_send: (before_send(), {"accepted": True})[1])

    reconciled = reconcile_full_height_sends(
        store.load(), mower(observed=NOW + timedelta(minutes=10), percent=13), ENV, NOW + timedelta(minutes=10),
    )

    assert sent.status == "SENT_UNCONFIRMED"
    entry = load_device_send_journal(reconciled)[0]
    assert entry["status"] == "UNKNOWN"
    assert entry["message_code"] == "HEIGHT_CONFIRMATION_TIMEOUT"


def test_rejected_matching_height_stays_rejected_without_a_resend():
    current = state()
    store = InMemoryStateStore(current)

    def rejected(*_args, **_kwargs):
        raise RuntimeError("PATCH_REJECTED")

    first = run(store, current, mower(), rejected)
    repeated = run(
        store, store.load(), mower(),
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("must not resend")),
    )

    assert first.status == "REJECTED"
    assert repeated.status == "REJECTED"
    assert repeated.reason_code == "DEVICE_SEND_REJECTED"


def test_sent_height_blocks_different_height_until_fresh_confirmation_or_timeout():
    current = state(target=26)
    store = InMemoryStateStore(current)
    sent = run(store, current, mower(), lambda *_args, before_send: (before_send(), {"accepted": True})[1])
    previous = store.load()
    changed = replace(
        previous,
        revision=previous.revision + 1,
        operator_request_id="height-2",
        operator_request_cutting_height_mm=27,
    )
    store.save(changed, expected_revision=previous.revision)

    blocked = run(
        store, store.load(), mower(),
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("must not resend")),
    )

    assert sent.status == "SENT_UNCONFIRMED"
    assert blocked.status == "UNKNOWN"
    assert blocked.reason_code == "HEIGHT_OUTCOME_UNCONFIRMED"
