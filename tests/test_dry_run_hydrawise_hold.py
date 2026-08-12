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
from mower.runtime import RuntimeSettings
from mower.state_store import InMemoryStateStore


NOW = datetime(2026, 8, 12, 8, 0, tzinfo=timezone.utc)


class DryRunHydrawiseHoldTests(unittest.TestCase):
    def test_clear_chain_is_persisted_and_releases_only_after_ten_minutes(self) -> None:
        config = {
            "timezone": "Europe/Berlin",
            "planning": {
                "day_start": "00:00",
                "day_end": "00:00",
                "minimum_mowing_window_minutes": 30,
            },
            "training": {
                "before_minutes": 30,
                "after_minutes": 30,
                "weekly": [],
            },
            "hydrawise": {
                "enabled": True,
                "include_all_zones": True,
                "before_minutes": 30,
                "after_minutes": 10,
            },
        }
        mower = MowerSnapshot(
            mower_id="mower-1",
            name="Schaf",
            model="test",
            battery_percent=100,
            activity="PARKED_IN_CS",
            state="IN_OPERATION",
            mode="MAIN_AREA",
            error_code=0,
            override_action="FORCE_PARK",
            restricted_reason="NOT_APPLICABLE",
            external_reason_id=AUTOMATION_EXTERNAL_REASON,
            next_start_timestamp_ms=None,
            work_areas=(
                {"id": 849199, "name": "Rasenfläche", "enabled": True},
            ),
        )
        settings = RuntimeSettings.from_mapping(
            {
                "CONTROL_MODE": "DRY_RUN",
                "ENABLE_LIVE_READS": "true",
                "PARK_LOOKAHEAD_MINUTES": "10",
            }
        )
        environment = {
            "HUSQVARNA_CLIENT_ID": "client",
            "HUSQVARNA_CLIENT_SECRET": "secret",
            "HYDRAWISE_API_KEY": "hydrawise",
            "HYDRAWISE_CLEAR_CONFIRMATION_MINUTES": "10",
        }
        store = InMemoryStateStore()

        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            config_path = directory / "config.json"
            matches_path = directory / "rasen.ics"
            config_path.write_text(json.dumps(config), encoding="utf-8")
            matches_path.write_text(
                "BEGIN:VCALENDAR\nEND:VCALENDAR\n",
                encoding="utf-8",
            )
            runtime_inputs = RuntimeInputPaths(
                config_path=str(config_path),
                matches_path=str(matches_path),
                source_kind="test",
            )

            def hydrawise_status(at: datetime) -> dict:
                return {
                    "time": int(at.timestamp()),
                    "relays": [
                        {
                            "relay_id": relay_id,
                            "name": f"Zone {relay_id}",
                            "time": 3600,
                            "run": 600,
                        }
                        for relay_id in range(1, 8)
                    ],
                }

            with (
                patch(
                    "mower.dry_run.resolve_runtime_inputs",
                    return_value=runtime_inputs,
                ),
                patch("mower.dry_run.fetch_mowers", return_value=[{}]),
                patch("mower.dry_run.select_mower", return_value={}),
                patch("mower.dry_run.parse_snapshot", return_value=mower),
                patch("mower.dry_run.fetch_status") as fetch_status,
            ):
                fetch_status.return_value = hydrawise_status(NOW)
                first = run_read_only_cycle(
                    now_utc=NOW,
                    settings=settings,
                    environment=environment,
                    past_due=False,
                    source="test",
                    state_store_factory=lambda _environment: store,
                )
                self.assertFalse(
                    first.details["hydrawise"]["release_confirmation"]["allowed"]
                )
                self.assertEqual(
                    first.decision_code,
                    "HYDRAWISE_UNCONFIRMED_HOLD",
                )
                self.assertTrue(first.details["automation_state"]["persisted"])

                for minute in range(1, 11):
                    cycle_time = NOW + timedelta(minutes=minute)
                    fetch_status.return_value = hydrawise_status(cycle_time)
                    second = run_read_only_cycle(
                        now_utc=cycle_time,
                        settings=settings,
                        environment=environment,
                        past_due=False,
                        source="test",
                        state_store_factory=lambda _environment: store,
                    )
                    if minute < 10:
                        self.assertFalse(
                            second.details["hydrawise"]
                            ["release_confirmation"]
                            ["allowed"]
                        )

        self.assertTrue(
            second.details["hydrawise"]["release_confirmation"]["allowed"]
        )
        self.assertNotEqual(
            second.decision_code,
            "HYDRAWISE_UNCONFIRMED_HOLD",
        )
        self.assertEqual(
            store.load().hydrawise_clear_since_utc,
            NOW.isoformat(),
        )


if __name__ == "__main__":
    unittest.main()
