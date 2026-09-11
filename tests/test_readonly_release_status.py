from __future__ import annotations

import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from mower.config_source import RuntimeInputPaths
from mower.decision import AUTOMATION_EXTERNAL_REASON
from mower.dry_run import run_read_only_cycle
from mower.husqvarna import MowerSnapshot
from mower.state import AutomationState
from mower.state_store import InMemoryStateStore
from platzwart_console import live_status


NOW = datetime(2026, 9, 9, 8, 0, tzinfo=timezone.utc)
ENVIRONMENT = {
    "CONTROL_MODE": "FULL_FAILSAFE", "ENABLE_LIVE_READS": "true",
    "HUSQVARNA_CLIENT_ID": "test", "HUSQVARNA_CLIENT_SECRET": "test",
    "HYDRAWISE_API_KEY": "test", "HYDRAWISE_EXPECTED_ZONE_COUNT": "7",
    "HYDRAWISE_EXPECTED_RELAY_IDS": "1,2,3,4,5,6,7",
    "POST_IRRIGATION_DRYING_MINUTES": "150",
    "HYDRAWISE_DATA_GAP_CONFIRMATION_MINUTES": "2",
    "SPECIAL_OCCUPANCY_ENABLED": "false",
}


class ReadOnlyReleaseStatusTests(unittest.TestCase):
    def displayed_status(
        self,
        state,
        *,
        store_unavailable=False,
        payload_override=None,
        environment=None,
        mower_status_timestamp_ms=None,
    ):
        class ReadOnlyStore(InMemoryStateStore):
            def load(self):
                if store_unavailable:
                    raise RuntimeError("state unavailable")
                return super().load()

            def save(self, *_args, **_kwargs):
                raise AssertionError("The app status must never write automation state")

        store = ReadOnlyStore(state)
        mower = MowerSnapshot(
            mower_id="test", name="Schaf", model="Automower 580 EPOS",
            battery_percent=95, activity="PARKED_IN_CS", state="IN_OPERATION",
            mode="HOME", error_code=0, override_action="FORCE_PARK",
            restricted_reason="NOT_APPLICABLE", external_reason_id=AUTOMATION_EXTERNAL_REASON,
            next_start_timestamp_ms=None, work_areas=({"id": 849199, "name": "Rasenfläche"},),
            connected=True,
            status_timestamp_ms=(
                int(NOW.timestamp() * 1000)
                if mower_status_timestamp_ms is None
                else mower_status_timestamp_ms
            ),
        )
        config = {
            "timezone": "Europe/Berlin",
            "planning": {"day_start": "00:00", "day_end": "00:00", "minimum_mowing_window_minutes": 30},
            "training": {"before_minutes": 30, "after_minutes": 30, "weekly": []},
            "hydrawise": {"enabled": True, "include_all_zones": True, "before_minutes": 30, "after_minutes": 10},
        }
        status = {"time": int(NOW.timestamp()), "relays": [
            {"relay_id": value, "relay": value, "name": f"Zone {value}", "time": 0, "run": 0}
            for value in range(1, 8)
        ]}
        if payload_override is not None:
            status = payload_override
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            config_path, matches_path = directory / "config.json", directory / "rasen.ics"
            config_path.write_text(json.dumps(config), encoding="utf-8")
            matches_path.write_text("BEGIN:VCALENDAR\nEND:VCALENDAR\n", encoding="utf-8")
            inputs = RuntimeInputPaths(config_path=str(config_path), matches_path=str(matches_path), source_kind="test")

            # This adapter only injects storage into the REAL read-only pipeline.
            # No artificial CycleResult or release fields are supplied to the UI.
            def read_cycle(**kwargs):
                return run_read_only_cycle(**kwargs, state_store_factory=lambda _: store)

            with (
                patch("mower.dry_run.resolve_runtime_inputs", return_value=inputs),
                patch("mower.dry_run.fetch_mowers", return_value=[{}]),
                patch("mower.dry_run.select_mower", return_value={}),
                patch("mower.dry_run.parse_snapshot", return_value=mower),
                patch("mower.dry_run.fetch_status", return_value=status),
                patch("platzwart_console.run_read_only_cycle", side_effect=read_cycle),
                patch("platzwart_console.AzureTableStateStore.from_environment", return_value=store),
                patch("platzwart_console._clubhouse_events", return_value={"events": []}),
                patch("platzwart_console._dashboard_statistics", return_value={}),
                patch("platzwart_console._dashboard_irrigation_statistics", return_value={}),
            ):
                output = live_status(environment or ENVIRONMENT, NOW)
        if not store_unavailable:
            self.assertEqual(store.load(), state)
        return output

    def test_real_full_failsafe_live_status_explains_complete_hold(self):
        end = NOW - timedelta(minutes=100)
        # Legacy COMPLETE_HOLD is intentionally tested without the new field.
        state = AutomationState(
            irrigation_phase="COMPLETE_HOLD", hydrawise_clear_origin="IRRIGATION_END",
            hydrawise_clear_since_utc=end.isoformat(),
            last_hydrawise_success_utc=(NOW - timedelta(minutes=1)).isoformat(),
            last_cycle_started_utc=(NOW - timedelta(minutes=1)).isoformat(),
        )
        output = self.displayed_status(state)
        release = output["irrigation"]["releaseConfirmation"]
        self.assertFalse(release["allowed"])
        self.assertTrue(release["telemetry_confirmed"])
        self.assertEqual(release["dry_until_utc"], (end + timedelta(minutes=150)).isoformat())
        self.assertEqual(output["coordination"]["dryUntil"], release["dry_until_utc"])
        self.assertIn("DRYING_OR_CONFIRMATION", {item["code"] for item in output["coordination"]["blockers"]})

    def test_short_gap_display_preserves_drying_and_requires_new_confirmation(self):
        end = NOW - timedelta(minutes=149)
        state = AutomationState(
            irrigation_phase="COMPLETE_HOLD", hydrawise_clear_origin="IRRIGATION_END",
            hydrawise_drying_since_utc=end.isoformat(), hydrawise_clear_since_utc=None,
            last_hydrawise_success_utc=(NOW - timedelta(minutes=2)).isoformat(),
        )
        output = self.displayed_status(state)
        release = output["irrigation"]["releaseConfirmation"]
        self.assertFalse(release["allowed"])
        self.assertFalse(release["telemetry_confirmed"])
        self.assertEqual(release["dry_until_utc"], (NOW + timedelta(minutes=1)).isoformat())
        self.assertEqual(release["release_at_utc"], (NOW + timedelta(minutes=2)).isoformat())

    def test_long_gap_display_does_not_release_from_old_complete_hold(self):
        state = AutomationState(
            irrigation_phase="COMPLETE_HOLD", hydrawise_clear_origin="IRRIGATION_END",
            hydrawise_drying_since_utc=(NOW - timedelta(minutes=180)).isoformat(),
            hydrawise_clear_since_utc=(NOW - timedelta(minutes=180)).isoformat(),
            last_hydrawise_success_utc=(NOW - timedelta(seconds=240)).isoformat(),
        )
        output = self.displayed_status(state)
        self.assertEqual(output["coordination"]["dryingReason"], "POSSIBLE_IRRIGATION_DURING_GAP")
        release = output["irrigation"]["releaseConfirmation"]
        self.assertFalse(release["allowed"])
        self.assertEqual(release["dry_until_utc"], (NOW + timedelta(minutes=150)).isoformat())

    def test_newer_persisted_snapshot_is_not_replayed_into_a_possible_gap(self):
        drying_since = NOW - timedelta(hours=3)
        state = AutomationState(
            hydrawise_clear_since_utc=drying_since.isoformat(),
            hydrawise_drying_since_utc=drying_since.isoformat(),
            hydrawise_clear_origin="IRRIGATION_END",
            last_hydrawise_success_utc=(NOW + timedelta(seconds=1)).isoformat(),
            last_cycle_started_utc=(NOW + timedelta(seconds=1)).isoformat(),
        )

        output = self.displayed_status(state)
        release = output["irrigation"]["releaseConfirmation"]
        self.assertFalse(release["allowed"])
        self.assertEqual(
            release["dry_until_utc"],
            (drying_since + timedelta(minutes=150)).isoformat(),
        )
        self.assertEqual(release["drying_since_utc"], drying_since.isoformat())
        self.assertEqual(output["automation"]["hydrawiseClearOrigin"], "IRRIGATION_END")
        self.assertNotEqual(output["coordination"]["dryingReason"], "POSSIBLE_IRRIGATION_DURING_GAP")

    def test_current_clear_after_observed_watering_starts_a_normal_drying_hold(self):
        state = AutomationState(
            hydrawise_clear_origin="IRRIGATION_ACTIVE",
            last_hydrawise_success_utc=(NOW - timedelta(minutes=1)).isoformat(),
            last_cycle_started_utc=(NOW - timedelta(minutes=1)).isoformat(),
            last_hydrawise_active_count=1,
        )

        output = self.displayed_status(state)
        release = output["irrigation"]["releaseConfirmation"]
        self.assertFalse(release["allowed"])
        self.assertEqual(release["drying_since_utc"], NOW.isoformat())
        self.assertEqual(release["dry_until_utc"], (NOW + timedelta(minutes=150)).isoformat())

    def test_unavailable_state_produces_no_release_and_disables_controls(self):
        output = self.displayed_status(AutomationState(), store_unavailable=True)
        self.assertFalse(output["irrigation"]["releaseConfirmation"]["allowed"])
        self.assertFalse(output["irrigation"]["releaseConfirmation"]["persistent_state_available"])
        self.assertFalse(output["controlsAvailable"])

    def test_real_readonly_pipeline_keeps_stop_gate_open_when_mower_telemetry_is_stale(self):
        fully_armed = {
            **ENVIRONMENT,
            "ENABLE_PARK_COMMANDS": "true",
            "ENABLE_START_COMMANDS": "true",
            "ENABLE_IRRIGATION_COMMANDS": "true",
            "FULL_MOWER_CONFIRMATION": "SSV53-TRAINING-MATCH-PARK-START",
            "FULL_FAILSAFE_CONFIRMATION": "SSV53-MOWER-HYDRAWISE-7-ZONES-150-MINUTES-ADAPTIVE-V1",
        }
        output = self.displayed_status(
            AutomationState(),
            environment=fully_armed,
            mower_status_timestamp_ms=int(
                (NOW - timedelta(seconds=181)).timestamp() * 1000
            ),
        )

        self.assertTrue(output["deviceControlsAvailable"])
        self.assertFalse(output["mower"]["telemetryFresh"])
        self.assertEqual(output["mower"]["statusAgeSeconds"], 181)
        self.assertIn(
            "MOWER_TELEMETRY",
            {item["code"] for item in output["coordination"]["blockers"]},
        )

    def test_malformed_hydrawise_retains_diagnostic_response_without_release(self):
        for relays in (None, {}, [None], [{"relay_id": "bad"}],
                       [{"relay_id": 1, "time": float("inf"), "run": 900}]):
            with self.subTest(relays=relays):
                output = self.displayed_status(
                    AutomationState(), payload_override={"time": int(NOW.timestamp()), "relays": relays},
                )
                self.assertFalse(output["irrigation"]["safety"]["clear_now"])
                self.assertFalse(output["irrigation"]["releaseConfirmation"]["allowed"])
                self.assertEqual(output["irrigation"]["zones"], [])


if __name__ == "__main__":
    unittest.main()
