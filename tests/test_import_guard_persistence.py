"""The release guard must survive restarts without accepting stale replays."""
from datetime import datetime, timedelta, timezone
import json

import pytest

import report_changes

NOW = datetime(2026, 9, 9, 10, tzinfo=timezone.utc)
ITEMS = [{"kind": "removed", "id": "dfb:test", "before": {}, "after": None}]


def observe(path, when):
    return report_changes.confirm_safety_decrease(
        ITEMS, state_path=path, now=when,
        required_confirmations=2, minimum_interval=timedelta(minutes=60),
    )


@pytest.mark.parametrize("damage", ["malformed_json", "invalid_count", "inflated_count", "future_clock", "missing_clock", "wrong_items"])
def test_corrupt_pending_state_restarts_confirmation_without_releasing(tmp_path, damage):
    path = tmp_path / "guard.json"
    observe(path, NOW)
    if damage == "malformed_json":
        path.write_text("{broken", encoding="utf-8")
    else:
        state = json.loads(path.read_text(encoding="utf-8"))
        if damage == "invalid_count":
            state["confirmations"] = "not-a-number"
        elif damage == "inflated_count":
            state["confirmations"] = 999
        elif damage == "future_clock":
            state["lastCountedAt"] = (NOW + timedelta(days=1)).isoformat()
        elif damage == "missing_clock":
            state["firstSeenAt"] = ""
        else:
            state["items"] = []
        path.write_text(json.dumps(state), encoding="utf-8")
    result = observe(path, NOW + timedelta(minutes=60))
    assert result["confirmed"] is False
    assert result["confirmations"] == 1


def test_replayed_acquisition_timestamp_does_not_count_as_new_read(tmp_path):
    path = tmp_path / "guard.json"
    assert observe(path, NOW)["confirmed"] is False
    assert observe(path, NOW)["confirmed"] is False
    assert observe(path, NOW + timedelta(minutes=59))["confirmed"] is False
    assert observe(path, NOW + timedelta(minutes=60))["confirmed"] is True


def test_cli_passes_source_acquisition_time_to_confirmation(tmp_path, monkeypatch):
    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return (NOW + timedelta(hours=2)).astimezone(tz or timezone.utc)

    monkeypatch.setattr(report_changes, "datetime", Clock)
    old = {"id": "dfb:test", "start": "2099-09-10T10:00:00+02:00", "end": "2099-09-10T12:00:00+02:00"}
    before, after = tmp_path / "before.json", tmp_path / "after.json"
    before.write_text(json.dumps({"matches": [old]}), encoding="utf-8")
    after.write_text(json.dumps({"matches": [], "generatedAt": NOW.isoformat()}), encoding="utf-8")
    monkeypatch.setattr(report_changes.os.sys, "argv", ["report_changes.py", "--before", str(before), "--after", str(after), "--json", str(tmp_path / "report.json"), "--markdown", str(tmp_path / "report.md"), "--confirmation-state", str(tmp_path / "guard.json")])
    assert report_changes.main() == 2
    state = json.loads((tmp_path / "guard.json").read_text(encoding="utf-8"))
    assert state["firstSeenAt"] == NOW.isoformat(timespec="seconds")
