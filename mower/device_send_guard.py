"""Durable reservations for device writes; this is not another planning queue."""
from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone
import hashlib
import inspect
import json
from typing import Any, Callable
import uuid


KINDS = frozenset({"PARK", "HEIGHT", "WATER_START", "WATER_STOP", "SUSPEND", "NATIVE_RESUME"})
UNRESOLVED = frozenset({"RESERVED", "DISPATCHING", "SENT_UNCONFIRMED", "UNKNOWN"})
TERMINAL = frozenset({"CONFIRMED", "REJECTED"})
MAX_RECORDS = 64
# AutomationState deliberately has one conservative bound for every opaque
# JSON column.  Keep this journal below that boundary before constructing the
# replacement state, so a reservation cannot fail with a generic ValueError.
MAX_JOURNAL_BYTES = 16_384


class DeviceSendBlocked(RuntimeError):
    def __init__(self, code: str, *, state=None, transport_started: bool = False):
        super().__init__(code)
        self.code = code
        self.state = state
        self.transport_started = transport_started


@dataclass(frozen=True)
class DeviceSendResult:
    state: Any
    response: Any
    reservation_id: str
    sent: bool = True


def _utc(value: datetime) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise DeviceSendBlocked("COMMAND_CLOCK_INVALID")
    return value.astimezone(timezone.utc)


def load_device_send_journal(state) -> list[dict[str, Any]]:
    raw = getattr(state, "device_send_journal_json", None)
    if raw in (None, ""):
        return []
    try:
        if not isinstance(raw, str) or len(raw.encode("utf-8")) > MAX_JOURNAL_BYTES:
            raise ValueError()
        entries = json.loads(raw)
        if not isinstance(entries, list) or len(entries) > MAX_RECORDS:
            raise ValueError()
        seen = set()
        for entry in entries:
            if (not isinstance(entry, dict) or entry.get("version") != 1
                    or entry.get("kind") not in KINDS
                    or entry.get("status") not in UNRESOLVED | TERMINAL):
                raise ValueError()
            for key in ("id", "target", "intent_key", "control_binding"):
                if not isinstance(entry.get(key), str) or not entry[key] or len(entry[key]) > 1024:
                    raise ValueError()
            for key in ("reserved_at_utc", "deadline_utc"):
                _utc(datetime.fromisoformat(entry[key].replace("Z", "+00:00")))
            if entry["id"] in seen:
                raise ValueError()
            seen.add(entry["id"])
        return entries
    except (ValueError, TypeError, KeyError, OverflowError, OSError, DeviceSendBlocked) as exc:
        raise DeviceSendBlocked("DEVICE_JOURNAL_INVALID", state=state) from exc


def unresolved_device_sends(state) -> list[dict[str, Any]]:
    return [entry for entry in load_device_send_journal(state) if entry["status"] in UNRESOLVED]


def ordinary_suspension_until(entry: dict[str, Any]) -> datetime | None:
    """Read the immutable absolute bound of the canonical irrigation sender.

    Never derive an old command's bound from the currently selected plan:
    Hydrawise may compact that plan while zones are being suspended.
    Other senders/unknown transport outcomes deliberately have no such proof.
    """
    if entry.get("kind") != "SUSPEND" or entry.get("status") != "SENT_UNCONFIRMED":
        return None
    try:
        prefix, suffix = str(entry["intent_key"]).split(":suspend:", 1)
        target, raw = suffix.split(":", 1)
        if not prefix.startswith("irrigation:") or not prefix[len("irrigation:"):] or target != entry["target"]:
            return None
        until = _utc(datetime.fromisoformat(raw.replace("Z", "+00:00")))
        sent = _utc(datetime.fromisoformat(entry["dispatched_at_utc"].replace("Z", "+00:00")))
        deadline = _utc(datetime.fromisoformat(entry["deadline_utc"].replace("Z", "+00:00")))
        return until if sent < deadline <= until else None
    except (ValueError, TypeError, KeyError, IndexError, DeviceSendBlocked):
        return None


def device_send_diagnostics(state) -> list[dict[str, Any]]:
    """Bounded receipts for ADMIN diagnosis; no secrets, payloads or bindings."""
    return [{key: entry.get(key) for key in (
        "id", "kind", "status", "reserved_at_utc", "dispatched_at_utc",
        "deadline_utc", "confirmed_at_utc", "evidence", "message_code",
    )} for entry in load_device_send_journal(state)]


def _journal_json(state, entries: list[dict[str, Any]]) -> str:
    """Serialize a bounded journal without ever discarding uncertain writes.

    A confirmed or rejected receipt is historical audit data; it may be
    evicted oldest-first.  Every other status is deliberately non-evictable:
    losing it could turn a possibly delivered device command into a replay.
    """
    compact = list(entries)
    while True:
        raw = json.dumps(compact, sort_keys=True, separators=(",", ":"))
        if len(compact) <= MAX_RECORDS and len(raw.encode("utf-8")) <= MAX_JOURNAL_BYTES:
            return raw
        candidates = [
            (index, entry) for index, entry in enumerate(compact)
            if entry.get("status") in TERMINAL
        ]
        if not candidates:
            raise DeviceSendBlocked("DEVICE_JOURNAL_FULL", state=state)
        # All loaded records have an aware reservation timestamp.  Sorting by
        # the parsed instant (then id) makes trimming deterministic across
        # instances even when an old record used a non-UTC ISO offset.
        index, _ = min(
            candidates,
            key=lambda item: (
                _utc(datetime.fromisoformat(item[1]["reserved_at_utc"].replace("Z", "+00:00"))),
                item[1]["id"],
            ),
        )
        compact.pop(index)


def _with_entries(state, entries, *, revision=None):
    return replace(
        state, revision=state.revision + 1 if revision is None else revision,
        device_send_journal_json=_journal_json(state, entries),
    )


def _binding(state) -> str:
    # Bind both the persistent manual intent and the existing one-shot request.
    # A newly confirmed Park must fence an older unsent Start/Park/valve action.
    values = [getattr(state, name, None) for name in (
        "manual_session_json", "operator_request_id", "operator_request_action",
        "operator_request_status", "operator_request_session_id", "operator_request_session_epoch",
    )]
    return hashlib.sha256(json.dumps(values, separators=(",", ":")).encode()).hexdigest()


def reconcile_device_send(state, intent_key: str, now_utc: datetime, confirmation_evidence: str):
    """Project confirmation only after the caller verified new vendor evidence.

    The caller persists this projection with its normal state CAS. HTTP success
    must never be passed as confirmation evidence.
    """
    if not isinstance(confirmation_evidence, str) or not confirmation_evidence.strip():
        raise DeviceSendBlocked("DEVICE_CONFIRMATION_EVIDENCE_REQUIRED", state=state)
    now = _utc(now_utc)
    changed = False
    entries = load_device_send_journal(state)
    for entry in entries:
        if entry["intent_key"] == intent_key and entry["status"] in UNRESOLVED:
            sent_at = entry.get("dispatched_at_utc")
            if not sent_at or now <= _utc(datetime.fromisoformat(sent_at)):
                continue
            entry.update(status="CONFIRMED", confirmed_at_utc=now.isoformat(),
                         evidence=confirmation_evidence[:512])
            changed = True
    return _with_entries(state, entries) if changed else state


def _supports_callback(sender) -> bool:
    try:
        parameters = inspect.signature(sender).parameters
        return "before_send" in parameters or any(
            p.kind == inspect.Parameter.VAR_KEYWORD for p in parameters.values()
        )
    except (ValueError, TypeError):
        return False


def dispatch_device_send(
    *, store, original, state, sender, args: tuple, kind: str, target: str,
    intent_key: str, now_utc: datetime, clock: Callable[[], datetime],
    before_send_check: Callable[[Any, datetime], None], deadline_utc: datetime | None = None,
) -> DeviceSendResult:
    """Reserve before I/O and fence again immediately before the device HTTP call.

    Canonical production senders support before_send, including after OAuth.
    Small injected fakes without the hook are checked immediately before call.
    Every ambiguous outcome remains durable and is never retried automatically.
    """
    if kind not in KINDS or not target or not intent_key or not callable(before_send_check):
        raise DeviceSendBlocked("DEVICE_SEND_INPUT_INVALID", state=original)
    now = _utc(now_utc)
    deadline = min(_utc(deadline_utc) if deadline_utc else now + timedelta(seconds=120),
                   now + timedelta(seconds=120))
    entries = load_device_send_journal(state)
    prior = next((entry for entry in reversed(entries) if entry["intent_key"] == intent_key
                  and entry["status"] != "REJECTED"), None)
    if prior:
        if prior["kind"] != kind or prior["target"] != str(target):
            raise DeviceSendBlocked("DEVICE_INTENT_REUSED", state=original)
        if prior["status"] == "CONFIRMED":
            return DeviceSendResult(original, {"already_confirmed": True}, prior["id"], False)
        raise DeviceSendBlocked("DEVICE_OUTCOME_UNCONFIRMED", state=original)
    if kind in {"WATER_START", "NATIVE_RESUME"} and any(e["status"] in UNRESOLVED for e in entries):
        raise DeviceSendBlocked("PREVIOUS_DEVICE_OUTCOME_UNCONFIRMED", state=original)
    if kind == "HEIGHT" and any(
        entry["kind"] == "HEIGHT" and entry["status"] in UNRESOLVED
        for entry in entries
    ):
        # A delayed PATCH can overwrite a later requested target.  Neither an
        # equal nor a different replacement is safe until fresh mower data
        # resolves the first transport outcome.
        raise DeviceSendBlocked("HEIGHT_OUTCOME_UNCONFIRMED", state=original)
    reservation_id = uuid.uuid4().hex
    entry = {
        "version": 1, "id": reservation_id, "kind": kind, "target": str(target),
        "intent_key": intent_key, "control_binding": _binding(state),
        "reserved_at_utc": now.isoformat(), "deadline_utc": deadline.isoformat(),
        "status": "RESERVED",
    }
    entries.append(entry)
    reserved = _with_entries(state, entries, revision=original.revision + 1)
    try:
        store.save(reserved, expected_revision=original.revision)
    except Exception as exc:
        raise DeviceSendBlocked("DEVICE_RESERVATION_CONFLICT", state=original) from exc
    transport_started = False
    callback_invoked = False

    def before_send():
        nonlocal transport_started, callback_invoked, reserved
        if callback_invoked:
            raise DeviceSendBlocked("DEVICE_SEND_CALLBACK_REUSED", state=reserved,
                                    transport_started=transport_started)
        latest = store.load()
        at = _utc(clock())  # Take time after the potentially slow state read.
        records = load_device_send_journal(latest)
        current = next((e for e in records if e["id"] == reservation_id), None)
        if (latest.revision != reserved.revision or _binding(latest) != entry["control_binding"]
                or current is None or current["status"] != "RESERVED"):
            raise DeviceSendBlocked("DEVICE_RESERVATION_REVOKED", state=latest)
        if at < now or at >= deadline:
            raise DeviceSendBlocked("DEVICE_RESERVATION_EXPIRED", state=latest)
        before_send_check(latest, at)
        current.update(status="DISPATCHING", dispatched_at_utc=at.isoformat())
        committing = _with_entries(latest, records)
        store.save(committing, expected_revision=latest.revision)
        reserved = committing
        callback_invoked = True
        transport_started = True

    def finish(status: str, code: str | None = None):
        # Never overwrite newer business fields with the pre-send snapshot.
        latest = store.load()
        records = load_device_send_journal(latest)
        current = next((e for e in records if e["id"] == reservation_id), None)
        if current is None:
            raise DeviceSendBlocked("DEVICE_RESERVATION_LOST", state=latest,
                                    transport_started=transport_started)
        if current["status"] not in TERMINAL:
            current.update(status=status, message_code=code)
            updated = _with_entries(latest, records)
            store.save(updated, expected_revision=latest.revision)
            latest = updated
        return latest

    try:
        if _supports_callback(sender):
            response = sender(*args, before_send=before_send)
        else:
            before_send()
            response = sender(*args)
        if not callback_invoked:
            raise DeviceSendBlocked("DEVICE_SENDER_IGNORED_FENCE", state=reserved)
    except Exception as exc:
        code = str(getattr(exc, "code", "DEVICE_TRANSPORT_UNKNOWN" if transport_started else "DEVICE_SEND_REJECTED"))
        try:
            latest = finish("UNKNOWN" if transport_started else "REJECTED", code)
        except Exception:
            # RESERVED/DISPATCHING remains an unresolved, non-replayable intent.
            latest = reserved
        raise DeviceSendBlocked(code, state=latest, transport_started=transport_started) from exc
    latest = finish("SENT_UNCONFIRMED")
    if _binding(latest) != entry["control_binding"]:
        raise DeviceSendBlocked("CONTROL_CHANGED_AFTER_SEND", state=latest, transport_started=True)
    return DeviceSendResult(latest, response, reservation_id)
