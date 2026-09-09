import copy
import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from occupancy.training_calendar import calendar_digest, legacy_source_hashes
import scripts.prepare_training_calendar_approval as approval_cli
from scripts.prepare_training_calendar_approval import ApprovalPreparationError, prepare_approved_copy


UTC = timezone.utc


def source_fixture(tmp_path: Path):
    candidate = {
        "schema_version": 1, "calendar_id": "manual-test", "revision": 1, "enabled": True,
        "timezone": "Europe/Berlin", "coverage": {"from": "2026-01-01", "through": "2026-12-31"},
        "buffers": {"before_minutes": 30, "after_minutes": 30},
        "weekly_patterns": {"Sommer": [{"id": "tue", "weekday": "Dienstag", "start": "17:00", "end": "18:00", "team": "E1", "resource_id": "rasen"}]},
        "season_periods": [], "holidays": {"reviewed": True, "periods": []}, "excluded_occurrences": [],
        "legacy_sources": {}, "approval": {"status": "pending", "reference": None, "approved_at_utc": None, "content_sha256": None},
    }
    occupancy = {"timezone": "Europe/Berlin", "effective_from": "2026-01-01", "effective_to": "2026-12-31", "resources": [], "seasons": {}, "cancelled_occurrences": []}
    mower = {"timezone": "Europe/Berlin", "training": {"before_minutes": 30, "after_minutes": 30, "weekly": [], "active_ranges": []}}
    candidate["legacy_sources"] = legacy_source_hashes(occupancy, mower)
    cal = tmp_path / "candidate.json"; occ = tmp_path / "occupancy.json"; mow = tmp_path / "mower.json"
    cal.write_text(json.dumps(candidate), encoding="utf-8"); occ.write_text(json.dumps(occupancy), encoding="utf-8"); mow.write_text(json.dumps(mower), encoding="utf-8")
    return cal, occ, mow, candidate


def run_prepare(tmp_path, **changes):
    cal, occ, mow, candidate = source_fixture(tmp_path)
    candidate.update(changes)
    cal.write_text(json.dumps(candidate), encoding="utf-8")
    output = tmp_path / "approved.json"
    result = prepare_approved_copy(cal, output, expected_content_sha256=calendar_digest(candidate), approval_reference="TEST ONLY", approved_at="2026-09-09T10:00:00Z", occupancy_config_path=occ, mower_config_path=mow, now_utc=datetime(2026, 9, 9, 12, tzinfo=UTC))
    return result, output, candidate


def test_creates_valid_detached_copy(tmp_path):
    result, output, candidate = run_prepare(tmp_path)
    assert result["approval"]["status"] == "approved"
    assert json.loads(output.read_text(encoding="utf-8"))["approval"]["content_sha256"] == calendar_digest(candidate)
    assert result is not candidate


@pytest.mark.parametrize("kwargs", [
    {"expected_content_sha256": "0" * 64},
    {"approval_reference": ""},
    {"approved_at": "2026-09-09T10:00:00"},
    {"approved_at": "2026-09-09T13:00:00Z"},
])
def test_bad_attestation_is_rejected_without_output(tmp_path, kwargs):
    cal, occ, mow, candidate = source_fixture(tmp_path)
    output = tmp_path / "approved.json"
    params = {"expected_content_sha256": calendar_digest(candidate), "approval_reference": "TEST ONLY", "approved_at": "2026-09-09T10:00:00Z"}
    params.update(kwargs)
    with pytest.raises(ApprovalPreparationError):
        prepare_approved_copy(cal, output, **params, occupancy_config_path=occ, mower_config_path=mow, now_utc=datetime(2026, 9, 9, 12, tzinfo=UTC))
    assert not output.exists()


def test_pending_or_schema_failure_is_rejected(tmp_path):
    cal, occ, mow, candidate = source_fixture(tmp_path)
    candidate["schema_version"] = 99
    cal.write_text(json.dumps(candidate), encoding="utf-8")
    output = tmp_path / "approved.json"
    with pytest.raises(ApprovalPreparationError):
        prepare_approved_copy(cal, output, expected_content_sha256=calendar_digest(candidate), approval_reference="TEST ONLY", approved_at="2026-09-09T10:00:00Z", occupancy_config_path=occ, mower_config_path=mow, now_utc=datetime(2026, 9, 9, 12, tzinfo=UTC))
    assert not output.exists()


def test_existing_output_is_never_replaced(tmp_path):
    cal, occ, mow, candidate = source_fixture(tmp_path)
    output = tmp_path / "approved.json"; output.write_text("keep", encoding="utf-8")
    with pytest.raises(ApprovalPreparationError):
        prepare_approved_copy(cal, output, expected_content_sha256=calendar_digest(candidate), approval_reference="TEST ONLY", approved_at="2026-09-09T10:00:00Z", occupancy_config_path=occ, mower_config_path=mow, now_utc=datetime(2026, 9, 9, 12, tzinfo=UTC))
    assert output.read_text(encoding="utf-8") == "keep"


def test_exclusive_create_race_never_deletes_foreign_file(tmp_path, monkeypatch):
    cal, occ, mow, candidate = source_fixture(tmp_path)
    output = tmp_path / "approved.json"
    original_open = approval_cli.os.open

    def racing_open(path, flags, mode=0o777):
        output.write_text("foreign-writer", encoding="utf-8")
        raise FileExistsError(17, "already exists", str(path))

    monkeypatch.setattr(approval_cli.os, "open", racing_open)
    with pytest.raises(ApprovalPreparationError):
        prepare_approved_copy(
            cal, output, expected_content_sha256=calendar_digest(candidate),
            approval_reference="TEST ONLY", approved_at="2026-09-09T10:00:00Z",
            occupancy_config_path=occ, mower_config_path=mow,
            now_utc=datetime(2026, 9, 9, 12, tzinfo=UTC),
        )
    assert output.read_text(encoding="utf-8") == "foreign-writer"
    monkeypatch.setattr(approval_cli.os, "open", original_open)
