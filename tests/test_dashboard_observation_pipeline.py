"""Real read pipeline: a controller read feeds the app without another GET."""
from contextlib import ExitStack, contextmanager
from datetime import datetime, timedelta, timezone
import json
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from mower.config_source import RuntimeInputPaths
from mower.dashboard_observations import InMemoryDashboardObservationStore
from mower.dry_run import run_read_only_cycle
from mower.full_failsafe import run_full_failsafe_cycle
from mower.husqvarna import MowerSnapshot
from mower.runtime import RuntimeSettings
from mower.state import AutomationState
from mower.state_store import InMemoryStateStore
from platzwart_console import live_status

NOW = datetime(2026, 9, 9, 8, tzinfo=timezone.utc)
ENV = {"CONTROL_MODE": "FULL_FAILSAFE", "ENABLE_LIVE_READS": "true",
       "HUSQVARNA_CLIENT_ID": "test", "HUSQVARNA_CLIENT_SECRET": "test",
       "HYDRAWISE_API_KEY": "test", "HYDRAWISE_CONTROLLER_ID": "123",
       "HYDRAWISE_EXPECTED_RELAY_IDS": "1,2,3,4,5,6,7", "HYDRAWISE_EXPECTED_ZONE_COUNT": "7",
       "HYDRAWISE_DASHBOARD_OBSERVATION_MODE": "AZURE_TABLE", "WEATHER_ENABLED": "false",
       "SPECIAL_OCCUPANCY_ENABLED": "false", "HYDRAWISE_DATA_GAP_CONFIRMATION_MINUTES": "2"}


@contextmanager
def pipeline(tmp_path, *, dashboard_store=None, vendor_fails=False):
    initial = AutomationState(
        revision=7, last_hydrawise_success_utc=NOW.isoformat(),
        hydrawise_clear_since_utc=NOW.isoformat(), hydrawise_clear_origin="IRRIGATION_END",
        hydrawise_drying_since_utc=(NOW - timedelta(minutes=150)).isoformat())
    state = InMemoryStateStore(initial)
    store = dashboard_store if dashboard_store is not None else InMemoryDashboardObservationStore()
    config, matches = tmp_path / "config.json", tmp_path / "rasen.ics"
    config.write_text(json.dumps({"timezone": "Europe/Berlin", "planning": {"day_start": "00:00", "day_end": "00:00"},
                                 "training": {"weekly": []}, "hydrawise": {"enabled": True, "include_all_zones": True}}))
    matches.write_text("BEGIN:VCALENDAR\nEND:VCALENDAR\n")
    inputs = RuntimeInputPaths(str(config), str(matches), "test")
    mower = MowerSnapshot(
        mower_id="test", name="Schaf", model="Automower 580 EPOS", battery_percent=70,
        activity="CHARGING", state="IN_OPERATION", mode="HOME", error_code=0,
        override_action="FORCE_PARK", restricted_reason="NOT_APPLICABLE", external_reason_id=None,
        next_start_timestamp_ms=None, work_areas=({"id": 1, "name": "Rasenfläche"},),
        connected=True, status_timestamp_ms=int(NOW.timestamp() * 1000))
    payload = {"time": int(NOW.timestamp()), "nextpoll": 60, "relays": [
        {"relay_id": n, "relay": n, "name": f"Zone {n}", "time": 0, "run": 600} for n in range(1, 8)]}
    def cycle(**kwargs):
        return run_read_only_cycle(**kwargs, state_store_factory=lambda _: state,
                                   cancellation_store_factory=lambda _: SimpleNamespace(list_active=lambda *_a: []),
                                   dashboard_store_factory=lambda _: store,
                                   dashboard_observation_clock=lambda: NOW)
    with ExitStack() as stack:
        for name, value in {"resolve_runtime_inputs": inputs, "fetch_mowers": [{}], "select_mower": {}, "parse_snapshot": mower}.items():
            stack.enter_context(patch("mower.dry_run." + name, return_value=value))
        vendor = stack.enter_context(patch("mower.dry_run.fetch_status", return_value=payload,
                                           side_effect=AssertionError("App made a vendor request") if vendor_fails else None))
        stack.enter_context(patch("platzwart_console.run_read_only_cycle", side_effect=cycle))
        stack.enter_context(patch("platzwart_console.AzureTableStateStore.from_environment", return_value=state))
        for name in ("_clubhouse_events", "_dashboard_statistics", "_dashboard_irrigation_statistics"):
            stack.enter_context(patch("platzwart_console." + name, return_value={}))
        yield SimpleNamespace(cycle=cycle, store=store, state=state, vendor=vendor, initial=initial)


def control_read(context, **extra):
    return context.cycle(now_utc=NOW, settings=RuntimeSettings.from_mapping(ENV), environment=ENV,
                         past_due=False, source="controller-test", publish_dashboard_snapshot=True, **extra)


def test_one_direct_control_read_serves_repeated_app_reads_without_state_write(tmp_path):
    with pipeline(tmp_path) as ctx:
        result = control_read(ctx)
        assert result.details["hydrawise"]["dashboard_observation"]["quality"] == "PUBLISHED"
        before = ctx.state.load()
        outputs = [live_status(ENV, NOW + timedelta(seconds=n)) for n in (30, 60, 120)]
        assert ctx.vendor.call_count == 1
        assert ctx.state.load() == before
        assert all(o["irrigation"]["safety"]["fresh"] for o in outputs)
        assert {o["irrigation"]["safety"]["observed_at_utc"] for o in outputs} == {NOW.isoformat()}
        assert all(not o["irrigation"]["releaseConfirmation"]["telemetry_confirmed"] for o in outputs)
        assert all(o["mower"]["telemetryFresh"] for o in outputs)
        assert {o["mower"]["statusAgeSeconds"] for o in outputs} == {30, 60, 120}
        # A read-only controller source cannot make console device controls available.
        assert all(not o["deviceControlsAvailable"] for o in outputs)


@pytest.mark.parametrize("populated", [False, True])
def test_empty_or_stale_app_snapshot_disables_controls_and_has_no_fallback(tmp_path, populated):
    with pipeline(tmp_path, vendor_fails=not populated) as ctx:
        if populated:
            control_read(ctx)
        before, calls = ctx.state.load(), ctx.vendor.call_count
        output = live_status(ENV, NOW + timedelta(seconds=181))
        assert not output["controlsAvailable"]
        assert not output["deviceControlsAvailable"]
        assert output["dataQuality"]["code"] == "IRRIGATION_STATUS_UNAVAILABLE"
        assert output["irrigation"]["safety"]["available"] is False
        assert ctx.vendor.call_count == calls and ctx.state.load() == before


def test_dashboard_storage_failure_cannot_abort_direct_control_read(tmp_path):
    class BrokenStore:
        def load(self, *_args):
            raise RuntimeError("storage unavailable")
    with pipeline(tmp_path, dashboard_store=BrokenStore()) as ctx:
        result = control_read(ctx)
        assert ctx.vendor.call_count == 1
        assert result.details["hydrawise"]["safety"]["available"] is True
        assert result.details["hydrawise"]["dashboard_observation"]["quality"] == "PUBLISH_FAILED"


def test_snapshot_reader_cannot_be_used_as_persistent_control_observation(tmp_path):
    with pipeline(tmp_path, vendor_fails=True) as ctx:
        with pytest.raises(ValueError, match="ausschließlich gelesen"):
            control_read(ctx, dashboard_snapshot_only=True)
        assert ctx.vendor.call_count == 0 and ctx.state.load() == ctx.initial


def test_disabled_snapshot_option_preserves_existing_app_read_path(tmp_path):
    with pipeline(tmp_path) as ctx:
        live_status({**ENV, "HYDRAWISE_DASHBOARD_OBSERVATION_MODE": "OFF"}, NOW)
        assert ctx.vendor.call_count == 1


@pytest.mark.parametrize("seconds", [30, 181])
def test_fast_status_preserves_all_safety_fields_without_optional_io(tmp_path, seconds):
    with pipeline(tmp_path) as ctx:
        control_read(ctx)
        at = NOW + timedelta(seconds=seconds)
        full = live_status(ENV, at)
        before = ctx.state.load()
        with ExitStack() as stack:
            for name in ("_dashboard_statistics", "_dashboard_irrigation_statistics", "_clubhouse_events"):
                stack.enter_context(patch("platzwart_console." + name,
                                          side_effect=AssertionError("Optional I/O blocks live status")))
            stack.enter_context(patch("platzwart_console.peek_dashboard_statistics", return_value=None))
            stack.enter_context(patch("platzwart_console._peek_display_cache",
                                      return_value={"available": False, "loading": True}))
            fast = live_status(ENV, at, include_details=False)
        for key in ("generatedAt", "controlsAvailable", "deviceControlsAvailable", "actionCapabilities",
                    "manualControl", "dataQuality", "overall", "mower", "irrigation", "occupancy",
                    "automation", "trainingControl", "irrigationSchedule", "protection", "operatorCommands"):
            assert fast[key] == full[key], key
        assert fast["coordination"] == full["coordination"]
        assert fast["detailsDeferred"] is True
        assert fast["statistics"]["loading"] is True
        assert "_chargingEvidence" not in fast["statistics"]
        assert ctx.state.load() == before and ctx.vendor.call_count == 1


def test_fast_display_cache_is_nonblocking_deep_copied_and_time_bounded():
    import threading
    from platzwart_console import _peek_display_cache
    lock = threading.Lock()
    cache = {"expires": NOW + timedelta(minutes=5), "available": True, "events": [{"title": "A"}]}
    with lock:
        assert _peek_display_cache(cache, lock, NOW)["loading"] is True
    result = _peek_display_cache(cache, lock, NOW)
    result["events"][0]["title"] = "changed"
    assert cache["events"][0]["title"] == "A"
    for at in (NOW - timedelta(seconds=1), NOW + timedelta(minutes=5)):
        assert _peek_display_cache(cache, lock, at)["loading"] is True
    cache["expires"] = NOW.replace(tzinfo=None)
    assert _peek_display_cache(cache, lock, NOW)["loading"] is True
    assert cache == {}


def test_no_clubhouse_integration_does_not_cause_endless_optional_reads(tmp_path):
    with pipeline(tmp_path) as ctx:
        control_read(ctx)
        with patch("platzwart_console.peek_dashboard_statistics", return_value={"available": True}), \
                patch("platzwart_console._peek_display_cache", return_value={"available": True}) as peek:
            fast = live_status(ENV, NOW, include_details=False)
        assert fast["detailsDeferred"] is False
        assert fast["clubhouse"] == {"available": False, "events": []}
        assert peek.call_count == 1  # irrigation only, no cache for an unconfigured integration


@pytest.mark.parametrize("history,water", [
    ({"estimatedAreaCycles7d": "bad"}, {}),
    ({}, {"zoneMinutes7d": 1}),
    ({}, {"zoneMinutes7d": [{"relayId": "bad", "minutes": 1}]}),
    ({}, {"attention": {"affectedRuns": 1}}),
    ({}, {"attention": {"affectedRuns": [{"confirmedRelayIds": [{}]}]}}),
])
def test_corrupt_optional_contents_never_destroy_a_live_safety_response(tmp_path, history, water):
    with pipeline(tmp_path) as ctx:
        result = control_read(ctx)
        result.details["mower"]["target_work_area"] = {"progress": 50}
        with patch("platzwart_console.run_read_only_cycle", return_value=result), \
                patch("platzwart_console.peek_dashboard_statistics", return_value=history), \
                patch("platzwart_console._peek_display_cache", return_value=water), \
                patch("platzwart_console._drop_display_cache") as drop:
            output = live_status(ENV, NOW, include_details=False)
        assert output["mower"]["activity"] == "CHARGING"
        assert output["irrigation"]["safety"]["fresh"] is True
        assert output["detailsDeferred"] is True
        assert output["coordination"]["chargingEndEstimate"] is None
        drop.assert_called_once()


def test_full_failsafe_canonical_reader_publishes_with_all_command_gates_locked(tmp_path):
    with pipeline(tmp_path) as ctx, patch(
        "mower.dry_run.dashboard_observation_store_from_environment", return_value=ctx.store
    ):
        # Replace the factory's default binding, preserving the canonical
        # callable's identity used by FULL_FAILSAFE. All transports stay fake.
        original_defaults = run_read_only_cycle.__kwdefaults__.copy()
        try:
            run_read_only_cycle.__kwdefaults__.update(
                dashboard_store_factory=lambda _: ctx.store,
                dashboard_observation_clock=lambda: NOW,
                state_store_factory=lambda _: ctx.state,
                cancellation_store_factory=lambda _: SimpleNamespace(list_active=lambda *_a: []))
            result = run_full_failsafe_cycle(
                now_utc=NOW, settings=RuntimeSettings.from_mapping(ENV), environment=ENV,
                past_due=False, source="timer-test", state_store_factory=lambda _: ctx.state,
                park_sender=lambda *_a: pytest.fail("park gate bypassed"),
                start_sender=lambda *_a, **_k: pytest.fail("start gate bypassed"),
                start_zone_sender=lambda *_a, **_k: pytest.fail("irrigation gate bypassed"))
        finally:
            run_read_only_cycle.__kwdefaults__.clear()
            run_read_only_cycle.__kwdefaults__.update(original_defaults)
        assert ctx.vendor.call_count == 1 and not result.command_sent
        assert result.details["hydrawise"]["dashboard_observation"]["quality"] == "PUBLISHED"
