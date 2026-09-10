"""Isolated, opt-in manual park and cutting-height controls.

This module deliberately has no top-level sender imports so read-only packages
can import the capability and status contract.  Only OPERATOR_ONLY journal
records created by :func:`queue_operator_action` are considered executable;
legacy console requests are never replayed here.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Mapping

from mower.cutting_height import (
    MAXIMUM_MM,
    MINIMUM_MM,
    cutting_height_mm_to_percent,
    supports_metric_cutting_height,
)
from mower.runtime import ControlMode, CycleResult, RuntimeSettings
from mower.state import AutomationState
from mower.state_store import AzureTableStateStore, StateConflictError, StateStore


OPERATOR_ACTIONS = frozenset({"PARK_MOWER", "SET_CUTTING_HEIGHT"})
FULL_FAILSAFE_ACTIONS = frozenset({
    "START_MOWING", "START_IRRIGATION", "START_IRRIGATION_ZONE",
    "STOP_IRRIGATION_AFTER_ZONE", "STOP_IRRIGATION_NOW", "RESET_BLADE_USAGE",
    "SKIP_NEXT_IRRIGATION", "PAUSE_IRRIGATION_UNTIL", "RESUME_IRRIGATION_SCHEDULE",
    "CUSTOMIZE_NEXT_IRRIGATION",
})
TERMINAL = frozenset({"CONFIRMED", "REJECTED", "EXPIRED", "UNKNOWN"})
JOURNAL_VERSION = 1
GENERATION = "operator-only-v1"
MAX_JOURNAL_ENTRIES = 32
REQUEST_LIFETIME = timedelta(minutes=10)
CONFIRMATION_TIMEOUT = timedelta(minutes=10)
# A physical mower status must advance beyond a certain terminal guard record
# (CONFIRMED, REJECTED, or EXPIRED; never UNKNOWN) before a still-blocked
# episode may be asserted again.  This is deliberately short enough for a
# renewed departure near active water, while rejecting a minute-by-minute
# replay of an unchanged vendor snapshot.
GUARD_REASSERT_COOLDOWN = timedelta(seconds=30)


class OperatorControlError(RuntimeError):
    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


def action_capabilities(settings: RuntimeSettings) -> dict[str, dict[str, Any]]:
    """Return machine-readable request admission, never a command permit."""
    # A FULL_FAILSAFE mode string alone must never surface an actionable
    # capability while the reader is intentionally disabled.
    full = settings.enable_live_reads and settings.full_failsafe_write_gate_enabled
    operator = settings.operator_control_gate_enabled
    park_available = (
        (settings.control_mode is ControlMode.OPERATOR_ONLY and operator and settings.enable_park_commands)
        or (settings.control_mode is ControlMode.FULL_FAILSAFE and full)
    )
    height_available = (
        (settings.control_mode is ControlMode.OPERATOR_ONLY and operator
         and settings.enable_operator_cutting_height_commands)
        or (settings.control_mode is ControlMode.FULL_FAILSAFE and full)
    )
    def reason(available: bool, enabled: bool = True) -> str:
        if available:
            return "AVAILABLE"
        if settings.control_mode not in {ControlMode.OPERATOR_ONLY, ControlMode.FULL_FAILSAFE}:
            return "MODE"
        if not settings.enable_live_reads:
            return "LIVE_READS"
        if settings.control_mode is ControlMode.OPERATOR_ONLY:
            if not settings.operator_control_gate_enabled:
                return "OPERATOR_CONFIRMATION"
            return "ACTION_GATE" if not enabled else "MODE"
        return "FULL_FAILSAFE_GATE"
    capabilities = {
        "PARK_MOWER": {"available": park_available, "reason": reason(park_available, settings.enable_park_commands)},
        "SET_CUTTING_HEIGHT": {"available": height_available, "reason": reason(height_available, settings.enable_operator_cutting_height_commands)},
    }
    for action in FULL_FAILSAFE_ACTIONS:
        capabilities[action] = {
            "available": settings.control_mode is ControlMode.FULL_FAILSAFE and full,
            "reason": "AVAILABLE" if settings.control_mode is ControlMode.FULL_FAILSAFE and full else "FULL_FAILSAFE_GATE",
        }
    return capabilities


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("now_utc muss zeitzonenbewusst sein.")
    return value.astimezone(timezone.utc)


def _parse_time(value: Any) -> datetime:
    parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("Zeit ohne Zeitzone.")
    return parsed.astimezone(timezone.utc)


def _load_journal(state: AutomationState) -> list[dict[str, Any]]:
    raw = state.operator_commands_json
    if not raw:
        return []
    try:
        entries = json.loads(raw)
    except (TypeError, ValueError) as exc:
        raise OperatorControlError("JOURNAL_INVALID") from exc
    if not isinstance(entries, list) or len(entries) > MAX_JOURNAL_ENTRIES:
        raise OperatorControlError("JOURNAL_INVALID")
    required = {"version", "action", "request_id", "mode", "generation", "status", "requested_at", "expires_at"}
    known = required | {"target_mm", "reserved_at", "sent_at", "confirmed_at", "message_code", "mower_id", "area_id", "origin", "guard_key"}
    clean: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in entries:
        if not isinstance(item, dict) or set(item) - known or not required <= set(item):
            raise OperatorControlError("JOURNAL_INVALID")
        if item.get("version") != JOURNAL_VERSION or item.get("action") not in OPERATOR_ACTIONS:
            raise OperatorControlError("JOURNAL_INVALID")
        if item.get("mode") != ControlMode.OPERATOR_ONLY.value or item.get("generation") != GENERATION:
            raise OperatorControlError("JOURNAL_INVALID")
        origin = item.get("origin", "MANUAL")
        if origin not in {"MANUAL", "SAFETY_GUARD"}:
            raise OperatorControlError("JOURNAL_INVALID")
        if origin == "SAFETY_GUARD":
            if (item.get("action") != "PARK_MOWER" or not isinstance(item.get("guard_key"), str)
                    or not item["guard_key"] or len(item["guard_key"]) > 256):
                raise OperatorControlError("JOURNAL_INVALID")
        elif "guard_key" in item:
            raise OperatorControlError("JOURNAL_INVALID")
        request_id = item.get("request_id")
        if not isinstance(request_id, str) or not request_id or len(request_id) > 64 or request_id in seen:
            raise OperatorControlError("JOURNAL_INVALID")
        seen.add(request_id)
        if item.get("status") not in {"QUEUED", "RESERVED", "SENT_UNCONFIRMED", *TERMINAL}:
            raise OperatorControlError("JOURNAL_INVALID")
        _parse_time(item["requested_at"]); _parse_time(item["expires_at"])
        for key in ("reserved_at", "sent_at", "confirmed_at"):
            if key in item:
                _parse_time(item[key])
        for key in ("mower_id",):
            if key in item and (not isinstance(item[key], str) or not item[key]):
                raise OperatorControlError("JOURNAL_INVALID")
        if "area_id" in item and (type(item["area_id"]) is not int or item["area_id"] <= 0):
            raise OperatorControlError("JOURNAL_INVALID")
        if item["action"] == "SET_CUTTING_HEIGHT":
            target = item.get("target_mm")
            if type(target) is not int or not MINIMUM_MM <= target <= MAXIMUM_MM:
                raise OperatorControlError("JOURNAL_INVALID")
        elif "target_mm" in item:
            raise OperatorControlError("JOURNAL_INVALID")
        dispatched = {"RESERVED", "SENT_UNCONFIRMED", "CONFIRMED", "UNKNOWN"}
        if not item.get("mower_id"):
            raise OperatorControlError("JOURNAL_INVALID")
        if item["action"] == "SET_CUTTING_HEIGHT" and item["status"] in dispatched and not item.get("area_id"):
            raise OperatorControlError("JOURNAL_INVALID")
        clean.append(dict(item))
    return clean


def _dump_journal(entries: list[dict[str, Any]]) -> str:
    retained = list(entries)
    while True:
        payload = json.dumps(retained, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
        if len(retained) <= MAX_JOURNAL_ENTRIES and len(payload.encode("utf-8")) <= 16_384:
            return payload
        # Confirmation adds timestamps after enqueue. Compact certain history
        # at serialization too; a count-only limit can exceed Azure's bounded
        # journal budget before the nominal 32nd completed command.
        removable = [(index, _parse_time(entry["requested_at"]))
                     for index, entry in enumerate(retained)
                     if entry["status"] in {"CONFIRMED", "REJECTED", "EXPIRED"}]
        if not removable:
            raise OperatorControlError("JOURNAL_FULL")
        retained.pop(min(removable, key=lambda item: item[1])[0])


def operator_commands_payload(state: AutomationState) -> dict[str, dict[str, Any]]:
    """Public redacted status keyed by action; invalid journals fail closed."""
    result: dict[str, dict[str, Any]] = {}
    try:
        entries = _load_journal(state)
    except OperatorControlError:
        return {action: {"status": "UNKNOWN", "messageCode": "JOURNAL_INVALID"} for action in OPERATOR_ACTIONS}
    for entry in entries:
        prior = result.get(entry["action"])
        if prior is None or str(entry["requested_at"]) >= str(prior["requestedAt"]):
            result[entry["action"]] = _public_entry(entry)
    return result


def _public_entry(entry: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "status": entry["status"], "requestId": entry["request_id"],
        "requestedAt": entry["requested_at"], "targetMm": entry.get("target_mm"),
        "confirmedAt": entry.get("confirmed_at"),
        "messageCode": entry.get("message_code") or entry["status"],
    }


def queue_operator_action(
    store: StateStore,
    settings: RuntimeSettings,
    action: str,
    request_id: str,
    now_utc: datetime,
    *,
    mower_id: str,
    cutting_height_mm: int | None = None,
    origin: str = "MANUAL",
    guard_key: str | None = None,
) -> dict[str, Any]:
    now = _utc(now_utc)
    normalized = str(action or "").strip().upper()
    if normalized not in OPERATOR_ACTIONS or settings.control_mode is not ControlMode.OPERATOR_ONLY:
        raise OperatorControlError("ACTION_UNAVAILABLE")
    capability = action_capabilities(settings)[normalized]
    if capability["available"] is not True:
        raise OperatorControlError(str(capability["reason"]))
    if not isinstance(request_id, str) or not request_id.strip() or len(request_id) > 64:
        raise OperatorControlError("REQUEST_ID_INVALID")
    request_id = request_id.strip()
    mower_id = str(mower_id or "").strip()
    if not mower_id or len(mower_id) > 128:
        raise OperatorControlError("MOWER_TARGET_UNAVAILABLE")
    if normalized == "SET_CUTTING_HEIGHT":
        if type(cutting_height_mm) is not int or not MINIMUM_MM <= cutting_height_mm <= MAXIMUM_MM:
            raise OperatorControlError("TARGET_INVALID")
    elif cutting_height_mm is not None:
        raise OperatorControlError("TARGET_INVALID")
    origin = str(origin or "").strip().upper()
    if origin not in {"MANUAL", "SAFETY_GUARD"}:
        raise OperatorControlError("ACTION_UNAVAILABLE")
    if origin == "SAFETY_GUARD":
        if not settings.enable_operator_safety_guard:
            raise OperatorControlError("SAFETY_GUARD_DISABLED")
        if normalized != "PARK_MOWER" or not isinstance(guard_key, str) or not guard_key or len(guard_key) > 256:
            raise OperatorControlError("ACTION_UNAVAILABLE")
    elif guard_key is not None:
        raise OperatorControlError("ACTION_UNAVAILABLE")
    state = store.load()
    entries = _load_journal(state)
    for entry in entries:
        if entry["request_id"] == request_id:
            if entry["action"] != normalized:
                raise OperatorControlError("REQUEST_ID_REUSED")
            if normalized == "SET_CUTTING_HEIGHT" and entry.get("target_mm") != cutting_height_mm:
                raise OperatorControlError("REQUEST_ID_REUSED")
            return _public_entry(entry)
    if normalized == "SET_CUTTING_HEIGHT" and any(
        entry["action"] == normalized and entry["status"] == "UNKNOWN" for entry in entries
    ):
        # A delayed first height PATCH could still arrive after a different
        # target was requested.  It needs an observed resolution first.
        raise OperatorControlError("OPERATOR_ACTION_UNCONFIRMED")
    if any(entry["action"] == normalized and entry["status"] not in TERMINAL for entry in entries):
        raise OperatorControlError("ACTION_PENDING")
    # Preserve uncertain outcomes indefinitely: deleting an UNKNOWN record
    # could make a possibly delivered command look safe to repeat.  Certain
    # terminal outcomes are the only bounded history that may be compacted.
    if len(entries) >= MAX_JOURNAL_ENTRIES:
        entries = _compact_certain_terminal_history(entries)
    if len(entries) >= MAX_JOURNAL_ENTRIES:
        raise OperatorControlError("JOURNAL_FULL")
    entry: dict[str, Any] = {
        "version": JOURNAL_VERSION, "action": normalized, "request_id": request_id,
        "mode": ControlMode.OPERATOR_ONLY.value, "generation": GENERATION,
        "status": "QUEUED", "requested_at": now.isoformat(),
        "expires_at": (now + REQUEST_LIFETIME).isoformat(), "message_code": "QUEUED",
        "mower_id": mower_id,
    }
    if normalized == "SET_CUTTING_HEIGHT":
        entry["target_mm"] = cutting_height_mm
    if origin == "SAFETY_GUARD":
        entry["origin"] = origin
        entry["guard_key"] = guard_key
    updated = replace(state, revision=state.revision + 1, operator_commands_json=_dump_journal([*entries, entry]))
    store.save(updated, expected_revision=state.revision)
    return operator_commands_payload(updated)[normalized]


def _replace_entry(entries: list[dict[str, Any]], request_id: str, **changes: Any) -> list[dict[str, Any]]:
    return [{**entry, **changes} if entry["request_id"] == request_id else entry for entry in entries]


def _compact_certain_terminal_history(entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Keep at most 31 entries before a new request, never dropping UNKNOWN."""
    removable = {"CONFIRMED", "REJECTED", "EXPIRED"}
    retained = list(entries)
    while len(retained) >= MAX_JOURNAL_ENTRIES:
        candidates = [
            (index, _parse_time(entry["requested_at"]))
            for index, entry in enumerate(retained)
            if entry["status"] in removable
        ]
        if not candidates:
            break
        index = min(candidates, key=lambda item: item[1])[0]
        retained.pop(index)
    return retained


def _guard_signal(result: CycleResult, mower: Mapping[str, Any]) -> tuple[str, str] | None:
    """Return a stable, evidence-derived protective-park episode key."""
    details = result.details if isinstance(result.details, dict) else None
    activity = str(mower.get("activity") or "").upper()
    state = str(mower.get("state") or "").upper()
    if state in {"PAUSED", "STOPPED"} or activity == "STOPPED_IN_GARDEN":
        return None
    if details is None:
        return "water-safety-unavailable", "WATER_SAFETY_UNAVAILABLE"
    plan_value = details.get("current_plan")
    plan = plan_value if isinstance(plan_value, dict) else None
    if plan is None or "parking_block" not in plan:
        return "water-safety-unavailable", "WATER_SAFETY_UNAVAILABLE"
    block_value = plan.get("parking_block")
    block = block_value if isinstance(block_value, dict) else None
    blocked_now_value = plan.get("blocked_now")
    blocked_now = blocked_now_value if isinstance(blocked_now_value, dict) else None
    hydrawise_value = details.get("hydrawise")
    hydrawise = hydrawise_value if isinstance(hydrawise_value, dict) else None
    safety_value = hydrawise.get("safety") if hydrawise is not None else None
    safety = safety_value if isinstance(safety_value, dict) else None
    release_value = hydrawise.get("release_confirmation") if hydrawise is not None else None
    release = release_value if isinstance(release_value, dict) else None
    override = str(mower.get("override_action") or "").upper()
    active_raw = safety.get("active_relay_ids", []) if safety is not None else None
    imminent_raw = safety.get("imminent_relay_ids", []) if safety is not None else None
    relay_lists_valid = isinstance(active_raw, (list, tuple)) and isinstance(imminent_raw, (list, tuple))
    active = [str(value) for value in active_raw] if relay_lists_valid else []
    imminent = [str(value) for value in imminent_raw] if relay_lists_valid else []
    active_block = blocked_now if blocked_now_value is not None else block
    if active_block is not None:
        try:
            block_end = _parse_time(active_block.get("end"))
            block_source = str(active_block.get("source") or "").strip()
            if not block_source:
                raise ValueError
        except (TypeError, ValueError, OverflowError, OSError):
            return "water-safety-unavailable", "WATER_SAFETY_UNAVAILABLE"
        # Irrigation's current-zone projection deliberately rolls its start
        # on every poll.  It is not an episode identity.  A rounded end makes
        # harmless second-level projection jitter stable without making an
        # unbounded per-minute queue.
        end_bucket = int(block_end.timestamp() // 300)
        return f"occupancy|{block_source}|{end_bucket}|{override}", "OCCUPANCY_OR_IMMINENT_WATER"
    if active:
        return "active-water|" + ",".join(sorted(active)), "ACTIVE_WATER"
    if imminent:
        return "imminent-water|" + ",".join(sorted(imminent)), "IMMINENT_WATER"
    if release is not None and release.get("allowed") is False:
        return "drying|" + str(release.get("release_at_utc") or "pending"), "DRYING_HOLD"
    active_count = safety.get("active_zone_count") if safety is not None else None
    imminent_count = safety.get("imminent_zone_count") if safety is not None else None
    counts_valid = (
        type(active_count) is int and active_count >= 0
        and type(imminent_count) is int and imminent_count >= 0
        and (active_count == 0) == (not active)
        and (imminent_count == 0) == (not imminent)
    )
    if (block_value is not None and block is None
            or blocked_now_value is not None and blocked_now is None
            or hydrawise is None or safety is None or release is None
            or not relay_lists_valid or not counts_valid
            or safety.get("available") is not True or safety.get("fresh") is not True
            or safety.get("relay_set_valid") is not True or safety.get("clear_now") is not True
            or release.get("allowed") is not True):
        return "water-safety-unavailable", "WATER_SAFETY_UNAVAILABLE"
    if str(details.get("decision", {}).get("hypothetical_command") if isinstance(details.get("decision"), dict) else "").upper() == "PARK":
        return "decision-park|" + str(result.decision_code), "PARK_DECISION"
    return None


def _guard_request_id(guard_key: str) -> str:
    return "guard-" + hashlib.sha256(guard_key.encode("utf-8")).hexdigest()[:48]


def _guard_base(guard_key: str) -> str:
    return guard_key.split("|reassert|", 1)[0]


def _mower_observed_at(mower: Mapping[str, Any]) -> datetime | None:
    try:
        return datetime.fromtimestamp(float(mower.get("status_timestamp_ms")) / 1000, timezone.utc)
    except (KeyError, TypeError, ValueError, OverflowError, OSError):
        return None


def _station_held(mower: Mapping[str, Any], now: datetime, *, expected_mower_id: str = "") -> bool:
    return (
        _mower_age_fresh(mower, now)
        and bool(expected_mower_id)
        and str(mower.get("mower_id") or "") == expected_mower_id
        and str(mower.get("activity") or "").upper() in {"CHARGING", "PARKED_IN_CS"}
        and str(mower.get("override_action") or "").upper() == "FORCE_PARK"
    )


def _guard_reassert_key(
    guard_key: str, entries: list[dict[str, Any]], mower: Mapping[str, Any], *, expected_mower_id: str = "",
) -> str | None:
    """Give a renewed departure a new deterministic guard request identity."""
    observed = _mower_observed_at(mower)
    activity = str(mower.get("activity") or "").upper()
    state = str(mower.get("state") or "").upper()
    if (observed is None or state in {"PAUSED", "STOPPED"}
            or activity == "STOPPED_IN_GARDEN"
            or _station_held(mower, observed, expected_mower_id=expected_mower_id)):
        return None
    matching = [
        entry for entry in entries
        if entry.get("origin") == "SAFETY_GUARD"
        and _guard_base(str(entry.get("guard_key") or "")) == _guard_base(guard_key)
        and entry.get("status") in {"CONFIRMED", "REJECTED", "EXPIRED"}
    ]
    if not matching:
        return None
    anchors: list[datetime] = []
    for entry in matching:
        try:
            anchors.append(_parse_time(entry.get("confirmed_at") or entry["requested_at"]))
        except (KeyError, TypeError, ValueError):
            continue
    if not anchors or observed < max(anchors) + GUARD_REASSERT_COOLDOWN:
        return None
    return f"{_guard_base(guard_key)}|reassert|{int(observed.timestamp() * 1000)}"


def _guard_status(settings: RuntimeSettings, result: CycleResult, mower: Mapping[str, Any]) -> tuple[str | None, str | None]:
    if not settings.enable_operator_safety_guard:
        return None, "DISABLED"
    if not settings.operator_control_gate_enabled or not settings.enable_park_commands:
        return None, "PARK_GATE_LOCKED"
    signal = _guard_signal(result, mower)
    return signal if signal is not None else (None, "NO_PROTECTIVE_EVIDENCE")


def _save_entries(store: StateStore, state: AutomationState, entries: list[dict[str, Any]], **state_changes: Any) -> AutomationState:
    updated = replace(state, revision=state.revision + 1, operator_commands_json=_dump_journal(entries), **state_changes)
    store.save(updated, expected_revision=state.revision)
    return updated


def _mower_age_fresh(mower: Mapping[str, Any], now: datetime, maximum: int = 180) -> bool:
    try:
        stamp = datetime.fromtimestamp(float(mower.get("status_timestamp_ms")) / 1000, timezone.utc)
    except (KeyError, TypeError, ValueError, OverflowError, OSError):
        return False
    return mower.get("connected") is True and -30 <= (now - stamp).total_seconds() <= maximum


def _target_area(mower: Mapping[str, Any]) -> dict[str, Any] | None:
    """Return the one explicitly named field area, never a generic/global one."""
    target = mower.get("target_work_area")
    areas = mower.get("work_areas")
    if isinstance(areas, list):
        named = [item for item in areas if isinstance(item, dict) and str(item.get("name") or "").casefold() == "rasenfläche"]
        if len(named) != 1:
            return None
        selected = dict(named[0])
        if isinstance(target, dict) and str(target.get("id") or "") != str(selected.get("id") or ""):
            return None
        return selected
    if isinstance(target, dict) and str(target.get("name") or "").casefold() == "rasenfläche":
        return dict(target)
    return None


def read_operator_mower(environment: Mapping[str, str]) -> dict[str, Any]:
    """Direct mower GET for a broken plan/water reader; no command endpoint."""
    from mower.husqvarna import fetch_mowers, parse_snapshot
    client_id = str(environment.get("HUSQVARNA_CLIENT_ID") or "")
    client_secret = str(environment.get("HUSQVARNA_CLIENT_SECRET") or "")
    configured_id = str(environment.get("HUSQVARNA_MOWER_ID") or "").strip()
    if not configured_id:
        raise OperatorControlError("MOWER_TARGET_UNCONFIGURED")
    snapshots = [parse_snapshot(item).to_dict() for item in fetch_mowers(client_id, client_secret)]
    found = [item for item in snapshots if str(item.get("mower_id") or "") == configured_id]
    if len(found) != 1:
        raise OperatorControlError("MOWER_TARGET_UNAVAILABLE")
    mower = found[0]
    area = _target_area(mower)
    if area is not None:
        mower["target_work_area"] = area
    return mower


def _result_mower(result: CycleResult) -> dict[str, Any]:
    mower = result.details.get("mower") if isinstance(result.details, dict) else None
    return dict(mower) if isinstance(mower, dict) else {}


def _fresh_station_confirmation(entry: Mapping[str, Any], mower: Mapping[str, Any], now: datetime) -> bool:
    try:
        dispatched_at = _parse_time(entry.get("sent_at") or entry["reserved_at"])
        observed = datetime.fromtimestamp(float(mower.get("status_timestamp_ms")) / 1000, timezone.utc)
    except (KeyError, TypeError, ValueError, OSError):
        return False
    return (
        _mower_age_fresh(mower, now)
        and str(mower.get("mower_id") or "") == str(entry.get("mower_id") or "")
        and observed > dispatched_at
        and str(mower.get("activity") or "").upper() in {"PARKED_IN_CS", "CHARGING"}
        and str(mower.get("override_action") or "").upper() == "FORCE_PARK"
    )


def _height_confirmation(entry: Mapping[str, Any], mower: Mapping[str, Any], now: datetime) -> bool:
    if not _mower_age_fresh(mower, now):
        return False
    try:
        observed = datetime.fromtimestamp(float(mower.get("status_timestamp_ms")) / 1000, timezone.utc)
        expected = cutting_height_mm_to_percent(int(entry["target_mm"]))
        dispatched_at = _parse_time(entry.get("sent_at") or entry["reserved_at"])
    except (KeyError, TypeError, ValueError, OSError):
        return False
    area = _target_area(mower)
    try:
        area_id = int(area.get("id")) if area is not None else 0
        entry_area_id = int(entry.get("area_id"))
        observed_height = int(area.get("cutting_height_percent")) if area is not None else -1
    except (TypeError, ValueError):
        return False
    return (
        observed > dispatched_at
        and area is not None
        and area.get("enabled") is True
        and str(mower.get("mower_id") or "") == str(entry.get("mower_id") or "")
        and area_id == entry_area_id
        and area.get("use_global_cutting_height") is False
        and observed_height == expected
    )


def _valid_height_area(area: Mapping[str, Any] | None) -> bool:
    if area is None or area.get("enabled") is not True or area.get("use_global_cutting_height") is not False:
        return False
    try:
        return int(area.get("id")) > 0
    except (TypeError, ValueError):
        return False


def _confirm_or_timeout(entries: list[dict[str, Any]], mower: Mapping[str, Any], now: datetime) -> list[dict[str, Any]]:
    updated = entries
    for entry in entries:
        if entry["status"] not in {"RESERVED", "SENT_UNCONFIRMED", "UNKNOWN"}:
            continue
        confirmed = _fresh_station_confirmation(entry, mower, now) if entry["action"] == "PARK_MOWER" else _height_confirmation(entry, mower, now)
        if confirmed:
            updated = _replace_entry(updated, entry["request_id"], status="CONFIRMED", confirmed_at=now.isoformat(), message_code="CONFIRMED")
        elif entry["status"] != "UNKNOWN":
            anchor = entry.get("sent_at") or entry.get("reserved_at")
            if anchor is not None and now >= _parse_time(anchor) + CONFIRMATION_TIMEOUT:
                updated = _replace_entry(updated, entry["request_id"], status="UNKNOWN", message_code="CONFIRMATION_TIMEOUT")
    return updated


def _next_dispatch(entries: list[dict[str, Any]], now: datetime) -> dict[str, Any] | None:
    park_in_flight = next(
        (entry for entry in entries if entry["action"] == "PARK_MOWER" and entry["status"] not in {"CONFIRMED", "REJECTED", "EXPIRED"}),
        None,
    )
    # Never let a queued or unconfirmed height update overtake a protective
    # park. An uncertain park also remains non-replayable and must be resolved
    # before another device write is attempted.
    if park_in_flight is not None and park_in_flight["status"] != "QUEUED":
        # UNKNOWN remains physically unresolved. It blocks height changes,
        # although a later explicitly queued PARK may still be selected below.
        queued_park = next(
            (entry for entry in entries if entry["action"] == "PARK_MOWER" and entry["status"] == "QUEUED"),
            None,
        )
        if queued_park is not None:
            return queued_park
        return None
    candidates = []
    for entry in entries:
        if entry["status"] == "QUEUED" and now < _parse_time(entry["expires_at"]):
            candidates.append(entry)
    for action in ("PARK_MOWER", "SET_CUTTING_HEIGHT"):
        found = next((entry for entry in candidates if entry["action"] == action), None)
        if found is not None:
            return found
    return None


def run_operator_cycle(
    *, now_utc: datetime, settings: RuntimeSettings, environment: Mapping[str, str], past_due: bool,
    source: str, read_only_runner: Callable[..., CycleResult] | None = None,
    state_store_factory: Callable[[Mapping[str, str]], StateStore] = AzureTableStateStore.from_environment,
    mower_reader: Callable[[Mapping[str, str]], dict[str, Any]] = read_operator_mower,
    park_sender: Callable[..., dict[str, Any]] | None = None,
    cutting_height_sender: Callable[..., dict[str, Any]] | None = None,
    command_clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
) -> CycleResult:
    """Observe first, then execute at most one newly queued manual command."""
    now = _utc(now_utc)
    if settings.control_mode is not ControlMode.OPERATOR_ONLY or not settings.enable_live_reads:
        raise RuntimeError("run_operator_cycle benötigt OPERATOR_ONLY mit ENABLE_LIVE_READS=true.")
    if read_only_runner is None:
        from mower.dry_run import run_read_only_cycle
        read_only_runner = run_read_only_cycle
    try:
        # The reader stays command-free and keeps the DRY_RUN water observation chain.
        result = read_only_runner(now_utc=now, settings=replace(settings, control_mode=ControlMode.DRY_RUN), environment=environment, past_due=past_due, source=source)
        mower = _result_mower(result)
    except Exception:
        mower = mower_reader(environment)
        result = CycleResult(2, now.isoformat(), source, ControlMode.OPERATOR_ONLY.value, bool(past_due), "OPERATOR_READ_FALLBACK", False, "OPERATOR_READ_FALLBACK", {"mower": mower, "safety": {"read_only": True, "command_sent": False}})
    store = state_store_factory(environment)
    state = store.load()
    entries = _load_journal(state)
    # Expire never-dispatched requests. A reservation can reflect a lost
    # response, so it remains confirmable from a later fresh observation.
    normalized = entries
    for entry in entries:
        if entry["status"] == "QUEUED" and now >= _parse_time(entry["expires_at"]):
            normalized = _replace_entry(normalized, entry["request_id"], status="EXPIRED", message_code="REQUEST_EXPIRED")
    normalized = _confirm_or_timeout(normalized, mower, now)
    if normalized != entries:
        state = _save_entries(store, state, normalized)
        entries = normalized
    configured_mower_id = str(environment.get("HUSQVARNA_MOWER_ID") or "").strip()
    guard_key, guard_reason = _guard_status(settings, result, mower)
    station_held = bool(guard_key) and _station_held(
        mower, now, expected_mower_id=configured_mower_id,
    )
    guard_details: dict[str, Any] = {
        "enabled": settings.enable_operator_safety_guard,
        # This is only physical proof from the current fresh mower snapshot;
        # a queued/reserved command is intentionally reported separately.
        "protected": station_held,
        "reason": guard_reason,
        "requestPending": False,
        "requestStatus": None,
    }
    if guard_key is not None:
        existing_park = [entry for entry in entries if entry["action"] == "PARK_MOWER"]
        existing_unknown = any(entry["status"] == "UNKNOWN" for entry in existing_park)
        existing_pending = any(entry["status"] not in TERMINAL for entry in existing_park)
        guard_history = [
            entry for entry in existing_park
            if entry.get("origin") == "SAFETY_GUARD"
            and _guard_base(str(entry.get("guard_key") or "")) == _guard_base(guard_key)
        ]
        guard_details["reason"] = guard_reason
        if station_held:
            # A current FORCE_PARK observation proves the physical hold.  Do
            # not issue a duplicate Park command merely because the calendar
            # or water block still exists.
            queued_guard = next((entry for entry in guard_history if entry["status"] == "QUEUED"), None)
            if queued_guard is not None:
                entries = _replace_entry(
                    entries, queued_guard["request_id"], status="CONFIRMED",
                    confirmed_at=now.isoformat(), message_code="ALREADY_FORCE_PARK",
                )
                state = _save_entries(store, state, entries)
        elif existing_unknown:
            guard_details.update(requestPending=True, requestStatus="UNKNOWN", reason="UNKNOWN_PARK_NOT_REPLAYED")
        elif existing_pending:
            pending = next(entry for entry in existing_park if entry["status"] not in TERMINAL)
            guard_details.update(requestPending=True, requestStatus=pending["status"], reason="EXISTING_PARK_REQUEST")
        else:
            reassert_key = _guard_reassert_key(
                guard_key, entries, mower, expected_mower_id=configured_mower_id,
            )
            if guard_history and reassert_key is None:
                guard_details["reason"] = "EPISODE_ALREADY_HANDLED"
            else:
                queued_guard_key = reassert_key or guard_key
                configured_id = configured_mower_id
                observed_id = str(mower.get("mower_id") or "").strip()
                if not configured_id or observed_id != configured_id or mower.get("connected") is not True:
                    guard_details["reason"] = "MOWER_TARGET_UNAVAILABLE"
                else:
                    try:
                        queue_operator_action(
                            store, settings, "PARK_MOWER", _guard_request_id(queued_guard_key), now,
                            mower_id=configured_id, origin="SAFETY_GUARD", guard_key=queued_guard_key,
                        )
                        state = store.load()
                        entries = _load_journal(state)
                        guard_details.update(requestPending=True, requestStatus="QUEUED", reason=guard_reason)
                    except (OperatorControlError, StateConflictError):
                        guard_details["reason"] = "GUARD_RESERVATION_FAILED"
    result = replace(result, details={**result.details, "operatorSafetyGuard": guard_details})
    entry = _next_dispatch(entries, now)
    if entry is None:
        return replace(result, control_mode=ControlMode.OPERATOR_ONLY.value, details={**result.details, "operatorCommands": operator_commands_payload(state)})
    if entry.get("origin") == "SAFETY_GUARD" and (
        str(mower.get("state") or "").upper() in {"PAUSED", "STOPPED"}
        or str(mower.get("activity") or "").upper() == "STOPPED_IN_GARDEN"
    ):
        entries = _replace_entry(
            entries, entry["request_id"], status="REJECTED",
            message_code="MANUAL_PAUSE_RESPECTED",
        )
        state = _save_entries(store, state, entries)
        details = {**result.details, "operatorCommands": operator_commands_payload(state)}
        details["operatorSafetyGuard"] = {
            **guard_details, "protected": False, "requestPending": False,
            "requestStatus": "REJECTED", "reason": "MANUAL_PAUSE_RESPECTED",
        }
        return replace(result, control_mode=ControlMode.OPERATOR_ONLY.value, details=details)
    if entry.get("origin") == "SAFETY_GUARD":
        current_signal = _guard_signal(result, mower)
        live_guard_key = str(entry.get("guard_key") or "")
        disabled = (not settings.enable_operator_safety_guard
                    or not settings.operator_control_gate_enabled
                    or not settings.enable_park_commands)
        cleared = (current_signal is None
                   or _guard_base(current_signal[0]) != _guard_base(live_guard_key))
        current_station_held = _station_held(
            mower, now, expected_mower_id=configured_mower_id,
        )
        if disabled or cleared or current_station_held:
            status = "CONFIRMED" if current_station_held and not disabled else "REJECTED"
            message = (
                "ALREADY_FORCE_PARK" if status == "CONFIRMED" else
                "SAFETY_GUARD_REVOKED" if disabled else "SAFETY_CONDITION_CLEARED"
            )
            changes: dict[str, Any] = {"status": status, "message_code": message}
            if status == "CONFIRMED":
                changes["confirmed_at"] = now.isoformat()
            entries = _replace_entry(entries, entry["request_id"], **changes)
            state = _save_entries(store, state, entries)
            details = {**result.details, "operatorCommands": operator_commands_payload(state)}
            details["operatorSafetyGuard"] = {
                **guard_details, "protected": status == "CONFIRMED",
                "requestPending": False, "requestStatus": status, "reason": message,
            }
            return replace(result, control_mode=ControlMode.OPERATOR_ONLY.value, details=details)
    caps = action_capabilities(settings)
    if caps[entry["action"]]["available"] is not True:
        return replace(result, control_mode=ControlMode.OPERATOR_ONLY.value, details={**result.details, "operatorCommands": operator_commands_payload(state)})
    mower_id = str(mower.get("mower_id") or "").strip()
    requested_mower_id = str(entry.get("mower_id") or "").strip()
    if entry["action"] == "PARK_MOWER":
        # A device error is a reason to park, not a reason to suppress a
        # protective manual park.  The configured exact target remains bound.
        safe = (bool(configured_mower_id) and mower_id == configured_mower_id == requested_mower_id
                and mower.get("connected") is True)
    else:
        area = _target_area(mower)
        safe = (_mower_age_fresh(mower, now) and bool(configured_mower_id) and mower_id == configured_mower_id == requested_mower_id and int(mower.get("error_code") or 0) == 0
                and supports_metric_cutting_height(mower.get("model")) and _valid_height_area(area))
    if not safe:
        return replace(result, control_mode=ControlMode.OPERATOR_ONLY.value, details={**result.details, "operatorCommands": operator_commands_payload(state)})
    reserved_at = _utc(command_clock()).isoformat()
    reservation_binding = {"mower_id": mower_id}
    if entry["action"] == "SET_CUTTING_HEIGHT":
        reservation_binding["area_id"] = int((_target_area(mower) or {})["id"])
    reserved_entries = _replace_entry(entries, entry["request_id"], status="RESERVED", reserved_at=reserved_at, message_code="RESERVED", **reservation_binding)
    state_changes: dict[str, Any] = {}
    if entry["action"] == "PARK_MOWER":
        state_changes = {"parked_by_automation": True, "automation_park_source": "operator", "automation_restart_allowed": False, "park_command_sent_utc": reserved_at, "continuous_mowing_owned": False, "continuous_mowing_window_end_utc": None}
    state = _save_entries(store, state, reserved_entries, **state_changes)
    reservation_revision = state.revision
    transport_started = False

    def before_send() -> None:
        nonlocal transport_started
        latest = store.load()
        latest_entries = _load_journal(latest)
        live = next((item for item in latest_entries if item["request_id"] == entry["request_id"]), None)
        if live is None or live.get("status") != "RESERVED" or live.get("reserved_at") != reserved_at:
            raise OperatorControlError("RESERVATION_LOST")
        if latest.revision != reservation_revision:
            raise OperatorControlError("RESERVATION_LOST")
        if _utc(command_clock()) >= _parse_time(live["expires_at"]):
            raise OperatorControlError("REQUEST_EXPIRED")
        if action_capabilities(settings)[entry["action"]]["available"] is not True:
            raise OperatorControlError("CAPABILITY_REVOKED")
        if str(environment.get("HUSQVARNA_MOWER_ID") or "").strip() != requested_mower_id:
            raise OperatorControlError("MOWER_TARGET_CHANGED")
        if entry.get("origin") == "SAFETY_GUARD":
            current_signal = _guard_signal(result, mower)
            if (not settings.enable_operator_safety_guard
                    or current_signal is None
                    or _guard_base(current_signal[0]) != _guard_base(str(entry.get("guard_key") or ""))
                    or _station_held(
                        mower, _utc(command_clock()), expected_mower_id=requested_mower_id,
                    )):
                raise OperatorControlError("SAFETY_CONDITION_CLEARED")
        transport_started = True

    try:
        if entry["action"] == "PARK_MOWER":
            if park_sender is None:
                from mower.husqvarna_actions import park_until_further_notice
                park_sender = park_until_further_notice
            response = park_sender(str(environment.get("HUSQVARNA_CLIENT_ID") or ""), str(environment.get("HUSQVARNA_CLIENT_SECRET") or ""), mower_id, before_send=before_send)
        else:
            if cutting_height_sender is None:
                from mower.husqvarna_cutting_height_actions import set_work_area_cutting_height
                cutting_height_sender = set_work_area_cutting_height
            area = _target_area(mower) or {}
            response = cutting_height_sender(str(environment.get("HUSQVARNA_CLIENT_ID") or ""), str(environment.get("HUSQVARNA_CLIENT_SECRET") or ""), mower_id, int(area["id"]), cutting_height_mm_to_percent(int(entry["target_mm"])), before_send=before_send)
    except Exception:
        # OAuth/request construction failures before the callback cannot have
        # reached the device. Resolve them so they neither wait ten minutes nor
        # become an uncertain command. Once the guarded callback ran, retain
        # the reservation: transport may have started and must never replay.
        if not transport_started:
            try:
                latest = store.load()
                latest_entries = _load_journal(latest)
                live = next((item for item in latest_entries if item["request_id"] == entry["request_id"]), None)
                if live is not None and live.get("status") == "RESERVED" and live.get("reserved_at") == reserved_at:
                    expired = _utc(command_clock()) >= _parse_time(live["expires_at"])
                    rejected = _replace_entry(
                        latest_entries, entry["request_id"],
                        status="EXPIRED" if expired else "REJECTED",
                        message_code="REQUEST_EXPIRED" if expired else "TRANSPORT_NOT_STARTED",
                    )
                    state = _save_entries(store, latest, rejected)
            except (OperatorControlError, StateConflictError, ValueError):
                pass
        return replace(result, control_mode=ControlMode.OPERATOR_ONLY.value, details={**result.details, "operatorCommands": operator_commands_payload(state)})
    latest = store.load()
    latest_entries = _load_journal(latest)
    live = next((item for item in latest_entries if item["request_id"] == entry["request_id"]), None)
    if live is None or live.get("status") != "RESERVED" or live.get("reserved_at") != reserved_at:
        return replace(result, control_mode=ControlMode.OPERATOR_ONLY.value, details={**result.details, "operatorCommands": operator_commands_payload(latest)})
    sent_at = _utc(command_clock()).isoformat()
    sent_entries = _replace_entry(latest_entries, entry["request_id"], status="SENT_UNCONFIRMED", sent_at=sent_at, message_code="SENT_UNCONFIRMED")
    state = _save_entries(store, latest, sent_entries)
    details = {**result.details, "operatorCommands": operator_commands_payload(state), "operatorAction": {"action": entry["action"], "response": response}}
    return replace(result, control_mode=ControlMode.OPERATOR_ONLY.value, command_sent=True, decision_code="OPERATOR_ACTION_SENT", details=details)
