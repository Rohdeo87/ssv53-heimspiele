"""Source validity must be independent of a freshly rendered calendar."""
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path

import pytest

from occupancy.service import build_occupancy_payload
from occupancy.runtime_source import resolve_occupancy_match_source

ROOT = Path(__file__).resolve().parents[1]


def payload(path, *, start="2026-09-12", end="2026-09-13"):
    return build_occupancy_payload(
        config_path=ROOT / "occupancy/config.json",
        matches_path=path,
        start=start,
        end=end,
    )


def valid_match(start="2026-09-12T10:00:00+02:00"):
    kickoff = datetime.fromisoformat(start).astimezone(timezone.utc)
    return {
        "id": "dfb:sample",
        "team": "Schönwalder SV",
        "calendar": "Rasen",
        "place": "rasen",
        "start": kickoff.isoformat(),
        "kickoff": kickoff.isoformat(),
        "end": (kickoff + timedelta(minutes=90)).isoformat(),
        "occupancyStart": (kickoff - timedelta(minutes=60)).isoformat(),
        "occupancyEnd": (kickoff + timedelta(minutes=150)).isoformat(),
        "matchDurationMinutes": 90,
        "durationRule": "test-duration",
        "competitionFormat": "league",
    }


def write_matches(path, matches):
    path.write_text(json.dumps({"schemaVersion": 2, "status": "ok", "matches": matches}), encoding="utf-8")


@pytest.mark.parametrize("bad_id", ["", "dfb:", " "])
def test_empty_source_id_is_not_masked_by_event_prefix(tmp_path, bad_id):
    match = {**valid_match(), "id": bad_id}
    path = tmp_path / "matches.json"
    write_matches(path, [match])
    with pytest.raises(ValueError, match="IDs"):
        payload(path)


def test_duplicate_event_identity_rejected_across_prefix_forms(tmp_path):
    path = tmp_path / "matches.json"
    write_matches(path, [valid_match(), {**valid_match(), "id": "sample"}])
    with pytest.raises(ValueError, match="IDs"):
        payload(path)


@pytest.mark.parametrize("field", ["start", "end", "kickoff", "occupancyStart", "occupancyEnd"])
def test_feed_requires_explicit_timestamp_offsets(tmp_path, field):
    match = valid_match()
    match[field] = datetime.fromisoformat(match[field]).replace(tzinfo=None).isoformat()
    path = tmp_path / "matches.json"
    write_matches(path, [match])
    with pytest.raises(ValueError, match="Zeitzone"):
        payload(path)


@pytest.mark.parametrize("calendar,place", [("Rasen", "kunstrasen"), ("Kunstrasen", "rasen"), ("unbekannt", "rasen")])
def test_conflicting_pitch_fields_are_not_silently_resolved(tmp_path, calendar, place):
    path = tmp_path / "matches.json"
    write_matches(path, [{**valid_match(), "calendar": calendar, "place": place}])
    with pytest.raises(ValueError, match="widersprechen"):
        payload(path)


@pytest.mark.parametrize("start,end", [("2026-03-29", "2026-03-30"), ("2026-10-25", "2026-10-26")])
def test_elapsed_match_and_buffer_minutes_survive_both_dst_changes(tmp_path, start, end):
    path = tmp_path / "matches.json"
    kickoff = start + ("T01:30:00+01:00" if "03-29" in start else "T01:30:00+02:00")
    write_matches(path, [valid_match(kickoff)])
    event = next(e for e in payload(path, start=start, end=end)["events"] if e["source"] == "match")
    clocks = {k: datetime.fromisoformat(event[k]).astimezone(timezone.utc) for k in ("start", "end", "occupancyStart", "occupancyEnd")}
    assert clocks["end"] - clocks["start"] == timedelta(minutes=90)
    assert clocks["start"] - clocks["occupancyStart"] == timedelta(minutes=60)
    assert clocks["occupancyEnd"] - clocks["end"] == timedelta(minutes=60)


def test_match_safety_interval_overlaps_next_day(tmp_path):
    path = tmp_path / "matches.json"
    write_matches(path, [valid_match("2026-09-11T23:30:00+02:00")])
    assert any(e["source"] == "match" for e in payload(path)["events"])


@pytest.mark.parametrize("start", ["2026-03-29T02:30", "2026-10-25T02:30"])
def test_ambiguous_or_nonexistent_local_request_time_needs_offset(tmp_path, start):
    path = tmp_path / "matches.json"
    write_matches(path, [])
    with pytest.raises(ValueError, match="Zeitumstellung"):
        payload(path, start=start, end=start[:10] + "T04:00")


@pytest.mark.parametrize("body", [None, "", "<html>Service unavailable</html>", "BEGIN:VCALENDAR\nBEGIN:VEVENT\nEND:VCALENDAR\n", "BEGIN:VCALENDAR\nBEGIN:VEVENT\nUID:one\nDTSTART:20260912T100000\nEND:VEVENT\nEND:VCALENDAR\n"])
def test_missing_and_incomplete_ics_never_mean_no_matches(tmp_path, body):
    path = tmp_path / "matches.ics"
    if body is not None:
        path.write_text(body, encoding="utf-8")
    with pytest.raises(ValueError):
        payload(path)


def test_explicit_valid_empty_ics_remains_distinguishable(tmp_path):
    path = tmp_path / "matches.ics"
    path.write_text("BEGIN:VCALENDAR\nEND:VCALENDAR\n", encoding="utf-8")
    assert not any(e["source"] == "match" for e in payload(path)["events"])


@pytest.mark.parametrize("age_minutes,fresh", [(5, True), (721, False), (-10, False)])
def test_package_freshness_uses_source_acquisition_not_render_time(tmp_path, age_minutes, fresh):
    now = datetime(2026, 9, 9, 12, tzinfo=timezone.utc)
    source = now - timedelta(minutes=age_minutes)
    path = tmp_path / "matches.json"
    path.write_text(json.dumps({"schemaVersion": 2, "status": "ok", "generatedAt": source.isoformat(), "matches": []}), encoding="utf-8")
    result = resolve_occupancy_match_source({"OCCUPANCY_MATCHES_PATH": str(path)}, now_utc=now)
    assert result.source_kind == "package"
    assert result.source_generated_at_utc == source.isoformat()
    assert result.fresh is fresh


def test_legacy_package_is_not_called_fresh_without_source_age(tmp_path):
    path = tmp_path / "matches.ics"
    path.write_text("BEGIN:VCALENDAR\nEND:VCALENDAR\n", encoding="utf-8")
    result = resolve_occupancy_match_source({"OCCUPANCY_MATCHES_PATH": str(path)}, now_utc=datetime(2026, 9, 9, tzinfo=timezone.utc))
    assert result.fresh is False
    assert result.age_minutes is None
