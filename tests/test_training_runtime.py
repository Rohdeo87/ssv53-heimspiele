from dataclasses import replace
from datetime import date, datetime, timezone
import copy
import json
from pathlib import Path

import pytest

from occupancy.training_calendar import TrainingCancellation, calendar_digest
from scripts.build_runtime_config_bundle import RuntimeBundleError
from tests.test_runtime_config_bundle import RuntimeConfigBundleTests
from occupancy.training_runtime import (
    ENVELOPE_KEY, make_training_envelope, resolve_runtime_training, resolve_training_file,
)
from test_training_calendar import fixture

ROOT = Path(__file__).resolve().parents[1]
UTC = timezone.utc
START = datetime(2026, 9, 7, tzinfo=UTC)
END = datetime(2026, 9, 9, tzinfo=UTC)
NOW = datetime(2026, 9, 8, tzinfo=UTC)


def envelope():
    document, occupancy, mower = fixture()
    document["enabled"] = True
    document["approval"] = {"status": "approved", "reference": "TEST ONLY", "approved_at_utc": "2026-01-01T00:00:00Z", "content_sha256": calendar_digest(document)}
    return make_training_envelope(document, occupancy_config=occupancy, mower_config=mower, now_utc=NOW)


def resolve(holder, *, mode="ACTIVE", consumer="occupancy", **kwargs):
    return resolve_runtime_training(holder, consumer=consumer, environment={"SHARED_TRAINING_MODE": mode}, legacy_config=kwargs.pop("legacy_config", (fixture()[1] if consumer == "occupancy" else fixture()[2])), range_start=START, range_end=END, now_utc=NOW, **kwargs)


def test_off_and_missing_file_produce_no_batch(tmp_path):
    assert resolve({}, mode="OFF").batch is None
    assert resolve_training_file(tmp_path / "missing.json", consumer="occupancy", environment={"SHARED_TRAINING_MODE": "ACTIVE"}, legacy_config=fixture()[1], range_start=START, range_end=END, now_utc=NOW).batch is None


def test_shadow_keeps_candidate_without_activation_and_active_matches_consumers():
    holder = {ENVELOPE_KEY: envelope()}
    shadow = resolve(holder, mode="SHADOW")
    active_app, active_mower = resolve(holder, consumer="occupancy"), resolve(holder, consumer="mower")
    assert shadow.batch is None and shadow.candidate is not None
    assert [e["id"] for e in active_app.batch.events] == [e["id"] for e in active_mower.batch.events]


@pytest.mark.parametrize("mutate", [
    lambda h: h.pop(ENVELOPE_KEY),
    lambda h: h[ENVELOPE_KEY].update(sha256="0" * 64),
    lambda h: h[ENVELOPE_KEY].update(calendar={}),
    lambda h: h[ENVELOPE_KEY].update(mower_baseline={}),
    lambda h: h[ENVELOPE_KEY].update(schema_version=2),
])
def test_missing_or_manipulated_envelope_blocks(mutate):
    holder = {ENVELOPE_KEY: envelope()}
    mutate(holder)
    result = resolve(holder)
    assert result.batch is None and result.blockers


def test_each_consumer_baseline_and_freshness_are_checked():
    holder = {ENVELOPE_KEY: envelope()}
    for consumer, config in (("occupancy", fixture()[1]), ("mower", fixture()[2])):
        changed = copy.deepcopy(config)
        changed["timezone"] = "UTC"
        result = resolve(holder, consumer=consumer, legacy_config=changed)
        assert result.batch is None
        assert resolve(holder, consumer=consumer, source_fresh=False).batch is None
    assert resolve(holder, mode="NOPE").blockers == ("SHARED_TRAINING_MODE_INVALID",)


def test_unapproved_template_does_not_activate():
    document, occupancy, mower = fixture()
    document["enabled"] = False
    document["approval"] = {}
    with pytest.raises(ValueError):
        make_training_envelope(document, occupancy_config=occupancy, mower_config=mower, now_utc=NOW)


def test_server_cancellation_pending_then_effective_and_relocation_filters_original():
    holder = {ENVELOPE_KEY: envelope()}
    cancellation = TrainingCancellation("test-e1", date(2026, 9, 8), datetime(2026, 9, 8, tzinfo=UTC), datetime(2026, 9, 8, 20, tzinfo=UTC))
    pending = resolve(holder, cancellations=(cancellation,))
    assert any(e["blocking"] and e["cancelled"] for e in pending.batch.events)
    effective = resolve_runtime_training(holder, consumer="occupancy", environment={"SHARED_TRAINING_MODE": "ACTIVE"}, legacy_config=fixture()[1], range_start=START, range_end=END, now_utc=datetime(2026, 9, 8, 21, tzinfo=UTC), cancellations=(cancellation,))
    assert any(e["cancelled"] is True and e["blocking"] is False for e in effective.batch.events)
    relocated = resolve(holder, relocated_keys={("test-e1", "2026-09-08")})
    assert all(e["scheduleId"] != "test-e1" for e in relocated.batch.events)


def test_builder_embeds_identical_envelope_and_manifest_hashes(tmp_path):
    document = json.loads((ROOT / "occupancy/training_calendar.template.json").read_text(encoding="utf-8"))
    occupancy = json.loads((ROOT / "occupancy/config.json").read_text(encoding="utf-8"))
    mower = json.loads((ROOT / "mower/config.json").read_text(encoding="utf-8"))
    document["enabled"] = True
    document["season_periods"] = [{"from": "2026-08-11", "through": "2027-07-09", "season": "Sommer"}]
    document["holidays"] = {"reviewed": True, "periods": []}
    from occupancy.training_calendar import legacy_source_hashes
    document["legacy_sources"] = legacy_source_hashes(occupancy, mower)
    document["approval"] = {"status": "approved", "reference": "TEST ONLY", "approved_at_utc": "2026-01-01T00:00:00Z", "content_sha256": calendar_digest(document)}
    calendar_path = tmp_path / "calendar.json"
    calendar_path.write_text(json.dumps(document), encoding="utf-8")
    summary = RuntimeConfigBundleTests()._build(tmp_path, shared_training_calendar_path=calendar_path, occupancy_config_path=ROOT / "occupancy/config.json")
    version = tmp_path / "bundle" / "versions" / "20260811T163000Z-test"
    config = json.loads((version / "mower" / "config.json").read_text(encoding="utf-8"))
    matches = json.loads((version / "public" / "matches.json").read_text(encoding="utf-8"))
    assert config["shared_training_calendar"] == matches["shared_training_calendar"]
    manifest = json.loads((tmp_path / "bundle" / "current" / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["config_sha256"] == summary["config_sha256"]
    assert manifest["matches_sha256"] == summary["matches_sha256"]


def test_builder_requires_optional_calendar_arguments_as_a_pair(tmp_path):
    with pytest.raises(RuntimeBundleError):
        RuntimeConfigBundleTests()._build(tmp_path, occupancy_config_path=ROOT / "occupancy/config.json")
    summary = RuntimeConfigBundleTests()._build(tmp_path)
    config = json.loads((tmp_path / "bundle" / "versions" / "20260811T163000Z-test" / "mower" / "config.json").read_text(encoding="utf-8"))
    assert "shared_training_calendar" not in config
    assert summary["shared_training_envelope_sha256"] is None


def test_resolver_accepts_64_local_days_across_autumn_dst():
    document, occupancy, mower = fixture()
    document["season_periods"] = [{"from": "2026-01-01", "through": "2026-12-31", "season": "Sommer"}]
    from occupancy.training_calendar import calendar_digest, legacy_source_hashes, resolve_training_calendar
    document["legacy_sources"] = legacy_source_hashes(occupancy, mower)
    document["approval"] = {"status": "approved", "reference": "TEST ONLY", "approved_at_utc": "2026-01-01T00:00:00Z", "content_sha256": calendar_digest(document)}
    result = resolve_training_calendar(document, range_start=datetime.fromisoformat("2026-10-01T00:00:00+02:00"), range_end=datetime.fromisoformat("2026-12-04T00:00:00+01:00"), now_utc=NOW, occupancy_config=occupancy, mower_config=mower)
    assert result.batch is not None
