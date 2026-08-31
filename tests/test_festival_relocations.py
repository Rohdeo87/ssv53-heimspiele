"""Regression fixtures from the failed 2026-08-31 source response; no network."""
from dataclasses import asdict
from datetime import datetime, timedelta
import json
from pathlib import Path

from bs4 import BeautifulSoup
import pytest

from poc_scraper import (
    ScrapeError, VenueRule, apply_venue_rules, block_rows,
    collapse_duplicate_detail_ids, parse_club_matchplan,
)
from create_feed import validate_timing

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "tests/fixture_relocation_festivals_20260831.html"
RELOCATED = "031DB8NS8C000000VS5489BUVUR5FS5A"
FESTIVALS = {"610015371", "610018452", "610018461"}


def config():
    return json.loads((ROOT / "config.json").read_text(encoding="utf-8"))


def fixture():
    return FIXTURE.read_text(encoding="utf-8")


def parse(html=None):
    audit = {}
    matches = parse_club_matchplan(html or fixture(), "fixture://20260831", config(), audit)
    for match in matches:
        apply_venue_rules(match, [VenueRule(**r) for r in config()["venue_rules"]],
                          config()["default_decision"], config()["local_venue_pattern"])
    return matches, audit


def test_exact_failure_resolved_without_extra_source_request():
    matches, audit = parse()
    assert len(matches) == 4
    match = next(m for m in matches if m.external_id == RELOCATED)
    assert match.kickoff == "2026-09-15T19:30+02:00"
    assert match.home_team.startswith("SpG Perwenitz/")
    assert match.away_team == "SV Falkensee-Finkenkrug"
    assert "Falkensee" in match.venue_raw
    assert match.decision == "exclude"  # Never inherit a venue from the obsolete row.
    resolution = audit["duplicate_resolutions"][0]["resolution_attempt"]
    assert resolution["method"] == "explicit_postponement_chain"
    assert resolution["relocations"] == [{"from": "2026-09-11T19:30+02:00", "to": match.kickoff}]
    assert audit["missing_festival_groups"] == []
    assert audit["source_festival_groups"] == 3


@pytest.mark.parametrize("calendar,venue", [
    ("Rasen", "Rasenplatz, Sportplatz Schönwalde Strandbad, Platz 1"),
    ("Kunstrasen", "Kunstrasenplatz, Sportplatz Schönwalde Strandbad, Platz 2, KR"),
])
def test_festival_times_links_and_own_team_survive(calendar, venue):
    soup = BeautifulSoup(fixture(), "lxml")
    # Replace only venue rows of the three festivals.
    rows = soup.select("tr.row-competition")
    for row in rows[2:]:
        for part in block_rows(row):
            if "row-venue" in part.get("class", []):
                part.clear()
                cell = soup.new_tag("td")
                cell.string = venue
                part.append(cell)
    matches, _ = parse(str(soup))
    festivals = [m for m in matches if m.external_id in FESTIVALS]
    assert len(festivals) == 3
    assert len({m.team_id for m in festivals}) == 3
    assert {m.team_id for m in festivals} == {
        "011KGG85I0000000VTVG0001VSK3M9SE", "01KPKBA8F8000000VV0AG811VSVU41QE",
        "01OFNF8544000000VV0AG80NVUUTMIT0",
    }
    assert {m.kickoff for m in festivals} == {
        "2026-09-19T14:00+02:00", "2026-09-27T13:30+02:00", "2026-09-27T15:30+02:00",
    }
    for m in festivals:
        assert m.decision == "include"
        assert m.calendar == calendar
        assert not m.team_id.startswith("auto-")
        assert "Kinderfestival" not in m.team_name
        assert m.team_role != "unknown"
        assert "/staffel/" in m.detail_url and m.detail_url.endswith("-G")
        assert m.match_duration_minutes == 90
        kickoff = datetime.fromisoformat(m.kickoff)
        assert datetime.fromisoformat(m.match_end) == kickoff + timedelta(minutes=90)
        assert datetime.fromisoformat(m.event_start) == kickoff - timedelta(minutes=60)
        assert datetime.fromisoformat(m.event_end) == kickoff + timedelta(minutes=150)


def test_festival_participant_must_belong_to_our_club():
    html = fixture().replace("Schönwalder SV 53 II", "Fremder Verein II")
    matches, _ = parse(html)
    event = next(m for m in matches if m.external_id == "610018461")
    assert event.team_id != "01OFNF8544000000VV0AG80NVUUTMIT0"


def test_duplicate_rendered_festivals_are_collapsed_not_duplicated():
    html = fixture().replace("</table>", fixture().split(">", 1)[1])
    matches, _ = parse(html)
    assert len(matches) == 4


@pytest.mark.parametrize("mutation", ["no_pointer", "missing_target", "wrong_team_id", "wrong_number", "cyclic"])
def test_unsafe_relocations_still_abort(mutation):
    soup = BeautifulSoup(fixture(), "lxml")
    rows = soup.select("tr.row-competition")
    pointer = soup.select_one(".column-score .info-text")
    if mutation == "no_pointer":
        pointer.decompose()
    elif mutation == "missing_target":
        for row in block_rows(rows[1]):
            row.decompose()
    elif mutation == "wrong_team_id":
        link = next(a for r in block_rows(rows[0]) for a in r.select("a[href*='/team-id/']"))
        link["href"] = link["href"].rsplit("/", 1)[0] + "/OTHERTEAM000000000000000000000"
    elif mutation == "wrong_number":
        rows[0].find(string=lambda t: t and "610090010" in t).replace_with("ME | 610090099")
    elif mutation == "cyclic":
        pointer.string = "11.09.2026 19:30"
    with pytest.raises(ScrapeError):
        parse(str(soup))


def test_unrecognized_festival_group_cannot_silently_disappear():
    with pytest.raises(ScrapeError, match="Festival-Links"):
        parse(fixture().replace("</table>", '<a href="/spieltag/test/-/staffel/UNKNOWN-G">Festival</a></table>'))


def test_relocation_chain_can_move_earlier_and_more_than_once():
    matches, _ = parse()
    from dataclasses import replace
    final = next(m for m in matches if m.external_id == RELOCATED)
    older = replace(final, kickoff="2026-09-22T19:30+02:00", postponed_to="2026-09-18T19:30+02:00")
    middle = replace(final, kickoff=older.postponed_to, postponed_to=final.kickoff)
    result, _, conflicts, details = collapse_duplicate_detail_ids([middle, final, older])
    assert result == [final]
    assert not conflicts
    assert len(details[0]["resolution_attempt"]["relocations"]) == 2


@pytest.mark.parametrize("bad_field,bad_value", [
    ("event_start", "2026-09-19T14:00+02:00"),
    ("event_end", "2026-09-19T15:30+02:00"),
    ("match_duration_minutes", 49),
    ("duration_rule", "unverified-90-minutes"),
    ("kickoff", "2026-09-19T14:00"),
])
def test_publication_rejects_different_rules_or_missing_safety_buffers(bad_field, bad_value):
    matches, _ = parse()
    item = asdict(next(m for m in matches if m.external_id == "610015371"))
    validate_timing(item, config())
    item[bad_field] = bad_value
    with pytest.raises(ValueError):
        validate_timing(item, config())
