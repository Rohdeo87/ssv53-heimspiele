from datetime import date, datetime, timezone
from unittest.mock import patch

import pytest

import occupancy_notifications as notifications
from occupancy.runtime_source import OccupancyMatchSource


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
    captured = {}
    payload_arguments = {}
    monkeypatch.setattr(notifications, "resolve_occupancy_match_source", lambda *_args, **_kwargs: source)
    monkeypatch.setattr(notifications.AzureTableCancellationStore, "from_environment", lambda *_: store)
    monkeypatch.setattr(notifications.AzureTableSpecialOccupancyStore, "from_environment", lambda *_: special)
    monkeypatch.setattr(notifications, "resolve_training_file", lambda *args, **kwargs: (captured.update(kwargs) or runtime))
    monkeypatch.setattr(notifications, "build_occupancy_payload", lambda **kwargs: (payload_arguments.update(kwargs) or {"range": {"start": "2026-09-08T00:00:00+02:00", "end": "2026-09-09T00:00:00+02:00"}, "events": []}))
    monkeypatch.setattr(notifications.Path, "read_text", lambda *_args, **_kwargs: "{}")
    notifications._current_payload(datetime(2026, 9, 8, 12, tzinfo=timezone.utc), {"SHARED_TRAINING_MODE": "ACTIVE"})
    assert captured["source_fresh"] is True
    assert captured["cancellations"] == [cancellation]
    assert payload_arguments["training_batch"] is batch
    assert store_calls[0][0] == date(2026, 9, 7)
