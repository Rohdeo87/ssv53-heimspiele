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
                    "SUMMARY:Schönwalder SV (Ü40): Schönwalder SV (Ü40) – SG Bornim Ü40",
                    "LOCATION:Rasenplatz\\, Schönwalde",
                    "DESCRIPTION:Fr\\, | Herren Ü40 | Kreispokal | https://www.fussball.de/spiel/test",
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

    def test_duplicate_ue40_team_prefix_is_removed(self) -> None:
        payload = self._payload("2026-08-21", "2026-08-22")
        match = next(event for event in payload["events"] if event["source"] == "match")
        self.assertEqual(match["title"], "Schönwalder SV (Ü40) – SG Bornim Ü40")
        self.assertEqual(match["team"], "Schönwalder SV (Ü40)")

    def test_duplicate_spielgemeinschaft_prefix_is_removed(self) -> None:
        self.matches.write_text(
            self.matches.read_text(encoding="utf-8").replace(
                "Schönwalder SV (Ü40): Schönwalder SV (Ü40) – SG Bornim Ü40",
                "Spielgemeinschaft Schönwalde-Perwenitz-Paaren: "
                "Spielgemeinschaft Schönwalde-Perwenitz-Paaren – VfL Nauen II",
            ),
            encoding="utf-8",
        )
        payload = self._payload("2026-08-21", "2026-08-22")
        match = next(event for event in payload["events"] if event["source"] == "match")
        self.assertEqual(
            match["title"],
            "Spielgemeinschaft Schönwalde-Perwenitz-Paaren – VfL Nauen II",
        )
        self.assertEqual(
            match["team"],
            "Spielgemeinschaft Schönwalde-Perwenitz-Paaren",
        )

    def test_duplicate_youth_team_prefix_is_structured_without_opponent(self) -> None:
        self.matches.write_text(
            self.matches.read_text(encoding="utf-8").replace(
                "Schönwalder SV (Ü40): Schönwalder SV (Ü40) – SG Bornim Ü40",
                "Schönwalder SV E2: Schönwalder SV E2 – Gegnerische D1",
            ),
            encoding="utf-8",
        )
        payload = self._payload("2026-08-21", "2026-08-22")
        match = next(event for event in payload["events"] if event["source"] == "match")
        self.assertEqual(match["title"], "Schönwalder SV E2 – Gegnerische D1")
        self.assertEqual(match["team"], "Schönwalder SV E2")

    def test_summary_without_duplicate_prefix_is_unchanged(self) -> None:
        self.matches.write_text(
            self.matches.read_text(encoding="utf-8").replace(
                "Schönwalder SV (Ü40): Schönwalder SV (Ü40) – SG Bornim Ü40",
                "Schönwalder SV (Ü40) – SG Bornim Ü40",
            ),
            encoding="utf-8",
        )
        payload = self._payload("2026-08-21", "2026-08-22")
        match = next(event for event in payload["events"] if event["source"] == "match")
        self.assertEqual(match["title"], "Schönwalder SV (Ü40) – SG Bornim Ü40")
        self.assertEqual(match["team"], "")

    def test_description_url_is_extracted_and_cleaned(self) -> None:
        payload = self._payload("2026-08-21", "2026-08-22")
        match = next(event for event in payload["events"] if event["source"] == "match")
        self.assertEqual(match["description"], "Herren Ü40 · Kreispokal")
        self.assertEqual(match["detailLink"], "https://www.fussball.de/spiel/test")
        self.assertNotIn("http", match["description"])

        self.matches.write_text(
            self.matches.read_text(encoding="utf-8").replace("https://", "http://"),
            encoding="utf-8",
        )
        payload = self._payload("2026-08-21", "2026-08-22")
        match = next(event for event in payload["events"] if event["source"] == "match")
        self.assertEqual(match["detailLink"], "http://www.fussball.de/spiel/test")

    def test_match_normalization_keeps_safety_times_unchanged(self) -> None:
        payload = self._payload("2026-08-21", "2026-08-22")
        match = next(event for event in payload["events"] if event["source"] == "match")
        self.assertEqual(match["start"], "2026-08-21T19:00:00+02:00")
        self.assertEqual(match["end"], "2026-08-21T20:30:00+02:00")
        self.assertEqual(match["occupancyStart"], "2026-08-21T18:00:00+02:00")
        self.assertEqual(match["occupancyEnd"], "2026-08-21T21:30:00+02:00")

    def test_appack_maps_azure_detail_link_to_existing_popup_field(self) -> None:
        appack = (
            Path(__file__).resolve().parents[1]
            / "appack-platzbelegungsplan-azure.html"
        ).read_text(encoding="utf-8")
        self.assertIn(
            'detailLink: String(item.detailLink || "").trim()',
            appack,
        )
        self.assertIn('eventKind: source,', appack)
        self.assertIn('team: String(item.team || "")', appack)
        self.assertIn('extendedProps: extendedProps', appack)

    def test_unknown_season_is_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "Unbekannte Saison"):
            self._payload("2026-08-10", "2026-08-12", season="Frühling")

    def test_range_is_limited(self) -> None:
        with self.assertRaisesRegex(ValueError, "Maximal 63 Tage"):
            self._payload("2026-08-10", "2026-11-01")

    def test_structured_match_source_includes_rasen_and_kunstrasen(self) -> None:
        structured = self.root / "matches.json"
        base = {
            "start": "2026-09-12T10:00+02:00",
            "end": "2026-09-12T11:00+02:00",
            "occupancyStart": "2026-09-12T09:00+02:00",
            "occupancyEnd": "2026-09-12T12:00+02:00",
            "kickoff": "2026-09-12T10:00+02:00",
            "matchDurationMinutes": 60,
            "durationRule": "flb-jugendordnung-2025-12-13-d-2x30",
            "competitionFormat": "league",
            "team": "Schönwalder SV",
            "teamCategory": "D-Junioren | 1. Kreisklasse",
            "teamRole": "home",
            "homeTeam": "Schönwalder SV",
            "awayTeam": "Gast",
            "competition": "D-Junioren | 1. Kreisklasse",
            "description": "D-Junioren · 1. Kreisklasse",
            "detailLink": "https://www.fussball.de/spiel/test",
        }
        structured.write_text(
            json.dumps(
                {
                    "schemaVersion": 2,
                    "status": "ok",
                    "matches": [
                        {
                            **base,
                            "id": "dfb:rasen",
                            "title": "Schönwalder SV – Gast Rasen",
                            "calendar": "Rasen",
                            "place": "rasen",
                        },
                        {
                            **base,
                            "id": "dfb:kunstrasen",
                            "title": "Schönwalder SV – Gast Kunstrasen",
                            "calendar": "Kunstrasen",
                            "place": "kunstrasen",
                        },
                    ],
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        payload = build_occupancy_payload(
            config_path=self.config,
            matches_path=structured,
            start="2026-09-12",
            end="2026-09-13",
            season="Sommer",
        )
        matches = [event for event in payload["events"] if event["source"] == "match"]
        self.assertEqual({"rasen", "kunstrasen"}, {item["resourceId"] for item in matches})
        self.assertTrue(all(item["start"].startswith("2026-09-12T10:00") for item in matches))
        self.assertTrue(all(item["occupancyStart"].startswith("2026-09-12T09:00") for item in matches))

    def test_structured_match_source_rejects_missing_safety_buffer(self) -> None:
        structured = self.root / "matches.json"
        structured.write_text(
            json.dumps(
                {
                    "schemaVersion": 2,
                    "status": "ok",
                    "matches": [
                        {
                            "id": "dfb:unsafe",
                            "title": "Schönwalder SV – Gast",
                            "start": "2026-09-12T10:00+02:00",
                            "end": "2026-09-12T11:00+02:00",
                            "kickoff": "2026-09-12T10:00+02:00",
                            "occupancyStart": "2026-09-12T09:30+02:00",
                            "occupancyEnd": "2026-09-12T12:00+02:00",
                            "matchDurationMinutes": 60,
                            "durationRule": "test",
                            "competitionFormat": "league",
                            "team": "Schönwalder SV",
                            "calendar": "Rasen",
                        }
                    ],
                }
            ),
            encoding="utf-8",
        )
        with self.assertRaisesRegex(ValueError, "60-Minuten-Spielvorlauf"):
            build_occupancy_payload(
                config_path=self.config,
                matches_path=structured,
                start="2026-09-12",
                end="2026-09-13",
                season="Sommer",
            )


class ProductionScheduleTests(unittest.TestCase):
    def test_e2_monday_uses_new_time_only_in_summer(self) -> None:
        root = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as temporary:
            empty_ics = Path(temporary) / "empty.ics"
            empty_ics.write_text("BEGIN:VCALENDAR\nEND:VCALENDAR\n", encoding="utf-8")
            summer = build_occupancy_payload(
                config_path=root / "occupancy" / "config.json",
                matches_path=empty_ics,
                start="2026-08-24",
                end="2026-08-25",
                season="Sommer",
            )
            winter = build_occupancy_payload(
                config_path=root / "occupancy" / "config.json",
                matches_path=empty_ics,
                start="2026-08-24",
                end="2026-08-25",
                season="Winter",
            )

        summer_e2 = next(
            event
            for event in summer["events"]
            if event["source"] == "training" and event["scheduleId"] == "som-kr-e2-mo"
        )
        winter_e2 = next(
            event
            for event in winter["events"]
            if event["source"] == "training" and event["scheduleId"] == "win-kr-e2-mo"
        )
        self.assertEqual(summer_e2["resourceId"], "kunstrasen")
        self.assertEqual(summer_e2["start"], "2026-08-24T16:45:00+02:00")
        self.assertEqual(summer_e2["end"], "2026-08-24T18:15:00+02:00")
        self.assertEqual(winter_e2["resourceId"], "kunstrasen")
        self.assertEqual(winter_e2["start"], "2026-08-24T17:00:00+02:00")
        self.assertEqual(winter_e2["end"], "2026-08-24T18:30:00+02:00")

    def test_ue40_monday_uses_grass_only_in_summer(self) -> None:
        root = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as temporary:
            empty_ics = Path(temporary) / "empty.ics"
            empty_ics.write_text("BEGIN:VCALENDAR\nEND:VCALENDAR\n", encoding="utf-8")
            summer = build_occupancy_payload(
                config_path=root / "occupancy" / "config.json",
                matches_path=empty_ics,
                start="2026-08-24",
                end="2026-08-25",
                season="Sommer",
            )
            winter = build_occupancy_payload(
                config_path=root / "occupancy" / "config.json",
                matches_path=empty_ics,
                start="2026-08-24",
                end="2026-08-25",
                season="Winter",
            )

        summer_ue40 = next(
            event
            for event in summer["events"]
            if event["source"] == "training" and event["scheduleId"] == "som-kr-ue40-mo"
        )
        winter_ue40 = next(
            event
            for event in winter["events"]
            if event["source"] == "training" and event["scheduleId"] == "win-kr-ue40-mo"
        )
        self.assertEqual(summer_ue40["resourceId"], "rasen")
        self.assertEqual(summer_ue40["start"], "2026-08-24T19:30:00+02:00")
        self.assertEqual(summer_ue40["end"], "2026-08-24T21:00:00+02:00")
        self.assertEqual(winter_ue40["resourceId"], "kunstrasen")

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
