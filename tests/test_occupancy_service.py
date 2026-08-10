from __future__ import annotations

import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from occupancy.service import build_occupancy_payload


class OccupancyServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.config = self.root / "config.json"
        self.matches = self.root / "rasen.ics"
        self.config.write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "timezone": "Europe/Berlin",
                    "effective_from": "2026-08-11",
                    "effective_to": "2027-07-09",
                    "resources": [
                        {"id": "rasen", "title": "Rasen"},
                        {"id": "kunstrasen", "title": "Kunstrasen"},
                    ],
                    "seasons": {
                        "Sommer": {
                            "weekly": [
                                {
                                    "id": "e1-di",
                                    "weekday": "Dienstag",
                                    "start": "17:00",
                                    "end": "18:30",
                                    "team": "E1",
                                    "resource_id": "rasen",
                                    "area": "vorne & hinten",
                                }
                            ]
                        },
                        "Winter": {"weekly": []},
                    },
                    "one_off_events": [],
                    "cancelled_occurrences": [],
                    "matches": {
                        "resource_id": "rasen",
                        "buffer_before_minutes": 60,
                        "buffer_after_minutes": 60,
                    },
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        self.matches.write_text(
            "\n".join(
                [
                    "BEGIN:VCALENDAR",
                    "BEGIN:VEVENT",
                    "UID:test-match",
                    "DTSTART;TZID=Europe/Berlin:20260821T180000",
                    "DTEND;TZID=Europe/Berlin:20260821T213000",
                    "SUMMARY:SSV 53 – Test",
                    "LOCATION:Rasenplatz\\, Schönwalde",
                    "DESCRIPTION:Kreispokal",
                    "END:VEVENT",
                    "END:VCALENDAR",
                ]
            ),
            encoding="utf-8",
        )

    def tearDown(self) -> None:
        self.temp.cleanup()

    def _payload(self, start: str, end: str, season: str = "Sommer"):
        return build_occupancy_payload(
            config_path=self.config,
            matches_path=self.matches,
            start=start,
            end=end,
            season=season,
            generated_at=datetime(2026, 8, 10, 18, 0, tzinfo=timezone.utc),
        )

    def test_new_schedule_starts_on_11_august(self) -> None:
        payload = self._payload("2026-08-10", "2026-08-12")
        trainings = [event for event in payload["events"] if event["source"] == "training"]
        self.assertEqual(len(trainings), 1)
        self.assertEqual(trainings[0]["title"], "E1")
        self.assertTrue(trainings[0]["start"].startswith("2026-08-11T17:00:00+02:00"))

    def test_no_training_before_effective_date(self) -> None:
        payload = self._payload("2026-08-03", "2026-08-05")
        self.assertFalse(any(event["source"] == "training" for event in payload["events"]))

    def test_match_is_debuffered_for_public_display(self) -> None:
        payload = self._payload("2026-08-21", "2026-08-22")
        match = next(event for event in payload["events"] if event["source"] == "match")
        self.assertEqual(match["start"], "2026-08-21T19:00:00+02:00")
        self.assertEqual(match["end"], "2026-08-21T20:30:00+02:00")
        self.assertEqual(match["occupancyStart"], "2026-08-21T18:00:00+02:00")
        self.assertEqual(match["occupancyEnd"], "2026-08-21T21:30:00+02:00")

    def test_unknown_season_is_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "Unbekannte Saison"):
            self._payload("2026-08-10", "2026-08-12", season="Frühling")

    def test_range_is_limited(self) -> None:
        with self.assertRaisesRegex(ValueError, "Maximal 63 Tage"):
            self._payload("2026-08-10", "2026-11-01")


class ProductionScheduleTests(unittest.TestCase):
    def test_11_august_contains_final_summer_training_plan(self) -> None:
        root = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as temporary:
            empty_ics = Path(temporary) / "empty.ics"
            empty_ics.write_text("BEGIN:VCALENDAR\nEND:VCALENDAR\n", encoding="utf-8")
            payload = build_occupancy_payload(
                config_path=root / "occupancy" / "config.json",
                matches_path=empty_ics,
                start="2026-08-11",
                end="2026-08-12",
                season="Sommer",
                generated_at=datetime(2026, 8, 10, 18, 0, tzinfo=timezone.utc),
            )

        actual = {
            (event["title"], event["resourceId"], event["start"][11:16], event["end"][11:16])
            for event in payload["events"]
            if event["source"] == "training"
        }
        self.assertEqual(
            actual,
            {
                ("G", "kunstrasen", "16:30", "17:30"),
                ("F", "kunstrasen", "16:45", "18:15"),
                ("E1", "rasen", "17:00", "18:30"),
                ("A", "rasen", "18:30", "20:00"),
            },
        )

    def test_final_weekly_schedule_sizes(self) -> None:
        root = Path(__file__).resolve().parents[1]
        config = json.loads((root / "occupancy" / "config.json").read_text(encoding="utf-8"))
        self.assertEqual(len(config["seasons"]["Sommer"]["weekly"]), 17)
        self.assertEqual(len(config["seasons"]["Winter"]["weekly"]), 18)
        self.assertEqual(config["effective_from"], "2026-08-11")


if __name__ == "__main__":
    unittest.main()
