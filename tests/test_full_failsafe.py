from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone

from mower.full_failsafe import run_full_failsafe_cycle
from mower.runtime import CycleResult, RuntimeSettings
from mower.state import AutomationState
from mower.state_store import InMemoryStateStore


NOW = datetime(2026, 8, 13, 2, 0, tzinfo=timezone.utc)
RELAYS = [9104894, 9104906, 9104909, 9104911, 9104913, 9104920, 9104921]
RUN_SECONDS = [1200, 1200, 1200, 1200, 1200, 1800, 1800]


def settings(*, live: bool = True) -> RuntimeSettings:
    return RuntimeSettings.from_mapping(
        {
            "CONTROL_MODE": "FULL_FAILSAFE",
            "ENABLE_LIVE_READS": "true",
            "ENABLE_PARK_COMMANDS": str(live).lower(),
            "ENABLE_START_COMMANDS": str(live).lower(),
            "ENABLE_IRRIGATION_COMMANDS": str(live).lower(),
            "FULL_MOWER_CONFIRMATION": "SSV53-TRAINING-MATCH-PARK-START" if live else "LOCKED",
            "FULL_FAILSAFE_CONFIRMATION": "SSV53-MOWER-HYDRAWISE-7-ZONES-90-MINUTES" if live else "LOCKED",
            "PARK_LOOKAHEAD_MINUTES": "10",
        }
    )


ENV = {
    "HUSQVARNA_CLIENT_ID": "client",
    "HUSQVARNA_CLIENT_SECRET": "secret",
    "HYDRAWISE_API_KEY": "key",
    "HYDRAWISE_CONTROLLER_ID": "controller",
    "HYDRAWISE_EXPECTED_ZONE_COUNT": "7",
    "HYDRAWISE_CLEAR_CONFIRMATION_MINUTES": "90",
    "MOWER_PARK_CONFIRMATION_MINUTES": "1",
    "MAX_AUTOMATIC_START_MINUTES": "720",
    "MOWER_CONTINUE_MIN_BATTERY_PERCENT": "60",
    "MOWER_RESTART_BATTERY_PERCENT": "90",
}


def zones() -> list[dict]:
    start = NOW + timedelta(minutes=30)
    result = []
    for index, (relay_id, run_seconds) in enumerate(zip(RELAYS, RUN_SECONDS, strict=True), start=1):
        end = start + timedelta(seconds=run_seconds)
        result.append(
            {
                "relay_id": relay_id,
                "zone": index,
                "name": f"Zone {index}",
                "running": False,
                "run_seconds": run_seconds,
                "scheduled_start_utc": start.isoformat(),
                "scheduled_end_utc": end.isoformat(),
            }
        )
        start = end
    return result


def result(
    *,
    command: str | None = None,
    block_source: str | None = None,
    activity: str = "PARKED_IN_CS",
    battery: int = 100,
    active_ids: list[int] | None = None,
    clear: bool = True,
) -> CycleResult:
    block = None
    if block_source:
        block = {
            "start": (NOW + timedelta(minutes=5)).isoformat(),
            "end": (NOW + timedelta(hours=4)).isoformat(),
            "title": "Sicherheitsblock",
            "source": block_source,
        }
    active_ids = active_ids or []
    return CycleResult(
        schema_version=2,
        executed_at_utc=NOW.isoformat(),
        source="test",
        control_mode="FULL_FAILSAFE",
        past_due=False,
        decision_code="WOULD_PARK" if command == "PARK" else "ALREADY_SAFE",
        command_sent=False,
        message="test",
        details={
            "decision": {"hypothetical_command": command},
            "current_plan": {
                "blocked_now": None,
                "parking_block": block,
                "next_block": block,
                "mowing_window_now": {
                    "start": (NOW - timedelta(hours=1)).isoformat(),
                    "end": (NOW + timedelta(hours=12)).isoformat(),
                },
            },
            "hydrawise": {
                "status": "live (7 Zonen)",
                "error": None,
                "zones": zones(),
                "safety": {
                    "available": True,
                    "fresh": True,
                    "clear_now": clear,
                    "observed_at_utc": NOW.isoformat(),
                    "selected_zone_count": 7,
                    "active_zone_count": len(active_ids),
                    "active_relay_ids": active_ids,
                    "reason": "frei" if clear else "läuft",
                },
            },
            "mower": {
                "mower_id": "mower-1",
                "activity": activity,
                "state": "IN_OPERATION",
                "override_action": "FORCE_PARK" if activity in {"PARKED_IN_CS", "CHARGING"} else "NOT_ACTIVE",
                "external_reason_id": 253053,
                "error_code": 0,
                "battery_percent": battery,
                "target_work_area": {"id": 849199, "name": "Rasenfläche", "enabled": True},
            },
            "safety": {"read_only": True, "command_sent": False},
        },
    )


def irrigation_state(*, phase: str, current: int | None = None) -> AutomationState:
    plan = zones()
    return AutomationState(
        parked_by_automation=True,
        automation_park_source="irrigation",
        automation_restart_allowed=True,
        park_command_sent_utc=(NOW - timedelta(minutes=10)).isoformat(),
        park_confirmed_utc=(NOW - timedelta(minutes=5)).isoformat(),
        irrigation_phase=phase,
        irrigation_plan_id="plan",
        irrigation_plan_json=__import__("json").dumps(plan),
        irrigation_suspended_relay_ids_json=__import__("json").dumps(RELAYS),
        irrigation_completed_relay_ids_json="[]",
        irrigation_current_relay_id=current,
        irrigation_zone_start_reserved_utc=(NOW - timedelta(minutes=1)).isoformat() if current else None,
    )


class FullFailsafeTests(unittest.TestCase):
    def _run(self, state: AutomationState, cycle: CycleResult, **senders):
        store = InMemoryStateStore(state)
        output = run_full_failsafe_cycle(
            now_utc=NOW,
            settings=settings(),
            environment=ENV,
            past_due=False,
            source="test",
            read_only_runner=lambda **_: cycle,
            state_store_factory=lambda _env: store,
            park_sender=senders.get("park", lambda *_: {"accepted": True}),
            start_sender=senders.get("start", lambda *_: {"accepted": True}),
            suspend_zone_sender=senders.get("suspend", lambda *_: {"message_type": "info"}),
            start_zone_sender=senders.get("zone", lambda *_: {"message_type": "info"}),
        )
        return output, store

    def test_irrigation_plan_parks_before_any_zone_command(self) -> None:
        park_calls = []
        zone_calls = []
        output, store = self._run(
            AutomationState(),
            result(command="PARK", block_source="irrigation", activity="MOWING", clear=False),
            park=lambda *args: park_calls.append(args) or {"accepted": True},
            zone=lambda *args: zone_calls.append(args) or {},
        )
        self.assertEqual(output.decision_code, "PARK_COMMAND_SENT")
        self.assertEqual(len(park_calls), 1)
        self.assertEqual(zone_calls, [])
        self.assertEqual(store.load().irrigation_phase, "PLANNED")

    def test_suspends_one_scheduled_zone_per_cycle(self) -> None:
        calls = []
        # Helper state already contains all suspended IDs; use an empty list.
        state = irrigation_state(phase="PLANNED")
        state = AutomationState.from_mapping({**state.to_dict(), "irrigation_suspended_relay_ids_json": "[]"})
        output, store = self._run(
            state,
            result(block_source="irrigation"),
            suspend=lambda *args: calls.append(args) or {"message_type": "info"},
        )
        self.assertEqual(output.decision_code, "IRRIGATION_ZONE_SUSPENDED")
        self.assertEqual(calls[-1][1], RELAYS[0])
        self.assertEqual(len(__import__("json").loads(store.load().irrigation_suspended_relay_ids_json)), 1)

    def test_starts_first_zone_only_after_all_schedule_starts_are_suspended(self) -> None:
        calls = []
        output, store = self._run(
            irrigation_state(phase="READY"),
            result(block_source="irrigation"),
            zone=lambda *args: calls.append(args) or {"message_type": "info"},
        )
        self.assertEqual(output.decision_code, "IRRIGATION_ZONE_START_SENT")
        self.assertEqual(calls[0][1:3], (RELAYS[0], 1200))
        self.assertEqual(store.load().irrigation_phase, "START_RESERVED")

    def test_unexpected_active_zone_fails_closed(self) -> None:
        output, store = self._run(
            irrigation_state(phase="START_RESERVED", current=RELAYS[0]),
            result(block_source="irrigation", active_ids=[RELAYS[1]], clear=False),
        )
        self.assertEqual(output.decision_code, "IRRIGATION_UNEXPECTED_ACTIVE_ZONE")
        self.assertEqual(store.load().irrigation_phase, "FAILED")

    def test_ninety_minute_hold_blocks_mower_start(self) -> None:
        state = irrigation_state(phase="COMPLETE_HOLD")
        state = AutomationState.from_mapping(
            {
                **state.to_dict(),
                "irrigation_completed_utc": (NOW - timedelta(minutes=89)).isoformat(),
                "hydrawise_clear_since_utc": (NOW - timedelta(minutes=89)).isoformat(),
            }
        )
        calls = []
        output, _store = self._run(
            state,
            result(),
            start=lambda *args: calls.append(args) or {},
        )
        self.assertEqual(output.decision_code, "HYDRAWISE_90_MINUTE_HOLD")
        self.assertEqual(calls, [])

    def test_continuous_restart_after_area_completion_uses_lower_battery_threshold(self) -> None:
        state = AutomationState(
            parked_by_automation=True,
            automation_park_source="continuous",
            automation_restart_allowed=True,
            park_command_sent_utc=(NOW - timedelta(hours=1)).isoformat(),
            park_confirmed_utc=(NOW - timedelta(minutes=2)).isoformat(),
            last_mower_activity="MOWING",
            hydrawise_clear_since_utc=(NOW - timedelta(minutes=120)).isoformat(),
            last_hydrawise_success_utc=(NOW - timedelta(minutes=1)).isoformat(),
        )
        calls = []
        output, store = self._run(
            state,
            result(activity="PARKED_IN_CS", battery=65),
            start=lambda *args: calls.append(args) or {"accepted": True},
        )
        self.assertEqual(output.decision_code, "CONTINUOUS_MOWING_START_SENT")
        self.assertEqual(len(calls), 1)
        self.assertTrue(store.load().continuous_mowing_owned)

    def test_training_match_and_irrigation_always_prevent_continuous_start(self) -> None:
        for source in ("training", "match", "irrigation"):
            with self.subTest(source=source):
                start_calls = []
                output, _store = self._run(
                    AutomationState(hydrawise_clear_since_utc=(NOW - timedelta(minutes=120)).isoformat()),
                    result(command="PARK", block_source=source, activity="MOWING"),
                    start=lambda *args: start_calls.append(args) or {},
                )
                self.assertIn(output.decision_code, {"PARK_COMMAND_SENT", "IRRIGATION_FAILED_HOLD"})
                self.assertEqual(start_calls, [])


if __name__ == "__main__":
    unittest.main()
