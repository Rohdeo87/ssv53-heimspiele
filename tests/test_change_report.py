import json
import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

import report_changes


def match(match_id: str, *, kickoff: str = "2026-09-01T18:00+02:00", calendar: str = "Rasen"):
    return {
        "id": match_id,
        "team": "SSV53 E2",
        "homeTeam": "SSV53 E2",
        "awayTeam": "Gastverein",
        "kickoff": kickoff,
        "start": kickoff,
        "end": kickoff,
        "occupancyStart": kickoff,
        "occupancyEnd": kickoff,
        "calendar": calendar,
        "place": calendar.casefold(),
        "competition": "Kreisliga",
        "status": "",
    }


class ChangeReportTests(unittest.TestCase):
    def test_detects_added_changed_and_removed(self):
        before = {
            "dfb:1": match("dfb:1"),
            "dfb:2": match("dfb:2"),
        }
        after = {
            "dfb:1": match("dfb:1", kickoff="2026-09-01T19:00+02:00"),
            "dfb:3": match("dfb:3"),
        }
        added, changed, removed = report_changes.compare_matches(before, after)
        self.assertEqual([item["id"] for item in added], ["dfb:3"])
        self.assertEqual([item["id"] for item in changed], ["dfb:1"])
        self.assertEqual([item["id"] for item in removed], ["dfb:2"])
        self.assertEqual(changed[0]["changes"][0]["field"], "kickoff")

    def test_checksum_only_change_is_ignored(self):
        before_item = match("dfb:1")
        before_item["checksum"] = "old"
        after_item = dict(before_item)
        after_item["checksum"] = "new"
        added, changed, removed = report_changes.compare_matches(
            {"dfb:1": before_item}, {"dfb:1": after_item}
        )
        self.assertEqual((added, changed, removed), ([], [], []))

    def test_empty_new_feed_is_blocked(self):
        reasons = report_changes.destructive_guard(
            before_count=4,
            after_count=0,
            removed_count=4,
            max_removal_ratio=0.60,
            min_previous_for_ratio=10,
            min_removed_for_ratio=5,
        )
        self.assertTrue(reasons)

    def test_large_reduction_is_blocked(self):
        reasons = report_changes.destructive_guard(
            before_count=20,
            after_count=5,
            removed_count=15,
            max_removal_ratio=0.60,
            min_previous_for_ratio=10,
            min_removed_for_ratio=5,
        )
        self.assertTrue(reasons)

    def test_small_legitimate_reduction_is_allowed(self):
        reasons = report_changes.destructive_guard(
            before_count=20,
            after_count=17,
            removed_count=3,
            max_removal_ratio=0.60,
            min_previous_for_ratio=10,
            min_removed_for_ratio=5,
        )
        self.assertEqual(reasons, [])

    def test_single_future_removal_requires_confirmation(self):
        now = datetime(2026, 9, 1, 10, tzinfo=timezone.utc)
        removed = [match("dfb:1", kickoff="2026-09-02T18:00+02:00")]
        items = report_changes.safety_decreasing_changes([], removed, now=now)
        self.assertEqual(1, len(items))
        self.assertEqual("removed", items[0]["kind"])

    def test_historical_removal_does_not_hold_current_feed(self):
        now = datetime(2026, 9, 3, 10, tzinfo=timezone.utc)
        removed = [match("dfb:1", kickoff="2026-09-01T18:00+02:00")]
        self.assertEqual(
            [], report_changes.safety_decreasing_changes([], removed, now=now)
        )

    def test_occupancy_extension_is_safe_but_shortening_is_confirmed(self):
        now = datetime(2026, 9, 1, 10, tzinfo=timezone.utc)
        old = match("dfb:1", kickoff="2026-09-02T18:00+02:00")
        old["occupancyStart"] = "2026-09-02T17:00+02:00"
        old["occupancyEnd"] = "2026-09-02T21:00+02:00"
        extended = dict(old)
        extended["occupancyStart"] = "2026-09-02T16:30+02:00"
        extended["occupancyEnd"] = "2026-09-02T21:30+02:00"
        shortened = dict(old)
        shortened["occupancyStart"] = "2026-09-02T17:30+02:00"
        changed_extended = [{"id": "dfb:1", "before": old, "after": extended}]
        changed_shortened = [{"id": "dfb:1", "before": old, "after": shortened}]
        self.assertEqual(
            [],
            report_changes.safety_decreasing_changes(
                changed_extended, [], now=now
            ),
        )
        self.assertEqual(
            1,
            len(report_changes.safety_decreasing_changes(
                changed_shortened, [], now=now
            )),
        )

    def test_two_time_separated_identical_observations_confirm_change(self):
        now = datetime(2026, 9, 1, 10, tzinfo=timezone.utc)
        items = [{"kind": "removed", "id": "dfb:1", "before": {}, "after": None}]
        with tempfile.TemporaryDirectory() as tmp:
            state = Path(tmp) / "confirmation.json"
            first = report_changes.confirm_safety_decrease(
                items,
                state_path=state,
                now=now,
                required_confirmations=2,
                minimum_interval=timedelta(minutes=60),
            )
            too_soon = report_changes.confirm_safety_decrease(
                items,
                state_path=state,
                now=now + timedelta(minutes=59),
                required_confirmations=2,
                minimum_interval=timedelta(minutes=60),
            )
            confirmed = report_changes.confirm_safety_decrease(
                items,
                state_path=state,
                now=now + timedelta(minutes=60),
                required_confirmations=2,
                minimum_interval=timedelta(minutes=60),
            )
        self.assertFalse(first["confirmed"])
        self.assertEqual(1, too_soon["confirmations"])
        self.assertTrue(confirmed["confirmed"])

    def test_changed_candidate_restarts_confirmation(self):
        now = datetime(2026, 9, 1, 10, tzinfo=timezone.utc)
        first_items = [{"kind": "removed", "id": "dfb:1", "before": {}, "after": None}]
        other_items = [{"kind": "removed", "id": "dfb:2", "before": {}, "after": None}]
        with tempfile.TemporaryDirectory() as tmp:
            state = Path(tmp) / "confirmation.json"
            report_changes.confirm_safety_decrease(
                first_items,
                state_path=state,
                now=now,
                required_confirmations=2,
                minimum_interval=timedelta(minutes=60),
            )
            result = report_changes.confirm_safety_decrease(
                other_items,
                state_path=state,
                now=now + timedelta(minutes=61),
                required_confirmations=2,
                minimum_interval=timedelta(minutes=60),
            )
        self.assertFalse(result["confirmed"])
        self.assertEqual(1, result["confirmations"])

    def test_main_writes_report_and_public_copy(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            before = root / "before.json"
            after = root / "after.json"
            output_json = root / "generated" / "change_report.json"
            output_md = root / "generated" / "change_report.md"
            public_json = root / "public" / "change_report.json"
            before.write_text(json.dumps({"matches": [match("dfb:1")]}), encoding="utf-8")
            after.write_text(json.dumps({"matches": [match("dfb:1"), match("dfb:2")]}), encoding="utf-8")

            old_argv = report_changes.os.sys.argv
            old_summary = os.environ.pop("GITHUB_STEP_SUMMARY", None)
            report_changes.os.sys.argv = [
                "report_changes.py",
                "--before", str(before),
                "--after", str(after),
                "--json", str(output_json),
                "--markdown", str(output_md),
                "--public-json", str(public_json),
            ]
            try:
                result = report_changes.main()
            finally:
                report_changes.os.sys.argv = old_argv
                if old_summary is not None:
                    os.environ["GITHUB_STEP_SUMMARY"] = old_summary

            self.assertEqual(result, 0)
            payload = json.loads(output_json.read_text(encoding="utf-8"))
            self.assertEqual(payload["counts"]["added"], 1)
            self.assertTrue(public_json.exists())
            self.assertIn("Neue Spiele", output_md.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
