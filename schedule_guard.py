#!/usr/bin/env python3
"""Zeitfenster- und Zufallsstartschutz für den GitHub-Abruf."""

from __future__ import annotations

import argparse
import json
import os
import random
from dataclasses import dataclass
from datetime import datetime, time, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo


@dataclass(frozen=True)
class ScheduleSettings:
    timezone: str = "Europe/Berlin"
    window_start: str = "06:00"
    window_end: str = "22:00"
    random_delay_min_seconds: int = 120
    random_delay_max_seconds: int = 720
    closing_margin_seconds: int = 60
    minimum_source_refresh_interval_minutes: int = 240
    source_summary_path: str = "public/summary.json"


def parse_clock(value: str) -> time:
    try:
        hour_text, minute_text = str(value).strip().split(":", 1)
        hour = int(hour_text)
        minute = int(minute_text)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"Ungültige Uhrzeit: {value!r}") from exc
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        raise ValueError(f"Ungültige Uhrzeit: {value!r}")
    return time(hour=hour, minute=minute)


def load_settings(path: Path) -> ScheduleSettings:
    data: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    raw = data.get("schedule_protection") or {}
    settings = ScheduleSettings(
        timezone=str(raw.get("timezone", "Europe/Berlin")),
        window_start=str(raw.get("window_start", "06:00")),
        window_end=str(raw.get("window_end", "22:00")),
        random_delay_min_seconds=int(raw.get("random_delay_min_seconds", 120)),
        random_delay_max_seconds=int(raw.get("random_delay_max_seconds", 720)),
        closing_margin_seconds=int(raw.get("closing_margin_seconds", 60)),
        minimum_source_refresh_interval_minutes=int(
            raw.get("minimum_source_refresh_interval_minutes", 240)
        ),
        source_summary_path=str(raw.get("source_summary_path", "public/summary.json")),
    )
    if settings.random_delay_min_seconds < 0:
        raise ValueError("random_delay_min_seconds darf nicht negativ sein")
    if settings.random_delay_max_seconds < settings.random_delay_min_seconds:
        raise ValueError("random_delay_max_seconds ist kleiner als das Minimum")
    if settings.closing_margin_seconds < 0:
        raise ValueError("closing_margin_seconds darf nicht negativ sein")
    if settings.minimum_source_refresh_interval_minutes < 0:
        raise ValueError(
            "minimum_source_refresh_interval_minutes darf nicht negativ sein"
        )
    if not settings.source_summary_path.strip():
        raise ValueError("source_summary_path darf nicht leer sein")
    parse_clock(settings.window_start)
    parse_clock(settings.window_end)
    ZoneInfo(settings.timezone)
    return settings


def localize(now: datetime, settings: ScheduleSettings) -> datetime:
    timezone = ZoneInfo(settings.timezone)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone)
    return now.astimezone(timezone)


def is_inside_window(now: datetime, settings: ScheduleSettings) -> bool:
    local = localize(now, settings)
    start = parse_clock(settings.window_start)
    end = parse_clock(settings.window_end)
    current = local.timetz().replace(tzinfo=None)
    if start < end:
        return start <= current < end
    return current >= start or current < end


def seconds_until_window_end(now: datetime, settings: ScheduleSettings) -> int:
    local = localize(now, settings)
    end = parse_clock(settings.window_end)
    end_datetime = local.replace(
        hour=end.hour,
        minute=end.minute,
        second=0,
        microsecond=0,
    )
    if parse_clock(settings.window_start) >= end and local.timetz().replace(tzinfo=None) >= parse_clock(settings.window_start):
        end_datetime = end_datetime.replace(day=local.day)  # Nachtfenster wird derzeit nicht genutzt.
    return max(0, int((end_datetime - local).total_seconds()))


def choose_delay_seconds(
    now: datetime,
    settings: ScheduleSettings,
    rng: random.Random | random.SystemRandom | None = None,
) -> int | None:
    if not is_inside_window(now, settings):
        return None
    remaining = seconds_until_window_end(now, settings)
    maximum = min(
        settings.random_delay_max_seconds,
        remaining - settings.closing_margin_seconds,
    )
    if maximum < settings.random_delay_min_seconds:
        return None
    generator = rng or random.SystemRandom()
    return generator.randint(settings.random_delay_min_seconds, maximum)


def read_source_age_minutes(now: datetime, summary_path: Path) -> float | None:
    """Liefert das Alter des letzten echten Feeds; fehlende/defekte Daten erzwingen Abruf."""

    try:
        raw = json.loads(summary_path.read_text(encoding="utf-8"))
        generated_at_text = str(raw["generated_at"]).strip().replace("Z", "+00:00")
        generated_at = datetime.fromisoformat(generated_at_text)
        if generated_at.tzinfo is None:
            generated_at = generated_at.replace(tzinfo=timezone.utc)
    except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError):
        return None

    current = now
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    age_minutes = (
        current.astimezone(timezone.utc) - generated_at.astimezone(timezone.utc)
    ).total_seconds() / 60
    if age_minutes < -5:
        return None
    return max(0.0, age_minutes)


def source_refresh_decision(
    now: datetime,
    settings: ScheduleSettings,
    summary_path: Path,
    *,
    force_refresh: bool = False,
) -> tuple[bool, float | None, str]:
    if force_refresh:
        return True, read_source_age_minutes(now, summary_path), "FORCED"
    age_minutes = read_source_age_minutes(now, summary_path)
    if age_minutes is None:
        return True, None, "SOURCE_MISSING_OR_INVALID"
    if age_minutes < settings.minimum_source_refresh_interval_minutes:
        return False, age_minutes, "SOURCE_FRESH"
    return True, age_minutes, "SOURCE_REFRESH_DUE"


def write_outputs(values: dict[str, str]) -> None:
    output_path = os.environ.get("GITHUB_OUTPUT")
    lines = [f"{key}={value}" for key, value in values.items()]
    if output_path:
        with open(output_path, "a", encoding="utf-8") as handle:
            handle.write("\n".join(lines) + "\n")
    for line in lines:
        print(line)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", choices=("prepare", "confirm"))
    parser.add_argument("--config", default="config.json")
    parser.add_argument(
        "--force-refresh",
        action="store_true",
        help="Manuellen Abruf trotz frischem Quellbestand ausführen",
    )
    args = parser.parse_args()

    config_path = Path(args.config)
    settings = load_settings(config_path)
    now = datetime.now(tz=ZoneInfo(settings.timezone))
    local_text = now.isoformat(timespec="seconds")
    summary_path = Path(settings.source_summary_path)
    if not summary_path.is_absolute():
        summary_path = config_path.resolve().parent / summary_path

    if not is_inside_window(now, settings):
        write_outputs({
            "should_run": "false",
            "delay_seconds": "0",
            "local_time": local_text,
            "skip_reason": "OUTSIDE_WINDOW",
            "source_age_minutes": "",
        })
        print(
            f"Kein Abruf: außerhalb des erlaubten Fensters "
            f"{settings.window_start}–{settings.window_end} Uhr."
        )
        return 0

    refresh_due, source_age, reason = source_refresh_decision(
        now,
        settings,
        summary_path,
        force_refresh=args.force_refresh,
    )
    source_age_output = "" if source_age is None else f"{source_age:.1f}"
    if not refresh_due:
        write_outputs({
            "should_run": "false",
            "delay_seconds": "0",
            "local_time": local_text,
            "skip_reason": reason,
            "source_age_minutes": source_age_output,
        })
        print(
            "Kein Abruf: Der letzte echte Spielplan-Abruf ist erst "
            f"{source_age_output} Minuten alt."
        )
        return 0

    if args.mode == "prepare":
        delay = choose_delay_seconds(now, settings)
        if delay is None:
            write_outputs({
                "should_run": "false",
                "delay_seconds": "0",
                "local_time": local_text,
                "skip_reason": "TOO_CLOSE_TO_WINDOW_END",
                "source_age_minutes": source_age_output,
            })
            print(
                f"Kein Abruf: außerhalb des erlaubten Fensters "
                f"{settings.window_start}–{settings.window_end} Uhr oder zu nah am Ende."
            )
            return 0
        write_outputs({
            "should_run": "true",
            "delay_seconds": str(delay),
            "local_time": local_text,
            "skip_reason": "",
            "source_age_minutes": source_age_output,
        })
        print(f"Zufällige Startverschiebung: {delay} Sekunden.")
        return 0

    write_outputs({
        "should_run": "true",
        "local_time": local_text,
        "skip_reason": "",
        "source_age_minutes": source_age_output,
    })
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
