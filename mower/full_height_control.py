"""Durable FULL_FAILSAFE cutting-height transport and observation confirmation.

HTTP acceptance is intentionally not a height confirmation.  The caller keeps
the legacy request pending until this helper sees a later fresh mower snapshot
for the configured mower and exact Rasenfläche with the requested percentage.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable, Mapping

from mower.cutting_height import MAXIMUM_MM, MINIMUM_MM, cutting_height_mm_to_percent, supports_metric_cutting_height
from mower.device_send_guard import DeviceSendBlocked, UNRESOLVED, _with_entries, dispatch_device_send, load_device_send_journal
from mower.runtime import ControlMode


@dataclass(frozen=True)
class FullHeightControlResult:
    state: Any
    status: str
    reason_code: str
    command_sent: bool
    target_mm: int | None = None
    target_percent: int | None = None
    area_id: int | None = None
    response: Any = None


CONFIRMATION_TIMEOUT_SECONDS = 10 * 60


def _utc(value: datetime) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("COMMAND_CLOCK_INVALID")
    return value.astimezone(timezone.utc)


def _target_area(mower: Mapping[str, Any]) -> dict[str, Any] | None:
    areas = mower.get("work_areas")
    selected = [
        dict(area) for area in areas if isinstance(area, Mapping)
        and str(area.get("name") or "").casefold() == "rasenfläche"
    ] if isinstance(areas, list) else []
    if len(selected) != 1:
        return None
    target = mower.get("target_work_area")
    if isinstance(target, Mapping) and str(target.get("id") or "") != str(selected[0].get("id") or ""):
        return None
    return selected[0]


def _fresh(mower: Mapping[str, Any], now: datetime) -> tuple[datetime, bool]:
    try:
        observed = datetime.fromtimestamp(float(mower.get("status_timestamp_ms")) / 1000, timezone.utc)
    except (TypeError, ValueError, OverflowError, OSError):
        return now, False
    return observed, mower.get("connected") is True and -30 <= (now - observed).total_seconds() <= 180


def _request(state) -> tuple[str, int] | None:
    request_id = str(getattr(state, "operator_request_id", "") or "").strip()
    target = getattr(state, "operator_request_cutting_height_mm", None)
    if (
        not request_id
        or getattr(state, "operator_request_action", None) != "SET_CUTTING_HEIGHT"
        or getattr(state, "operator_request_status", None) not in {"PENDING", "RESERVED"}
        or type(target) is not int
        or not MINIMUM_MM <= target <= MAXIMUM_MM
    ):
        return None
    return request_id, target


def _admission(
    state, mower: Mapping[str, Any], environment: Mapping[str, str], settings, now: datetime,
) -> tuple[str, int, int, int] | None:
    request = _request(state)
    configured = str(environment.get("HUSQVARNA_MOWER_ID") or "").strip()
    area = _target_area(mower)
    observed, fresh = _fresh(mower, now)
    if (
        request is None or not configured or not fresh
        or str(mower.get("mower_id") or "").strip() != configured
        or type(mower.get("error_code")) is not int or mower.get("error_code") != 0
        or not supports_metric_cutting_height(mower.get("model"))
        or area is None or area.get("enabled") is not True or area.get("use_global_cutting_height") is not False
    ):
        return None
    try:
        area_id = int(area.get("id"))
    except (TypeError, ValueError):
        return None
    if area_id <= 0:
        return None
    request_id, target_mm = request
    return request_id, target_mm, area_id, cutting_height_mm_to_percent(target_mm)


def _height_evidence(
    mower: Mapping[str, Any], *, configured_mower_id: str, area_id: int, target_percent: int, now: datetime,
    dispatched_at: datetime,
) -> str | None:
    observed, fresh = _fresh(mower, now)
    area = _target_area(mower)
    try:
        observed_percent = int(area.get("cutting_height_percent")) if area is not None else -1
        observed_area_id = int(area.get("id")) if area is not None else 0
    except (TypeError, ValueError):
        return None
    if not (
        fresh and observed > dispatched_at
        and str(mower.get("mower_id") or "").strip() == configured_mower_id
        and area is not None and area.get("enabled") is True and area.get("use_global_cutting_height") is False
        and observed_area_id == area_id and observed_percent == target_percent
    ):
        return None
    return f"mower:{configured_mower_id}:area:{area_id}:height:{target_percent}:observed:{observed.isoformat()}"


def _persist(store, original, before, after) -> Any:
    """Persist a cycle projection against the actual stored predecessor.

    The full-failsafe reader may already have projected ``before`` to
    ``original.revision + 1`` without saving it.  Its CAS predecessor remains
    ``original``; saving against the projection would always conflict.
    """
    if after is before:
        return before
    store.save(after, expected_revision=original.revision)
    return after


def _entry_target(entry: Mapping[str, Any]) -> tuple[str, int, int] | None:
    try:
        mower_id, raw_area, raw_percent = str(entry.get("target") or "").rsplit(":", 2)
        area_id, percent = int(raw_area), int(raw_percent)
    except (TypeError, ValueError):
        return None
    return (mower_id, area_id, percent) if mower_id and area_id > 0 and 0 <= percent <= 100 else None


def reconcile_full_height_sends(state, mower: Mapping[str, Any], environment: Mapping[str, str], now_utc: datetime):
    """Purely project fresh HEIGHT evidence or a ten-minute uncertain outcome.

    This deliberately scans all persisted HEIGHT records, even if the legacy
    request was superseded by a later PARK.  It never calls a sender and must
    be persisted by the caller with the cycle's original CAS revision.
    """
    now = _utc(now_utc)
    configured = str(environment.get("HUSQVARNA_MOWER_ID") or "").strip()
    entries = load_device_send_journal(state)
    changed = False
    for entry in entries:
        if entry["kind"] != "HEIGHT" or entry["status"] not in UNRESOLVED:
            continue
        target = _entry_target(entry)
        sent_at = entry.get("dispatched_at_utc")
        dispatched = None
        if isinstance(sent_at, str):
            try:
                dispatched = _utc(datetime.fromisoformat(sent_at.replace("Z", "+00:00")))
            except (ValueError, TypeError, OverflowError, OSError):
                dispatched = None
        if target is not None and dispatched is not None:
            mower_id, area_id, percent = target
            evidence = _height_evidence(
                mower, configured_mower_id=configured, area_id=area_id,
                target_percent=percent, now=now, dispatched_at=dispatched,
            ) if mower_id == configured else None
            if evidence:
                entry.update(status="CONFIRMED", confirmed_at_utc=now.isoformat(), evidence=evidence[:512])
                changed = True
                continue
        anchor = dispatched
        if anchor is None:
            try:
                anchor = _utc(datetime.fromisoformat(entry["reserved_at_utc"].replace("Z", "+00:00")))
            except (KeyError, TypeError, ValueError, OverflowError, OSError):
                continue
        if (now - anchor).total_seconds() >= CONFIRMATION_TIMEOUT_SECONDS:
            entry.update(status="UNKNOWN", message_code="HEIGHT_CONFIRMATION_TIMEOUT")
            changed = True
    return _with_entries(state, entries) if changed else state


def run_full_height_control(
    *, store, original, state, mower: Mapping[str, Any], environment: Mapping[str, str], settings,
    now_utc: datetime, clock: Callable[[], datetime], sender,
) -> FullHeightControlResult:
    """Dispatch one legacy FULL_FAILSAFE height request, then await observation.

    The helper has no planning role.  It is opt-in for manual-session builds;
    callers retain the historical path when that feature flag is false.
    """
    now = _utc(now_utc)
    if (
        getattr(settings, "control_mode", None) is not ControlMode.FULL_FAILSAFE
        or getattr(settings, "enable_live_reads", False) is not True
        or getattr(settings, "full_failsafe_write_gate_enabled", False) is not True
        or getattr(settings, "enable_manual_sessions", False) is not True
    ):
        return FullHeightControlResult(state, "BLOCKED", "CUTTING_HEIGHT_GATE_LOCKED", False)
    projected = reconcile_full_height_sends(state, mower, environment, now)
    if projected is not state:
        try:
            state = _persist(store, original, state, projected)
        except Exception:
            return FullHeightControlResult(state, "UNKNOWN", "HEIGHT_CONFIRMATION_PERSIST_FAILED", False)
    admitted = _admission(state, mower, environment, settings, now)
    if admitted is None:
        return FullHeightControlResult(state, "REJECTED", "CUTTING_HEIGHT_INPUT_UNSAFE", False)
    request_id, target_mm, area_id, target_percent = admitted
    mower_id = str(environment.get("HUSQVARNA_MOWER_ID") or "").strip()
    target = f"{mower_id}:{area_id}:{target_percent}"
    intent_key = f"full-height:{request_id}:{target}"
    entries = load_device_send_journal(state)
    matching = next((entry for entry in reversed(entries) if entry["intent_key"] == intent_key), None)
    if matching is not None:
        if matching["status"] == "CONFIRMED":
            return FullHeightControlResult(state, "CONFIRMED", "HEIGHT_CONFIRMED", False, target_mm, target_percent, area_id)
        if matching["status"] == "REJECTED":
            return FullHeightControlResult(state, "REJECTED", str(matching.get("message_code") or "HEIGHT_REJECTED"), False, target_mm, target_percent, area_id)
        sent_at = matching.get("dispatched_at_utc")
        if sent_at:
            try:
                dispatched = _utc(datetime.fromisoformat(sent_at.replace("Z", "+00:00")))
            except (TypeError, ValueError, OverflowError, OSError):
                return FullHeightControlResult(state, "UNKNOWN", "DEVICE_JOURNAL_INVALID", False, target_mm, target_percent, area_id)
            evidence = _height_evidence(
                mower, configured_mower_id=mower_id, area_id=area_id, target_percent=target_percent,
                now=now, dispatched_at=dispatched,
            )
            if evidence:
                # The all-record projection above already persisted this exact
                # evidence. Reaching this branch means a malformed concurrent
                # caller; leave it unresolved rather than inventing a result.
                return FullHeightControlResult(state, "UNKNOWN", "HEIGHT_CONFIRMATION_PERSIST_FAILED", False, target_mm, target_percent, area_id)
        status = "UNKNOWN" if matching["status"] == "UNKNOWN" else "SENT_UNCONFIRMED"
        return FullHeightControlResult(state, status, "HEIGHT_CONFIRMATION_PENDING", False, target_mm, target_percent, area_id)

    def before_send_check(latest, at: datetime) -> None:
        # Re-evaluate the request, configured target and snapshot freshness
        # after OAuth. A new manual PARK changes device_guard's binding too.
        if _admission(latest, mower, environment, settings, at) != admitted:
            raise DeviceSendBlocked("CUTTING_HEIGHT_RESERVATION_REVOKED")

    try:
        sent = dispatch_device_send(
            store=store, original=original, state=state, sender=sender,
            args=(str(environment.get("HUSQVARNA_CLIENT_ID") or ""), str(environment.get("HUSQVARNA_CLIENT_SECRET") or ""), mower_id, area_id, target_percent),
            kind="HEIGHT", target=target, intent_key=intent_key, now_utc=now, clock=clock,
            before_send_check=before_send_check,
        )
    except DeviceSendBlocked as exc:
        current = exc.state if exc.state is not None else state
        status = "UNKNOWN" if exc.transport_started or exc.code == "HEIGHT_OUTCOME_UNCONFIRMED" else "REJECTED"
        return FullHeightControlResult(current, status, exc.code, exc.transport_started, target_mm, target_percent, area_id)
    return FullHeightControlResult(sent.state, "SENT_UNCONFIRMED", "HEIGHT_SENT_UNCONFIRMED", sent.sent, target_mm, target_percent, area_id, sent.response)
