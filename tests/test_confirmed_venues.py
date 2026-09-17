"""Offline regressions for the missing D-junior venue reported on 17 September."""
from copy import deepcopy
import json
from pathlib import Path

import pytest

import create_feed
import poc_scraper

ROOT = Path(__file__).resolve().parents[1]
MATCH_ID = "0323TKOBKG000000VS5489BTVT7QHFUC"
EMPTY = '<table class="club-matchplan-table"></table>'


def settings():
    return json.loads((ROOT / "config.json").read_text(encoding="utf-8"))


def replay(tmp_path, monkeypatch, config):
    config_path = tmp_path / "config.json"
    config_path.write_text(json.dumps(config), encoding="utf-8")
    fixture = (ROOT / "tests/fixture_home_missing_venue_20260917.html").read_text(encoding="utf-8")
    bodies = [fixture, EMPTY, EMPTY, EMPTY]
    calls = []

    def fetch(client, config, start, end):
        calls.append((start, end))
        return bodies[len(calls) - 1], "fixture://" + start

    monkeypatch.setattr(poc_scraper, "fetch_club_matchplan", fetch)
    output = tmp_path / "generated"
    result = poc_scraper.run(config_path, output)
    assert len(calls) == 4  # No additional source/detail request.
    return result, output, config_path


def test_missing_venue_still_blocks_and_identifies_exact_fixture(tmp_path, monkeypatch, caplog):
    config = settings()
    config.pop("confirmed_venue_assignments")
    result, output, _ = replay(tmp_path, monkeypatch, config)
    assert result == 2
    quality = json.loads((output / "quality_report.json").read_text(encoding="utf-8"))
    assert quality["publishable"] is False
    assert "23T18:00+02:00" in caplog.text
    assert "SC Oberhavel Velten I (U13)" in caplog.text
    assert "Spielstätte fehlt" in caplog.text
    assert MATCH_ID in caplog.text


def test_confirmed_fixture_produces_rasen_feed_with_source_provenance(tmp_path, monkeypatch):
    result, output, config_path = replay(tmp_path, monkeypatch, settings())
    assert result == 0
    assert json.loads((output / "review_matches.json").read_text(encoding="utf-8")) == []
    rows = json.loads((output / "included_matches.json").read_text(encoding="utf-8"))
    assert len(rows) == 1
    row = rows[0]
    assert row["calendar"] == "Rasen"
    assert row["event_start"] == "2026-09-23T17:00+02:00"
    assert row["event_end"] == "2026-09-23T20:15+02:00"
    assert row["venue_assignment"]["source_venue_raw"] == ""
    assert row["venue_assignment"]["confirmed_on"] == "2026-09-17"
    assert "Spielstätte fehlt" not in row["warnings"]
    target = tmp_path / "candidate"
    monkeypatch.setattr("sys.argv", ["create_feed.py", "--input", str(output), "--output", str(target), "--config", str(config_path)])
    assert create_feed.main() == 0
    match = json.loads((target / "matches.json").read_text(encoding="utf-8"))["matches"][0]
    assert match["place"] == "rasen"
    assert match["locationSource"] == "club-confirmation"
    assert match["id"] == "dfb:" + MATCH_ID


def source_match():
    # Exercise assignment eligibility independently from the HTML parser.
    config = settings()
    fixture = (ROOT / "tests/fixture_home_missing_venue_20260917.html").read_text(encoding="utf-8")
    matches = poc_scraper.parse_club_matchplan(fixture, "fixture://home", config)
    return matches[0]


@pytest.mark.parametrize("field,value", [
    ("external_id", "ANOTHER-MATCH"),
    ("kickoff", "2026-09-24T18:00+02:00"),
    ("home_team_id", "ANOTHER-HOME-TEAM"),
    ("away_team_id", "ANOTHER-OPPONENT"),
])
def test_confirmation_does_not_apply_to_other_fixture_or_changed_date(field, value):
    match = source_match()
    setattr(match, field, value)
    config = settings()
    rules = [poc_scraper.VenueRule(**rule) for rule in config["venue_rules"]]
    poc_scraper.apply_confirmed_venue(match, config["confirmed_venue_assignments"], rules)
    poc_scraper.apply_venue_rules(match, rules, "exclude", config["local_venue_pattern"])
    assert match.decision == "review"
    assert match.venue_raw == ""
    assert not match.venue_assignment


@pytest.mark.parametrize("venue,decision,calendar", [
    ("Kunstrasen, Sportplatz Schönwalde Strandbad, Platz 2", "include", "Kunstrasen"),
    ("Rasenplatz, Sportplatz Perwenitz", "exclude", ""),
    ("Sportplatz Schönwalde Strandbad", "review", ""),
])
def test_published_source_venue_takes_precedence(venue, decision, calendar):
    match = source_match()
    match.venue_raw = venue
    config = settings()
    rules = [poc_scraper.VenueRule(**rule) for rule in config["venue_rules"]]
    poc_scraper.apply_confirmed_venue(match, config["confirmed_venue_assignments"], rules)
    poc_scraper.apply_venue_rules(match, rules, "exclude", config["local_venue_pattern"])
    assert match.decision == decision
    assert match.calendar == calendar
    assert match.venue_raw == venue
    assert not match.venue_assignment


def test_cancelled_fixture_is_not_restored_by_confirmation():
    match = source_match()
    match.status = "Absetzung"
    config = settings()
    rules = [poc_scraper.VenueRule(**rule) for rule in config["venue_rules"]]
    poc_scraper.apply_confirmed_venue(match, config["confirmed_venue_assignments"], rules)
    poc_scraper.apply_venue_rules(match, rules, "exclude", config["local_venue_pattern"])
    assert match.decision == "exclude"
    assert not match.venue_assignment


def test_inconsistent_approval_fails_closed(tmp_path, monkeypatch):
    config = deepcopy(settings())
    config["confirmed_venue_assignments"][MATCH_ID]["calendar"] = "Kunstrasen"
    result, output, _ = replay(tmp_path, monkeypatch, config)
    assert result == 2
    quality = json.loads((output / "quality_report.json").read_text(encoding="utf-8"))
    assert quality["publishable"] is False
    assert any("Platzregeln" in error for error in quality["errors"])
