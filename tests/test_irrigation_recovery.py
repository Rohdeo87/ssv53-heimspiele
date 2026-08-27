from __future__ import annotations

import unittest
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path

from mower.husqvarna import MowerSnapshot
from mower.irrigation_recovery import (
    IrrigationRecoveryError,
    RESET_CONFIRMATION,
    reset_failed_irrigation,
)
from mower.state import AutomationState
from mower.state_store import InMemoryStateStore


NOW = datetime(2026, 8, 13, 13, 0, tzinfo=timezone.utc)
RELAYS = [9104894, 9104906, 9104909, 9104911, 9104913, 9104920, 9104921]
ENV = {
    "CONTROL_MODE": "FULL_FAILSAFE",
    "ENABLE_LIVE_READS": "true",
    "ENABLE_PARK_COMMANDS": "true",
    "ENABLE_START_COMMANDS": "true",
    "ENABLE_IRRIGATION_COMMANDS": "true",
    "FULL_MOWER_CONFIRMATION": "SSV53-TRAINING-MATCH-PARK-START",
    "FULL_FAILSAFE_CONFIRMATION": "SSV53-MOWER-HYDRAWISE-7-ZONES-120-MINUTES",
    "HUSQVARNA_CLIENT_ID": "client",
    "HUSQVARNA_CLIENT_SECRET": "secret",
    "HYDRAWISE_API_KEY": "key",
    "HYDRAWISE_CONTROLLER_ID": "controller",
    "HYDRAWISE_EXPECTED_ZONE_COUNT": "7",
    "HYDRAWISE_EXPECTED_RELAY_IDS": ",".join(str(value) for value in RELAYS),
    "HYDRAWISE_CLEAR_CONFIRMATION_MINUTES": "120",
    "MOWER_PARK_CONFIRMATION_MINUTES": "1",
}


MOWER = MowerSnapshot(
    mower_id="mower-1",
    name="Schaf",
    model="580 EPOS",
    battery_percent=88,
    activity="PARKED_IN_CS",
    state="IN_OPERATION",
    mode="MAIN_AREA",
    error_code=0,
    override_action="FORCE_PARK",
    restricted_reason="NOT_APPLICABLE",
    external_reason_id=253053,
    next_start_timestamp_ms=None,
    work_areas=(),
)


def failed_state() -> AutomationState:
    return AutomationState(
        revision=7,
        parked_by_automation=True,
        automation_park_source="irrigation",
        automation_restart_allowed=True,
        park_command_sent_utc=(NOW - timedelta(minutes=10)).isoformat(),
        park_confirmed_utc=(NOW - timedelta(minutes=5)).isoformat(),
        irrigation_phase="FAILED",
        irrigation_plan_id="plan",
        irrigation_plan_json="[]",
        irrigation_suspended_relay_ids_json="[]",
        irrigation_failed_reason="Testfehler",
        hydrawise_clear_since_utc=(NOW - timedelta(hours=2)).isoformat(),
    )


def hydrawise_status(*, relay_ids: list[int] | None = None, active: int | None = None) -> dict:
    selected = RELAYS if relay_ids is None else relay_ids
    return {
        "time": int(NOW.timestamp()),
        "relays": [
            {
                "relay_id": relay_id,
                "relay": index,
                "name": f"Zone {index}",
                "time": 1 if relay_id == active else 3 * 60 * 60,
                "run": 600,
            }
            for index, relay_id in enumerate(selected, start=1)
        ],
    }


class IrrigationRecoveryTests(unittest.TestCase):
    def test_http_route_is_function_key_protected_and_recovery_has_no_actions(self) -> None:
        root = Path(__file__).resolve().parents[1]
        function_source = (root / "function_app.py").read_text(encoding="utf-8")
        recovery_source = (root / "mower" / "irrigation_recovery.py").read_text(
            encoding="utf-8"
        )
        self.assertIn('route="irrigation/recover-failed"', function_source)
        self.assertIn("auth_level=func.AuthLevel.FUNCTION", function_source)
        self.assertIn('methods=["POST"]', function_source)
        self.assertNotIn("husqvarna_actions", recovery_source)
        self.assertNotIn("hydrawise_actions", recovery_source)
        self.assertNotIn("start_zone_for", recovery_source)
        self.assertNotIn("park_until_further_notice", recovery_source)

    def _run(
        self,
        state: AutomationState,
        *,
        mower: MowerSnapshot = MOWER,
        status: dict | None = None,
        confirmation: str = RESET_CONFIRMATION,
        expected_revision: int = 7,
    ):
        store = InMemoryStateStore(state)
        result = reset_failed_irrigation(
            now_utc=NOW,
            environment=ENV,
            expected_revision=expected_revision,
            confirmation=confirmation,
            state_store_factory=lambda _env: store,
            mower_fetcher=lambda *_: [{}],
            mower_selector=lambda items: items[0],
            mower_parser=lambda _item: mower,
            hydrawise_fetcher=lambda *_: status or hydrawise_status(),
        )
        return result, store

    def test_success_requires_dock_and_restarts_configured_clear_chain(self) -> None:
        result, store = self._run(failed_state())
        saved = store.load()
        self.assertEqual(result.code, "IRRIGATION_FAILED_RESET")
        self.assertEqual(result.previous_revision, 7)
        self.assertEqual(result.revision, 8)
        self.assertEqual(result.previous_failure_reason, "Testfehler")
        self.assertIsNone(saved.irrigation_phase)
        self.assertIsNone(saved.irrigation_failed_reason)
        self.assertEqual(saved.hydrawise_clear_since_utc, NOW.isoformat())
        self.assertTrue(saved.parked_by_automation)
        self.assertEqual(saved.park_confirmed_utc, (NOW - timedelta(minutes=5)).isoformat())
        self.assertEqual(result.hydrawise["observed_relay_ids"], RELAYS)

    def test_success_preserves_fresh_continuous_post_irrigation_proof(self) -> None:
        clear_since = NOW - timedelta(hours=2)
        state = replace(
            failed_state(),
            last_hydrawise_success_utc=(NOW - timedelta(minutes=1)).isoformat(),
            hydrawise_clear_since_utc=clear_since.isoformat(),
            hydrawise_clear_origin="IRRIGATION_END",
        )

        _result, store = self._run(state)

        saved = store.load()
        self.assertEqual(saved.hydrawise_clear_since_utc, clear_since.isoformat())
        self.assertEqual(saved.hydrawise_clear_origin, "IRRIGATION_END")

    def test_wrong_confirmation_stops_before_any_live_read(self) -> None:
        calls = {"mower": 0, "hydrawise": 0}
        store = InMemoryStateStore(failed_state())
        with self.assertRaisesRegex(IrrigationRecoveryError, "Bestätigung"):
            reset_failed_irrigation(
                now_utc=NOW,
                environment=ENV,
                expected_revision=7,
                confirmation="falsch",
                state_store_factory=lambda _env: store,
                mower_fetcher=lambda *_: calls.__setitem__("mower", calls["mower"] + 1),
                hydrawise_fetcher=lambda *_: calls.__setitem__(
                    "hydrawise", calls["hydrawise"] + 1
                ),
            )
        self.assertEqual(calls, {"mower": 0, "hydrawise": 0})
        self.assertEqual(store.load().revision, 7)

    def test_revision_conflict_is_rejected(self) -> None:
        with self.assertRaisesRegex(IrrigationRecoveryError, "Zustandsrevision"):
            self._run(failed_state(), expected_revision=6)

    def test_mower_must_be_confirmed_in_dock_without_error(self) -> None:
        for mower in (
            replace(MOWER, activity="MOWING"),
            replace(MOWER, error_code=42),
            replace(MOWER, state="ERROR"),
        ):
            with self.subTest(activity=mower.activity, error=mower.error_code):
                with self.assertRaisesRegex(IrrigationRecoveryError, "Dock"):
                    self._run(failed_state(), mower=mower)

    def test_active_imminent_or_substituted_zone_blocks_reset(self) -> None:
        active = hydrawise_status(active=RELAYS[0])
        imminent = hydrawise_status()
        imminent["relays"][0]["time"] = 30 * 60
        substituted = hydrawise_status(relay_ids=[*RELAYS[:-1], 999999])
        for payload in (active, imminent, substituted):
            with self.subTest(payload=payload):
                with self.assertRaisesRegex(IrrigationRecoveryError, "Hydrawise"):
                    self._run(failed_state(), status=payload)

    def test_reset_requires_stable_owned_irrigation_park(self) -> None:
        cases = (
            replace(failed_state(), parked_by_automation=False),
            replace(failed_state(), automation_park_source="training"),
            replace(
                failed_state(),
                park_confirmed_utc=(NOW - timedelta(seconds=30)).isoformat(),
            ),
        )
        for state in cases:
            with self.subTest(state=state):
                with self.assertRaises(IrrigationRecoveryError):
                    self._run(state)


if __name__ == "__main__":
    unittest.main()
