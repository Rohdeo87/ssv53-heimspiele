"""Offline replay of the 15 October import failure and its safety boundaries."""
from dataclasses import replace
import json
from pathlib import Path

import pytest

import poc_scraper

ROOT = Path(__file__).resolve().parents[1]
MATCH_ID = "031DB8NS70000000VS5489BUVUR5FS5A"
FIXTURE = ROOT / "tests/fixture_relocation_missing_away_venue_20261015.html"
EMPTY = '<table class="club-matchplan-table"></table>'
LOCAL_VENUE = "Kunstrasenplatz, Sportplatz Schönwalde Strandbad, Platz 2"


def settings():
    return json.loads((ROOT / "config.json").read_text(encoding="utf-8"))


def source_rows():
    return poc_scraper.parse_club_matchplan(
        FIXTURE.read_text(encoding="utf-8"), "fixture://20261015", settings(),
        defer_duplicate_resolution=True,
    )


def classify(match):
    config = settings()
    poc_scraper.apply_venue_rules(
        match, [poc_scraper.VenueRule(**rule) for rule in config["venue_rules"]],
        config["default_decision"], config["local_venue_pattern"],
    )


def test_actual_home_away_exchange_uses_explicit_target_without_guessing_venue():
    audit = {}
    matches = poc_scraper.parse_club_matchplan(
        FIXTURE.read_text(encoding="utf-8"), "fixture://20261015", settings(), audit,
    )
    assert len(matches) == 1
    match = matches[0]
    assert match.external_id == MATCH_ID
    assert match.kickoff == "2026-10-30T20:00+01:00"
    assert match.home_team == "SG Bornim Ü50"
    assert match.team_role == "away"
    assert match.team_id == match.away_team_id
    assert match.venue_raw == ""
    classify(match)
    assert match.decision == "exclude"
    assert match.calendar == ""
    assert match.venue_rule == "Auswärtsspiel ohne Spielstätte"
    resolution = audit["duplicate_resolutions"][0]["resolution_attempt"]
    assert resolution["method"] == "explicit_postponement_chain"
    assert resolution["relocations"] == [{
        "from": "2026-10-02T20:00+02:00", "to": "2026-10-30T20:00+01:00",
    }]


def test_old_local_venue_is_never_copied_to_missing_away_target():
    old, target = source_rows()
    old.venue_raw = LOCAL_VENUE
    result, _, conflicts, _ = poc_scraper.collapse_duplicate_detail_ids([target, old])
    assert result == [target]
    assert not conflicts
    assert target.venue_raw == ""
    classify(target)
    assert target.decision == "exclude"


@pytest.mark.parametrize("mutation", [
    "home", "unknown", "wrong_club_id", "missing_away_id", "mixed_final_roles",
])
def test_missing_target_venue_requires_unambiguous_linked_away_identity(mutation):
    old, target = source_rows()
    rows = [old, target]
    if mutation in {"home", "unknown"}:
        target.team_role = mutation
    elif mutation == "wrong_club_id":
        target.team_id = target.home_team_id
    elif mutation == "missing_away_id":
        target.away_team_id = ""
    else:
        rows.append(replace(target, team_role="home"))
    with pytest.raises(poc_scraper.ScrapeError):
        poc_scraper.collapse_duplicate_detail_ids(rows)


@pytest.mark.parametrize("mutation", ["target_missing", "number_changed", "team_changed", "cycle"])
def test_away_exception_does_not_weaken_relocation_identity_or_chain(mutation):
    old, target = source_rows()
    rows = [old, target]
    if mutation == "target_missing":
        rows.pop()
    elif mutation == "number_changed":
        target.match_number = "610090099"
    elif mutation == "team_changed":
        target.home_team_id = "OTHER_TEAM"
    else:
        target.postponed_to = old.kickoff
    with pytest.raises(poc_scraper.ScrapeError):
        poc_scraper.collapse_duplicate_detail_ids(rows)


def test_later_local_venue_still_blocks_local_pitch_even_for_away_role():
    old, target = source_rows()
    target.venue_raw = LOCAL_VENUE
    result, _, conflicts, _ = poc_scraper.collapse_duplicate_detail_ids([old, target])
    assert result == [target]
    assert not conflicts
    classify(target)
    assert target.decision == "include"
    assert target.calendar == "Kunstrasen"


def test_missing_away_venue_does_not_block_other_matches_or_add_source_calls(tmp_path, monkeypatch):
    config_path = tmp_path / "config.json"
    config_path.write_text(json.dumps(settings()), encoding="utf-8")
    bodies = [
        (ROOT / "tests/fixture_home_missing_venue_20260917.html").read_text(encoding="utf-8"),
        FIXTURE.read_text(encoding="utf-8"), EMPTY, EMPTY,
    ]
    calls = []

    def fetch(client, config, start, end):
        calls.append((start, end))
        return bodies[len(calls) - 1], "fixture://" + start

    monkeypatch.setattr(poc_scraper, "fetch_club_matchplan", fetch)
    output = tmp_path / "output"
    assert poc_scraper.run(config_path, output) == 0
    assert len(calls) == 4
    quality = json.loads((output / "quality_report.json").read_text(encoding="utf-8"))
    included = json.loads((output / "included_matches.json").read_text(encoding="utf-8"))
    excluded = json.loads((output / "excluded_matches.json").read_text(encoding="utf-8"))
    assert quality["publishable"] is True
    assert len(included) == 1
    assert included[0]["calendar"] == "Rasen"
    assert len(excluded) == 1
    assert excluded[0]["external_id"] == MATCH_ID
    assert excluded[0]["kickoff"] == "2026-10-30T20:00+01:00"
    assert excluded[0]["venue_raw"] == ""
