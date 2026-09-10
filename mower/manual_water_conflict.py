"""Stable, fail-closed identity and transaction state for manual water choices."""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta, timezone
from typing import Any, Mapping


VERSION = 1
ACTIVE_PHASES = frozenset({"PLANNED", "SUSPENDING", "READY", "START_RESERVED", "RUNNING", "STOPPING"})
TERMINAL_PHASES = frozenset({"CLEAR_CONFIRMED", "IRRIGATION_COMPLETED", "FAILED"})


class ManualWaterConflictError(ValueError):
    pass


def _utc(value: datetime) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ManualWaterConflictError("MANUAL_WATER_TIME_INVALID")
    return value.astimezone(timezone.utc)


def _parse(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        return None
    return parsed.astimezone(timezone.utc)


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _session_active(state: Any) -> bool:
    try:
        from mower.manual_session import load_manual_session
        session = load_manual_session(state)
    except Exception:
        return False
    return bool(
        session
        and session.get("kind") == "START"
        and session.get("status") in {"PREPARED", "PENDING", "ACTIVE", "UNKNOWN"}
    )


def _normalized_plan(details: Mapping[str, Any]) -> list[dict[str, Any]] | None:
    hydra = _dict(details.get("hydrawise"))
    safety = _dict(hydra.get("safety"))
    observed = safety.get("observed_relay_ids")
    expected = safety.get("expected_relay_ids")
    if not isinstance(observed, list) or not isinstance(expected, list):
        return None
    try:
        observed_ids = {int(v) for v in observed}
        expected_ids = {int(v) for v in expected}
    except (TypeError, ValueError):
        return None
    if (
        safety.get("available") is not True
        or safety.get("fresh") is not True
        or safety.get("relay_set_valid") is not True
        or observed_ids != expected_ids
    ):
        return None
    rows = hydra.get("zone_observations")
    if not isinstance(rows, list) or len(rows) != len(expected):
        rows = hydra.get("zones")
    if not isinstance(rows, list) or len(rows) != len(expected):
        return None
    plan: list[dict[str, Any]] = []
    for raw in rows:
        row = _dict(raw)
        try:
            relay = int(row.get("relay_id") or 0)
            run = int(row.get("run_seconds") or 0)
            zone = int(row.get("zone") or 0)
        except (TypeError, ValueError):
            return None
        start = _parse(row.get("scheduled_start_utc"))
        end = _parse(row.get("scheduled_end_utc"))
        if relay not in expected_ids or relay <= 0 or zone <= 0 or not 60 <= run <= 7200:
            return None
        # A running native zone may no longer expose its nominal time. That is
        # insufficient proof for suppressing the rest of the original run.
        if start is None or end is None or end <= start:
            return None
        plan.append({
            "relay_id": relay,
            "zone": zone,
            "run_seconds": run,
            "scheduled_start_utc": start.isoformat(),
            "scheduled_end_utc": end.isoformat(),
        })
    if len({row["relay_id"] for row in plan}) != len(expected):
        return None
    plan.sort(key=lambda row: (row["scheduled_start_utc"], row["zone"]))
    return plan


def load_transaction(state: Any) -> dict[str, Any] | None:
    raw = getattr(state, "manual_water_conflict_json", None)
    if not raw:
        return None
    try:
        value = json.loads(raw)
    except (TypeError, ValueError) as exc:
        raise ManualWaterConflictError("MANUAL_WATER_TRANSACTION_INVALID") from exc
    if not isinstance(value, dict) or value.get("version") != VERSION:
        raise ManualWaterConflictError("MANUAL_WATER_TRANSACTION_INVALID")
    identifier = value.get("id")
    phase = value.get("phase")
    if not isinstance(identifier, str) or not identifier or not isinstance(phase, str):
        raise ManualWaterConflictError("MANUAL_WATER_TRANSACTION_INVALID")
    return value


def water_conflict_context(
    state: Any, details: Mapping[str, Any], now_utc: datetime,
) -> dict[str, Any]:
    """Return one stable conflict identity shared by console and executor."""
    now = _utc(now_utc)
    transaction = load_transaction(state)
    if transaction and transaction.get("phase") not in TERMINAL_PHASES:
        return {
            "id": transaction["id"], "required": True, "known": True,
            "phase": transaction["phase"], "source": transaction.get("source"),
            "activeRelayIds": list(transaction.get("active_relay_ids") or []),
            "nextStartUtc": transaction.get("next_start_utc"),
        }

    hydra = _dict(details.get("hydrawise"))
    safety = _dict(hydra.get("safety"))
    try:
        active = sorted({int(v) for v in safety.get("active_relay_ids", [])})
        active_count = int(safety.get("active_zone_count") or 0)
        imminent_count = int(safety.get("imminent_zone_count") or 0)
    except (TypeError, ValueError):
        active, active_count, imminent_count = [], 0, 0
    phase = str(getattr(state, "irrigation_phase", None) or "") or None
    next_start = _parse(getattr(state, "next_irrigation_start_utc", None))
    if next_start is None:
        next_start = _parse(_dict(details.get("current_plan")).get("next_irrigation_start_utc"))
    # A choice is per concrete conflict. Ask only as the established mower
    # parking horizon reaches the occurrence; a possible session lasting until
    # charging is not evidence that tomorrow's run conflicts now.
    upcoming = next_start is not None and now <= next_start <= now + timedelta(minutes=10)
    required = bool(
        active_count or active or imminent_count or phase in ACTIVE_PHASES or upcoming
    )
    if not required:
        return {"id": None, "required": False, "known": True, "phase": phase,
                "source": None, "activeRelayIds": [], "nextStartUtc": None}
    plan = _normalized_plan(details)
    known = bool(
        safety.get("available") is True
        and safety.get("fresh") is True
        and safety.get("relay_set_valid") is True
        and active_count == len(active)
        and active_count <= 1
        and plan is not None
    )
    if not known:
        return {"id": None, "required": True, "known": False, "phase": phase,
                "source": "CENTRAL" if phase in ACTIVE_PHASES else "NATIVE",
                "activeRelayIds": active, "nextStartUtc": next_start.isoformat() if next_start else None}
    source_plan_id = getattr(state, "irrigation_plan_id", None)
    plan_start = plan[0]["scheduled_start_utc"]
    identity = {
        "source_plan_id": source_plan_id,
        "plan": plan,
        "active_relay_ids": active,
        "next_start_utc": plan_start,
    }
    identifier = hashlib.sha256(
        json.dumps(identity, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    return {
        "id": identifier, "required": True, "known": True, "phase": phase,
        "source": "CENTRAL" if phase in ACTIVE_PHASES else "NATIVE",
        "activeRelayIds": active, "nextStartUtc": identity["next_start_utc"],
        "sourcePlanId": source_plan_id, "plan": plan,
    }


def new_transaction(context: Mapping[str, Any], *, session: Mapping[str, Any], now_utc: datetime) -> dict[str, Any]:
    if context.get("required") is not True or context.get("known") is not True or not context.get("id"):
        raise ManualWaterConflictError("MANUAL_WATER_CONFLICT_UNKNOWN")
    if session.get("water_conflict_id") != context.get("id") or session.get("water_choice") not in {"MOWER", "IRRIGATION"}:
        raise ManualWaterConflictError("MANUAL_WATER_CHOICE_CHANGED")
    now = _utc(now_utc)
    plan = context.get("plan")
    if not isinstance(plan, list) or not plan:
        raise ManualWaterConflictError("MANUAL_WATER_PLAN_UNKNOWN")
    return {
        "version": VERSION, "id": context["id"], "session_id": session["session_id"],
        "session_epoch": session["epoch"], "choice": session["water_choice"],
        "source": context.get("source"), "phase": "IRRIGATION_ALLOWED" if session["water_choice"] == "IRRIGATION" else "SUSPENDING",
        "created_at_utc": now.isoformat(), "updated_at_utc": now.isoformat(),
        "source_plan_id": context.get("sourcePlanId"), "plan": plan,
        "active_relay_ids": list(context.get("activeRelayIds") or []),
        "next_start_utc": context.get("nextStartUtc"), "suspended_relay_ids": [],
        "suspend_until_utc": None, "stop_reserved_utc": None,
        "clear_since_utc": None, "error_code": None,
    }


def dump_transaction(value: Mapping[str, Any] | None) -> str | None:
    if value is None:
        return None
    payload = json.dumps(dict(value), sort_keys=True, separators=(",", ":"))
    if len(payload.encode("utf-8")) > 16_384:
        raise ManualWaterConflictError("MANUAL_WATER_TRANSACTION_TOO_LARGE")
    # Round-trip through the same structural boundary used for persisted data.
    class _State:
        manual_water_conflict_json = payload
    load_transaction(_State())
    return payload


def update_transaction(
    transaction: Mapping[str, Any], *, now_utc: datetime, phase: str | None = None,
    **changes: Any,
) -> dict[str, Any]:
    current = dict(transaction)
    if current.get("version") != VERSION:
        raise ManualWaterConflictError("MANUAL_WATER_TRANSACTION_INVALID")
    current.update(changes)
    if phase is not None:
        current["phase"] = phase
    current["updated_at_utc"] = _utc(now_utc).isoformat()
    dump_transaction(current)
    return current
