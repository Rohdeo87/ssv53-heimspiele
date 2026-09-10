"""Last-moment, fail-closed fence for an already reserved mower START.

The controller has already made the scheduling decision and persisted a START
reservation before this guard runs.  The guard intentionally does not make a
new release decision: it only proves that the same reservation, its source
observations and its bounded window still exist immediately before HTTP is
sent.  A rejection leaves the reservation in place because no device outcome
can be inferred from a previous or concurrent request.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timezone
from typing import Any, Mapping

from mower.device_send_guard import DeviceSendBlocked, unresolved_device_sends
from mower.manual_session import load_manual_session, start_dispatch_permission


Clock = Callable[[], datetime]


class StartDispatchBlocked(RuntimeError):
    """The request has not reached the mower action endpoint."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise StartDispatchBlocked("COMMAND_CLOCK_INVALID")
    return value.astimezone(timezone.utc)


def _parse_utc(value: object) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        return None
    return parsed.astimezone(timezone.utc)


def _mower_observation_fresh(
    mower: Mapping[str, Any], *, now_utc: datetime, max_age_seconds: int
) -> bool:
    try:
        observed = datetime.fromtimestamp(
            float(mower.get("status_timestamp_ms")) / 1000,
            tz=timezone.utc,
        )
    except (TypeError, ValueError, OSError):
        return False
    age = (now_utc - observed).total_seconds()
    return mower.get("connected") is True and -60 <= age <= max_age_seconds


def _hydrawise_observation_fresh(
    safety: Mapping[str, Any], *, now_utc: datetime, max_age_seconds: int,
) -> bool:
    observed = _parse_utc(safety.get("observed_at_utc"))
    if observed is None:
        return False
    age = (now_utc - observed).total_seconds()
    try:
        active_zone_count = int(safety.get("active_zone_count") or 0)
    except (TypeError, ValueError):
        return False
    safe_observation = (
        safety.get("available") is True
        and safety.get("fresh") is True
        and safety.get("relay_set_valid") is True
        and active_zone_count == 0
        and -60 <= age <= max_age_seconds
    )
    if not safe_observation:
        return False
    return safety.get("clear_now") is True


def _same_reservation(current: Any, reserved: Any) -> bool:
    return (
        current.revision == reserved.revision
        and current.last_command_fingerprint == reserved.last_command_fingerprint
        and current.last_command_utc == reserved.last_command_utc
        and current.mower_start_pending_since_utc
        == reserved.mower_start_pending_since_utc
        and current.mower_start_pending_deadline_utc
        == reserved.mower_start_pending_deadline_utc
        and current.mower_start_pending_session_id
        == reserved.mower_start_pending_session_id
        and current.mower_start_pending_session_epoch
        == reserved.mower_start_pending_session_epoch
    )


def prepare_start_dispatch(
    *,
    clock: Clock,
    store: Any,
    reserved: Any,
    mower: Mapping[str, Any],
    hydrawise_safety: Mapping[str, Any],
    safe_command_deadline_utc: datetime,
    command_end_utc: datetime,
    requested_duration_minutes: int,
    mower_status_max_age_seconds: int,
    hydrawise_status_max_age_seconds: int,
    manual_session_id: str | None = None,
    manual_session_epoch: int | None = None,
) -> int:
    """Return the still safe whole-minute duration or block before POST.

    This is called twice for the production sender: once before invoking a
    legacy-compatible sender wrapper and once after OAuth, immediately before
    the action HTTP request.  The latter decides the encoded duration.
    """

    deadline = min(
        _utc(safe_command_deadline_utc),
        _utc(command_end_utc),
    )
    try:
        current = store.load()
    except Exception as exc:
        raise StartDispatchBlocked("START_RESERVATION_UNAVAILABLE") from exc
    # The remote state read can itself block. Take the final clock sample only
    # after it returns so neither duration nor telemetry freshness uses the
    # pre-read instant.
    try:
        now = _utc(clock())
    except StartDispatchBlocked:
        raise
    except Exception as exc:
        raise StartDispatchBlocked("COMMAND_CLOCK_INVALID") from exc
    remaining_minutes = int((deadline - now).total_seconds() // 60)
    if remaining_minutes < 1:
        raise StartDispatchBlocked("START_WINDOW_EXPIRED")
    if not _same_reservation(current, reserved):
        raise StartDispatchBlocked("START_RESERVATION_CHANGED")
    try:
        if unresolved_device_sends(current):
            # A prior Park, valve, suspend, or native-resume call can still
            # reach a device after a lost response. No mower START may pass
            # that ambiguity; protective Park/Stop dispatches have their own
            # guarded paths and are intentionally not handled here.
            raise StartDispatchBlocked("PREVIOUS_DEVICE_OUTCOME_UNCONFIRMED")
    except DeviceSendBlocked as exc:
        raise StartDispatchBlocked(exc.code) from exc
    if (manual_session_id is None) != (manual_session_epoch is None):
        raise StartDispatchBlocked("MANUAL_SESSION_FENCE_INVALID")
    if manual_session_id is not None:
        if (
            current.mower_start_pending_session_id != manual_session_id
            or current.mower_start_pending_session_epoch != manual_session_epoch
            or reserved.mower_start_pending_session_id != manual_session_id
            or reserved.mower_start_pending_session_epoch != manual_session_epoch
            or current.operator_request_session_id != manual_session_id
            or current.operator_request_session_epoch != manual_session_epoch
        ):
            raise StartDispatchBlocked("MANUAL_SESSION_FENCE_CHANGED")
        try:
            permission = start_dispatch_permission(
                load_manual_session(current), session_id=manual_session_id,
                epoch=manual_session_epoch, mower_id=str(mower.get("mower_id") or ""), now_utc=now,
            )
        except Exception as exc:
            raise StartDispatchBlocked("MANUAL_SESSION_INVALID") from exc
        if permission.get("allowed") is not True:
            raise StartDispatchBlocked(str(permission.get("code") or "MANUAL_SESSION_INVALID"))
    if current.maintenance_mode:
        raise StartDispatchBlocked("MAINTENANCE_MODE")
    if (
        current.operator_request_status == "PENDING"
        and str(current.operator_request_action or "").strip().upper()
        in {"PARK_MOWER", "STOP_MOWER", "STOP_IRRIGATION_NOW", "STOP_IRRIGATION_AFTER_ZONE"}
    ):
        raise StartDispatchBlocked("MANUAL_STOP_PENDING")
    if not _mower_observation_fresh(
        mower, now_utc=now, max_age_seconds=mower_status_max_age_seconds
    ):
        raise StartDispatchBlocked("MOWER_STATUS_STALE")
    if not _hydrawise_observation_fresh(
        hydrawise_safety,
        now_utc=now,
        max_age_seconds=hydrawise_status_max_age_seconds,
    ):
        raise StartDispatchBlocked("HYDRAWISE_STATUS_STALE")
    return min(int(requested_duration_minutes), remaining_minutes)
