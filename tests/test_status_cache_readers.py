"""Offline integration checks for the opt-in, read-only Hydrawise cache."""
from dataclasses import replace
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from mower import generate_schedule
from mower.dry_run import run_read_only_cycle
from mower.irrigation_recovery import (
    IrrigationRecoveryError, RESET_CONFIRMATION, _read_status_for_recovery, reset_failed_irrigation,
)
from mower.status_cache import CachedStatusRead, read_status_cached
from mower.status_cache_store import InMemoryStatusCacheStore
from mower.config_source import RuntimeInputPaths
from mower.runtime import RuntimeSettings
from mower.state import AutomationState
from mower.state_store import InMemoryStateStore
from tests.test_irrigation_recovery import MOWER


NOW = datetime(2026, 9, 9, 8, tzinfo=timezone.utc)
CONFIG = {"enabled": True, "include_all_zones": True, "expected_relay_ids": [1, 2],
          "expected_zone_count": 2, "relay_ids": [1, 2], "before_minutes": 30}
ENV = {"HYDRAWISE_STATUS_CACHE_MODE": "AZURE_TABLE", "HYDRAWISE_API_KEY": "test-key",
       "HYDRAWISE_CONTROLLER_ID": "123"}
STATUS = {"time": int(NOW.timestamp()), "nextpoll": 60,
          "relays": [{"relay_id": relay, "relay": relay, "name": f"Zone {relay}", "time": 7200, "run": 600}
                     for relay in (1, 2)]}
CACHED = CachedStatusRead(
    STATUS, STATUS, "CACHED", source_observed_at_utc=NOW.isoformat(), fetched_at_utc=NOW.isoformat(),
    next_poll_at_utc=(NOW + timedelta(seconds=60)).isoformat(),
)


def test_wrapper_uses_real_clock_instead_of_earlier_cycle_timestamp():
    result = read_status_cached(
        "test-key", "123", environment=ENV, hydrawise_config=CONFIG,
        now_utc=NOW - timedelta(seconds=20), clock=lambda: NOW,
        store=InMemoryStatusCacheStore(), fetcher=lambda *_a, **_k: STATUS,
    )
    assert result.new_observation and result.fetched_at_utc == NOW.isoformat()
    assert result.source_observed_at_utc == NOW.isoformat()


def test_standalone_off_mode_generator_still_imports_without_azure_sdks():
    root = Path(__file__).resolve().parents[1]
    code = """
import importlib.abc
import sys
class NoAzure(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname == 'azure' or fullname.startswith('azure.'):
            raise AssertionError('OFF-mode generator imported Azure SDK')
sys.meta_path.insert(0, NoAzure())
import mower.generate_schedule
"""
    result = subprocess.run([sys.executable, "-c", code], cwd=root, capture_output=True, text=True, timeout=10)
    assert result.returncode == 0, result.stderr


def _generate(tmp_path, environment, *, cached=CACHED, no_hydrawise=False):
    args = SimpleNamespace(config="unused", matches=str(tmp_path / "matches.ics"), output=str(tmp_path / "plan"),
                           start_date="2026-09-09", days=1, no_hydrawise=no_hydrawise)
    (tmp_path / "matches.ics").write_text("", encoding="utf-8")
    config = {"timezone": "Europe/Berlin", "planning": {}, "hydrawise": CONFIG}
    with (patch.dict(os.environ, environment, clear=True),
          patch.object(generate_schedule, "parse_args", return_value=args),
          patch.object(generate_schedule, "load_json", return_value=config),
          patch.object(generate_schedule, "read_match_blocks", return_value=[]),
          patch.object(generate_schedule, "create_plan", return_value=([], [])) as planner,
          patch.object(generate_schedule, "read_status_cached", return_value=cached) as coordinated,
          patch.object(generate_schedule, "fetch_status", return_value=STATUS) as direct):
        assert generate_schedule.main() == 0
    return json.loads((tmp_path / "plan" / "mowing_plan.json").read_text(encoding="utf-8")), planner, coordinated, direct


def test_offline_schedule_reuses_original_cache_time_without_direct_fetch(tmp_path):
    plan, planner, coordinated, direct = _generate(tmp_path, ENV)
    direct.assert_not_called()
    coordinated.assert_called_once()
    assert coordinated.call_args.kwargs["hydrawise_config"]["expected_relay_ids"] == [1, 2]
    assert planner.call_args.args[2] == STATUS
    assert plan["metadata"]["hydrawise_cache"]["source_observed_at_utc"] == NOW.isoformat()
    assert plan["metadata"]["hydrawise_cache"]["fetched_at_utc"] == NOW.isoformat()
    assert "gemeinsamer Status" in plan["metadata"]["hydrawise_status"]


def test_unavailable_cache_never_silently_uses_last_known_plan_or_direct_fallback(tmp_path):
    failed = replace(CACHED, status=None, quality="HTTP_429")
    plan, planner, _, direct = _generate(tmp_path, ENV, cached=failed)
    direct.assert_not_called()
    assert planner.call_args.args[2] is None
    assert any("HTTP_429" in warning for warning in plan["warnings"])
    assert plan["metadata"]["hydrawise_cache"]["quality"] == "HTTP_429"


def test_off_mode_keeps_existing_schedule_fetch_without_cache_effects(tmp_path):
    plan, planner, coordinated, direct = _generate(tmp_path, {**ENV, "HYDRAWISE_STATUS_CACHE_MODE": "OFF"})
    coordinated.assert_not_called()
    direct.assert_called_once_with("test-key", "123")
    assert planner.call_args.args[2] == STATUS
    assert "hydrawise_cache" not in plan["metadata"]


def test_explicit_no_hydrawise_switch_blocks_both_cache_and_direct_calls(tmp_path):
    _, planner, coordinated, direct = _generate(tmp_path, ENV, no_hydrawise=True)
    direct.assert_not_called()
    coordinated.assert_not_called()
    assert planner.call_args.args[2] is None


def test_invalid_cache_mode_does_not_fall_back_to_unbudgeted_schedule_fetch(tmp_path):
    plan, planner, coordinated, direct = _generate(tmp_path, {**ENV, "HYDRAWISE_STATUS_CACHE_MODE": "TYPO"})
    direct.assert_not_called()
    coordinated.assert_not_called()
    assert planner.call_args.args[2] is None
    assert plan["warnings"]


@pytest.mark.parametrize("cached", [
    CACHED,
    replace(CACHED, status=None, new_observation=True, quality="FETCH_FAILED"),
    replace(CACHED, new_observation=True, fetched_at_utc=None),
    replace(CACHED, new_observation=True, fetched_at_utc="2026-09-09T08:00:00"),
    replace(CACHED, new_observation=True, fetched_at_utc=(NOW - timedelta(seconds=181)).isoformat()),
    replace(CACHED, new_observation=True, fetched_at_utc=(NOW + timedelta(seconds=31)).isoformat()),
])
def test_prepared_recovery_rejects_reuse_error_or_unproven_receipt(cached):
    with pytest.raises(IrrigationRecoveryError) as error:
        _read_status_for_recovery(
            "test-key", "123", environment=ENV, hydrawise_config=CONFIG, now_utc=NOW,
            fetcher=lambda *_a: pytest.fail("unbudgeted retry"), cached_reader=lambda *_a, **_k: cached,
        )
    assert error.value.code == "RESET_FRESH_HYDRAWISE_REQUIRED"


def test_prepared_recovery_preserves_actual_new_fetch_receipt_time():
    fresh = replace(CACHED, new_observation=True, fetched_at_utc=(NOW + timedelta(seconds=5)).isoformat())
    status, observed, metadata = _read_status_for_recovery(
        "test-key", "123", environment=ENV, hydrawise_config=CONFIG, now_utc=NOW,
        fetcher=lambda *_a: pytest.fail("unexpected direct fetch"), cached_reader=lambda *_a, **_k: fresh,
    )
    assert status == STATUS and observed == NOW + timedelta(seconds=5)
    assert metadata["source_observed_at_utc"] == NOW.isoformat()


def test_off_mode_recovery_keeps_original_direct_read_contract():
    status, observed, metadata = _read_status_for_recovery(
        "test-key", "123", environment={}, hydrawise_config=CONFIG, now_utc=NOW,
        fetcher=lambda _key, _controller: STATUS,
        cached_reader=lambda *_a, **_k: pytest.fail("cache must be disabled"),
    )
    assert status == STATUS and observed == NOW and metadata is None


def test_device_mode_runtime_gate_stops_reset_before_storage_or_vendor_reads():
    with pytest.raises(ValueError, match="befehlsfreie"):
        reset_failed_irrigation(
            now_utc=NOW, environment={**ENV, "CONTROL_MODE": "FULL_FAILSAFE"},
            expected_revision=1, confirmation=RESET_CONFIRMATION,
            state_store_factory=lambda *_a: pytest.fail("state accessed"),
            mower_fetcher=lambda *_a: pytest.fail("mower read"),
            hydrawise_fetcher=lambda *_a: pytest.fail("Hydrawise read"),
        )


@pytest.mark.parametrize("persist", [False, True])
def test_actual_dry_run_cached_clear_does_not_earn_confirmation_time_or_app_state_writes(tmp_path, persist):
    environment = {**ENV, "CONTROL_MODE": "DRY_RUN", "ENABLE_LIVE_READS": "true",
                   "HUSQVARNA_CLIENT_ID": "test", "HUSQVARNA_CLIENT_SECRET": "test",
                   "HYDRAWISE_DATA_GAP_CONFIRMATION_MINUTES": "2", "WEATHER_ENABLED": "false"}
    initial = AutomationState(
        revision=3, last_hydrawise_success_utc=NOW.isoformat(),
        hydrawise_clear_since_utc=NOW.isoformat(), hydrawise_clear_origin="IRRIGATION_END",
        hydrawise_drying_since_utc=(NOW - timedelta(minutes=150)).isoformat(),
    )
    state = InMemoryStateStore(initial)
    config_path, matches_path = tmp_path / "config.json", tmp_path / "rasen.ics"
    config_path.write_text(json.dumps({"timezone": "Europe/Berlin", "planning": {"day_start": "00:00", "day_end": "00:00"},
                                       "training": {"weekly": []}, "hydrawise": CONFIG}), encoding="utf-8")
    matches_path.write_text("BEGIN:VCALENDAR\nEND:VCALENDAR\n", encoding="utf-8")
    paths = RuntimeInputPaths(config_path=str(config_path), matches_path=str(matches_path), source_kind="test")
    with (patch("mower.dry_run.resolve_runtime_inputs", return_value=paths),
          patch("mower.dry_run.fetch_mowers", return_value=[{}]),
          patch("mower.dry_run.select_mower", return_value={}),
          patch("mower.dry_run.parse_snapshot", return_value=MOWER),
          patch("mower.dry_run.special_occupancy_enabled", return_value=False),
          patch("mower.dry_run.fetch_status", side_effect=AssertionError("direct vendor fallback")),
          patch("mower.dry_run.read_status_cached", return_value=CACHED)):
        result = run_read_only_cycle(
            now_utc=NOW + timedelta(minutes=2), settings=RuntimeSettings.from_mapping(environment),
            environment=environment, past_due=False, source="platzwart-status" if not persist else "timer-test",
            state_store_factory=lambda _env: state,
            cancellation_store_factory=lambda _env: SimpleNamespace(list_active=lambda *_a: []),
            persist_observations=persist,
        )
    confirmation = result.details["hydrawise"]["release_confirmation"]
    assert confirmation["allowed"] is False and confirmation["telemetry_confirmed"] is False
    assert confirmation["confirmed_for_seconds"] == 0
    assert confirmation["dry_until_utc"] == NOW.isoformat()
    assert result.command_sent is False
    assert result.details["safety"]["persistent_safety_state_write"] is persist
    saved = state.load()
    assert saved.last_hydrawise_success_utc == NOW.isoformat()
    assert saved.hydrawise_drying_since_utc == initial.hydrawise_drying_since_utc
    if not persist:
        assert saved == initial
