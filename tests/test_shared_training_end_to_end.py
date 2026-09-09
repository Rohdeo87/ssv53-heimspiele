"""Synthetic calendars and in-memory stores; every vendor call is replaced."""
import json
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import azure.functions as func
import pytest

import function_app
from mower.config_source import RuntimeInputPaths
from mower.decision import AUTOMATION_EXTERNAL_REASON
from mower.dry_run import run_read_only_cycle
from mower.husqvarna import MowerSnapshot
from mower.runtime import RuntimeSettings
from mower.safety import occupancy_override_allowed
from mower.state_store import InMemoryStateStore
from occupancy.runtime_source import OccupancyMatchSource
from occupancy.training_runtime import make_training_envelope
from special_occupancy import InMemorySpecialOccupancyStore
from training_cancellations import InMemoryCancellationStore
from test_training_calendar import fixture

NOW = datetime(2026, 9, 8, 15, tzinfo=timezone.utc)
EVENT_ID = "training:sommer:test-e1:2026-09-08"


@pytest.fixture
def runtime(tmp_path, monkeypatch):
    document, occupancy, mower = fixture()
    occupancy["schema_version"] = 1
    mower.update(planning={"day_start": "00:00", "day_end": "00:00", "minimum_mowing_window_minutes": 30},
                 hydrawise={"enabled": True, "include_all_zones": True, "before_minutes": 30, "after_minutes": 150})
    envelope = make_training_envelope(document, occupancy_config=occupancy, mower_config=mower, now_utc=NOW)
    mower["shared_training_calendar"] = envelope
    occ_path, mower_path, feed_path, ics = (tmp_path / name for name in ("occupancy.json", "mower.json", "matches.json", "rasen.ics"))
    occ_path.write_text(json.dumps(occupancy), encoding="utf-8")
    mower_path.write_text(json.dumps(mower), encoding="utf-8")
    feed_path.write_text(json.dumps({"schemaVersion": 2, "status": "ok", "matches": [], "shared_training_calendar": envelope}), encoding="utf-8")
    ics.write_text("BEGIN:VCALENDAR\nEND:VCALENDAR\n", encoding="utf-8")
    monkeypatch.setenv("SHARED_TRAINING_MODE", "ACTIVE")
    monkeypatch.setenv("OCCUPANCY_CONFIG_PATH", str(occ_path))
    source = OccupancyMatchSource(matches_path=str(feed_path), source_kind="test", fresh=True)
    monkeypatch.setattr(function_app, "_occupancy_match_source", lambda **kwargs: source)
    store = InMemoryCancellationStore()
    monkeypatch.setattr(function_app.AzureTableCancellationStore, "from_environment", lambda _env: store)
    monkeypatch.setattr(function_app, "special_occupancy_enabled", lambda _env: False)
    with patch.object(function_app, "datetime", wraps=datetime) as clock:
        clock.now.return_value = NOW
        yield {"mower_path": mower_path, "feed_path": feed_path, "ics": ics, "store": store,
               "source": source, "clock": clock, "mower": mower}


def app_request():
    return func.HttpRequest(method="GET", url="https://example.test/api/occupancy", headers={},
        params={"start": "2026-09-08", "end": "2026-09-10", "season": "Winter"}, body=b"")


def controller(runtime, *, now=NOW):
    snapshot = MowerSnapshot(mower_id="test", name="test", model="test", battery_percent=90,
        activity="PARKED_IN_CS", state="IN_OPERATION", mode="MAIN_AREA", error_code=0,
        override_action="FORCE_PARK", restricted_reason="NOT_APPLICABLE",
        external_reason_id=AUTOMATION_EXTERNAL_REASON, next_start_timestamp_ms=None, work_areas=())
    status = {"time": int(now.timestamp()), "relays": [
        {"relay_id": n, "name": f"test{n}", "time": 864000, "run": 600} for n in range(1, 8)
    ]}
    settings = RuntimeSettings.from_mapping({"CONTROL_MODE": "DRY_RUN", "ENABLE_LIVE_READS": "true"})
    environment = {"SHARED_TRAINING_MODE": "ACTIVE", "HUSQVARNA_CLIENT_ID": "fake",
                   "HUSQVARNA_CLIENT_SECRET": "fake", "HYDRAWISE_API_KEY": "fake"}
    paths = RuntimeInputPaths(str(runtime["mower_path"]), str(runtime["ics"]), "test")
    with (patch("mower.dry_run.resolve_runtime_inputs", return_value=paths),
          patch("mower.dry_run.fetch_mowers", return_value=[{}]),
          patch("mower.dry_run.select_mower", return_value={}),
          patch("mower.dry_run.parse_snapshot", return_value=snapshot),
          patch("mower.dry_run.fetch_status", return_value=status),
          patch("mower.dry_run.special_occupancy_enabled", return_value=False)):
        return run_read_only_cycle(now_utc=now, settings=settings, environment=environment,
            past_due=False, source="test", state_store_factory=lambda _env: InMemoryStateStore(),
            cancellation_store_factory=lambda _env: runtime["store"], persist_observations=False)


def test_published_source_drives_app_controller_and_server_lookup(runtime):
    response = function_app.ssv53_occupancy(app_request())
    assert response.status_code == 200, response.get_body()
    payload = json.loads(response.get_body())
    event = next(e for e in payload["events"] if e["id"] == EVENT_ID)
    looked_up = function_app._trainer_training_occurrence(EVENT_ID)
    result = controller(runtime)
    assert looked_up["calendarSha256"] == event["calendarSha256"]
    assert result.details["training_calendar"]["calendar_sha256"] == event["calendarSha256"]
    block = result.details["current_plan"]["parking_block"]
    assert block["start"] == event["occupancyStart"]
    assert block["end"] == "2026-09-08T20:30:00+02:00"  # second overlapping training, one buffer each
    assert not result.command_sent


def test_pending_and_effective_cancellation_match_app_and_controller(runtime):
    occurrence = function_app._trainer_training_occurrence(EVENT_ID)
    runtime["store"].cancel(occurrence, now_utc=NOW, release_delay_minutes=30)
    pending = json.loads(function_app.ssv53_occupancy(app_request()).get_body())
    event = next(e for e in pending["events"] if e["id"] == EVENT_ID)
    assert event["cancelled"] and event["blocking"]
    assert controller(runtime).details["current_plan"]["parking_block"]["start"] == event["occupancyStart"]
    runtime["clock"].now.return_value = NOW + timedelta(minutes=30)
    effective = json.loads(function_app.ssv53_occupancy(app_request()).get_body())
    assert next(e for e in effective["events"] if e["id"] == EVENT_ID)["blocking"] is False
    result = controller(runtime, now=NOW + timedelta(minutes=30))
    blocks = result.details["current_plan"]["upcoming_blocks"]
    assert blocks[0]["start"] == "2026-09-08T18:00:00+02:00"
    conflicts = function_app._trainer_occupancy_conflicts(
        start=NOW, end=NOW + timedelta(minutes=25), resource_id="rasen", store=InMemorySpecialOccupancyStore())
    assert not conflicts


def test_missing_shared_source_returns_plain_error_and_nonoverridable_hold(runtime, monkeypatch):
    runtime["feed_path"].write_text('{"schema_version":1,"matches":[]}', encoding="utf-8")
    del runtime["mower"]["shared_training_calendar"]
    runtime["mower_path"].write_text(json.dumps(runtime["mower"]), encoding="utf-8")
    response = function_app.ssv53_occupancy(app_request())
    assert response.status_code == 503
    error = json.loads(response.get_body())
    assert "Trainingstermine" in error["error"] and "sha256" not in error["error"]
    result = controller(runtime)
    assert result.details["training_calendar"]["fail_closed"]
    assert not occupancy_override_allowed(result.details["current_plan"]["parking_block"])
    assert result.details["current_plan"]["safe_mowing_windows"] == []
    assert not result.command_sent
    monkeypatch.setattr(function_app, "_authorize_occupancy_write", lambda *args: {"requesterId": "test"})
    request = func.HttpRequest(method="POST", url="https://example.test/api/training-cancellations", headers={}, params={},
        body=json.dumps({"eventId": EVENT_ID, "action": "cancel", "confirmation": "TRAINING_FAELLT_AUS"}).encode())
    response = function_app.ssv53_training_cancellations(request)
    assert response.status_code == 503
    assert runtime["store"].list_active(NOW.date(), NOW.date()) == []


def test_cancellation_store_failure_retains_training_in_both_consumers(runtime, monkeypatch):
    def unavailable(*args):
        raise RuntimeError("test store unavailable")
    monkeypatch.setattr(runtime["store"], "list_active", unavailable)
    payload = json.loads(function_app.ssv53_occupancy(app_request()).get_body())
    assert payload["training_cancellations"]["fail_closed"]
    assert all(e["blocking"] for e in payload["events"])
    result = controller(runtime)
    assert result.details["training_cancellations"]["fail_closed"]
    assert result.details["current_plan"]["parking_block"] is not None
