from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone

from mower.full_mower import run_full_mower_cycle
from mower.runtime import CycleResult, RuntimeSettings
from mower.state import AutomationState
from mower.state_store import InMemoryStateStore


NOW = datetime(2026, 8, 11, 17, 0, tzinfo=timezone.utc)


def settings(
    *,
    park: bool = True,
    start: bool = True,
    confirmation: bool = True,
) -> RuntimeSettings:
    return RuntimeSettings.from_mapping(
        {
            "CONTROL_MODE": "FULL_MOWER",
            "ENABLE_LIVE_READS": "true",
            "ENABLE_PARK_COMMANDS": str(park).lower(),
            "ENABLE_START_COMMANDS": str(start).lower(),
            "FULL_MOWER_CONFIRMATION": (
                "SSV53-TRAINING-MATCH-PARK-START"
                if confirmation
                else ""
            ),
        }
    )


ENVIRONMENT = {
    "HUSQVARNA_CLIENT_ID": "client",
    "HUSQVARNA_CLIENT_SECRET": "secret",
    "HYDRAWISE_CLEAR_CONFIRMATION_MINUTES": "2",
    "MOWER_PARK_CONFIRMATION_MINUTES": "1",
    "MAX_AUTOMATIC_START_MINUTES": "360",
}


def live_result(
    *,
    command: str | None = None,
    block_source: str | None = None,
    hydrawise_clear: bool = True,
    hydrawise_available: bool = True,
    activity: str | None = None,
) -> CycleResult:
    parking_block = None
    if block_source is not None:
        parking_block = {
            "start": (NOW + timedelta(minutes=5)).isoformat(),
            "end": (NOW + timedelta(hours=2)).isoformat(),
            "title": "Sperrfenster",
            "source": block_source,
        }
    mower_activity = activity or ("MOWING" if command == "PARK" else "PARKED_IN_CS")
    return CycleResult(
        schema_version=2,
        executed_at_utc=NOW.isoformat(),
        source="test",
        control_mode="FULL_MOWER",
        past_due=False,
        decision_code=("WOULD_PARK" if command == "PARK" else "EXTERNAL_OVERRIDE"),
        command_sent=False,
        message="Testentscheidung",
        details={
            "mode": "read_only_live_dry_run",
            "decision": {
                "code": "WOULD_PARK" if command == "PARK" else "EXTERNAL_OVERRIDE",
                "hypothetical_command": command,
            },
            "current_plan": {
                "blocked_now": None,
                "mowing_window_now": {
                    "start": (NOW - timedelta(minutes=5)).isoformat(),
                    "end": (NOW + timedelta(hours=3)).isoformat(),
                },
                "next_block": parking_block,
                "parking_block": parking_block,
            },
            "hydrawise": {
                "status": "live (7 Zonen)" if hydrawise_available else "Abruf fehlgeschlagen",
                "error": None if hydrawise_available else "offline",
                "safety": {
                    "available": hydrawise_available,
                    "fresh": hydrawise_available,
                    "clear_now": hydrawise_clear,
                    "observed_at_utc": NOW.isoformat() if hydrawise_available else None,
                    "active_zone_count": 0 if hydrawise_clear else 1,
                    "reason": "frei" if hydrawise_clear else "Beregnung läuft",
                },
            },
            "mower": {
                "mower_id": "mower-1",
                "activity": mower_activity,
                "state": "IN_OPERATION",
                "error_code": 0,
                "battery_percent": 100,
                "target_work_area": {
                    "id": 849199,
                    "name": "Rasenfläche",
                    "enabled": True,
                },
            },
            "safety": {
                "read_only": True,
                "command_functions_present": False,
                "command_sent": False,
            },
        },
    )


def parked_state(source: str) -> AutomationState:
    return AutomationState(
        parked_by_automation=True,
        automation_park_source=source,
        automation_restart_allowed=source in {"training", "match"},
        park_command_sent_utc=(NOW - timedelta(minutes=10)).isoformat(),
        park_confirmed_utc=(NOW - timedelta(minutes=5)).isoformat(),
        hydrawise_clear_since_utc=(NOW - timedelta(minutes=5)).isoformat(),
        last_hydrawise_observed_utc=(NOW - timedelta(minutes=1)).isoformat(),
        last_hydrawise_active_count=0,
    )


class FullMowerParkingTests(unittest.TestCase):
    def test_training_and_match_parks_are_restart_eligible(self) -> None:
        for source in ("training", "match", "match+training"):
            with self.subTest(source=source):
                store = InMemoryStateStore()
                calls = []
                result = run_full_mower_cycle(
                    now_utc=NOW,
                    settings=settings(),
                    environment=ENVIRONMENT,
                    past_due=False,
                    source="test",
                    read_only_runner=lambda **_: live_result(
                        command="PARK",
                        block_source=source,
                    ),
                    state_store_factory=lambda _env, store=store: store,
                    park_sender=lambda *_: calls.append("park") or {"accepted": True},
                    start_sender=lambda *_: {},
                )
                self.assertEqual(result.decision_code, "PARK_COMMAND_SENT")
                self.assertEqual(calls, ["park"])
                self.assertTrue(store.load().automation_restart_allowed)

    def test_irrigation_or_mixed_block_parks_but_never_allows_restart(self) -> None:
        for source in ("irrigation", "irrigation+training"):
            with self.subTest(source=source):
                store = InMemoryStateStore()
                result = run_full_mower_cycle(
                    now_utc=NOW,
                    settings=settings(),
                    environment=ENVIRONMENT,
                    past_due=False,
                    source="test",
                    read_only_runner=lambda **_: live_result(
                        command="PARK",
                        block_source=source,
                    ),
                    state_store_factory=lambda _env, store=store: store,
                    park_sender=lambda *_: {"accepted": True},
                    start_sender=lambda *_: {},
                )
                self.assertTrue(result.command_sent)
                self.assertFalse(store.load().automation_restart_allowed)

    def test_unconfirmed_hydrawise_forces_safety_park_without_restart(self) -> None:
        store = InMemoryStateStore()
        result = run_full_mower_cycle(
            now_utc=NOW,
            settings=settings(),
            environment=ENVIRONMENT,
            past_due=False,
            source="test",
            read_only_runner=lambda **_: live_result(
                command="PARK",
                hydrawise_clear=False,
                hydrawise_available=False,
                activity="MOWING",
            ),
            state_store_factory=lambda _env: store,
            park_sender=lambda *_: {"accepted": True},
            start_sender=lambda *_: {},
        )
        self.assertEqual(result.decision_code, "PARK_COMMAND_SENT")
        self.assertEqual(store.load().automation_park_source, "unknown")
        self.assertFalse(store.load().automation_restart_allowed)

    def test_park_gate_defaults_can_keep_real_command_locked(self) -> None:
        calls = []
        result = run_full_mower_cycle(
            now_utc=NOW,
            settings=settings(park=False),
            environment=ENVIRONMENT,
            past_due=False,
            source="test",
            read_only_runner=lambda **_: live_result(
                command="PARK",
                block_source="training",
            ),
            state_store_factory=lambda _env: InMemoryStateStore(),
            park_sender=lambda *_: calls.append("park") or {},
            start_sender=lambda *_: {},
        )
        self.assertEqual(result.decision_code, "FULL_MOWER_PARK_LOCKED")
        self.assertEqual(calls, [])

    def test_failed_park_response_never_leaves_restart_ownership(self) -> None:
        store = InMemoryStateStore()
        with self.assertRaisesRegex(RuntimeError, "network"):
            run_full_mower_cycle(
                now_utc=NOW,
                settings=settings(),
                environment=ENVIRONMENT,
                past_due=False,
                source="test",
                read_only_runner=lambda **_: live_result(
                    command="PARK",
                    block_source="training",
                ),
                state_store_factory=lambda _env: store,
                park_sender=lambda *_: (_ for _ in ()).throw(RuntimeError("network")),
                start_sender=lambda *_: {},
            )
        self.assertFalse(store.load().parked_by_automation)

    def test_park_sent_without_state_write_never_grants_restart(self) -> None:
        calls = []

        class BrokenStore(InMemoryStateStore):
            def save(self, state, *, expected_revision):
                raise RuntimeError("storage unavailable")

        store = BrokenStore()
        result = run_full_mower_cycle(
            now_utc=NOW,
            settings=settings(),
            environment=ENVIRONMENT,
            past_due=False,
            source="test",
            read_only_runner=lambda **_: live_result(
                command="PARK",
                block_source="training",
            ),
            state_store_factory=lambda _env: store,
            park_sender=lambda *_: calls.append("park") or {"accepted": True},
            start_sender=lambda *_: {},
        )
        self.assertEqual(result.decision_code, "PARK_SENT_STATE_NOT_OWNED")
        self.assertEqual(calls, ["park"])
        self.assertFalse(store.load().parked_by_automation)


class FullMowerStartTests(unittest.TestCase):
    def _run_start(
        self,
        *,
        state: AutomationState,
        result: CycleResult | None = None,
        runtime_settings: RuntimeSettings | None = None,
    ) -> tuple[CycleResult, list[tuple], InMemoryStateStore]:
        store = InMemoryStateStore(state)
        calls = []
        output = run_full_mower_cycle(
            now_utc=NOW,
            settings=runtime_settings or settings(),
            environment=ENVIRONMENT,
            past_due=False,
            source="test",
            read_only_runner=lambda **_: result or live_result(),
            state_store_factory=lambda _env: store,
            park_sender=lambda *_: {},
            start_sender=lambda *args: calls.append(args) or {"accepted": True},
        )
        return output, calls, store

    def test_start_is_allowed_only_after_training_or_match(self) -> None:
        for source in ("training", "match"):
            with self.subTest(source=source):
                result, calls, store = self._run_start(
                    state=parked_state(source),
                )
                self.assertEqual(
                    result.decision_code,
                    "START_COMMAND_SENT_AFTER_TRAINING_OR_MATCH",
                )
                self.assertEqual(len(calls), 1)
                self.assertFalse(store.load().parked_by_automation)

    def test_irrigation_park_never_automatically_starts(self) -> None:
        state = parked_state("irrigation")
        state = AutomationState.from_mapping(
            {**state.to_dict(), "automation_restart_allowed": False}
        )
        result, calls, _store = self._run_start(state=state)
        self.assertEqual(result.decision_code, "AUTOSTART_SOURCE_FORBIDDEN")
        self.assertEqual(calls, [])

    def test_running_irrigation_blocks_start(self) -> None:
        result, calls, _store = self._run_start(
            state=parked_state("training"),
            result=live_result(hydrawise_clear=False),
        )
        self.assertEqual(
            result.decision_code,
            "HYDRAWISE_RELEASE_NOT_CONFIRMED",
        )
        self.assertEqual(calls, [])

    def test_hydrawise_must_be_clear_for_multiple_minutes(self) -> None:
        state = parked_state("match")
        state = AutomationState.from_mapping(
            {
                **state.to_dict(),
                "hydrawise_clear_since_utc": (
                    NOW - timedelta(minutes=1)
                ).isoformat(),
            }
        )
        result, calls, _store = self._run_start(state=state)
        self.assertEqual(
            result.decision_code,
            "HYDRAWISE_RELEASE_NOT_CONFIRMED",
        )
        self.assertEqual(calls, [])

    def test_all_independent_start_gates_are_required(self) -> None:
        for runtime_settings in (
            settings(start=False),
            settings(confirmation=False),
        ):
            with self.subTest(runtime_settings=runtime_settings):
                result, calls, _store = self._run_start(
                    state=parked_state("training"),
                    runtime_settings=runtime_settings,
                )
                self.assertEqual(
                    result.decision_code,
                    "FULL_MOWER_START_LOCKED",
                )
                self.assertEqual(calls, [])

    def test_upcoming_irrigation_block_keeps_mower_parked(self) -> None:
        result, calls, store = self._run_start(
            state=parked_state("training"),
            result=live_result(block_source="irrigation"),
        )
        self.assertEqual(result.decision_code, "AUTOMATION_PARK_WAIT")
        self.assertEqual(calls, [])
        self.assertIsNone(store.load().hydrawise_clear_since_utc)


if __name__ == "__main__":
    unittest.main()
