import json
from pathlib import Path

import pytest

from occupancy.training_calendar import calendar_digest
from scripts.build_runtime_config_bundle import RuntimeBundleError
from tests import test_runtime_config_bundle as bundle_fixtures

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize("approved,manual", [(True, True), (False, True), (True, False)])
def test_manual_calendar_is_bound_to_approval_and_explicit_builder_mode(tmp_path, approved, manual):
    document = json.loads((ROOT / "occupancy/training_calendar.manual-control.candidate.json").read_text(encoding="utf-8"))
    if approved:
        document["approval"] = {"status": "approved", "reference": "TEST ONLY - no production approval", "approved_at_utc": "2026-08-01T00:00:00Z", "content_sha256": calendar_digest(document)}
    calendar = tmp_path / "calendar.json"
    calendar.write_text(json.dumps(document), encoding="utf-8")
    fixture = bundle_fixtures.RuntimeConfigBundleTests()
    arguments = dict(shared_training_calendar_path=calendar, occupancy_config_path=ROOT / "occupancy/config.json", manual_training_control=manual)
    if not (approved and manual):
        with pytest.raises(RuntimeBundleError):
            fixture._build(tmp_path, **arguments)
        return
    fixture._build(tmp_path, **arguments)
    manifest = json.loads((tmp_path / "bundle/current/manifest.json").read_text(encoding="utf-8"))
    config = json.loads((tmp_path / "bundle" / manifest["config_blob"]).read_text(encoding="utf-8"))
    assert config["shared_training_calendar"]["calendar"] == document


def test_manual_mode_never_builds_without_calendar(tmp_path):
    with pytest.raises(RuntimeBundleError, match="Wintertrainingsschalter"):
        bundle_fixtures.RuntimeConfigBundleTests()._build(tmp_path, manual_training_control=True)
