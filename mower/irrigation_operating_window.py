"""Conservative local operating window for an already planned water run."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from typing import Sequence
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


LOCAL_ZONE = "Europe/Berlin"
EARLIEST_LOCAL = time(3, 30)
DEADLINE_LOCAL = time(8, 0)


@dataclass(frozen=True)
class OperatingWindowResult:
    code: str
    earliest_start_utc: datetime | None = None
    latest_end_utc: datetime | None = None
    projected_end_utc: datetime | None = None
    latest_start_utc: datetime | None = None
    reason: str = ""


def _utc(value: datetime) -> datetime | None:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        return None
    return value.astimezone(timezone.utc)


def _seconds(value: object) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        return None
    return value


def _invalid(reason: str) -> OperatingWindowResult:
    return OperatingWindowResult("INVALID", reason=reason)


def _window_for(day: date, zone: ZoneInfo) -> tuple[datetime, datetime]:
    earliest = datetime.combine(day, EARLIEST_LOCAL, tzinfo=zone).astimezone(timezone.utc)
    deadline = datetime.combine(day, DEADLINE_LOCAL, tzinfo=zone).astimezone(timezone.utc)
    return earliest, deadline


def operating_bounds(now_utc: datetime, *, timezone_name: str = LOCAL_ZONE) -> tuple[datetime, datetime] | None:
    """Return today's absolute UTC bounds for a candidate start."""
    now = _utc(now_utc)
    if now is None:
        return None
    try:
        return _window_for(now.astimezone(ZoneInfo(timezone_name)).date(), ZoneInfo(timezone_name))
    except (ZoneInfoNotFoundError, TypeError, ValueError, OverflowError):
        return None


def _validate_zone_sequence(
    starts_utc: Sequence[datetime],
    durations_seconds: Sequence[int],
    pauses_seconds: Sequence[int] = (),
    *,
    validation_margin_seconds: int = 0,
    timezone_name: str = LOCAL_ZONE,
) -> OperatingWindowResult:
    """Validate absolute starts without changing durations or pauses.

    ``validation_margin_seconds`` is an explicit conservative assumption for
    scheduling/clock uncertainty; it is added after the projected end. No
    flow-rate or water-volume model is inferred.
    """
    try:
        zone = ZoneInfo(timezone_name)
    except (ZoneInfoNotFoundError, TypeError):
        return _invalid("timezone unavailable")
    try:
        if not starts_utc or len(starts_utc) != len(durations_seconds):
            return _invalid("starts and durations must be non-empty and equal length")
    except (TypeError, AttributeError):
        return _invalid("starts and durations must be non-empty and equal length")
    try:
        if len(pauses_seconds) not in {0, len(starts_utc) - 1}:
            return _invalid("pauses must contain one value between each pair of zones")
    except (TypeError, AttributeError):
        return _invalid("pauses must contain one value between each pair of zones")
    margin = _seconds(validation_margin_seconds)
    if margin is None:
        return _invalid("validation margin must be a non-negative integer")
    starts: list[datetime] = []
    durations: list[int] = []
    for start, duration in zip(starts_utc, durations_seconds):
        parsed = _utc(start)
        seconds = _seconds(duration)
        if parsed is None or seconds is None or seconds <= 0:
            return _invalid("starts must be aware datetimes and durations positive integers")
        starts.append(parsed)
        durations.append(seconds)
    pauses = list(pauses_seconds) if pauses_seconds else [0] * (len(starts) - 1)
    if any(_seconds(value) is None for value in pauses):
        return _invalid("pauses must be non-negative integers")
    local_day = starts[0].astimezone(zone).date()
    if any(start.astimezone(zone).date() != local_day for start in starts):
        return _invalid("all zones must be on the same local day")
    earliest, deadline = _window_for(local_day, zone)
    total_span = sum(durations) + sum(pauses) + margin
    projected_end = starts[-1] + timedelta(seconds=durations[-1])
    for index in range(1, len(starts)):
        minimum = starts[index - 1] + timedelta(seconds=durations[index - 1] + pauses[index - 1])
        if starts[index] < minimum:
            return OperatingWindowResult("INVALID", earliest, deadline, projected_end, deadline - timedelta(seconds=total_span), "planned pause is shortened")
    # Absolute starts may contain larger gaps than the optional minimum pauses;
    # retain those gaps instead of recomputing a shortened schedule.
    actual_span = int((starts[-1] + timedelta(seconds=durations[-1]) - starts[0]).total_seconds())
    latest_start = deadline - timedelta(seconds=actual_span + margin)
    if starts[0] < earliest:
        return OperatingWindowResult("TOO_EARLY", earliest, deadline, projected_end, latest_start, "earliest local start is 03:30 Europe/Berlin")
    if projected_end + timedelta(seconds=margin) > deadline:
        return OperatingWindowResult("TOO_LATE", earliest, deadline, projected_end, latest_start, "all zones must finish by 08:00 Europe/Berlin")
    return OperatingWindowResult("OK", earliest, deadline, projected_end, latest_start)


def validate_zone_sequence(
    starts_utc: Sequence[datetime], durations_seconds: Sequence[int],
    pauses_seconds: Sequence[int] = (), *, validation_margin_seconds: int = 0,
    timezone_name: str = LOCAL_ZONE,
) -> OperatingWindowResult:
    """Public fail-closed boundary for sequence validation."""
    try:
        return _validate_zone_sequence(
            starts_utc, durations_seconds, pauses_seconds,
            validation_margin_seconds=validation_margin_seconds,
            timezone_name=timezone_name,
        )
    except (TypeError, ValueError, OverflowError):
        return _invalid("malformed or overflowing operating-window input")


def validate_fresh_start(
    now_utc: datetime,
    *,
    duration_seconds: int,
    validation_margin_seconds: int = 0,
    timezone_name: str = LOCAL_ZONE,
) -> OperatingWindowResult:
    """Check a new run beginning now against the same local-day bounds."""
    now = _utc(now_utc)
    if now is None:
        return _invalid("now must be a timezone-aware datetime")
    try:
        return validate_zone_sequence(
            [now], [duration_seconds], (),
            validation_margin_seconds=validation_margin_seconds,
            timezone_name=timezone_name,
        )
    except (TypeError, ValueError, OverflowError):
        return _invalid("malformed or overflowing operating-window input")
