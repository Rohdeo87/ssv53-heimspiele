from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from occupancy.match_model import MatchTimingError, resolve_match_timing
from poc_scraper import Match, recalculate_event_times


def timing_config() -> dict:
    return json.loads((ROOT / "config.json").read_text(encoding="utf-8"))[
        "event_timing"
    ]


def decision(
    category: str,
    *,
    team: str = "Schönwalder SV",
    competition: str = "Kreisliga",
    match_type: str = "ME",
):
    return resolve_match_timing(
        team_name=team,
        team_category=category,
        competition=competition,
        match_type=match_type,
        timing_config=timing_config(),
    )


class MatchTimingRulesTest(unittest.TestCase):
    def test_senior_league_durations(self) -> None:
        cases = (
            ("Herren | Kreisliga", "Spielgemeinschaft Schönwalde", 105, "HERREN"),
            ("Herren Ü40 | Kreisliga", "Schönwalder SV (Ü40)", 105, "UE40"),
            ("Herren Ü50 | Kreisliga", "SpG Perwenitz/Schönwalde Ü50", 105, "UE50"),
        )
        for category, team, minutes, age_class in cases:
            with self.subTest(category=category):
                result = decision(category, team=team)
                self.assertEqual(minutes, result.minutes)
                self.assertEqual(age_class, result.age_class)

    def test_ue40_cup_uses_current_age_class_rule_without_invented_extension(self) -> None:
        result = decision(
            "Herren Ü40 | Kreispokal",
            team="Schönwalder SV (Ü40)",
            competition="Herren Ü40 | Kreispokal",
            match_type="PO",
        )
        self.assertEqual(105, result.minutes)
        self.assertEqual("cup", result.competition_format)
        self.assertNotIn("cup-max", result.duration_rule)

    def test_all_regular_youth_age_classes(self) -> None:
        expected = {"A": 105, "B": 95, "C": 85, "D": 75, "E": 65, "F": 55, "G": 55}
        for age_class, minutes in expected.items():
            with self.subTest(age_class=age_class):
                result = decision(f"{age_class}-Junioren | Kreisliga")
                self.assertEqual(minutes, result.minutes)
                self.assertEqual(age_class, result.age_class)
                self.assertEqual("league", result.competition_format)

    def test_youth_cup_includes_age_specific_maximum_extension(self) -> None:
        expected = {"A": 135, "B": 115, "C": 95, "D": 85, "E": 75, "F": 65, "G": 65}
        for age_class, minutes in expected.items():
            with self.subTest(age_class=age_class):
                result = decision(
                    f"{age_class}-Junioren | Kreispokal",
                    competition=f"{age_class}-Junioren | Kreispokal",
                    match_type="PO",
                )
                self.assertEqual(minutes, result.minutes)
                self.assertIn("cup-max", result.duration_rule)

    def test_g_youth_festival_uses_current_dfb_maximum(self) -> None:
        result = decision(
            "G-Junioren | Kinderfußball-Festival",
            competition="Kinderfußball-Festival",
            match_type="TU",
        )
        self.assertEqual(49, result.minutes)
        self.assertEqual("festival", result.competition_format)
        self.assertEqual("dfb-kinderfussball-g-festival-max-7x7", result.duration_rule)

    def test_unverified_twin_format_fails_closed(self) -> None:
        with self.assertRaisesRegex(MatchTimingError, "Sonderformat 'twin'"):
            decision(
                "D-Junioren | Twin",
                competition="D-Junioren Zwillingsmodus",
            )

    def test_f_festival_without_concrete_round_format_fails_closed(self) -> None:
        with self.assertRaisesRegex(MatchTimingError, "Sonderformat 'festival'"):
            decision(
                "F-Junioren | Kinderfußball-Festival",
                competition="Kinderfußball-Festival",
                match_type="TU",
            )

    def test_recalculation_keeps_public_and_occupancy_times_separate(self) -> None:
        match = Match(
            external_id="duration-test",
            match_number="1",
            team_id="team",
            team_name="Schönwalder SV",
            team_category="D-Junioren | 1. Kreisklasse",
            team_role="home",
            kickoff="2026-09-12T10:00+02:00",
            home_team="Schönwalder SV",
            away_team="Gast",
            competition="D-Junioren | 1. Kreisklasse",
            match_type="ME",
            status="",
            venue_raw="Sportplatz Schönwalde Strandbad, Platz 1",
            detail_url="https://www.fussball.de/spiel/test/-/spiel/DURATIONTEST",
            source_url="fixture://duration",
        )
        recalculate_event_times(match, {"event_timing": timing_config()})
        self.assertEqual("2026-09-12T10:00+02:00", match.kickoff)
        self.assertEqual("2026-09-12T11:15+02:00", match.match_end)
        self.assertEqual("2026-09-12T09:00+02:00", match.event_start)
        self.assertEqual("2026-09-12T12:15+02:00", match.event_end)
        self.assertEqual(75, match.match_duration_minutes)


if __name__ == "__main__":
    unittest.main()
