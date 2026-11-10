"""Persistent mower-only winter/service pause, with optimistic admission.

Changing the latch never sends a device command or changes a water plan.
The caller must already have authenticated a Platzwart session.
"""
from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone
from functools import wraps
import inspect
import json


REASONS = {"WINTER": "Winterpause", "MAINTENANCE": "Wartung", "DEFECT": "Defekt"}
MOWER_ACTIONS = frozenset({"PARK_MOWER", "START_MOWING", "SET_CUTTING_HEIGHT", "RESET_BLADE_USAGE", "MANUAL_CONTROL"})
ACTIVE_WATER_PHASES = frozenset({"PLANNED", "SUSPENDING", "READY", "START_RESERVED", "RUNNING", "STOPPING"})


class MowerServiceError(ValueError):
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code


def fence_mower_sender(sender, store, service_revision):
    """Recheck after vendor OAuth, including an intervening on/off cycle.

    Water senders never use this fence. Existing journal callbacks retain
    their own reservation, time and physical-clearance checks.
    """
    if sender is None:
        return None
    try:
        supports_callback = "before_send" in inspect.signature(sender).parameters
    except (TypeError, ValueError):
        supports_callback = False

    @wraps(sender)
    def guarded(*args, **kwargs):
        previous_check = kwargs.pop("before_send", None)

        def check():
            latest = store.load()
            if latest.mower_service_active or latest.mower_service_revision != service_revision:
                from mower.husqvarna import HusqvarnaError
                raise HusqvarnaError("MOWER_SERVICE_CHANGED")
            if previous_check is not None:
                previous_check()

        if supports_callback:
            return sender(*args, before_send=check, **kwargs)
        check()
        return sender(*args, **kwargs)

    return guarded


def public_payload(state, *, available=True):
    return {
        "available": available,
        "active": state.mower_service_active if available else None,
        "reason": state.mower_service_reason if available else None,
        "label": REASONS.get(state.mower_service_reason, "Wartung / Winterpause"),
        "since": state.mower_service_since_utc if available else None,
        "changedAt": state.mower_service_changed_utc if available else None,
        "revision": state.mower_service_revision if available else None,
    }


def validate_request(payload):
    if (not isinstance(payload, dict) or set(payload) != {"enabled", "reason", "revision"}
            or type(payload["enabled"]) is not bool
            or type(payload["revision"]) is not int or payload["revision"] < 0
            or not isinstance(payload["reason"], str) or payload["reason"] not in REASONS):
        raise MowerServiceError("MOWER_SERVICE_INVALID", "Bitte den Wartungsschalter neu öffnen.")


def _safe_to_return(mower, environment, now):
    try:
        stamp = datetime.fromtimestamp(float(mower["status_timestamp_ms"]) / 1000, timezone.utc)
        age = (now - stamp).total_seconds()
        target = str(environment.get("HUSQVARNA_MOWER_ID") or "").strip()
        return (bool(target) and str(mower.get("mower_id") or "") == target
                and mower.get("connected") is True and -30 <= age <= 180
                and type(mower.get("error_code")) is int and mower["error_code"] == 0
                and mower.get("state") == "IN_OPERATION" and mower.get("mode") == "HOME"
                and mower.get("activity") in {"PARKED_IN_CS", "CHARGING"})
    except (KeyError, TypeError, ValueError, OverflowError, OSError):
        return False


def change_service_mode(*, store, state, payload, request_id, now_utc, mower, environment):
    # Read-only and park-only packages can import the status projection
    # without importing the full manual/write implementation.
    from mower.device_send_guard import load_device_send_journal
    from mower.manual_session import load_manual_session, dump_manual_session, with_session_status
    from mower.manual_water_conflict import load_transaction, TERMINAL_PHASES
    validate_request(payload)
    enabled, reason, revision = payload["enabled"], payload["reason"], payload["revision"]
    now = now_utc.astimezone(timezone.utc)
    if state.mower_service_request_id == request_id:
        if (state.mower_service_active == enabled and state.mower_service_reason == reason
                and state.mower_service_revision == revision + 1):
            return state
        raise MowerServiceError("MOWER_SERVICE_CHANGED", "Die Anfrage passt nicht zum gespeicherten Stand. Bitte aktualisieren.")
    if state.mower_service_revision != revision:
        raise MowerServiceError("MOWER_SERVICE_CHANGED", "Der Wartungsschalter wurde zwischenzeitlich geändert. Bitte aktualisieren.")
    if not enabled and not state.mower_service_active:
        raise MowerServiceError("MOWER_SERVICE_CHANGED", "Der Mäher ist bereits im Betrieb. Bitte aktualisieren.")
    journal = load_device_send_journal(state)
    # Uncertain transports must retain their protective holds. A RESERVED
    # write has not reached transport and can be revoked with this same CAS.
    if (state.mower_start_pending_since_utc is not None or any(
            e["kind"] in {"PARK", "HEIGHT"} and e["status"] in {"DISPATCHING", "SENT_UNCONFIRMED", "UNKNOWN"}
            for e in journal)):
        raise MowerServiceError("MOWER_SERVICE_PENDING", "Eine Mäheraktion ist noch nicht bestätigt. Bitte zuerst den Mäherzustand klären.")
    if state.irrigation_phase in ACTIVE_WATER_PHASES:
        raise MowerServiceError("MOWER_SERVICE_WATER_ACTIVE", "Bitte den laufenden Bewässerungsablauf zuerst sicher beenden.")
    water_transaction = load_transaction(state)
    if water_transaction and water_transaction["phase"] not in TERMINAL_PHASES:
        raise MowerServiceError("MOWER_SERVICE_WATER_ACTIVE", "Bitte die laufende Abstimmung mit der Bewässerung zuerst sicher beenden.")
    if state.operator_request_status == "PENDING" and state.operator_request_action == "RESET_BLADE_USAGE":
        raise MowerServiceError("MOWER_SERVICE_PENDING", "Bitte das Zurücksetzen des Klingenzählers zuerst abschließen lassen.")
    if not enabled and not _safe_to_return(mower or {}, environment, now):
        raise MowerServiceError("MOWER_SERVICE_RETURN_UNSAFE", "Zur Wiederinbetriebnahme muss der Mäher mit frischer, fehlerfreier Meldung in der Station stehen (HOME).")
    changes = {}
    for entry in journal:
        if entry["kind"] in {"PARK", "HEIGHT"} and entry["status"] == "RESERVED":
            entry.update(status="REJECTED", message_code="MOWER_SERVICE_CHANGED")
            changes["device_send_journal_json"] = json.dumps(journal, separators=(",", ":"))
    # A mode change cancels only unsent mower work. Preserve water requests,
    # device receipts, unrelated maintenance flags and all historical proof.
    if state.operator_request_action in MOWER_ACTIONS and state.operator_request_status == "PENDING":
        changes.update(operator_request_status="REJECTED", operator_request_result="Wartungsschalter geändert; ohne Gerätebefehl verworfen.")
    session = load_manual_session(state)
    if session and session["kind"] == "START" and session["status"] != "ENDED":
        changes["manual_session_json"] = dump_manual_session(with_session_status(session, "ENDED", now_utc=now))
    # OPERATOR_ONLY has a separate journal, which must not replay old work
    # after a later runtime-mode change either.
    if state.operator_commands_json:
        from mower.operator_controls import _load_journal, _dump_journal
        entries = _load_journal(state)
        if any(e["status"] in {"RESERVED", "SENT_UNCONFIRMED", "UNKNOWN"} for e in entries):
            raise MowerServiceError("MOWER_SERVICE_PENDING", "Eine frühere Mäheraktion ist noch nicht bestätigt.")
        for entry in entries:
            if entry["status"] == "QUEUED":
                entry.update(status="EXPIRED", message_code="MOWER_SERVICE_CHANGED")
        changes["operator_commands_json"] = _dump_journal(entries)
    updated = replace(
        state, revision=state.revision + 1,
        mower_service_active=enabled, mower_service_reason=reason,
        mower_service_since_utc=(state.mower_service_since_utc if state.mower_service_active else now.isoformat()) if enabled else None,
        mower_service_changed_utc=now.isoformat(), mower_service_revision=revision + 1,
        mower_service_request_id=request_id,
        continuous_mowing_owned=False, continuous_mowing_work_area_id=None,
        continuous_mowing_window_end_utc=None, automation_restart_allowed=False,
        operator_occupancy_override_key=None, operator_occupancy_override_until_utc=None,
        coordination_execution_request_json=None,
        **changes,
    )
    store.save(updated, expected_revision=state.revision)
    return updated
