from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "create_feed.py"


class StructuredFeedTests(unittest.TestCase):
    def test_feed_uses_public_times_and_separate_occupancy_times(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "generated"
            target = root / "public"
            source.mkdir()
            item = {
                "external_id": "TEST",
                "team_name": "Schönwalder SV",
                "team_category": "D-Junioren | 1. Kreisklasse",
                "team_role": "home",
                "kickoff": "2026-09-12T10:00+02:00",
                "match_end": "2026-09-12T11:00+02:00",
                "event_start": "2026-09-12T09:00+02:00",
                "event_end": "2026-09-12T12:00+02:00",
                "match_duration_minutes": 60,
                "duration_rule": "flb-jugendordnung-2025-12-13-d-2x30",
                "competition_format": "league",
                "home_team": "Schönwalder SV",
                "away_team": "Gast",
                "competition": "Sa, | D-Junioren | 1. Kreisklasse",
                "calendar": "Kunstrasen",
                "venue_raw": "Sportplatz Schönwalde Strandbad, Platz 2",
                "status": "",
                "detail_url": "https://www.fussball.de/spiel/test",
                "checksum": "checksum",
            }
            files = {
                "included_matches.json": [item],
                "summary.json": {"generated_at": "2026-08-11T06:00:00+00:00"},
                "failed_teams.json": [],
                "quality_report.json": {"publishable": True, "errors": []},
            }
            for name, value in files.items():
                (source / name).write_text(
                    json.dumps(value, ensure_ascii=False), encoding="utf-8"
                )

            subprocess.run(
                [
                    sys.executable,
                    str(SCRIPT),
                    "--input",
                    str(source),
                    "--output",
                    str(target),
                ],
                check=True,
                cwd=ROOT,
                capture_output=True,
                text=True,
            )
            feed = json.loads((target / "matches.json").read_text(encoding="utf-8"))
            self.assertEqual(2, feed["schemaVersion"])
            match = feed["matches"][0]
            self.assertEqual(item["kickoff"], match["start"])
            self.assertEqual(item["match_end"], match["end"])
            self.assertEqual(item["event_start"], match["occupancyStart"])
            self.assertEqual(item["event_end"], match["occupancyEnd"])
            self.assertEqual("kunstrasen", match["place"])
            self.assertEqual("Schönwalder SV – Gast", match["title"])
            self.assertEqual("D-Junioren · 1. Kreisklasse", match["description"])
            self.assertEqual(60, match["matchDurationMinutes"])


if __name__ == "__main__":
    unittest.main()
