from datetime import date, datetime, timezone
import json
from unittest.mock import patch

import pytest

import occupancy_notifications as notifications
from mower.state import AutomationState
from mower.state_store import InMemoryStateStore
from occupancy.runtime_source import OccupancyMatchSource
from occupancy.training_control import resolve_training_control
from occupancy.training_runtime import make_training_envelope
from test_training_calendar import fixture


def event(source, **values):
    return {"source": source, "resourceId": "rasen", "start": "2026-09-08T17:00:00+02:00", "end": "2026-09-08T18:00:00+02:00", **values}


def test_find_collisions_uses_blocking_and_occupancy_interval():
    match = event("match", occupancyStart="2026-09-08T16:30:00+02:00", occupancyEnd="2026-09-08T18:30:00+02:00")
    pending = event("training", cancelled=True, blocking=True, start="2026-09-08T17:30:00+02:00", end="2026-09-08T18:00:00+02:00")
    effective = dict(pending, blocking=False)
    assert notifications.find_collisions([match, pending]) == [(match, pending)]
    assert notifications.find_collisions([match, effective]) == []


def test_find_collisions_keeps_legacy_cancelled_behavior():
    match = event("match")
    legacy = event("training", cancelled=False)
    assert notifications.find_collisions([match, legacy]) == [(match, legacy)]
    assert notifications.find_collisions([match, dict(legacy, cancelled=True)]) == []


def test_current_payload_off_uses_one_source_and_legacy_projection(monkeypatch):
    source = OccupancyMatchSource(matches_path="synthetic.json", source_kind="test", fresh=True)
    store = type("Store", (), {"list_active": lambda self, *_: []})()
    special = type("Special", (), {"list_active": lambda self, *_: []})()
    captured = {}
    def fake_payload(**kwargs):
        captured.update(kwargs)
        return {"range": {"start": "2026-09-08T00:00:00+02:00", "end": "2026-09-09T00:00:00+02:00"}, "events": []}
    monkeypatch.setattr(notifications, "resolve_occupancy_match_source", lambda *_args, **_kwargs: source)
    monkeypatch.setattr(notifications.AzureTableCancellationStore, "from_environment", lambda *_: store)
    monkeypatch.setattr(notifications.AzureTableSpecialOccupancyStore, "from_environment", lambda *_: special)
    monkeypatch.setattr(notifications, "build_occupancy_payload", fake_payload)
    payload = notifications._current_payload(datetime(2026, 9, 8, 12, tzinfo=timezone.utc), {"SHARED_TRAINING_MODE": "OFF"})
    assert payload["events"] == []
    assert captured["matches_path"] == "synthetic.json"
    assert captured["training_batch"] is None


def test_current_payload_active_passes_cancellations_and_batch(monkeypatch, tmp_path):
    source = OccupancyMatchSource(matches_path="synthetic.json", source_kind="test", fresh=True)
    store_calls = []
    cancellation = type("Cancellation", (), {"occurrence_key": ("test-night", "2026-09-07")})()
    store = type("Store", (), {"list_active": lambda self, *args: (store_calls.append(args) or [cancellation])})()
    special = type("Special", (), {"list_active": lambda self, *_: []})()
    batch = object()
    runtime = type("Runtime", (), {"batch": batch, "require_available": lambda self: None})()
    control_snapshot = object()
    captured = {}
    payload_arguments = {}
    monkeypatch.setattr(notifications, "resolve_occupancy_match_source", lambda *_args, **_kwargs: source)
    monkeypatch.setattr(notifications.AzureTableCancellationStore, "from_environment", lambda *_: store)
    monkeypatch.setattr(notifications.AzureTableSpecialOccupancyStore, "from_environment", lambda *_: special)
    monkeypatch.setattr(notifications, "resolve_training_control", lambda *_args, **_kwargs: control_snapshot)
    monkeypatch.setattr(notifications, "resolve_training_file", lambda *args, **kwargs: (captured.update(kwargs) or runtime))
    monkeypatch.setattr(notifications, "build_occupancy_payload", lambda **kwargs: (payload_arguments.update(kwargs) or {"range": {"start": "2026-09-08T00:00:00+02:00", "end": "2026-09-09T00:00:00+02:00"}, "events": []}))
    monkeypatch.setattr(notifications.Path, "read_text", lambda *_args, **_kwargs: "{}")
    notifications._current_payload(
        datetime(2026, 9, 8, 12, tzinfo=timezone.utc),
        {"SHARED_TRAINING_MODE": "ACTIVE", "WINTER_TRAINING_CONTROL_ENABLED": "true"},
    )
    assert captured["source_fresh"] is True
    assert captured["cancellations"] == [cancellation]
    assert captured["control_snapshot"] is control_snapshot
    assert payload_arguments["training_batch"] is batch
    assert store_calls[0][0] == date(2026, 9, 7)


def test_current_payload_active_uses_a_real_persisted_training_control_snapshot(monkeypatch, tmp_path):
    now = datetime(2026, 9, 10, 12, tzinfo=timezone.utc)
    document, occupancy, mower = fixture()
    occupancy["schema_version"] = 1
    envelope = make_training_envelope(
        document, occupancy_config=occupancy, mower_config=mower, now_utc=now,
    )
    config_path = tmp_path / "occupancy.json"
    feed_path = tmp_path / "matches.json"
    config_path.write_text(json.dumps(occupancy), encoding="utf-8")
    feed_path.write_text(json.dumps({
        "schemaVersion": 2, "status": "ok", "matches": [],
        "shared_training_calendar": envelope,
    }), encoding="utf-8")
    state = AutomationState(
        revision=3,
        winter_training_enabled=False,
        winter_training_history_valid_from_utc="2026-09-08T22:00:00+00:00",
        winter_training_history_approval_reference="approved-d9-anchor",
    )
    snapshot = resolve_training_control(
        {"WINTER_TRAINING_CONTROL_ENABLED": "true", "SHARED_TRAINING_MODE": "ACTIVE"},
        now_utc=now, state_store_factory=lambda _env: InMemoryStateStore(state),
    )
    assert snapshot.available and snapshot.season == "Sommer"
    source = OccupancyMatchSource(matches_path=str(feed_path), source_kind="test", fresh=True)
    store = type("Store", (), {"list_active": lambda self, *_: []})()
    special = type("Special", (), {"list_active": lambda self, *_: []})()
    monkeypatch.setattr(notifications, "resolve_occupancy_match_source", lambda *_args, **_kwargs: source)
    monkeypatch.setattr(notifications.AzureTableCancellationStore, "from_environment", lambda *_: store)
    monkeypatch.setattr(notifications.AzureTableSpecialOccupancyStore, "from_environment", lambda *_: special)
    monkeypatch.setattr(notifications, "resolve_training_control", lambda *_args, **_kwargs: snapshot)
    payload = notifications._current_payload(now, {
        "SHARED_TRAINING_MODE": "ACTIVE", "WINTER_TRAINING_CONTROL_ENABLED": "true",
        "OCCUPANCY_CONFIG_PATH": str(config_path),
    })
    assert payload["range"]["start"].startswith("2026-09-10T00:00:00")
