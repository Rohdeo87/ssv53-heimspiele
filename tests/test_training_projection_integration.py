from dataclasses import replace
from datetime import date, datetime, timezone
from pathlib import Path

import pytest

from mower.planner import create_plan
from occupancy.service import build_occupancy_payload, build_training_occurrences
from occupancy.training_calendar import TrainingCancellation
from test_training_calendar import fixture, resolve as resolve_calendar


ROOT = Path(__file__).resolve().parents[1]
UTC = timezone.utc


def batch(*, blocking=True):
    document, occ, mower = fixture()
    cancellations = () if blocking else (TrainingCancellation("test-e1", date(2026, 9, 8), datetime(2026, 9, 8, tzinfo=UTC), datetime(2026, 9, 8, 20, tzinfo=UTC)),)
    result = resolve_calendar(document, occ, mower, start="2026-09-07T00:00:00+02:00", end="2026-09-09T00:00:00+02:00", now=datetime(2026, 9, 8, tzinfo=UTC), cancellations=cancellations)
    assert result.batch is not None
    return result.batch


def test_app_uses_batch_even_when_client_selects_winter():
    result = build_occupancy_payload(config_path=ROOT / "occupancy/config.json", matches_path=ROOT / "public/rasen.ics",
                                     start="2026-09-08T00:00:00+02:00", end="2026-09-09T00:00:00+02:00",
                                     season="Winter", training_batch=batch())
    assert [event["id"] for event in result["events"] if event["source"] == "training"] == ["training:sommer:test-e1:2026-09-08", "training:sommer:test-a:2026-09-08"]


def test_app_and_mower_share_nominal_and_buffered_times():
    b = batch()
    occurrences = build_training_occurrences(config_path=ROOT / "occupancy/config.json",
        start="2026-09-08T00:00:00+02:00", end="2026-09-09T00:00:00+02:00", season="Winter", training_batch=b)
    assert occurrences[0]["start"] == "2026-09-08T17:00:00+02:00"
    _, blocks = create_plan({"timezone": "Europe/Berlin", "planning": {"day_start": "06:00", "day_end": "22:00"}, "hydrawise": {}}, [], None,
                            datetime(2026, 9, 8).date(), 1, {("test-e1", "2026-09-08")}, training_batch=b)
    training = next(item for item in blocks if item.source == "training")
    assert (training.start.isoformat(), training.end.isoformat()) == ("2026-09-08T16:30:00+02:00", "2026-09-08T20:30:00+02:00")


def test_pending_cancellation_stays_a_mower_block():
    _, blocks = create_plan({"timezone": "Europe/Berlin", "planning": {"day_start": "06:00", "day_end": "22:00"}, "hydrawise": {}}, [], None,
                            datetime(2026, 9, 8).date(), 1, set(), training_batch=batch(blocking=False))
    assert any(item.source == "training" for item in blocks)

def test_effective_cancellation_releases_mower_projection():
    document, occ, mower = fixture()
    cancellation = (TrainingCancellation("test-e1", date(2026, 9, 8), datetime(2026, 9, 8, tzinfo=UTC), datetime(2026, 9, 8, 20, tzinfo=UTC)),)
    resolved = resolve_calendar(document, occ, mower, start="2026-09-07T00:00:00+02:00", end="2026-09-09T00:00:00+02:00", now=datetime(2026, 9, 8, 21, tzinfo=UTC), cancellations=cancellation)
    _, blocks = create_plan({"timezone": "Europe/Berlin", "planning": {"day_start": "06:00", "day_end": "22:00"}, "hydrawise": {}}, [], None, date(2026, 9, 8), 1, set(), training_batch=resolved.batch)
    assert all("test-e1" not in str(item.details) for item in blocks if item.source == "training")


def test_invalid_or_horizon_foreign_batch_is_rejected():
    with pytest.raises(ValueError):
        build_training_occurrences(config_path=ROOT / "occupancy/config.json", start="2026-09-09T00:00:00+02:00",
                                   end="2026-09-11T00:00:00+02:00", training_batch=batch())
    with pytest.raises(TypeError):
        build_occupancy_payload(config_path=ROOT / "occupancy/config.json", matches_path=ROOT / "public/rasen.ics",
                                start="2026-09-08T00:00:00+02:00", end="2026-09-09T00:00:00+02:00", training_batch=[])
    b = batch()
    with pytest.raises(ValueError):
        build_training_occurrences(config_path=ROOT / "occupancy/config.json", start="2026-09-08T00:00:00+02:00", end="2026-09-09T00:00:00+02:00", training_batch=replace(b, events=(dict(b.events[0], calendarSha256="f" * 64),)))
    with pytest.raises(ValueError):
        create_plan({"timezone": "Europe/Berlin", "planning": {"day_start": "06:00", "day_end": "22:00"}, "hydrawise": {}}, [], None, date(2026, 9, 8), 1, training_batch=replace(b, revision=True))

def test_consumers_reject_inconsistent_evaluation_and_release_state():
    b = batch()
    cases = (
        dict(b.events[0], evaluatedAtUtc="2026-09-08T01:00:00+00:00"),
        dict(b.events[0], cancelled=False, blocking=False),
        dict(b.events[0], cancelled=True, blocking=False, releaseNotBeforeUtc="2026-09-08T20:00:00+00:00"),
    )
    for event in cases:
        invalid = replace(b, events=(event,))
        with pytest.raises(ValueError):
            build_training_occurrences(config_path=ROOT / "occupancy/config.json", start="2026-09-08T00:00:00+02:00", end="2026-09-09T00:00:00+02:00", training_batch=invalid)
        with pytest.raises(ValueError):
            create_plan({"timezone": "Europe/Berlin", "planning": {"day_start": "06:00", "day_end": "22:00"}, "hydrawise": {}}, [], None, date(2026, 9, 8), 1, training_batch=invalid)


def test_none_keeps_legacy_projection():
    result = build_training_occurrences(config_path=ROOT / "occupancy/config.json", start="2026-09-09T00:00:00+02:00",
                                        end="2026-09-10T00:00:00+02:00", season="Sommer")
    assert result and all("calendarId" not in event for event in result)
