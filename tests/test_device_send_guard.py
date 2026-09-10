from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone
import json

import pytest

from mower.device_send_guard import (
    DeviceSendBlocked,
    dispatch_device_send,
    load_device_send_journal,
    reconcile_device_send,
)
from mower.state import AutomationState
from mower.state_store import InMemoryStateStore


NOW = datetime(2026, 9, 10, 8, 0, tzinfo=timezone.utc)


def _send(
    store: InMemoryStateStore,
    state: AutomationState,
    sender,
    *,
    kind: str = "PARK",
    intent_key: str = "park-intent",
    target: str = "mower-1",
    clock=lambda: NOW,
):
    return dispatch_device_send(
        store=store,
        original=state,
        state=state,
        sender=sender,
        args=(),
        kind=kind,
        target=target,
        intent_key=intent_key,
        now_utc=NOW,
        clock=clock,
        before_send_check=lambda _state, _at: None,
    )


def _entry(index: int, *, status: str, evidence_size: int = 0) -> dict:
    return {
        "version": 1,
        "id": f"record-{index:03d}",
        "kind": "PARK",
        "target": "mower-1",
        "intent_key": f"old-intent-{index:03d}",
        "control_binding": "b" * 64,
        "reserved_at_utc": (NOW + timedelta(microseconds=index)).isoformat(),
        "deadline_utc": (NOW + timedelta(minutes=2, microseconds=index)).isoformat(),
        "status": status,
        "evidence": "x" * evidence_size,
    }


def _near_limit_entries(*, status: str) -> list[dict]:
    entries: list[dict] = []
    while len(entries) < 64:
        candidate = entries + [_entry(len(entries), status=status, evidence_size=220)]
        raw = json.dumps(candidate, sort_keys=True, separators=(",", ":"))
        if len(raw.encode("utf-8")) > 15_900:
            break
        entries = candidate
    assert entries
    assert len(json.dumps(entries, sort_keys=True, separators=(",", ":")).encode("utf-8")) > 15_500
    return entries


def test_concurrent_reservation_has_one_winner_and_one_sender_call():
    initial = AutomationState()
    store = InMemoryStateStore(initial)
    loser = []
    sent = []

    def second_sender(*, before_send):
        pytest.fail("losing reservation reached sender")

    def sender(*, before_send):
        with pytest.raises(DeviceSendBlocked, match="DEVICE_RESERVATION_CONFLICT") as exc:
            _send(store, initial, second_sender, intent_key="competing")
        loser.append(exc.value.code)
        before_send()
        sent.append(True)
        return {"accepted": True}

    result = _send(store, initial, sender)

    assert result.sent is True
    assert loser == ["DEVICE_RESERVATION_CONFLICT"]
    assert sent == [True]


def test_auth_delay_after_reservation_expires_without_transport():
    state = AutomationState()
    store = InMemoryStateStore(state)
    now = [NOW]
    sent = []

    def delayed_sender(*, before_send):
        now[0] = NOW + timedelta(seconds=121)
        before_send()
        sent.append(True)

    with pytest.raises(DeviceSendBlocked, match="DEVICE_RESERVATION_EXPIRED") as exc:
        _send(store, state, delayed_sender, clock=lambda: now[0])

    assert exc.value.transport_started is False
    assert sent == []
    assert load_device_send_journal(store.load())[0]["status"] == "REJECTED"


def test_revised_control_intent_before_http_revokes_reservation():
    state = AutomationState()
    store = InMemoryStateStore(state)
    sent = []

    def sender(*, before_send):
        current = store.load()
        revised = replace(
            current,
            revision=current.revision + 1,
            operator_request_id="later-request",
            operator_request_action="PARK_MOWER",
            operator_request_status="PENDING",
        )
        store.save(revised, expected_revision=current.revision)
        before_send()
        sent.append(True)

    with pytest.raises(DeviceSendBlocked, match="DEVICE_RESERVATION_REVOKED") as exc:
        _send(store, state, sender)

    assert exc.value.transport_started is False
    assert sent == []
    assert load_device_send_journal(store.load())[0]["status"] == "REJECTED"


def test_lost_response_stays_unknown_and_is_never_retried():
    state = AutomationState()
    store = InMemoryStateStore(state)
    attempts = []

    def lost_response(*, before_send):
        before_send()
        attempts.append(True)
        raise TimeoutError("reply may have been lost")

    with pytest.raises(DeviceSendBlocked, match="DEVICE_TRANSPORT_UNKNOWN") as exc:
        _send(store, state, lost_response)
    assert exc.value.transport_started is True
    current = store.load()
    assert load_device_send_journal(current)[0]["status"] == "UNKNOWN"

    with pytest.raises(DeviceSendBlocked, match="DEVICE_OUTCOME_UNCONFIRMED"):
        _send(store, current, lambda *, before_send: pytest.fail("must not retry"))
    assert attempts == [True]


def test_control_change_after_response_is_unknown_and_not_resent():
    state = AutomationState()
    store = InMemoryStateStore(state)
    attempts = []

    def sender(*, before_send):
        before_send()
        current = store.load()
        revised = replace(
            current,
            revision=current.revision + 1,
            operator_request_id="post-response-change",
            operator_request_action="PARK_MOWER",
            operator_request_status="PENDING",
        )
        store.save(revised, expected_revision=current.revision)
        attempts.append(True)
        return {"accepted": True}

    with pytest.raises(DeviceSendBlocked, match="CONTROL_CHANGED_AFTER_SEND") as exc:
        _send(store, state, sender)
    assert exc.value.transport_started is True
    current = store.load()
    assert load_device_send_journal(current)[0]["status"] == "SENT_UNCONFIRMED"
    with pytest.raises(DeviceSendBlocked, match="DEVICE_OUTCOME_UNCONFIRMED"):
        _send(store, current, lambda *, before_send: pytest.fail("must not retry"))
    assert attempts == [True]


def test_protective_park_is_allowed_while_another_write_is_unknown():
    unknown = _entry(0, status="UNKNOWN")
    unknown["kind"] = "WATER_START"
    unknown["intent_key"] = "water-unknown"
    state = AutomationState(device_send_journal_json=json.dumps([unknown]))
    store = InMemoryStateStore(state)
    sent = []

    def sender(*, before_send):
        before_send()
        sent.append(True)
        return {"accepted": True}

    result = _send(store, state, sender, intent_key="protective-park")

    assert result.sent is True
    assert sent == [True]
    statuses = {entry["intent_key"]: entry["status"] for entry in load_device_send_journal(store.load())}
    assert statuses == {"water-unknown": "UNKNOWN", "protective-park": "SENT_UNCONFIRMED"}


def test_confirmation_evidence_cannot_confirm_a_reservation_before_dispatch():
    reserved = _entry(0, status="RESERVED")
    state = AutomationState(device_send_journal_json=json.dumps([reserved]))

    unchanged = reconcile_device_send(state, "old-intent-000", NOW + timedelta(minutes=1), "fresh vendor read")

    assert unchanged == state
    assert load_device_send_journal(unchanged)[0]["status"] == "RESERVED"


def test_terminal_entries_are_trimmed_before_the_state_json_limit():
    entries = _near_limit_entries(status="CONFIRMED")
    state = AutomationState(device_send_journal_json=json.dumps(entries, sort_keys=True, separators=(",", ":")))
    store = InMemoryStateStore(state)

    def sender(*, before_send):
        before_send()
        return {"accepted": True}

    result = _send(store, state, sender, target="m" * 256, intent_key="new-intent-" + "x" * 512)
    persisted = store.load()
    raw = persisted.device_send_journal_json.encode("utf-8")

    assert result.sent is True
    assert len(raw) <= 16_384
    records = load_device_send_journal(persisted)
    assert any(record["id"] == result.reservation_id for record in records)
    assert len(records) < len(entries) + 1


def test_full_unresolved_journal_fails_without_dropping_or_sending():
    entries = _near_limit_entries(status="UNKNOWN")
    state = AutomationState(device_send_journal_json=json.dumps(entries, sort_keys=True, separators=(",", ":")))
    store = InMemoryStateStore(state)
    sent = []

    with pytest.raises(DeviceSendBlocked, match="DEVICE_JOURNAL_FULL") as exc:
        _send(
            store,
            state,
            lambda *, before_send: sent.append(True),
            target="m" * 256,
            intent_key="new-intent-" + "x" * 512,
        )

    assert exc.value.transport_started is False
    assert sent == []
    assert store.load().device_send_journal_json == state.device_send_journal_json
