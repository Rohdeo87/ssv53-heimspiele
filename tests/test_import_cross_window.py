"""Offline regressions for the 09.09.2026 cross-quarter relocation failure."""
import json
from pathlib import Path

from bs4 import BeautifulSoup
import pytest

import poc_scraper

ROOT = Path(__file__).resolve().parents[1]
EMPTY = '<table class="club-matchplan-table"></table>'
MATCH_ID = "031DB8NS8C000000VS5489BUVUR5FS5A"


def relocation_windows():
    soup = BeautifulSoup(
        (ROOT / "tests/fixture_relocation_festivals_20260831.html").read_text(
            encoding="utf-8"
        ),
        "lxml",
    )
    rows = soup.select("tr.row-competition")
    outputs = []
    for row in rows[:2]:
        header = row.find_previous_sibling("tr")
        body = str(header) + "".join(map(str, poc_scraper.block_rows(row)))
        body = body.replace("15.09.2026", "08.10.2026").replace("15.09.26", "08.10.26")
        body = body.replace(
            "Rasenplatz, Sportplatz Leistikowstraße, Leistikowstr. 72, 14612 Falkensee",
            "Rasenplatz, Sportplatz Schönwalde Strandbad, Platz 1",
        )
        outputs.append('<table class="club-matchplan-table">' + body + "</table>")
    return outputs


def run_windows(tmp_path, monkeypatch, first, second):
    config = tmp_path / "config.json"
    config.write_text((ROOT / "config.json").read_text(encoding="utf-8"), encoding="utf-8")
    bodies = [first, second, EMPTY, EMPTY]
    calls = []

    def fetch(client, settings, start, end):
        calls.append((start, end))
        return bodies[len(calls) - 1], "fixture://" + start

    monkeypatch.setattr(poc_scraper, "fetch_club_matchplan", fetch)
    status = poc_scraper.run(config, tmp_path / "output")
    return status, calls, json.loads((tmp_path / "output/quality_report.json").read_text(encoding="utf-8"))


def test_explicit_cross_quarter_relocation_is_resolved_after_all_windows(tmp_path, monkeypatch):
    first, second = relocation_windows()
    status, calls, quality = run_windows(tmp_path, monkeypatch, first, second)
    assert status == 0
    assert len(calls) == 4  # No added HTTP lookup for the verified target.
    matches = json.loads((tmp_path / "output/included_matches.json").read_text(encoding="utf-8"))
    assert len(matches) == 1
    assert matches[0]["external_id"] == MATCH_ID
    assert matches[0]["kickoff"] == "2026-10-08T19:30+02:00"
    assert matches[0]["calendar"] == "Rasen"
    assert matches[0]["event_start"] == "2026-10-08T18:30+02:00"
    assert quality["publishable"] is True
    resolution = quality["cross_window_resolution"]["duplicate_resolutions"][0]
    assert resolution["resolution_attempt"]["method"] == "explicit_postponement_chain"


@pytest.mark.parametrize("mutation", ["target_missing", "team_changed", "number_changed", "venue_missing"])
def test_cross_window_resolution_keeps_identity_and_target_safety(tmp_path, monkeypatch, mutation):
    first, second = relocation_windows()
    if mutation == "target_missing":
        second = EMPTY
    elif mutation == "team_changed":
        second = second.replace("02PJD5K8O8000000VS5489B1VUI2QQ8R", "UNRELATEDTEAM")
    elif mutation == "number_changed":
        second = second.replace("610090010", "610090099")
    else:
        second = second.replace("Rasenplatz, Sportplatz Schönwalde Strandbad, Platz 1", "")
    status, _, quality = run_windows(tmp_path, monkeypatch, first, second)
    assert status == 2
    assert quality["publishable"] is False
    assert any("Verlegung" in error for error in quality["errors"])


def test_standalone_partial_window_still_rejects_missing_target():
    first, _ = relocation_windows()
    config = json.loads((ROOT / "config.json").read_text(encoding="utf-8"))
    with pytest.raises(poc_scraper.ScrapeError, match="Ziel fehlt"):
        poc_scraper.parse_club_matchplan(first, "fixture://single-window", config)


def test_changed_row_markup_cannot_be_mistaken_for_empty_import():
    _, second = relocation_windows()
    config = json.loads((ROOT / "config.json").read_text(encoding="utf-8"))
    broken = second.replace("row-competition", "new-upstream-row-class")
    with pytest.raises(poc_scraper.ScrapeError, match="kein leerer Spielplan"):
        poc_scraper.parse_club_matchplan(broken, "fixture://changed-markup", config)
