"""Regression coverage for delayed overnight GitHub schedule deliveries."""

from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import random
import tempfile
import unittest
from unittest.mock import patch

import schedule_guard as guard


class ScheduleRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.settings = guard.ScheduleSettings()
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.summary = Path(self.directory.name) / "summary.json"

    def set_source(self, stamp):
        self.summary.write_text(json.dumps({"generated_at": stamp}), encoding="utf-8")

    def run_main(self, now, mode="prepare", force=False):
        config = Path(self.directory.name) / "config.json"
        config.write_text(json.dumps({"schedule_protection": {
            "source_summary_path": str(self.summary),
        }}), encoding="utf-8")

        class Clock(datetime):
            @classmethod
            def now(cls, tz=None):
                return now.astimezone(tz) if tz else now.replace(tzinfo=None)

        args = ["schedule_guard.py", mode, "--config", str(config)]
        if force:
            args.append("--force-refresh")
        with patch.object(guard, "datetime", Clock), patch("sys.argv", args), patch.object(guard, "write_outputs") as output:
            self.assertEqual(guard.main(), 0)
        return output.call_args.args[0]

    def test_incident_timeline_refreshes_at_night_then_skips_recent_source(self):
        self.set_source("2026-10-07T16:40:00Z")
        self.assertEqual(self.run_main(datetime.fromisoformat("2026-10-07T22:03:00+00:00"))["should_run"], "false")
        night = datetime.fromisoformat("2026-10-08T02:02:00+00:00")
        for mode in ("prepare", "confirm"):
            result = self.run_main(night, mode)
            self.assertEqual(result["should_run"], "true")
            if mode == "prepare":
                self.assertGreaterEqual(int(result["delay_seconds"]), 120)
                self.assertLessEqual(int(result["delay_seconds"]), 720)
        self.set_source(night.isoformat())
        # Nighttime recovery does not create a duplicate just because it was forced.
        self.assertEqual(self.run_main(night + timedelta(minutes=10), force=True)["should_run"], "false")
        self.assertEqual(self.run_main(datetime.fromisoformat("2026-10-08T04:03:00+00:00"))["should_run"], "false")

    def test_nighttime_boundary_ignores_force_and_preserves_six_hour_floor(self):
        now = datetime.fromisoformat("2026-10-08T02:02:00+00:00")
        for age, expected in ((239, False), (359.99, False), (360, True), (721, True)):
            self.set_source((now - timedelta(minutes=age)).isoformat())
            for force in (False, True):
                with self.subTest(age=age, force=force):
                    due, _, reason = guard.source_refresh_decision(now, self.settings, self.summary, force_refresh=force)
                    self.assertEqual(due, expected)
                    self.assertEqual(reason, "SOURCE_RECOVERY_DUE" if expected else "OUTSIDE_WINDOW")

    def test_normal_daytime_minimum_is_still_four_hours(self):
        now = datetime.fromisoformat("2026-10-08T10:00:00+00:00")
        for age, expected in ((239.99, False), (240, True)):
            self.set_source((now - timedelta(minutes=age)).isoformat())
            self.assertEqual(guard.source_refresh_decision(now, self.settings, self.summary)[0], expected)

    def test_recovery_survives_closing_time_but_regular_refresh_does_not(self):
        before = datetime.fromisoformat("2026-10-08T21:59:00+02:00")
        after = datetime.fromisoformat("2026-10-08T22:05:00+02:00")
        self.set_source((before - timedelta(hours=7)).isoformat())
        self.assertEqual(self.run_main(before)["should_run"], "true")
        self.assertEqual(self.run_main(after, "confirm")["should_run"], "true")
        self.set_source((before - timedelta(hours=4)).isoformat())
        self.assertEqual(self.run_main(before)["should_run"], "false")
        self.assertEqual(self.run_main(after, "confirm")["should_run"], "false")

    def test_recovery_jitter_keeps_range_and_is_not_fixed(self):
        now = datetime(2026, 10, 8, 2, tzinfo=timezone.utc)
        values = {guard.choose_delay_seconds(now, self.settings, random.Random(seed), allow_outside_window_recovery=True) for seed in range(20)}
        self.assertGreater(len(values), 1)
        self.assertTrue(all(120 <= value <= 720 for value in values))
        self.assertIsNone(guard.choose_delay_seconds(now, self.settings))

    def test_missing_invalid_or_future_source_recovers_without_relabeled_timestamp(self):
        now = datetime(2026, 10, 8, 2, tzinfo=timezone.utc)
        for content in (None, "{}", '{"generated_at":"broken"}', '{"generated_at":"2099-01-01T00:00:00Z"}'):
            with self.subTest(content=content):
                if content is not None:
                    self.summary.write_text(content, encoding="utf-8")
                self.assertEqual(self.run_main(now)["should_run"], "true")
                if content is not None:
                    self.assertEqual(self.summary.read_text(encoding="utf-8"), content)

    def test_recovery_configuration_cannot_undercut_normal_minimum(self):
        config = Path(self.directory.name) / "config.json"
        config.write_text(json.dumps({"schedule_protection": {
            "minimum_source_refresh_interval_minutes": 240,
            "outside_window_recovery_after_minutes": 239,
        }}), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "Mindestintervall"):
            guard.load_settings(config)


if __name__ == "__main__":
    unittest.main()
