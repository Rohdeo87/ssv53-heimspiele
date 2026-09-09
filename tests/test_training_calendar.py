"""All approved calendars below are synthetic fixtures, never club policy."""
import copy
import json
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from mower.planner import build_training_blocks
from occupancy.service import build_training_occurrences
from occupancy.training_calendar import (
    TrainingCancellation, calendar_digest, legacy_source_hashes, load_calendar,
    resolve_training_calendar, validate_calendar,
)
from occupancy.training_calendar_audit import compare_training_blocks, source_comparison_report


ROOT = Path(__file__).resolve().parents[1]
UTC = timezone.utc
TZ = ZoneInfo("Europe/Berlin")
NOW = datetime(2026, 9, 9, 12, tzinfo=UTC)


def sources():
    return (json.loads((ROOT / "occupancy/config.json").read_text(encoding="utf-8")),
            json.loads((ROOT / "mower/config.json").read_text(encoding="utf-8")))


def approve_synthetic(document):
    document["enabled"] = True
    document["approval"] = {
        "status": "approved", "reference": "TEST ONLY; no club approval",
        "approved_at_utc": "2026-01-01T00:00:00Z", "content_sha256": calendar_digest(document),
    }
    return document


def fixture():
    summer = [
        {"id": "test-e1", "weekday": "Dienstag", "start": "17:00", "end": "18:30", "team": "Test E1", "resource_id": "rasen"},
        {"id": "test-a", "weekday": "Dienstag", "start": "18:30", "end": "20:00", "team": "Test A", "resource_id": "rasen"},
    ]
    winter = [{"id": "test-winter", "weekday": "Dienstag", "start": "17:00", "end": "18:30", "team": "Test E1", "resource_id": "kunstrasen"}]
    occ = {"timezone": "Europe/Berlin", "effective_from": "2026-01-01", "effective_to": "2026-12-31",
           "resources": [{"id": "rasen"}, {"id": "kunstrasen"}],
           "seasons": {"Sommer": {"weekly": summer}, "Winter": {"weekly": winter}}, "cancelled_occurrences": []}
    mower = {"timezone": "Europe/Berlin", "training": {
        "before_minutes": 30, "after_minutes": 30,
        "active_ranges": [{"from": "2026-01-01", "to": "2026-12-31"}],
        "weekly": [{key: value for key, value in item.items() if key != "resource_id"} for item in summer],
    }}
    document = {
        "schema_version": 1, "calendar_id": "test-training", "revision": 1, "enabled": False,
        "timezone": "Europe/Berlin", "coverage": {"from": "2026-01-01", "through": "2026-12-31"},
        "buffers": {"before_minutes": 30, "after_minutes": 30},
        "weekly_patterns": {"Sommer": copy.deepcopy(summer), "Winter": copy.deepcopy(winter)},
        "season_periods": [
            {"from": "2026-01-01", "through": "2026-09-30", "season": "Sommer"},
            {"from": "2026-10-01", "through": "2026-12-31", "season": "Winter"},
        ],
        "holidays": {"reviewed": True, "periods": []}, "excluded_occurrences": [],
        "legacy_sources": legacy_source_hashes(occ, mower), "approval": {},
    }
    return approve_synthetic(document), occ, mower


def resolve(document, occ, mower, *, start="2026-09-08T00:00:00+02:00", end="2026-09-09T00:00:00+02:00", now=NOW, cancellations=()):
    return resolve_training_calendar(document, range_start=datetime.fromisoformat(start),
                                     range_end=datetime.fromisoformat(end), now_utc=now,
                                     occupancy_config=occ, mower_config=mower, cancellations=cancellations)


def test_repository_template_preserves_unapproved_gates_and_current_sources():
    document = load_calendar(ROOT / "occupancy/training_calendar.template.json")
    occ, mower = sources()
    result = resolve(document, occ, mower)
    assert result.retain_existing and result.batch is None
    blockers = " ".join(result.validation.activation_blockers)
    assert "CALENDAR_DISABLED" in blockers
    assert "SEASON_DATES_UNCONFIRMED" in blockers
    assert "HOLIDAY_POLICY_UNCONFIRMED" in blockers
    assert "CALENDAR_APPROVAL_REQUIRED" in blockers
    assert result.validation.errors == ()
    report = source_comparison_report(document, occ, mower, now_utc=NOW)
    assert report["templatePatternsMatchSource"]
    assert report["templateExclusionsMatchSource"]
    assert report["retainExistingTraining"] is True
    assert report["seasonComparisons"]["Sommer"]["controllerOnlyScheduleIds"] == []
    assert report["seasonComparisons"]["Sommer"]["changedPatterns"] == []
    assert report["seasonComparisons"]["Sommer"]["appTrainingCount"] == 17
    assert report["seasonComparisons"]["Winter"]["appTrainingCount"] == 18
    assert len(report["seasonComparisons"]["Winter"]["controllerOnlyScheduleIds"]) == 8


@pytest.mark.parametrize("change", [
    lambda value: value.update(enabled="false"),
    lambda value: value.update(revision=True),
    lambda value: value.update(schema_version=2),
    lambda value: value["buffers"].update(before_minutes=29),
    lambda value: value["weekly_patterns"]["Sommer"][0].update(resource_id="unknown"),
    lambda value: value["weekly_patterns"]["Sommer"][0].update(start="17:00:00"),
    lambda value: value["weekly_patterns"]["Sommer"][1].update(id="test-e1"),
    lambda value: value["weekly_patterns"]["Sommer"][0].update(weekdays="Dienstag"),
    lambda value: value["season_periods"][1].update({"from": "2026-09-30"}),
    lambda value: value["holidays"]["periods"].append({"from": "2026-09-08", "through": "2026-09-08", "schedule_ids": ["unknown"], "reference": "test"}),
])
def test_malformed_calendar_never_replaces_existing_blocks(change):
    document, occ, mower = fixture()
    original = build_training_blocks(mower["training"], date(2026, 9, 8), 1, TZ)
    change(document)
    document["approval"]["content_sha256"] = calendar_digest(document)
    result = resolve(document, occ, mower)
    effective = original if result.retain_existing else result.batch.mower_blocks()
    assert result.batch is None
    assert effective is original
    assert result.validation.errors


@pytest.mark.parametrize("change,expected", [
    (lambda value: value.update(enabled=False), "CALENDAR_DISABLED"),
    (lambda value: value.update(season_periods=[]), "SEASON_DATES_UNCONFIRMED"),
    (lambda value: value["season_periods"][0].update(through="2026-09-29"), "SEASON_DATES_UNCONFIRMED"),
    (lambda value: value["holidays"].update(reviewed=False), "HOLIDAY_POLICY_UNCONFIRMED"),
    (lambda value: value["approval"].update(status="pending"), "CALENDAR_APPROVAL_REQUIRED"),
    (lambda value: value["approval"].update(approved_at_utc="2026-09-10T00:00:00Z"), "APPROVAL_TIME_REQUIRED"),
    (lambda value: value["approval"].update(approved_at_utc="2026-09-08T00:00:00"), "APPROVAL_TIME_REQUIRED"),
    (lambda value: value["weekly_patterns"]["Sommer"][0].update(start="17:30"), "APPROVAL_CONTENT_MISMATCH"),
])
def test_missing_approval_does_not_create_winter_or_holiday_release(change, expected):
    document, occ, mower = fixture()
    change(document)
    result = resolve(document, occ, mower, start="2026-11-03T00:00:00+01:00", end="2026-11-04T00:00:00+01:00")
    assert result.retain_existing
    assert expected in " ".join(result.validation.activation_blockers)


def test_source_drift_and_larger_existing_buffer_block_migration():
    document, occ, mower = fixture()
    occ["seasons"]["Sommer"]["weekly"][0]["start"] = "16:45"
    result = resolve(document, occ, mower)
    assert result.retain_existing
    assert "LEGACY_SOURCE_CHANGED" in " ".join(result.validation.activation_blockers)
    mower["training"]["before_minutes"] = 45
    document["legacy_sources"] = legacy_source_hashes(occ, mower)
    approve_synthetic(document)
    assert resolve(document, occ, mower).validation.errors


def test_same_batch_projects_nominal_and_once_buffered_times_to_both_consumers():
    document, occ, mower = fixture()
    batch = resolve(document, occ, mower).batch
    assert batch is not None
    blocks = batch.mower_blocks()
    events = batch.app_events()
    for event, block in zip(events, blocks):
        assert event["id"] == block.details["occurrence_id"]
        assert event["start"] == block.details["nominal_start"]
        assert event["end"] == block.details["nominal_end"]
        assert datetime.fromisoformat(event["occupancyStart"]) == block.start
        assert datetime.fromisoformat(event["occupancyEnd"]) == block.end
        assert datetime.fromisoformat(event["start"]) - block.start == timedelta(minutes=30)
    original = build_training_blocks(mower["training"], date(2026, 9, 8), 1, TZ)
    comparison = compare_training_blocks(batch, original)
    assert comparison["existingBlockedMinutes"] == 240
    assert comparison["candidateBlockedMinutes"] == 240
    assert comparison["removedBlockedMinutes"] == comparison["addedBlockedMinutes"] == 0
    events[0]["blocking"] = False  # a frontend copy cannot change the shared batch
    assert len(batch.mower_blocks()) == 2
    with pytest.raises(TypeError):
        batch.events[0]["blocking"] = False


def test_existing_occurrence_ids_survive_shared_source_and_revision(tmp_path):
    document = load_calendar(ROOT / "occupancy/training_calendar.template.json")
    occ, mower = sources()
    document["season_periods"] = [{**document["coverage"], "season": "Sommer"}]
    document["holidays"]["reviewed"] = True
    approve_synthetic(document)
    batch = resolve(document, occ, mower).batch
    old = build_training_occurrences(config_path=ROOT / "occupancy/config.json", start="2026-09-08", end="2026-09-09", season="Sommer")
    assert {item["id"] for item in batch.events} == {item["id"] for item in old}
    path = tmp_path / "restart.json"
    path.write_text(json.dumps(document), encoding="utf-8")
    restarted = resolve(load_calendar(path), occ, mower).batch
    assert restarted.app_events() == batch.app_events()
    document["revision"] += 1
    approve_synthetic(document)
    upgraded = resolve(document, occ, mower).batch
    assert [item["id"] for item in upgraded.events] == [item["id"] for item in batch.events]
    assert upgraded.content_sha256 != batch.content_sha256


def test_synthetic_approved_season_switch_uses_same_resource_in_app_and_mower():
    document, occ, mower = fixture()
    before = resolve(document, occ, mower, start="2026-09-29T00:00:00+02:00", end="2026-09-30T00:00:00+02:00").batch
    after = resolve(document, occ, mower, start="2026-10-06T00:00:00+02:00", end="2026-10-07T00:00:00+02:00").batch
    assert len(before.mower_blocks()) == 2
    assert [item["resourceId"] for item in after.events] == ["kunstrasen"]
    assert after.mower_blocks() == []


def test_approved_scoped_holiday_reports_removed_union_without_double_counting():
    document, occ, mower = fixture()
    document["holidays"]["periods"] = [{"from": "2026-09-08", "through": "2026-09-08", "schedule_ids": ["test-e1"], "reference": "synthetic holiday rule"}]
    approve_synthetic(document)
    batch = resolve(document, occ, mower).batch
    assert [item["scheduleId"] for item in batch.events] == ["test-a"]
    original = build_training_blocks(mower["training"], date(2026, 9, 8), 1, TZ)
    comparison = compare_training_blocks(batch, [*original, original[0]])
    assert comparison["existingBlockedMinutes"] == 240
    assert comparison["candidateBlockedMinutes"] == 150
    assert comparison["removedBlockedMinutes"] == 90
    assert comparison["addedBlockedMinutes"] == 0
    assert comparison["automaticReleaseAuthorized"] is False


def test_pending_cancellation_is_visible_but_blocks_until_server_release_time():
    document, occ, mower = fixture()
    requested = datetime(2026, 9, 8, 12, tzinfo=UTC)
    cancellation = TrainingCancellation("test-e1", date(2026, 9, 8), requested, requested + timedelta(minutes=30))
    previous = TrainingCancellation("test-e1", date(2026, 9, 8), requested, requested + timedelta(minutes=20))
    pending = resolve(document, occ, mower, now=requested + timedelta(minutes=29), cancellations=iter([previous, cancellation])).batch
    released = resolve(document, occ, mower, now=requested + timedelta(minutes=30), cancellations=[cancellation]).batch
    assert pending.events[0]["cancelled"] and pending.events[0]["blocking"]
    assert len(pending.mower_blocks()) == 2
    assert released.events[0]["cancelled"] and not released.events[0]["blocking"]
    assert len(released.mower_blocks()) == 1
    assert pending.events[0]["id"] == released.events[0]["id"]


def change_to_overnight(document, weekday, start, end):
    document["weekly_patterns"]["Sommer"] = [{"id": "overnight", "weekday": weekday, "start": start, "end": end,
                                                "team": "Synthetic overnight", "resource_id": "rasen"}]
    document["season_periods"] = [{**document["coverage"], "season": "Sommer"}]
    approve_synthetic(document)


def test_midnight_query_keeps_previous_day_training_and_next_day_buffer():
    document, occ, mower = fixture()
    change_to_overnight(document, "Dienstag", "23:00", "00:30")
    batch = resolve(document, occ, mower, start="2026-09-09T00:00:00+02:00", end="2026-09-09T01:00:00+02:00").batch
    assert len(batch.events) == 1
    assert batch.events[0]["id"].endswith(":2026-09-08")
    assert batch.events[0]["end"] == "2026-09-09T00:30:00+02:00"
    change_to_overnight(document, "Mittwoch", "00:15", "01:00")
    batch = resolve(document, occ, mower, start="2026-09-08T23:40:00+02:00", end="2026-09-09T00:00:00+02:00").batch
    assert len(batch.events) == 1
    assert batch.events[0]["occupancyStart"] == "2026-09-08T23:45:00+02:00"


@pytest.mark.parametrize("start,end,minutes", [
    ("2026-03-29T00:00:00+01:00", "2026-03-29T04:00:00+02:00", 240),
    ("2026-10-25T00:00:00+02:00", "2026-10-25T04:00:00+01:00", 360),
])
def test_overnight_across_dst_has_real_elapsed_time_and_full_buffers(start, end, minutes):
    document, occ, mower = fixture()
    change_to_overnight(document, "Samstag", "23:00", "03:00")
    batch = resolve(document, occ, mower, start=start, end=end).batch
    block = batch.mower_blocks()[0]
    assert (block.end.astimezone(UTC) - block.start.astimezone(UTC)).total_seconds() / 60 == minutes


@pytest.mark.parametrize("day,offset", [("2026-03-29", "+01:00"), ("2026-10-25", "+02:00")])
def test_nonexistent_or_ambiguous_training_time_retains_existing_calendar(day, offset):
    document, occ, mower = fixture()
    change_to_overnight(document, "Sonntag", "02:30", "04:00")
    result = resolve(document, occ, mower, start=day + "T00:00:00" + offset, end=day + "T05:00:00" + offset)
    assert result.retain_existing
    assert "Zeitumstellung" in " ".join(result.validation.errors)


def test_calendar_boundary_requires_neighboring_anchor_day_and_duplicate_json_is_rejected(tmp_path):
    document, occ, mower = fixture()
    result = resolve(document, occ, mower, start="2026-01-01T00:00:00+01:00", end="2026-01-02T00:00:00+01:00")
    assert result.retain_existing
    assert "CALENDAR_COVERAGE_INCOMPLETE" in " ".join(result.validation.errors)
    path = tmp_path / "duplicate.json"
    path.write_text('{"enabled":false,"enabled":true}', encoding="utf-8")
    with pytest.raises(ValueError, match="Doppelter"):
        load_calendar(path)
