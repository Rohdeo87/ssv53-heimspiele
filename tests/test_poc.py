import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import requests

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from poc_scraper import (
    Client,
    RateLimitError,
    SecurityLockError,
    RequestBudgetExceeded,
    ScrapeError,
    Team,
    VenueRule,
    apply_venue_rules,
    evaluate_quality,
    parse_matchplan,
    primary_matchplan_url,
)


def response(status: int, text: str = "", headers: dict | None = None) -> requests.Response:
    item = requests.Response()
    item.status_code = status
    item._content = text.encode("utf-8")
    item.headers.update(headers or {})
    item.url = "https://www.fussball.de/test"
    return item


def client_config(**overrides):
    request = {
        "timeout_seconds": 5,
        "max_retries": 1,
        "delay_seconds": 3.0,
        "jitter_seconds": 0.0,
        "max_requests_per_run": 10,
        "user_agent": "SSV53-Test/1.0 (mailto:test@ssv53.de)",
    }
    request.update(overrides)
    return {"request": request}


class PocParserTest(unittest.TestCase):
    def test_parser_and_mapping(self):
        fixture = (ROOT / "tests" / "fixture_matchplan.html").read_text(encoding="utf-8")
        team = Team(name="Test", team_id="TEAM")
        matches = parse_matchplan(fixture, team, "fixture://matchplan")
        self.assertEqual(4, len(matches))
        rules_data = json.loads(
            (ROOT / "config.example.json").read_text(encoding="utf-8")
        )["venue_rules"]
        rules = [VenueRule(**item) for item in rules_data]
        for match in matches:
            apply_venue_rules(match, rules, "review")
        by_number = {m.match_number: m for m in matches}
        self.assertEqual(
            ("include", "Rasen"),
            (by_number["710029006"].decision, by_number["710029006"].calendar),
        )
        self.assertEqual("exclude", by_number["810005035"].decision)
        self.assertEqual("exclude", by_number["810005036"].decision)
        self.assertEqual(
            ("include", "Kunstrasen"),
            (by_number["610000001"].decision, by_number["610000001"].calendar),
        )
        self.assertTrue(
            by_number["710029006"].detail_url.endswith(
                "031C84AB5K000000VS5489BUVUR5FS5A"
            )
        )
        self.assertEqual(
            "031C84AB5K000000VS5489BUVUR5FS5A",
            by_number["710029006"].external_id,
        )
        self.assertEqual(
            "2026-08-21T19:00+02:00",
            by_number["710029006"].kickoff,
        )
        self.assertEqual(
            "2026-08-05T19:00+02:00",
            by_number["810005035"].kickoff,
        )
        self.assertEqual(
            "2026-08-22T10:00+02:00",
            by_number["610000001"].kickoff,
        )


    def test_unparsed_detail_link_aborts_instead_of_silently_losing_a_game(self):
        fixture = (ROOT / "tests" / "fixture_matchplan.html").read_text(encoding="utf-8")
        fixture = fixture.replace(
            "</table>",
            '<tr class="unexpected"><td><a href="/spiel/x-y/-/spiel/MISSINGGAME123456789">Zum Spiel</a></td></tr></table>',
        )
        with self.assertRaises(ScrapeError):
            parse_matchplan(fixture, Team(name="Test", team_id="TEAM"), "fixture://matchplan")

    def test_quality_guard_rejects_too_few_rasen_matches(self):
        fixture = (ROOT / "tests" / "fixture_matchplan.html").read_text(encoding="utf-8")
        team = Team(name="Test", team_id="TEAM")
        audit = {}
        matches = parse_matchplan(fixture, team, "fixture://matchplan", audit=audit)
        rules_data = json.loads(
            (ROOT / "config.example.json").read_text(encoding="utf-8")
        )["venue_rules"]
        rules = [VenueRule(**item) for item in rules_data]
        for match in matches:
            apply_venue_rules(match, rules, "review")
        report = evaluate_quality(
            matches,
            [audit],
            {
                "quality_guard": {
                    "require_no_review": True,
                    "minimum_included_by_calendar": {"Rasen": 2},
                }
            },
        )
        self.assertFalse(report["publishable"])
        self.assertTrue(any("Rasen" in error for error in report["errors"]))

    def test_spielfrei_is_excluded_without_venue_review(self):
        from poc_scraper import Match

        match = Match(
            external_id="610436029",
            match_number="610436029",
            team_id="TEAM",
            team_name="Herren Ü40",
            team_role="home",
            kickoff="2027-04-09T19:00+02:00",
            home_team="Schönwalder SV (Ü40)",
            away_team="spielfrei",
            competition="Kreisliga",
            match_type="ME",
            status="",
            venue_raw="",
            detail_url="",
            source_url="fixture://matchplan",
            warnings=["Spielstätte fehlt"],
        )
        apply_venue_rules(match, [], "review")
        self.assertEqual("exclude", match.decision)
        self.assertEqual("Spielfrei", match.venue_rule)
        self.assertNotIn("Spielstätte fehlt", match.warnings)

    def test_regular_endpoint_loads_all_matches_not_only_formal_home_games(self):
        cfg = {"request": {
            "matchplan_endpoint_template":
                "https://www.fussball.de/ajax.team.matchplan/-/mime-type/HTML/"
                "show-venues/true/datum-von/{date_from}/datum-bis/{date_to}/"
                "team-id/{team_id}"
        }}
        url = primary_matchplan_url(cfg, "TEAM", "2026-07-01", "2027-06-30")
        self.assertNotIn("match-type/1", url)
        self.assertIn("show-venues/true", url)

    def test_formal_away_game_on_platz_1_is_included(self):
        fixture = """
        <div class='club-matchplan-table'><table>
          <tr class='row-headline'><td>Freitag, 12.03.2027 - 19:00 Uhr | Kreisliga</td></tr>
          <tr class='row-competition'><td class='column-date'>Fr, 12.03.27 | 19:00</td>
            <td class='column-competition'>Kreisliga ME | 610000099</td></tr>
          <tr class='row-game'><td><div class='club-name'>Testverein</div></td>
            <td><div class='club-name'>Schönwalder SV (Ü40)</div></td>
            <td><a href='/spiel/a-b/-/spiel/AWAYATPLATZ100000000000000000'>Zum Spiel</a></td></tr>
          <tr><td class='location'>Rasenplatz, Sportplatz Schönwalde Strandbad, Platz 1, Kurmärkische Str. 2</td></tr>
        </table></div>
        """
        team = Team(
            name="Herren Ü40",
            team_id="TEAM",
            home_aliases=["Schönwalder SV (Ü40)"],
        )
        matches = parse_matchplan(fixture, team, "fixture://all-matches")
        self.assertEqual(1, len(matches))
        rules_data = json.loads(
            (ROOT / "config.example.json").read_text(encoding="utf-8")
        )["venue_rules"]
        apply_venue_rules(matches[0], [VenueRule(**item) for item in rules_data], "review")
        self.assertEqual("away", matches[0].team_role)
        self.assertEqual(("include", "Rasen"), (matches[0].decision, matches[0].calendar))


class RequestProtectionTest(unittest.TestCase):
    def make_client(self, config=None, state_path=None):
        client = Client(config or client_config(), state_path=state_path)
        client._throttle = lambda: None
        return client

    def test_hard_safety_limits_cannot_be_raised_by_config(self):
        client = self.make_client(
            client_config(
                max_retries=99,
                max_requests_per_run=999,
                delay_seconds=0,
            )
        )
        self.assertEqual(1, client.max_retries)
        self.assertEqual(10, client.max_requests)
        self.assertGreaterEqual(client.delay, 3.0)

    def test_403_sets_permanent_global_security_lock(self):
        with tempfile.TemporaryDirectory() as folder:
            state_path = Path(folder) / "request_state.json"
            client = self.make_client(state_path=state_path)
            client.session = Mock()
            client.session.get.return_value = response(403, "Forbidden")
            with self.assertRaises(SecurityLockError):
                client.get_text("https://www.fussball.de/403")
            self.assertEqual(1, client.session.get.call_count)
            state = json.loads(state_path.read_text(encoding="utf-8"))
            self.assertTrue(state["security_lock"])
            self.assertTrue(state["manual_unlock_required"])
            self.assertEqual(403, state["security_lock_http_status"])
            self.assertEqual("security_locked", state["last_status"])

    def test_406_sets_permanent_global_security_lock(self):
        with tempfile.TemporaryDirectory() as folder:
            state_path = Path(folder) / "request_state.json"
            client = self.make_client(state_path=state_path)
            client.session = Mock()
            client.session.get.return_value = response(406, "Not Acceptable")
            with self.assertRaises(SecurityLockError):
                client.get_text("https://www.fussball.de/406")
            self.assertEqual(1, client.session.get.call_count)
            state = json.loads(state_path.read_text(encoding="utf-8"))
            self.assertTrue(state["security_lock"])
            self.assertEqual(406, state["security_lock_http_status"])

    def test_challenge_page_with_http_200_sets_security_lock(self):
        with tempfile.TemporaryDirectory() as folder:
            state_path = Path(folder) / "request_state.json"
            client = self.make_client(state_path=state_path)
            client.session = Mock()
            client.session.get.return_value = response(
                200,
                "<html><title>Just a moment...</title>"
                "<script src='/cdn-cgi/challenge-platform/x.js'></script></html>",
                {"Content-Type": "text/html; charset=UTF-8", "Server": "cloudflare"},
            )
            with self.assertRaises(SecurityLockError):
                client.get_text("https://www.fussball.de/challenge")
            self.assertEqual(1, client.session.get.call_count)
            state = json.loads(state_path.read_text(encoding="utf-8"))
            self.assertTrue(state["security_lock"])
            self.assertIn("Challenge", state["security_lock_reason"])

    def test_existing_security_lock_prevents_any_network_request(self):
        with tempfile.TemporaryDirectory() as folder:
            state_path = Path(folder) / "request_state.json"
            state_path.write_text(json.dumps({
                "security_lock": True,
                "security_lock_reason": "HTTP 403",
                "security_lock_at": "2026-07-24T12:00:00+00:00",
                "manual_unlock_required": True,
            }), encoding="utf-8")
            client = self.make_client(state_path=state_path)
            client.session = Mock()
            with self.assertRaises(SecurityLockError):
                client.get_text("https://www.fussball.de/never-called")
            client.session.get.assert_not_called()

    def test_404_is_not_retried(self):
        client = self.make_client()
        client.session = Mock()
        client.session.get.return_value = response(404, "nicht gefunden")
        with self.assertRaises(ScrapeError):
            client.get_text("https://www.fussball.de/404")
        self.assertEqual(1, client.session.get.call_count)

    @patch("poc_scraper.time.sleep", return_value=None)
    def test_503_is_retried_only_once(self, _sleep):
        client = self.make_client()
        client.session = Mock()
        client.session.get.side_effect = [
            response(503, "temporär"),
            response(200, "club-matchplan-table"),
        ]
        result = client.get_text("https://www.fussball.de/503")
        self.assertIn("club-matchplan-table", result)
        self.assertEqual(2, client.session.get.call_count)

    def test_429_stops_immediately_and_persists_retry_after(self):
        with tempfile.TemporaryDirectory() as folder:
            state_path = Path(folder) / "request_state.json"
            client = self.make_client(state_path=state_path)
            client.session = Mock()
            client.session.get.return_value = response(
                429, "zu viele", {"Retry-After": "120"}
            )
            with self.assertRaises(RateLimitError):
                client.get_text("https://www.fussball.de/429")
            self.assertEqual(1, client.session.get.call_count)
            state = json.loads(state_path.read_text(encoding="utf-8"))
            self.assertEqual("rate_limited", state["last_status"])
            self.assertEqual(120, state["last_retry_after_seconds"])
            self.assertTrue(state["blocked_until"])

    def test_request_budget_stops_before_an_extra_network_call(self):
        client = self.make_client(client_config(max_requests_per_run=1))
        client.session = Mock()
        client.session.get.return_value = response(200, "club-matchplan-table")
        client.get_text("https://www.fussball.de/first")
        with self.assertRaises(RequestBudgetExceeded):
            client.get_text("https://www.fussball.de/second")
        self.assertEqual(1, client.session.get.call_count)


if __name__ == "__main__":
    unittest.main()


class ScheduleProtectionTest(unittest.TestCase):
    def setUp(self):
        from schedule_guard import ScheduleSettings

        self.settings = ScheduleSettings(
            timezone="Europe/Berlin",
            window_start="06:00",
            window_end="22:00",
            random_delay_min_seconds=120,
            random_delay_max_seconds=720,
            closing_margin_seconds=60,
        )

    def test_window_is_open_from_0600_until_before_2200(self):
        from datetime import datetime
        from schedule_guard import is_inside_window

        self.assertFalse(
            is_inside_window(datetime.fromisoformat("2026-01-15T05:59:59+01:00"), self.settings)
        )
        self.assertTrue(
            is_inside_window(datetime.fromisoformat("2026-01-15T06:00:00+01:00"), self.settings)
        )
        self.assertTrue(
            is_inside_window(datetime.fromisoformat("2026-07-15T21:59:59+02:00"), self.settings)
        )
        self.assertFalse(
            is_inside_window(datetime.fromisoformat("2026-07-15T22:00:00+02:00"), self.settings)
        )

    def test_random_delay_stays_between_two_and_twelve_minutes(self):
        import random
        from datetime import datetime
        from schedule_guard import choose_delay_seconds

        delay = choose_delay_seconds(
            datetime.fromisoformat("2026-07-15T12:00:00+02:00"),
            self.settings,
            rng=random.Random(42),
        )
        self.assertIsNotNone(delay)
        self.assertGreaterEqual(delay, 120)
        self.assertLessEqual(delay, 720)

    def test_run_is_skipped_too_close_to_2200(self):
        from datetime import datetime
        from schedule_guard import choose_delay_seconds

        self.assertIsNone(
            choose_delay_seconds(
                datetime.fromisoformat("2026-07-15T21:58:30+02:00"),
                self.settings,
            )
        )


class EndpointCompletenessTest(unittest.TestCase):
    def test_primary_endpoint_requests_complete_non_paginated_matchplan(self):
        from poc_scraper import primary_matchplan_url

        config = json.loads((ROOT / "config.json").read_text(encoding="utf-8"))
        url = primary_matchplan_url(
            config,
            "TEAMID",
            "2026-07-01",
            "2027-06-30",
        )
        self.assertNotIn("/mode/PAGE/", url)
        self.assertNotIn("/match-type/1/", url)
        self.assertIn("/show-venues/true/", url)
        self.assertTrue(url.endswith("/team-id/TEAMID"))
