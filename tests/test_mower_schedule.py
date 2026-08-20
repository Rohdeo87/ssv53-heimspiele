from __future__ import annotations

import tempfile
import unittest
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from mower.planner import (
    Block,
    build_hydrawise_blocks,
    build_training_blocks,
    create_plan,
    merge_blocks,
    read_match_blocks,
)
from mower.decision import parking_block_for
from mower.dry_run import _safe_mowing_windows


TZ = ZoneInfo("Europe/Berlin")


class MowerPlannerTests(unittest.TestCase):
    def test_next_safe_window_skips_time_too_short_before_training(self) -> None:
        config = {
            "timezone": "Europe/Berlin",
            "planning": {
                "day_start": "06:00",
                "day_end": "23:00",
                "minimum_mowing_window_minutes": 30,
            },
            "training": {
                "before_minutes": 30,
                "after_minutes": 30,
                "weekly": [
                    {
                        "weekday": "Donnerstag",
                        "start": "19:30",
                        "end": "20:30",
                        "team": "Test",
                    }
                ],
            },
            "hydrawise": {},
        }
        now = datetime(2026, 8, 20, 18, 25, tzinfo=TZ)
        plans, _ = create_plan(config, [], None, now.date(), 1)
        windows = _safe_mowing_windows(
            plans,
            now,
            park_lookahead_minutes=10,
            minimum_mowing_minutes=30,
        )
        self.assertEqual(len(windows), 1)
        self.assertEqual(windows[0]["start"], datetime(2026, 8, 20, 21, 0, tzinfo=TZ).isoformat())
        self.assertEqual(windows[0]["command_deadline"], datetime(2026, 8, 20, 22, 50, tzinfo=TZ).isoformat())

    def test_training_gets_30_minute_buffer_and_adjacent_sessions_merge(self) -> None:
        config = {
            "before_minutes": 30,
            "after_minutes": 30,
            "weekly": [
                {"weekday": "Dienstag", "start": "17:00", "end": "18:30", "team": "E1"},
                {"weekday": "Dienstag", "start": "18:30", "end": "20:00", "team": "A"},
            ],
        }
        blocks = build_training_blocks(config, date(2026, 8, 25), 1, TZ)
        merged = merge_blocks(blocks)
        self.assertEqual(len(merged), 1)
        self.assertEqual(merged[0].start, datetime(2026, 8, 25, 16, 30, tzinfo=TZ))
        self.assertEqual(merged[0].end, datetime(2026, 8, 25, 20, 30, tzinfo=TZ))

    def test_training_park_command_is_forty_minutes_before_training(self) -> None:
        blocks = build_training_blocks(
            {
                "before_minutes": 30,
                "after_minutes": 30,
                "weekly": [
                    {"weekday": "Dienstag", "start": "17:00", "end": "18:30", "team": "E1"}
                ],
            },
            date(2026, 8, 25),
            1,
            TZ,
        )
        now = datetime(2026, 8, 25, 16, 20, tzinfo=TZ)
        self.assertIsNotNone(
            parking_block_for(
                active_block=None,
                next_block=blocks[0],
                now=now,
                lookahead_minutes=10,
            )
        )
        self.assertEqual(blocks[0].start, datetime(2026, 8, 25, 16, 30, tzinfo=TZ))

    def test_match_ics_is_used_without_additional_buffer(self) -> None:
        content = """BEGIN:VCALENDAR
BEGIN:VEVENT
UID:test@ssv53.de
DTSTART;TZID=Europe/Berlin:20260821T180000
DTEND;TZID=Europe/Berlin:20260821T213000
SUMMARY:Testspiel
END:VEVENT
END:VCALENDAR
"""
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "rasen.ics"
            path.write_text(content, encoding="utf-8")
            blocks = read_match_blocks(path, TZ)
        self.assertEqual(blocks[0].start, datetime(2026, 8, 21, 18, 0, tzinfo=TZ))
        self.assertEqual(blocks[0].end, datetime(2026, 8, 21, 21, 30, tzinfo=TZ))

    def test_match_park_command_is_seventy_minutes_before_kickoff(self) -> None:
        kickoff = datetime(2026, 8, 21, 19, 0, tzinfo=TZ)
        buffered = Block(
            start=kickoff - timedelta(minutes=60),
            end=kickoff + timedelta(minutes=150),
            source="match",
            title="Testspiel",
        )
        now = kickoff - timedelta(minutes=70)
        self.assertIsNotNone(
            parking_block_for(
                active_block=None,
                next_block=buffered,
                now=now,
                lookahead_minutes=10,
            )
        )

    def test_hydrawise_running_and_future_zone_are_blocked(self) -> None:
        status = {
            "time": int(datetime(2026, 8, 25, 4, 0, tzinfo=timezone.utc).timestamp()),
            "relays": [
                {"relay_id": 1, "relay": 1, "name": "Rasen Nord", "time": 1, "run": 600},
                {"relay_id": 2, "relay": 2, "name": "Rasen Süd", "time": 3600, "run": 900},
            ],
        }
        blocks = build_hydrawise_blocks(
            status,
            {"include_all_zones": True, "before_minutes": 15, "after_minutes": 30},
            TZ,
            datetime(2026, 8, 25, 0, 0, tzinfo=TZ),
            datetime(2026, 8, 26, 0, 0, tzinfo=TZ),
        )
        self.assertEqual(len(blocks), 2)
        self.assertTrue(blocks[0].details["running"])
        self.assertFalse(blocks[1].details["running"])

    def test_short_free_window_is_removed(self) -> None:
        config = {
            "timezone": "Europe/Berlin",
            "planning": {
                "day_start": "06:00",
                "day_end": "22:00",
                "minimum_mowing_window_minutes": 30,
            },
            "training": {"weekly": []},
            "hydrawise": {},
        }
        blocks = [
            Block(
                start=datetime(2026, 8, 24, 6, 20, tzinfo=TZ),
                end=datetime(2026, 8, 24, 21, 50, tzinfo=TZ),
                source="match",
                title="Lange Sperre",
            )
        ]
        plans, _ = create_plan(config, blocks, None, date(2026, 8, 24), 1)
        self.assertEqual(plans[0].mowing_windows, [])

    def test_expected_tuesday_windows_are_created(self) -> None:
        config = {
            "timezone": "Europe/Berlin",
            "planning": {
                "day_start": "06:00",
                "day_end": "22:00",
                "minimum_mowing_window_minutes": 30,
            },
            "training": {
                "before_minutes": 30,
                "after_minutes": 30,
                "weekly": [
                    {"weekday": "Dienstag", "start": "17:00", "end": "18:30", "team": "E1"},
                    {"weekday": "Dienstag", "start": "18:30", "end": "20:00", "team": "A"},
                ],
            },
            "hydrawise": {},
        }
        plans, _ = create_plan(config, [], None, date(2026, 8, 25), 1)
        windows = plans[0].mowing_windows
        self.assertEqual([(window.start.time(), window.end.time()) for window in windows], [(time(6), time(16, 30)), (time(20, 30), time(22))])


if __name__ == "__main__":
    unittest.main()
