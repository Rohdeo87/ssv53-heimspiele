import json
import os
import tempfile
import unittest
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
