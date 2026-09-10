"""Server-side confirmation context and admission for manual mower priority."""
from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone
import hashlib
import hmac
import json
from typing import Any, Mapping

from mower.manual_session import (
    block_key, dump_manual_session, load_manual_session, new_session, with_session_status,
)
from mower.runtime import ControlMode, RuntimeSettings
from mower.safety import occupancy_override_allowed


class ManualControlError(ValueError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def _time(value):
    try:
        result = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return result.astimezone(timezone.utc) if result.tzinfo and result.utcoffset() is not None else None
    except (TypeError, ValueError):
        return None


def _fresh(mower, now):
    try:
        at = datetime.fromtimestamp(float(mower.get("status_timestamp_ms")) / 1000, timezone.utc)
        return mower.get("connected") is True and -30 <= (now - at).total_seconds() <= 180
    except (ValueError, TypeError, OverflowError, OSError):
        return False


def manual_context(state, details: Mapping[str, Any], environment, now_utc):
    from mower.manual_water_conflict import water_conflict_context

    now = now_utc.astimezone(timezone.utc)
    settings = RuntimeSettings.from_mapping(environment)
    mower = dict(details.get("mower") or {})
    target = str(environment.get("HUSQVARNA_MOWER_ID") or "").strip()
    exact = bool(target) and str(mower.get("mower_id") or "") == target
    session = load_manual_session(state)
    plan = details.get("current_plan") or {}
    blocks = []
    keys = set()
    forbidden = False
    for candidate in (plan.get("blocked_now"), plan.get("parking_block")):
        if candidate in (None, {}):
            continue
        if not isinstance(candidate, Mapping):
            forbidden = True
            continue
        # Irrigation conflicts have a separate explicit decision/transaction.
        if str(candidate.get("source") or "").lower() == "irrigation":
            continue
        key = block_key(candidate)
        if not key or not occupancy_override_allowed(candidate):
            forbidden = True
        if key and key not in keys:
            keys.add(key)
            blocks.append(dict(candidate))
    hydrawise = details.get("hydrawise") or {}
    safety = hydrawise.get("safety") or {}
    release = hydrawise.get("release_confirmation") or {}
    dry_until = _time(release.get("dry_until_utc"))
    drying = dry_until is not None and dry_until > now
    conflict = water_conflict_context(state, details, now)
    enabled = (settings.enable_manual_sessions and settings.control_mode is ControlMode.FULL_FAILSAFE
               and settings.enable_live_reads and settings.full_failsafe_write_gate_enabled)
    current_session = session if session and session["status"] != "ENDED" else None
    water_known = (safety.get("available") is True and safety.get("fresh") is True
                   and safety.get("relay_set_valid") is True)
    for count_name, ids_name in (("active_zone_count", "active_relay_ids"), ("imminent_zone_count", "imminent_relay_ids")):
        count, ids = safety.get(count_name), safety.get(ids_name)
        water_known = water_known and type(count) is int and count >= 0 and isinstance(ids, list) and len(set(ids)) == count
    water_known = water_known and (safety.get("clear_now") is True or (conflict.get("required") is True and conflict.get("known") is True))
    # This context comes only from the authoritative read pipeline. Display
    # fallback snapshots explicitly mark themselves unavailable at admission.
    forbidden = forbidden or details.get("manual_sources_available") is False
    for name in ("special_occupancy", "training_cancellations"):
        quality = details.get(name)
        if isinstance(quality, Mapping) and (quality.get("fail_closed") is True or quality.get("available") is False):
            forbidden = True
    inputs = details.get("input_files") or {}
    forbidden = forbidden or inputs.get("matches_found") is not True or inputs.get("fallback_used") is not False
    forbidden = forbidden or not all(key in plan for key in ("blocked_now", "parking_block", "mowing_window_now"))
    confirmation = {
        "block_keys": sorted(keys), "dry_until_utc": dry_until.isoformat() if drying else None,
        "water_conflict_id": conflict.get("id") if conflict.get("required") else None,
        "mower_id": target, "epoch": session["epoch"] if session else 0,
        "forbidden": forbidden,
    }
    token = hashlib.sha256(json.dumps(confirmation, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    try:
        fault_free = type(mower.get("error_code")) is int and mower["error_code"] == 0
    except (KeyError, TypeError):
        fault_free = False
    can_start = (enabled and exact and _fresh(mower, now) and not forbidden and not state.maintenance_mode
                 and fault_free
                 and str(mower.get("state") or "").upper() not in {"STOPPED", "OFF", "ERROR", "FATAL_ERROR"}
                 and water_known and (not conflict.get("required") or conflict.get("known") is True))
    status, title, message, until = "AUTOMATIC", "Automatik", "Der Mäher folgt der Platzpflegeplanung.", None
    if current_session:
        if current_session["status"] == "UNKNOWN":
            status, title, message = "UNKNOWN", "Aktion noch nicht bestätigt", "Bitte am Mäher nachsehen und die Bedienung bestätigen."
        elif current_session["kind"] == "PARK":
            status, title, message = "MANUAL_PARKED", "Manuell geparkt", "Die Automatik wartet auf deine Freigabe."
            if not (_fresh(mower, now) and str(mower.get("mode") or "").upper() == "HOME"
                    and mower.get("activity") in {"PARKED_IN_CS", "CHARGING"}):
                title, message = "Parken angefordert", "Bitte warten, bis der Mäher die Station erreicht hat."
        elif conflict.get("required"):
            status, title, message = "WAITING_WATER", "Bewässerung und Mähen abstimmen", "Bitte für diesen Lauf wählen: Mähen oder Bewässern."
            if current_session.get("water_conflict_id") == conflict.get("id"):
                if current_session.get("water_choice") == "MOWER":
                    message = "Der Mäher wartet, bis die Bewässerung sicher beendet ist."
                elif current_session.get("water_choice") == "IRRIGATION":
                    message = "Der Mäher wartet auf die Bewässerung."
        elif current_session["source"] == "HUSQVARNA" and current_session["status"] == "PREPARED":
            status, title, message = "PREPARED", "Manueller Start vorbereitet", "Jetzt am Mäher oder über Husqvarna starten."
            until = current_session.get("prepared_until_utc")
        elif current_session["status"] == "ACTIVE":
            status, title, message = "MANUAL_MOWING", "Manuell gestartet", "Gilt bis zur nächsten Ladefahrt."
        else:
            status, title, message = "PREPARED", "Manueller Start angefordert", "Bitte auf die Bestätigung des Mähers warten."
    active_start = bool(current_session and current_session["kind"] == "START" and current_session["status"] == "ACTIVE")
    occupancy_override = bool(active_start and not forbidden and keys and keys.issubset(set(current_session["confirmed_block_keys"])))
    drying_override = bool(active_start and drying and current_session.get("confirmed_dry_until_utc") == dry_until.isoformat()
                          and water_known and safety.get("clear_now") is True and release.get("telemetry_confirmed") is True
                          and release.get("persistent_state_available") is True)
    public = {
        "occupancyOverrideActive": occupancy_override, "dryingOverrideActive": drying_override,
        "enabled": enabled, "status": status, "title": title, "message": message,
        "source": current_session.get("source") if current_session else None,
        "until": until, "sessionId": current_session.get("session_id") if current_session else None,
        "epoch": session["epoch"] if session else 0, "contextToken": token,
        "canStart": can_start,
        "canPark": enabled and exact and mower.get("connected") is True,
        "canResume": enabled and current_session is not None and current_session["status"] != "UNKNOWN"
                     and state.mower_start_pending_since_utc is None and not state.maintenance_mode,
        "waterChoice": current_session.get("water_choice") if current_session else None,
        "requestId": state.operator_request_id,
        "requestStatus": state.operator_request_status,
        "confirmations": {
            "occupancyRequired": bool(blocks), "occupancyTitles": [str(b.get("title") or "Platzbelegung") for b in blocks],
            "dryingRequired": drying, "dryUntil": dry_until.isoformat() if drying else None,
            "waterChoiceRequired": bool(conflict.get("required")),
            "waterConflictId": conflict.get("id") if conflict.get("required") else None,
        },
    }
    return public, confirmation, conflict


def request_manual_control(*, store, state, details, environment, now_utc, request_id, payload):
    if not isinstance(payload, Mapping):
        raise ManualControlError("MANUAL_CONFIRMATION_REQUIRED", "Bitte die manuelle Bedienung bestätigen.")
    allowed = {"operation", "source", "contextToken", "approveOccupancy", "approveDrying", "waterChoice", "sessionId"}
    if set(payload) - allowed or any(type(payload.get(key, False)) is not bool for key in ("approveOccupancy", "approveDrying")):
        raise ManualControlError("MANUAL_CONFIRMATION_INVALID", "Die Bestätigung ist unvollständig. Bitte neu öffnen.")
    operation = payload.get("operation")
    source = payload.get("source", "APP")
    if operation not in {"START", "PARK", "RESUME", "DECIDE"} or source not in {"APP", "HUSQVARNA"}:
        raise ManualControlError("MANUAL_ACTION_INVALID", "Bitte eine gültige Bedienaktion wählen.")
    public, context, conflict = manual_context(state, details, environment, now_utc)
    if not public["enabled"]:
        raise ManualControlError("MANUAL_CONTROL_LOCKED", "Die manuelle Vorrangregel ist noch nicht eingeschaltet.")
    previous = load_manual_session(state)
    try:
        receipts = json.loads(state.manual_control_receipts_json or "[]")
        if not isinstance(receipts, list) or len(receipts) > 32 or any(
            not isinstance(row, dict) or set(row) != {"id", "hash", "status"}
            or any(not isinstance(row[k], str) for k in row) for row in receipts
        ):
            raise ValueError
        fingerprint = hashlib.sha256(json.dumps(dict(payload), sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    except (TypeError, ValueError) as exc:
        raise ManualControlError("MANUAL_RECEIPT_INVALID", "Der letzte Auftrag ist nicht eindeutig. Bitte den Platzwart informieren.") from exc
    for receipt in receipts:
        if receipt["id"] == request_id:
            if receipt["hash"] != fingerprint:
                raise ManualControlError("REQUEST_ID_REUSED", "Die Anfragenummer gehört zu einer anderen Aktion.")
            return state, {"accepted": True, "requestId": request_id, "status": receipt["status"], "manualControl": public}
    token = payload.get("contextToken")
    # A freshly confirmed protective PARK is always allowed even if a new
    # occupancy/water reading arrived while the dialog was open.
    if operation != "PARK" and (not isinstance(token, str) or not hmac.compare_digest(token, public["contextToken"])):
        raise ManualControlError("MANUAL_CONTEXT_CHANGED", "Die Belegung oder Bewässerung hat sich geändert. Bitte erneut prüfen.")
    epoch = previous["epoch"] + 1 if previous else 1
    now = now_utc.astimezone(timezone.utc)
    if operation == "RESUME":
        if not public["canResume"] or not previous:
            raise ManualControlError("MANUAL_SESSION_MISSING", "Es gibt keine manuelle Sperre aufzuheben.")
        session = with_session_status(previous, "ENDED", now_utc=now)
        session["epoch"] = epoch
        action = None
    elif operation == "PARK":
        if not public["canPark"]:
            raise ManualControlError("MANUAL_PARK_UNAVAILABLE", "Der Mäher ist nicht erreichbar. Bitte am Mäher parken.")
        session = new_session(session_id=request_id, epoch=epoch, mower_id=context["mower_id"],
                              kind="PARK", source=source, now_utc=now)
        session["status"] = "PENDING" if source == "APP" else "PREPARED"
        action = "PARK_MOWER" if source == "APP" else None
    else:
        if operation == "DECIDE" and (not previous or previous["kind"] != "START"
                                       or payload.get("sessionId") != previous["session_id"]
                                       or previous["status"] in {"ENDED", "UNKNOWN"}):
            raise ManualControlError("MANUAL_SESSION_CHANGED", "Der manuelle Auftrag hat sich geändert. Bitte neu öffnen.")
        if not public["canStart"]:
            raise ManualControlError("MANUAL_START_UNAVAILABLE", "Start noch nicht möglich. Bitte den angezeigten Sperrgrund prüfen.")
        if context["block_keys"] and payload.get("approveOccupancy") is not True:
            raise ManualControlError("MANUAL_OCCUPANCY_CONFIRMATION_REQUIRED", "Bitte bestätigen, dass der belegte Platz tatsächlich frei ist.")
        if context["dry_until_utc"] and payload.get("waterChoice") != "IRRIGATION" and payload.get("approveDrying") is not True:
            raise ManualControlError("MANUAL_DRYING_CONFIRMATION_REQUIRED", "Bitte vor Ort prüfen und bestätigen, dass der Platz ausreichend trocken ist.")
        choice = payload.get("waterChoice")
        if conflict.get("required") and choice not in {"MOWER", "IRRIGATION"}:
            raise ManualControlError("MANUAL_WATER_CHOICE_REQUIRED", "Bitte für diesen Lauf zwischen Mähen und Bewässern wählen.")
        if not conflict.get("required") and choice is not None:
            raise ManualControlError("MANUAL_WATER_CONFLICT_CHANGED", "Der Bewässerungskonflikt besteht nicht mehr. Bitte neu öffnen.")
        if state.mower_start_pending_since_utc is not None:
            raise ManualControlError("MANUAL_START_UNCONFIRMED", "Der vorherige Start ist noch nicht bestätigt. Bitte am Mäher prüfen.")
        if operation == "DECIDE":
            session = {**previous, "epoch": epoch,
                       "confirmed_block_keys": context["block_keys"],
                       "confirmed_dry_until_utc": context["dry_until_utc"] if choice != "IRRIGATION" else None,
                       "water_conflict_id": context["water_conflict_id"], "water_choice": choice}
            action = "START_MOWING" if session["source"] == "APP" and not session["departure_observed_utc"] else None
        else:
            session = new_session(
                session_id=request_id, epoch=epoch, mower_id=context["mower_id"],
                kind="START", source=source, now_utc=now,
                confirmed_block_keys=context["block_keys"],
                confirmed_dry_until_utc=_time(context["dry_until_utc"]) if choice != "IRRIGATION" else None,
                water_conflict_id=context["water_conflict_id"], water_choice=choice,
            )
            if source == "APP":
                session["status"] = "PENDING"
            action = "START_MOWING" if source == "APP" else None
    # A new manual request cancels an unsent legacy request in the SAME CAS.
    # Existing start-outcome and outbound-journal latches remain untouched.
    changes = {
        "revision": state.revision + 1, "manual_session_json": dump_manual_session(session),
        "operator_request_id": request_id, "operator_request_action": action,
        "operator_requested_utc": now.isoformat(),
        "operator_request_expires_utc": (now + timedelta(minutes=10)).isoformat(),
        "operator_request_status": "PENDING" if action else "CONFIRMED",
        "operator_request_result": None,
        "manual_control_receipts_json": json.dumps((receipts + [{
            "id": request_id, "hash": fingerprint, "status": "PENDING" if action else "CONFIRMED",
        }])[-32:], sort_keys=True, separators=(",", ":")),
        "operator_request_session_id": session["session_id"] if action else None,
        "operator_request_session_epoch": session["epoch"] if action else None,
        "operator_request_zone": None, "operator_request_run_seconds": None,
        "operator_request_cutting_height_mm": None, "operator_request_occupancy_override_key": None,
        "operator_request_irrigation_schedule_json": None,
        "operator_occupancy_override_key": None, "operator_occupancy_override_until_utc": None,
    }
    if operation == "PARK":
        changes["automation_restart_allowed"] = False
    updated = replace(state, **changes)
    store.save(updated, expected_revision=state.revision)
    response_public = manual_context(updated, details, environment, now)[0]
    return updated, {"accepted": True, "requestId": request_id,
                     "status": "PENDING" if action else "CONFIRMED", "manualControl": response_public}
