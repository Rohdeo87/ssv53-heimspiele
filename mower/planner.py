from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable
from zoneinfo import ZoneInfo


WEEKDAYS = {
    "monday": 0,
    "montag": 0,
    "tuesday": 1,
    "dienstag": 1,
    "wednesday": 2,
    "mittwoch": 2,
    "thursday": 3,
    "donnerstag": 3,
    "friday": 4,
    "freitag": 4,
    "saturday": 5,
    "samstag": 5,
    "sunday": 6,
    "sonntag": 6,
}


@dataclass(frozen=True)
class Block:
    start: datetime
    end: datetime
    source: str
    title: str
    details: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.start.tzinfo is None or self.end.tzinfo is None:
            raise ValueError("Block-Zeitpunkte müssen eine Zeitzone besitzen.")
        if self.end <= self.start:
            raise ValueError("Block-Ende muss nach dem Beginn liegen.")


@dataclass(frozen=True)
class Window:
    start: datetime
    end: datetime

    @property
    def minutes(self) -> int:
        return int((self.end - self.start).total_seconds() // 60)


@dataclass
class DayPlan:
    day: date
    blocked: list[Block]
    mowing_windows: list[Window]

    @property
    def available_minutes(self) -> int:
        return sum(window.minutes for window in self.mowing_windows)


def load_json(path: str | Path) -> dict[str, Any]:
    with Path(path).open("r", encoding="utf-8") as handle:
        data = json.load(handle)
    if not isinstance(data, dict):
        raise ValueError(f"{path} muss ein JSON-Objekt enthalten.")
    return data


def parse_clock(value: str) -> time:
    try:
        return time.fromisoformat(value)
    except ValueError as exc:
        raise ValueError(f"Ungültige Uhrzeit: {value!r}") from exc


def parse_date(value: str) -> date:
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise ValueError(f"Ungültiges Datum: {value!r}") from exc


def _unfold_ics_lines(text: str) -> list[str]:
    lines: list[str] = []
    for raw_line in text.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        if raw_line.startswith((" ", "\t")) and lines:
            lines[-1] += raw_line[1:]
        else:
            lines.append(raw_line)
    return lines


def _parse_ics_datetime(name_and_params: str, value: str, default_tz: ZoneInfo) -> datetime:
    params = name_and_params.split(";")[1:]
    tz = default_tz
    for param in params:
        if param.upper().startswith("TZID="):
            tz = ZoneInfo(param.split("=", 1)[1])

    value = value.strip()
    if value.endswith("Z"):
        return datetime.strptime(value, "%Y%m%dT%H%M%SZ").replace(tzinfo=timezone.utc).astimezone(default_tz)
    if "T" not in value:
        return datetime.strptime(value, "%Y%m%d").replace(tzinfo=tz)
    return datetime.strptime(value, "%Y%m%dT%H%M%S").replace(tzinfo=tz).astimezone(default_tz)


def read_match_blocks(path: str | Path, tz: ZoneInfo) -> list[Block]:
    ics_path = Path(path)
    if not ics_path.exists():
        return []

    lines = _unfold_ics_lines(ics_path.read_text(encoding="utf-8"))
    events: list[dict[str, str]] = []
    current: dict[str, str] | None = None
    for line in lines:
        if line == "BEGIN:VEVENT":
            current = {}
            continue
        if line == "END:VEVENT":
            if current is not None:
                events.append(current)
            current = None
            continue
        if current is None or ":" not in line:
            continue
        key, value = line.split(":", 1)
        current[key] = value

    blocks: list[Block] = []
    for event in events:
        start_key = next((key for key in event if key.upper().startswith("DTSTART")), None)
        end_key = next((key for key in event if key.upper().startswith("DTEND")), None)
        if not start_key or not end_key:
            continue
        start = _parse_ics_datetime(start_key, event[start_key], tz)
        end = _parse_ics_datetime(end_key, event[end_key], tz)
        title = event.get("SUMMARY", "Heimspiel").replace("\\,", ",")
        blocks.append(
            Block(
                start=start,
                end=end,
                source="match",
                title=title,
                details={"uid": event.get("UID", "")},
            )
        )
    return blocks


def _weekday_number(value: str | int) -> int:
    if isinstance(value, int) and 0 <= value <= 6:
        return value
    key = str(value).strip().lower()
    if key in WEEKDAYS:
        return WEEKDAYS[key]
    raise ValueError(f"Ungültiger Wochentag: {value!r}")


def _is_in_active_ranges(day: date, active_ranges: list[dict[str, str]]) -> bool:
    if not active_ranges:
        return True
    for active_range in active_ranges:
        start = parse_date(active_range["from"])
        end = parse_date(active_range["to"])
        if start <= day <= end:
            return True
    return False


def build_training_blocks(
    training_config: dict[str, Any],
    start_day: date,
    days: int,
    tz: ZoneInfo,
    cancelled_occurrences: set[tuple[str, str]] | None = None,
) -> list[Block]:
    before = timedelta(minutes=int(training_config.get("before_minutes", 30)))
    after = timedelta(minutes=int(training_config.get("after_minutes", 30)))
    active_ranges = list(training_config.get("active_ranges", []))
    weekly = list(training_config.get("weekly", []))

    blocks: list[Block] = []
    for offset in range(days):
        day = start_day + timedelta(days=offset)
        if not _is_in_active_ranges(day, active_ranges):
            continue
        for session in weekly:
            if _weekday_number(session["weekday"]) != day.weekday():
                continue
            schedule_id = str(session.get("id", "")).strip() or (
                "legacy:"
                + ":".join(
                    str(session.get(key, "")).strip().casefold()
                    for key in ("weekday", "start", "end", "team")
                )
            )
            if (schedule_id, day.isoformat()) in (cancelled_occurrences or set()):
                continue
            start = datetime.combine(day, parse_clock(session["start"]), tzinfo=tz) - before
            end = datetime.combine(day, parse_clock(session["end"]), tzinfo=tz) + after
            team = str(session.get("team", "Training"))
            blocks.append(
                Block(
                    start=start,
                    end=end,
                    source="training",
                    title=f"Training {team}",
                    details={"team": team, "schedule_id": schedule_id},
                )
            )
    return blocks


def build_hydrawise_blocks(
    status: dict[str, Any] | None,
    hydrawise_config: dict[str, Any],
    tz: ZoneInfo,
    horizon_start: datetime,
    horizon_end: datetime,
) -> list[Block]:
    if not status:
        return []

    include_all = bool(hydrawise_config.get("include_all_zones", False))
    relay_ids = {int(value) for value in hydrawise_config.get("relay_ids", [])}
    name_patterns = [re.compile(pattern, re.IGNORECASE) for pattern in hydrawise_config.get("zone_name_patterns", [])]
    before = timedelta(minutes=int(hydrawise_config.get("before_minutes", 15)))
    after = timedelta(minutes=int(hydrawise_config.get("after_minutes", 30)))

    status_epoch = int(status.get("time", datetime.now(tz=timezone.utc).timestamp()))
    status_time = datetime.fromtimestamp(status_epoch, tz=timezone.utc).astimezone(tz)
    blocks: list[Block] = []

    for relay in status.get("relays", []):
        relay_id = int(relay.get("relay_id", -1))
        name = str(relay.get("name", f"Zone {relay.get('relay', '?')}"))
        selected = include_all or relay_id in relay_ids or any(pattern.search(name) for pattern in name_patterns)
        if not selected:
            continue

        seconds_until = int(float(relay.get("time", 0) or 0))
        run_seconds = int(float(relay.get("run", 0) or 0))
        if run_seconds <= 0 or seconds_until <= 0:
            continue

        running = seconds_until == 1
        irrigation_start = status_time if running else status_time + timedelta(seconds=seconds_until)
        irrigation_end = irrigation_start + timedelta(seconds=run_seconds)
        block_start = irrigation_start - before
        block_end = irrigation_end + after
        if block_end <= horizon_start or block_start >= horizon_end:
            continue

        blocks.append(
            Block(
                start=block_start,
                end=block_end,
                source="irrigation",
                title=f"Beregnung {name}",
                details={
                    "relay_id": relay_id,
                    "zone": relay.get("relay"),
                    "running": running,
                    "irrigation_start": irrigation_start.isoformat(),
                    "irrigation_end": irrigation_end.isoformat(),
                },
            )
        )
    return blocks


def merge_blocks(blocks: Iterable[Block]) -> list[Block]:
    ordered = sorted(blocks, key=lambda block: (block.start, block.end))
    if not ordered:
        return []

    merged: list[Block] = []
    group: list[Block] = [ordered[0]]
    current_end = ordered[0].end

    def flush(items: list[Block]) -> Block:
        start = min(item.start for item in items)
        end = max(item.end for item in items)
        sources = sorted({item.source for item in items})
        titles = list(dict.fromkeys(item.title for item in items))
        return Block(
            start=start,
            end=end,
            source="+".join(sources),
            title="; ".join(titles),
            details={"items": [block_to_dict(item) for item in items]},
        )

    for block in ordered[1:]:
        if block.start <= current_end:
            group.append(block)
            current_end = max(current_end, block.end)
        else:
            merged.append(flush(group))
            group = [block]
            current_end = block.end
    merged.append(flush(group))
    return merged


def _clip_block(block: Block, start: datetime, end: datetime) -> Block | None:
    clipped_start = max(block.start, start)
    clipped_end = min(block.end, end)
    if clipped_end <= clipped_start:
        return None
    return Block(
        start=clipped_start,
        end=clipped_end,
        source=block.source,
        title=block.title,
        details=block.details,
    )


def build_day_plan(
    day: date,
    merged_blocks: list[Block],
    tz: ZoneInfo,
    day_start: time,
    day_end: time,
    minimum_window_minutes: int,
) -> DayPlan:
    start = datetime.combine(day, day_start, tzinfo=tz)
    end = datetime.combine(day, day_end, tzinfo=tz)
    if end <= start:
        end += timedelta(days=1)

    blocked = [clipped for block in merged_blocks if (clipped := _clip_block(block, start, end)) is not None]
    cursor = start
    windows: list[Window] = []
    minimum = timedelta(minutes=minimum_window_minutes)
    for block in blocked:
        if block.start > cursor and block.start - cursor >= minimum:
            windows.append(Window(start=cursor, end=block.start))
        cursor = max(cursor, block.end)
    if end > cursor and end - cursor >= minimum:
        windows.append(Window(start=cursor, end=end))

    return DayPlan(day=day, blocked=blocked, mowing_windows=windows)


def create_plan(
    config: dict[str, Any],
    match_blocks: list[Block],
    hydrawise_status: dict[str, Any] | None,
    start_day: date,
    days: int,
    cancelled_occurrences: set[tuple[str, str]] | None = None,
) -> tuple[list[DayPlan], list[Block]]:
    tz = ZoneInfo(config.get("timezone", "Europe/Berlin"))
    planning = config.get("planning", {})
    day_start = parse_clock(planning.get("day_start", "06:00"))
    day_end = parse_clock(planning.get("day_end", "22:00"))
    minimum_window = int(planning.get("minimum_mowing_window_minutes", 30))

    horizon_start = datetime.combine(start_day, time.min, tzinfo=tz)
    horizon_end = horizon_start + timedelta(days=days)
    training_blocks = build_training_blocks(
        config.get("training", {}),
        start_day,
        days,
        tz,
        cancelled_occurrences,
    )
    relevant_matches = [block for block in match_blocks if block.end > horizon_start and block.start < horizon_end]
    irrigation_blocks = build_hydrawise_blocks(
        hydrawise_status,
        config.get("hydrawise", {}),
        tz,
        horizon_start,
        horizon_end,
    )
    merged = merge_blocks([*training_blocks, *relevant_matches, *irrigation_blocks])
    plans = [
        build_day_plan(
            start_day + timedelta(days=offset),
            merged,
            tz,
            day_start,
            day_end,
            minimum_window,
        )
        for offset in range(days)
    ]
    return plans, merged


def block_to_dict(block: Block) -> dict[str, Any]:
    return {
        "start": block.start.isoformat(),
        "end": block.end.isoformat(),
        "source": block.source,
        "title": block.title,
        "details": block.details,
    }


def plan_to_dict(
    plans: list[DayPlan],
    merged_blocks: list[Block],
    warnings: list[str],
    metadata: dict[str, Any],
) -> dict[str, Any]:
    return {
        "metadata": metadata,
        "warnings": warnings,
        "days": [
            {
                "date": day_plan.day.isoformat(),
                "available_minutes": day_plan.available_minutes,
                "mowing_windows": [
                    {"start": window.start.isoformat(), "end": window.end.isoformat(), "minutes": window.minutes}
                    for window in day_plan.mowing_windows
                ],
                "blocked": [block_to_dict(block) for block in day_plan.blocked],
            }
            for day_plan in plans
        ],
        "merged_blocks": [block_to_dict(block) for block in merged_blocks],
    }
