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
    Match,
    RateLimitError,
    RequestBudgetExceeded,
    ScrapeError,
    SecurityLockError,
    VenueRule,
    apply_venue_rules,
    build_duplicate_detail_resolver,
    build_initial_windows,
    build_team_registry,
    club_matchplan_url,
    evaluate_quality,
    has_more_results,
    parse_club_matchplan,
    parse_detail_page_reference,
    run,
    split_window,
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


def config() -> dict:
    return json.loads((ROOT / "config.json").read_text(encoding="utf-8"))


def rules() -> list[VenueRule]:
    return [VenueRule(**item) for item in config()["venue_rules"]]


class ClubParserTest(unittest.TestCase):
    def fixture(self) -> str:
        return (ROOT / "tests" / "fixture_club_matchplan.html").read_text(encoding="utf-8")

    def test_all_club_teams_are_parsed_without_fixed_team_list(self):
        matches = parse_club_matchplan(self.fixture(), "fixture://club", config())
        self.assertEqual(5, len(matches))
        self.assertIn("Schönwalder SV 53 I", {m.team_name for m in matches})
        self.assertIn("Spielgemeinschaft Schönwalde-Perwenitz-Paaren", {m.team_name for m in matches})

    def test_new_team_id_and_category_are_extracted(self):
        matches = parse_club_matchplan(self.fixture(), "fixture://club", config())
        item = next(m for m in matches if m.match_number == "610000001")
        self.assertEqual("NEWTEAM000000000000000000000001", item.team_id)
        self.assertEqual("E-Junioren", item.team_category)
        self.assertEqual("home", item.team_role)

    def test_formal_away_game_on_local_pitch_remains_detectable(self):
        matches = parse_club_matchplan(self.fixture(), "fixture://club", config())
        item = next(m for m in matches if m.external_id == "AWAYLOCAL0000000000000000000001")
        self.assertEqual("away", item.team_role)
        apply_venue_rules(item, rules(), "exclude", config()["local_venue_pattern"])
        self.assertEqual(("include", "Rasen"), (item.decision, item.calendar))

    def test_venue_rules_include_both_local_pitches_and_exclude_deetz(self):
        matches = parse_club_matchplan(self.fixture(), "fixture://club", config())
        for item in matches:
            apply_venue_rules(item, rules(), "exclude", config()["local_venue_pattern"])
        by_number = {m.match_number: m for m in matches}
        self.assertEqual(("include", "Rasen"), (by_number["710029006"].decision, by_number["710029006"].calendar))
        self.assertEqual(("include", "Kunstrasen"), (by_number["610000001"].decision, by_number["610000001"].calendar))
        self.assertEqual("exclude", by_number["610479008"].decision)
        self.assertEqual("Auswärtige Spielstätte", by_number["610479008"].venue_rule)

    def test_spielfrei_is_excluded_without_review(self):
        matches = parse_club_matchplan(self.fixture(), "fixture://club", config())
        item = next(m for m in matches if m.match_number == "610090001")
        apply_venue_rules(item, rules(), "exclude", config()["local_venue_pattern"])
        self.assertEqual("exclude", item.decision)
        self.assertEqual("Spielfrei", item.venue_rule)

    def test_requested_timing_is_60_minutes_before_and_after_the_match(self):
        matches = parse_club_matchplan(self.fixture(), "fixture://club", config())
        item = next(m for m in matches if m.match_number == "610000001")
        self.assertEqual("2026-08-22T09:00+02:00", item.event_start)
        self.assertEqual("2026-08-22T12:30+02:00", item.event_end)

    def test_identical_duplicate_detail_id_is_collapsed_safely(self):
        fixture = self.fixture()
        start = fixture.index('<tr class="row-headline">')
        end_marker = '</tr>\n\n<tr class="row-headline">'
        end = fixture.index(end_marker, start) + len('</tr>')
        duplicate_block = fixture[start:end]
        fixture = fixture.replace('</table>', duplicate_block + '\n</table>')
        audit = {}
        matches = parse_club_matchplan(fixture, "fixture://duplicate", config(), audit)
        self.assertEqual(5, len(matches))
        self.assertEqual(
            ["031C84AB5K000000VS5489BUVUR5FS5A"],
            audit["collapsed_duplicate_detail_ids"],
        )
        self.assertEqual([], audit["duplicate_detail_ids"])
        merged = next(
            item for item in matches
            if item.external_id == "031C84AB5K000000VS5489BUVUR5FS5A"
        )
        self.assertTrue(any("Mehrfachdarstellung" in warning for warning in merged.warnings))

    def test_conflicting_duplicate_detail_id_still_aborts(self):
        fixture = self.fixture()
        start = fixture.index('<tr class="row-headline">')
        end_marker = '</tr>\n\n<tr class="row-headline">'
        end = fixture.index(end_marker, start) + len('</tr>')
        duplicate_block = fixture[start:end].replace(
            'Fr, 21.08.26 | 19:00',
            'Fr, 21.08.26 | 20:00',
        ).replace(
            'Freitag, 21.08.2026 - 19:00 Uhr',
            'Freitag, 21.08.2026 - 20:00 Uhr',
        )
        fixture = fixture.replace('</table>', duplicate_block + '\n</table>')
        with self.assertRaisesRegex(ScrapeError, "Widersprüchliche Mehrfachdarstellungen"):
            parse_club_matchplan(fixture, "fixture://conflict", config())

    def test_detail_page_title_provides_canonical_date(self):
        reference = parse_detail_page_reference(
            "<html><head><title>Spiel A - Spiel B - 04.09.2026</title></head></html>"
        )
        self.assertEqual(["2026-09-04"], reference["dates"])
        self.assertEqual([], reference["exact_kickoffs"])

    def test_kickoff_conflict_is_resolved_by_official_detail_page(self):
        fixture = self.fixture()
        start = fixture.index('<tr class="row-headline">')
        end_marker = '</tr>\n\n<tr class="row-headline">'
        end = fixture.index(end_marker, start) + len('</tr>')
        duplicate_block = fixture[start:end].replace(
            'Fr, 21.08.26 | 19:00',
            'Sa, 22.08.26 | 20:00',
        ).replace(
            'Freitag, 21.08.2026 - 19:00 Uhr',
            'Samstag, 22.08.2026 - 20:00 Uhr',
        )
        fixture = fixture.replace('</table>', duplicate_block + '\n</table>')
        client = Mock()
        client.get_text.return_value = (
            '<html><head><title>Schönwalder SV - SG Bornim - 21.08.2026</title></head></html>'
        )
        audit = {}
        resolver = build_duplicate_detail_resolver(client, config())
        matches = parse_club_matchplan(
            fixture,
            "fixture://resolved-conflict",
            config(),
            audit=audit,
            duplicate_resolver=resolver,
        )
        item = next(
            match for match in matches
            if match.external_id == "031C84AB5K000000VS5489BUVUR5FS5A"
        )
        self.assertEqual("2026-08-21T19:00+02:00", item.kickoff)
        self.assertEqual("2026-08-21T18:00+02:00", item.event_start)
        self.assertEqual("2026-08-21T21:30+02:00", item.event_end)
        self.assertEqual([], audit["duplicate_detail_ids"])
        self.assertEqual(1, len(audit["duplicate_resolutions"]))
        self.assertTrue(audit["duplicate_resolutions"][0]["resolution_attempt"]["resolved"])
        client.get_text.assert_called_once()

    def test_unparsed_detail_link_aborts(self):
        fixture = self.fixture().replace(
            "</div>",
            '</div><a href="/spiel/x-y/-/spiel/MISSINGGAME000000000000000001">Zum Spiel</a>',
            1,
        )
        with self.assertRaises(ScrapeError):
            parse_club_matchplan(fixture, "fixture://club", config())

    def test_empty_window_is_valid(self):
        fixture = (ROOT / "tests" / "fixture_empty_matchplan.html").read_text(encoding="utf-8")
        audit = {}
        matches = parse_club_matchplan(fixture, "fixture://empty", config(), audit)
        self.assertEqual([], matches)
        self.assertEqual(0, audit["competition_rows"])

    def test_more_load_marker_is_detected(self):
        fixture = (ROOT / "tests" / "fixture_truncated_matchplan.html").read_text(encoding="utf-8")
        self.assertTrue(has_more_results(fixture))
        self.assertFalse(has_more_results(self.fixture()))


class WindowAndQualityTest(unittest.TestCase):
    def test_initial_quarter_windows_cover_the_full_season(self):
        windows = build_initial_windows("2026-07-01", "2027-06-30", 3)
        self.assertEqual([
            ("2026-07-01", "2026-09-30"),
            ("2026-10-01", "2026-12-31"),
            ("2027-01-01", "2027-03-31"),
            ("2027-04-01", "2027-06-30"),
        ], windows)

    def test_window_split_is_non_overlapping_and_complete(self):
        self.assertEqual(
            [("2026-07-01", "2026-07-16"), ("2026-07-17", "2026-07-31")],
            split_window("2026-07-01", "2026-07-31"),
        )

    def test_club_endpoint_uses_club_id_venues_dates_and_max_rows(self):
        url = club_matchplan_url(config(), "2026-07-01", "2026-09-30")
        self.assertIn("ajax.club.matchplan", url)
        self.assertIn("00ES8GNBL0000049VV0AG08LVUPGND5I", url)
        self.assertIn("show-venues/true", url)
        self.assertIn("datum-von/2026-07-01", url)
        self.assertIn("datum-bis/2026-09-30", url)
        self.assertIn("max/50", url)
        self.assertNotIn("team-id", url)

    def test_quality_accepts_complete_coverage_without_arbitrary_match_minimum(self):
        audits = [
            {"date_from": "2026-07-01", "date_to": "2026-12-31", "accepted": True, "truncated": False, "missing_detail_ids": [], "duplicate_detail_ids": []},
            {"date_from": "2027-01-01", "date_to": "2027-06-30", "accepted": True, "truncated": False, "missing_detail_ids": [], "duplicate_detail_ids": []},
        ]
        cfg = config()
        report = evaluate_quality([], audits, cfg, 2)
        self.assertTrue(report["publishable"])

    def test_quality_rejects_a_gap_in_window_coverage(self):
        audits = [
            {"date_from": "2026-07-01", "date_to": "2026-12-30", "accepted": True, "truncated": False, "missing_detail_ids": [], "duplicate_detail_ids": []},
            {"date_from": "2027-01-01", "date_to": "2027-06-30", "accepted": True, "truncated": False, "missing_detail_ids": [], "duplicate_detail_ids": []},
        ]
        report = evaluate_quality([], audits, config(), 2)
        self.assertFalse(report["publishable"])
        self.assertTrue(any("lückenlos" in error for error in report["errors"]))

    def test_quality_rejects_review_matches(self):
        item = Match(
            external_id="X",
            match_number="",
            team_id="T",
            team_name="Test",
            team_category="Test",
            team_role="unknown",
            kickoff="2026-08-01T10:00+02:00",
            home_team="Test",
            away_team="Gast",
            competition="Liga",
            match_type="ME",
            status="",
            venue_raw="Sportplatz Schönwalde Strandbad",
            detail_url="https://www.fussball.de/spiel/a-b/-/spiel/REVIEW000000000000000000000001",
            source_url="fixture://x",
            decision="review",
            event_start="2026-08-01T09:00+02:00",
            event_end="2026-08-01T11:00+02:00",
        )
        audits = [{"date_from": "2026-07-01", "date_to": "2027-06-30", "accepted": True, "truncated": False, "missing_detail_ids": [], "duplicate_detail_ids": []}]
        report = evaluate_quality([item], audits, config(), 1)
        self.assertFalse(report["publishable"])
        self.assertTrue(any("Platzprüfung" in error for error in report["errors"]))

    def test_adaptive_split_replaces_a_truncated_parent_window(self):
        cfg = config()
        cfg["date_from"] = "2026-07-01"
        cfg["date_to"] = "2026-07-02"
        cfg["adaptive_windows"]["initial_window_months"] = 12
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            cfg_path = root / "config.json"
            cfg_path.write_text(json.dumps(cfg), encoding="utf-8")
            output = root / "generated"
            state = root / "state.json"
            registry = root / "registry.json"
            truncated = (ROOT / "tests" / "fixture_truncated_matchplan.html").read_text(encoding="utf-8")
            empty = (ROOT / "tests" / "fixture_empty_matchplan.html").read_text(encoding="utf-8")
            responses = iter([truncated, empty, empty])
            def fake_get_text(client, _url):
                client.request_count += 1
                return next(responses)
            with patch.object(Client, "get_text", new=fake_get_text):
                result = run(cfg_path, output, state, registry)
            self.assertEqual(0, result)
            summary = json.loads((output / "summary.json").read_text(encoding="utf-8"))
            self.assertEqual(3, summary["request_count"])
            self.assertEqual(2, len(summary["accepted_windows"]))


class RegistryTest(unittest.TestCase):
    def test_registry_reports_new_and_known_teams(self):
        fixture = (ROOT / "tests" / "fixture_club_matchplan.html").read_text(encoding="utf-8")
        matches = parse_club_matchplan(fixture, "fixture://club", config())
        first = build_team_registry(matches, {"teams": []}, config()["club_id"])
        self.assertIn("Schönwalder SV 53 I", first["changes"]["new"])
        second = build_team_registry(matches, first, config()["club_id"])
        self.assertEqual([], second["changes"]["new"])
        self.assertIn("Schönwalder SV 53 I", second["changes"]["known"])


class RequestProtectionTest(unittest.TestCase):
    def make_client(self, cfg=None, state_path=None):
        client = Client(cfg or client_config(), state_path=state_path)
        client._throttle = lambda: None
        return client

    def test_hard_safety_limits_cannot_be_raised_by_config(self):
        client = self.make_client(client_config(max_retries=99, max_requests_per_run=999, delay_seconds=0))
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
            state = json.loads(state_path.read_text(encoding="utf-8"))
            self.assertTrue(state["security_lock"])
            self.assertTrue(state["manual_unlock_required"])

    def test_406_sets_permanent_global_security_lock(self):
        with tempfile.TemporaryDirectory() as folder:
            state_path = Path(folder) / "request_state.json"
            client = self.make_client(state_path=state_path)
            client.session = Mock()
            client.session.get.return_value = response(406, "Not Acceptable")
            with self.assertRaises(SecurityLockError):
                client.get_text("https://www.fussball.de/406")
            state = json.loads(state_path.read_text(encoding="utf-8"))
            self.assertTrue(state["security_lock"])

    def test_challenge_page_with_http_200_sets_security_lock(self):
        with tempfile.TemporaryDirectory() as folder:
            state_path = Path(folder) / "request_state.json"
            client = self.make_client(state_path=state_path)
            client.session = Mock()
            client.session.get.return_value = response(
                200,
                "<html><title>Just a moment...</title><script src='/cdn-cgi/challenge-platform/x.js'></script></html>",
                {"Content-Type": "text/html; charset=UTF-8", "Server": "cloudflare"},
            )
            with self.assertRaises(SecurityLockError):
                client.get_text("https://www.fussball.de/challenge")
            state = json.loads(state_path.read_text(encoding="utf-8"))
            self.assertTrue(state["security_lock"])

    def test_existing_security_lock_prevents_network_request(self):
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
        client.session.get.side_effect = [response(503, "temporär"), response(200, "club-matchplan-table")]
        result = client.get_text("https://www.fussball.de/503")
        self.assertIn("club-matchplan-table", result)
        self.assertEqual(2, client.session.get.call_count)

    def test_429_stops_and_persists_retry_after(self):
        with tempfile.TemporaryDirectory() as folder:
            state_path = Path(folder) / "request_state.json"
            client = self.make_client(state_path=state_path)
            client.session = Mock()
            client.session.get.return_value = response(429, "zu viele", {"Retry-After": "120"})
            with self.assertRaises(RateLimitError):
                client.get_text("https://www.fussball.de/429")
            state = json.loads(state_path.read_text(encoding="utf-8"))
            self.assertEqual(120, state["last_retry_after_seconds"])

    def test_request_budget_stops_before_extra_network_call(self):
        client = self.make_client(client_config(max_requests_per_run=1))
        client.session = Mock()
        client.session.get.return_value = response(200, "club-matchplan-table")
        client.get_text("https://www.fussball.de/first")
        with self.assertRaises(RequestBudgetExceeded):
            client.get_text("https://www.fussball.de/second")
        self.assertEqual(1, client.session.get.call_count)


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
        self.assertFalse(is_inside_window(datetime.fromisoformat("2026-01-15T05:59:59+01:00"), self.settings))
        self.assertTrue(is_inside_window(datetime.fromisoformat("2026-01-15T06:00:00+01:00"), self.settings))
        self.assertTrue(is_inside_window(datetime.fromisoformat("2026-07-15T21:59:59+02:00"), self.settings))
        self.assertFalse(is_inside_window(datetime.fromisoformat("2026-07-15T22:00:00+02:00"), self.settings))

    def test_random_delay_stays_between_two_and_twelve_minutes(self):
        import random
        from datetime import datetime
        from schedule_guard import choose_delay_seconds
        delay = choose_delay_seconds(datetime.fromisoformat("2026-07-15T12:00:00+02:00"), self.settings, rng=random.Random(42))
        self.assertIsNotNone(delay)
        self.assertGreaterEqual(delay, 120)
        self.assertLessEqual(delay, 720)

    def test_run_is_skipped_too_close_to_2200(self):
        from datetime import datetime
        from schedule_guard import choose_delay_seconds
        self.assertIsNone(choose_delay_seconds(datetime.fromisoformat("2026-07-15T21:58:30+02:00"), self.settings))


if __name__ == "__main__":
    unittest.main()
