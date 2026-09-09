"""Fail-closed reservation boundary for the optional coordination pilot.

This module has no vendor sender.  It treats the read-only handover as
untrusted and creates a durable, single-use reservation before the existing
FULL_FAILSAFE schedule transaction may touch Hydrawise.
"""
from __future__ import annotations

import json
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from typing import Any, Mapping

from mower.coordination_request import build_coordination_request
from mower.coordination_shadow import compare_charging_window
from mower.state import AutomationState

CONFIRMATION = "SSV53-COORDINATED-IRRIGATION-PILOT-V1"
RESERVATION_LIMIT = 24


def enabled(environment: Mapping[str, str], *, full_failsafe_gate: bool) -> bool:
    """This is deliberately stricter than an input permission flag."""
    return (
        full_failsafe_gate
        and str(environment.get("COORDINATION_EXECUTION_ENABLED") or "").strip().lower() == "true"
        and str(environment.get("COORDINATION_EXECUTION_CONFIRMATION") or "").strip() == CONFIRMATION
    )


def _utc(value: Any, label: str) -> datetime:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label} fehlt oder ist ungültig")
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError(f"{label} muss eine Zeitzone enthalten")
    return parsed.astimezone(timezone.utc)


def _identity(value: Any, label: str) -> str:
    text = str(value or "").strip()
    if not text or len(text) > 256:
        raise ValueError(f"{label} ist ungültig")
    return text


def _load(value: str | None, label: str) -> list[dict[str, Any]]:
    if not value:
        return []
    parsed = json.loads(value)
    if not isinstance(parsed, list):
        raise ValueError(f"{label} ist ungültig")
    records: list[dict[str, Any]] = []
    for item in parsed:
        if not isinstance(item, dict):
            raise ValueError(f"{label} ist ungültig")
        _identity(item.get("need_id"), f"{label}.need_id")
        _identity(item.get("source_plan_id"), f"{label}.source_plan_id")
        _identity(item.get("request_id"), f"{label}.request_id")
        digest = _identity(item.get("need_sha256"), f"{label}.need_sha256")
        if len(digest) != 64:
            raise ValueError(f"{label}.need_sha256 ist ungültig")
        _identity(item.get("status"), f"{label}.status")
        _utc(item.get("reserved_utc"), f"{label}.reserved_utc")
        if item.get("terminal_utc") is not None:
            _utc(item.get("terminal_utc"), f"{label}.terminal_utc")
        records.append(dict(item))
    return records


def reservations(state: AutomationState) -> list[dict[str, Any]]:
    return _load(state.coordination_execution_reservations_json, "Koordinationsreservierungen")


def request(state: AutomationState) -> dict[str, Any] | None:
    if not state.coordination_execution_request_json:
        return None
    parsed = json.loads(state.coordination_execution_request_json)
    if not isinstance(parsed, dict):
        raise ValueError("Koordinationsanforderung ist ungültig")
    for key in ("request_id", "need_id", "source_plan_id", "need_sha256", "selected_start_utc", "valid_until_utc"):
        if key in {"selected_start_utc", "valid_until_utc"}:
            _utc(parsed.get(key), f"Koordinationsanforderung.{key}")
        else:
            _identity(parsed.get(key), f"Koordinationsanforderung.{key}")
    if len(str(parsed.get("need_sha256"))) != 64:
        raise ValueError("Koordinationsanforderung.need_sha256 ist ungültig")
    return dict(parsed)


def _dump(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _input(value: Any) -> tuple[Mapping[str, Any] | None, list[Mapping[str, Any]], Mapping[str, Any] | None, Mapping[str, Any] | None]:
    allowed = {
        "schema_version", "available", "needs", "need", "previous_cycle",
        "charging_end_estimate", "permission_to_start", "blockers",
        "approval_document_sha256",
    }
    required = {"schema_version", "needs", "need", "previous_cycle", "charging_end_estimate"}
    if (not isinstance(value, Mapping) or not required.issubset(value)
            or not set(value).issubset(allowed) or value.get("schema_version") != 1
            or value.get("permission_to_start") is not False):
        raise ValueError("COORDINATION_EXECUTION_INPUT_INVALID")
    need = value.get("need")
    needs = value.get("needs")
    previous = value.get("previous_cycle")
    estimate = value.get("charging_end_estimate")
    if need is not None and not isinstance(need, Mapping):
        raise ValueError("COORDINATION_NEED_INVALID")
    if not isinstance(needs, list) or len(needs) > 64 or not all(isinstance(item, Mapping) for item in needs):
        raise ValueError("COORDINATION_NEEDS_INVALID")
    if previous is not None and not isinstance(previous, Mapping):
        raise ValueError("COORDINATION_PREVIOUS_CYCLE_INVALID")
    if estimate is not None and not isinstance(estimate, Mapping):
        raise ValueError("COORDINATION_CHARGING_ESTIMATE_INVALID")
    return need, list(needs), previous, estimate


def _digest(value: Mapping[str, Any]) -> str:
    import hashlib
    return hashlib.sha256(_dump(value).encode("utf-8")).hexdigest()


def draft_for_cycle(*, cycle: Mapping[str, Any], execution_input: Any) -> dict[str, Any]:
    """Recheck the source material and return only a command-free draft."""
    need, needs, previous, estimate = _input(execution_input)
    if need is None or not any(_digest(item) == _digest(need) for item in needs):
        raise ValueError("COORDINATION_SELECTED_NEED_NOT_IN_CURRENT_CONFIG")
    if execution_input.get("available") is not True or execution_input.get("blockers"):
        raise ValueError("COORDINATION_INPUT_UNAVAILABLE")
    proposal = compare_charging_window(
        cycle=cycle, need=need, previous_cycle=previous,
        charging_end_estimate=estimate, minimum_lead_minutes=45,
    )
    return build_coordination_request(
        proposal=proposal, cycle=cycle, need=need, previous_cycle=previous,
        charging_end_estimate=estimate, minimum_lead_minutes=45,
    )


def reserve(
    state: AutomationState, *, cycle: Mapping[str, Any], execution_input: Any,
    now_utc: datetime,
) -> tuple[AutomationState, dict[str, Any]]:
    """Durably consume a need/source-plan pair.  Callers must CAS-save it."""
    outcome: dict[str, Any] = {"accepted": False, "reason": None, "draft": None}
    try:
        if state.maintenance_mode or state.operator_request_status == "PENDING":
            raise ValueError("MANUAL_OR_MAINTENANCE_ACTIVE")
        if state.irrigation_phase is not None or state.irrigation_schedule_override_json:
            raise ValueError("IRRIGATION_TRANSACTION_ACTIVE")
        if request(state) is not None:
            raise ValueError("COORDINATION_REQUEST_ALREADY_RESERVED")
        draft = draft_for_cycle(cycle=cycle, execution_input=execution_input)
        outcome["draft"] = draft
        if draft.get("status") != "DRAFT" or draft.get("permission_to_start") is not False:
            raise ValueError("COORDINATION_DRAFT_BLOCKED")
        need_id = str(draft.get("need_id") or "")
        source_plan_id = str(draft.get("source_plan_id") or "")
        request_id = str(draft.get("request_id") or "")
        if not need_id or not source_plan_id or not request_id:
            raise ValueError("COORDINATION_DRAFT_IDENTITY_INVALID")
        ledger = reservations(state)
        if len(ledger) >= RESERVATION_LIMIT:
            raise ValueError("COORDINATION_RESERVATION_LEDGER_FULL")
        if any(
            item.get("need_id") == need_id or item.get("source_plan_id") == source_plan_id
            for item in ledger
        ):
            raise ValueError("COORDINATION_NEED_ALREADY_CONSUMED")
        selected_need, _needs, _previous, _estimate = _input(execution_input)
        assert selected_need is not None
        request_value = {**draft, "need_sha256": _digest(selected_need)}
        record = {
            "need_id": need_id, "source_plan_id": source_plan_id,
            "request_id": request_id, "need_sha256": request_value["need_sha256"],
            "status": "RESERVED", "reserved_utc": now_utc.astimezone(timezone.utc).isoformat(),
        }
        saved = replace(
            state,
            revision=state.revision + 1,
            coordination_execution_reservations_json=_dump([*ledger, record]),
            coordination_execution_request_json=_dump(request_value),
        )
        outcome.update(accepted=True, reason="RESERVED")
        return saved, outcome
    except (AttributeError, KeyError, TypeError, ValueError, OverflowError, json.JSONDecodeError) as exc:
        outcome["reason"] = str(exc)
        return state, outcome


def consume_request(
    state: AutomationState, *, cycle: Mapping[str, Any], execution_input: Any,
    now_utc: datetime,
) -> tuple[AutomationState, dict[str, Any] | None, str | None]:
    """Recheck a reservation before it enters CUSTOM_NEXT; never recreate it.

    After Hydrawise has been suspended, ``need`` may intentionally be null.
    The full copied ``needs`` list remains the durable authorization source.
    """
    try:
        saved = request(state)
        if saved is None:
            return state, None, "COORDINATION_REQUEST_MISSING"
        if state.maintenance_mode or state.operator_request_status == "PENDING":
            raise ValueError("MANUAL_OR_MAINTENANCE_ACTIVE")
        if state.irrigation_phase is not None or state.irrigation_schedule_override_json:
            raise ValueError("IRRIGATION_TRANSACTION_ACTIVE")
        if (state.parked_by_automation
                and "operator" in str(state.automation_park_source or "").lower()):
            raise ValueError("MANUAL_STOP_ACTIVE")
        _selected, needs, _previous, _estimate = _input(execution_input)
        if not isinstance(execution_input, Mapping) or execution_input.get("available") is not True:
            raise ValueError("COORDINATION_INPUT_UNAVAILABLE")
        need_id = str(saved.get("need_id") or "")
        source_plan_id = str(saved.get("source_plan_id") or "")
        need_sha = str(saved.get("need_sha256") or "")
        matching = [item for item in needs if str(item.get("need_id") or "") == need_id]
        if len(matching) != 1 or _digest(matching[0]) != need_sha:
            raise ValueError("COORDINATION_AUTHORIZATION_CHANGED")
        if str(matching[0].get("source_plan_id") or "") != source_plan_id:
            raise ValueError("COORDINATION_SOURCE_PLAN_AUTHORIZATION_CHANGED")
        # The second observation must prove the exact persisted slot.  A new
        # candidate one minute later is never evidence for the old one.
        _selected, _needs, previous, estimate = _input(execution_input)
        proposal = compare_charging_window(
            cycle=cycle, need=matching[0], previous_cycle=previous,
            charging_end_estimate=estimate, minimum_lead_minutes=0,
            fixed_start_utc=str(saved.get("selected_start_utc") or ""),
        )
        if (proposal.get("status") != "SHADOW_PROPOSAL"
                or proposal.get("selected_start_utc") != saved.get("selected_start_utc")):
            raise ValueError("COORDINATION_EXACT_SLOT_REVALIDATION_FAILED")
        return state, saved, None
    except (AttributeError, KeyError, TypeError, ValueError, OverflowError, json.JSONDecodeError) as exc:
        # A reservation is deliberately retained and cannot be replayed after
        # unknown input, expiry or a restart.
        return state, None, str(exc)


def start_authorized(*, override: Mapping[str, Any], cycle: Mapping[str, Any],
                     execution_input: Any, now_utc: datetime,
                     remaining_plan: list[Mapping[str, Any]],
                     projected_end_utc: datetime) -> str | None:
    """Revalidate authorization and the actual persisted remaining water horizon."""
    try:
        _selected, needs, _previous, _estimate = _input(execution_input)
        if not isinstance(execution_input, Mapping) or execution_input.get("available") is not True:
            raise ValueError("COORDINATION_INPUT_UNAVAILABLE")
        need_id = _identity(override.get("coordination_need_id"), "coordination_need_id")
        source_plan_id = _identity(override.get("coordination_source_plan_id"), "coordination_source_plan_id")
        need_sha = _identity(override.get("coordination_need_sha256"), "coordination_need_sha256")
        if len(need_sha) != 64:
            raise ValueError("COORDINATION_AUTHORIZATION_CHANGED")
        matching = [item for item in needs if str(item.get("need_id") or "") == need_id]
        if (len(matching) != 1 or _digest(matching[0]) != need_sha
                or str(matching[0].get("source_plan_id") or "") != source_plan_id):
            raise ValueError("COORDINATION_AUTHORIZATION_CHANGED")
        now = now_utc.astimezone(timezone.utc)
        valid_until = _utc(matching[0].get("valid_until_utc"), "valid_until_utc")
        if now > valid_until:
            raise ValueError("COORDINATION_APPROVAL_EXPIRED")
        if not remaining_plan:
            raise ValueError("COORDINATION_REMAINING_PLAN_MISSING")
        for zone in remaining_plan:
            _utc(zone.get("scheduled_start_utc"), "remaining scheduled_start_utc")
            _utc(zone.get("scheduled_end_utc"), "remaining scheduled_end_utc")
        projected_end = projected_end_utc.astimezone(timezone.utc)
        if projected_end < now:
            raise ValueError("COORDINATION_PROJECTED_END_IN_PAST")
        release = projected_end + timedelta(minutes=150)
        captured = (cycle.get("details") or {}).get("coordination_shadow_input")
        if not isinstance(captured, Mapping) or captured.get("schema_version") != 1:
            raise ValueError("COORDINATION_COMPLETE_CAPTURE_MISSING")
        captured_at = _utc(captured.get("captured_at_utc"), "captured_at_utc")
        complete_from = _utc(captured.get("complete_from_utc"), "complete_from_utc")
        complete_until = _utc(captured.get("complete_until_utc"), "complete_until_utc")
        if not timedelta(0) <= now - captured_at <= timedelta(minutes=3):
            raise ValueError("COORDINATION_CAPTURE_NOT_CURRENT")
        if complete_from > now:
            raise ValueError("COORDINATION_OCCUPANCY_CAPTURE_STARTS_IN_FUTURE")
        if (captured.get("occupancy_complete") is not True or captured.get("state_available") is not True
                or captured.get("manual_stop") is not False or captured.get("uncertain_start") is not False):
            raise ValueError("COORDINATION_OCCUPANCY_OR_STATE_UNKNOWN")
        if complete_until < release:
            raise ValueError("COORDINATION_OCCUPANCY_HORIZON_TOO_SHORT")
        intervals = captured.get("occupancy")
        if not isinstance(intervals, list):
            raise ValueError("COORDINATION_OCCUPANCY_INVALID")
        for item in intervals:
            if not isinstance(item, Mapping):
                raise ValueError("COORDINATION_OCCUPANCY_INVALID")
            begin = _utc(item.get("start"), "occupancy.start")
            finish = _utc(item.get("end"), "occupancy.end")
            if begin >= finish:
                raise ValueError("COORDINATION_OCCUPANCY_INVALID")
            if begin < release and finish > now:
                raise ValueError("COORDINATION_OCCUPANCY_CHANGED")
        return None
    except (AttributeError, KeyError, TypeError, ValueError, OverflowError) as exc:
        return str(exc)

def mark_terminal(state: AutomationState, *, status: str, now_utc: datetime) -> AutomationState:
    """Keep the dedup evidence while clearing a request that must not replay."""
    try:
        saved = request(state)
        if saved is None:
            return state
        ledger = reservations(state)
        rid = str(saved.get("request_id") or "")
        changed = [
            {**item, "status": status, "terminal_utc": now_utc.astimezone(timezone.utc).isoformat()}
            if item.get("request_id") == rid else item
            for item in ledger
        ]
        return replace(
            state, revision=state.revision + 1,
            coordination_execution_reservations_json=_dump(changed),
            coordination_execution_request_json=None,
        )
    except (TypeError, ValueError, json.JSONDecodeError):
        # Preserve a corrupt ledger: it makes every later reservation fail
        # closed rather than erasing an unknown previously accepted need.
        return replace(
            state, revision=state.revision + 1,
            coordination_execution_request_json=None,
        )
