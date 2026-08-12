from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from mower.safety import CommandIntent, evaluate_command_gate, is_heartbeat_stale
from mower.state import AutomationState
from mower.state_store import (
    InMemoryStateStore,
    JsonFileStateStore,
    StateConflictError,
)


NOW = datetime(2026, 8, 2, 12, 0, tzinfo=timezone.utc)


class AutomationStateTests(unittest.TestCase):
    def test_roundtrip_preserves_state(self) -> None:
        state = AutomationState().record_cycle(
            started_utc=NOW,
            success=True,
            decision_code="ALREADY_MOWING",
            mower_activity="MOWING",
            mower_state="IN_OPERATION",
            error_code=0,
        )
        restored = AutomationState.from_mapping(state.to_dict())
        self.assertEqual(restored, state)
        self.assertEqual(restored.revision, 1)

    def test_record_park_marks_automation_ownership(self) -> None:
        state = AutomationState().record_command(
            fingerprint="abc",
            sent_utc=NOW,
            action="PARK",
            park_until_utc=NOW + timedelta(hours=1),
        )
        self.assertTrue(state.parked_by_automation)
        self.assertEqual(state.park_command_sent_utc, NOW.isoformat())

    def test_record_start_clears_automation_ownership(self) -> None:
        parked = AutomationState(parked_by_automation=True)
        started = parked.record_command(
            fingerprint="def",
            sent_utc=NOW,
            action="START",
        )
        self.assertFalse(started.parked_by_automation)
        self.assertEqual(started.last_start_command_utc, NOW.isoformat())

    def test_hydrawise_clear_confirmation_starts_at_poll_time(self) -> None:
        state = AutomationState().record_cycle(
            started_utc=NOW,
            success=True,
            decision_code="HYDRAWISE_CLEAR",
            hydrawise_success_utc=NOW,
            hydrawise_observed_utc=NOW - timedelta(minutes=2),
            hydrawise_clear=True,
            hydrawise_active_count=0,
        )
        self.assertEqual(state.hydrawise_clear_since_utc, NOW.isoformat())

        interrupted = state.record_cycle(
            started_utc=NOW + timedelta(minutes=1),
            success=True,
            decision_code="HYDRAWISE_RUNNING",
            hydrawise_clear=False,
            hydrawise_active_count=1,
        )
        self.assertIsNone(interrupted.hydrawise_clear_since_utc)

    def test_hydrawise_clear_chain_restarts_after_cycle_gap(self) -> None:
        first = AutomationState().record_cycle(
            started_utc=NOW,
            success=True,
            decision_code="HYDRAWISE_CLEAR",
            hydrawise_success_utc=NOW,
            hydrawise_observed_utc=NOW,
            hydrawise_clear=True,
            hydrawise_active_count=0,
        )
        after_gap = first.record_cycle(
            started_utc=NOW + timedelta(minutes=4),
            success=True,
            decision_code="HYDRAWISE_CLEAR",
            hydrawise_success_utc=NOW + timedelta(minutes=4),
            hydrawise_observed_utc=NOW + timedelta(minutes=4),
            hydrawise_clear=True,
            hydrawise_active_count=0,
        )
        self.assertEqual(
            after_gap.hydrawise_clear_since_utc,
            (NOW + timedelta(minutes=4)).isoformat(),
        )


class StateStoreTests(unittest.TestCase):
    def test_in_memory_store_uses_optimistic_revision(self) -> None:
        store = InMemoryStateStore()
        initial = store.load()
        updated = initial.record_cycle(
            started_utc=NOW,
            success=True,
            decision_code="OK",
        )
        store.save(updated, expected_revision=0)
        with self.assertRaises(StateConflictError):
            store.save(updated.record_cycle(
                started_utc=NOW + timedelta(minutes=1),
                success=True,
                decision_code="OK",
            ), expected_revision=0)

    def test_json_store_writes_and_loads_atomically(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "state.json"
            store = JsonFileStateStore(path)
            updated = store.load().record_cycle(
                started_utc=NOW,
                success=True,
                decision_code="OK",
            )
            store.save(updated, expected_revision=0)
            self.assertEqual(store.load(), updated)
            self.assertTrue(path.exists())


class CommandSafetyTests(unittest.TestCase):
    def test_duplicate_command_is_blocked(self) -> None:
        intent = CommandIntent(
            action="PARK",
            target="schaf",
            reason="Beregnung",
        )
        state = AutomationState().record_command(
            fingerprint=intent.fingerprint,
            sent_utc=NOW,
            action="PARK",
        )
        decision = evaluate_command_gate(
            state=state,
            intent=intent,
            now_utc=NOW + timedelta(minutes=5),
        )
        self.assertFalse(decision.allowed)
        self.assertEqual(decision.code, "DUPLICATE_COMMAND")

    def test_start_without_owned_park_is_blocked(self) -> None:
        decision = evaluate_command_gate(
            state=AutomationState(),
            intent=CommandIntent(
                action="START",
                target="schaf",
                reason="Platz wieder frei",
            ),
            now_utc=NOW,
        )
        self.assertFalse(decision.allowed)
        self.assertEqual(decision.code, "START_NOT_OWNED")

    def test_start_after_owned_park_can_be_allowed(self) -> None:
        state = AutomationState(parked_by_automation=True)
        decision = evaluate_command_gate(
            state=state,
            intent=CommandIntent(
                action="START",
                target="schaf",
                reason="Platz wieder frei",
                valid_until_utc=NOW + timedelta(minutes=30),
            ),
            now_utc=NOW,
        )
        self.assertTrue(decision.allowed)

    def test_maintenance_mode_blocks_commands(self) -> None:
        state = AutomationState(maintenance_mode=True)
        decision = evaluate_command_gate(
            state=state,
            intent=CommandIntent(
                action="PARK",
                target="schaf",
                reason="Test",
            ),
            now_utc=NOW,
        )
        self.assertEqual(decision.code, "MAINTENANCE_MODE")

    def test_stale_heartbeat_is_detected(self) -> None:
        self.assertTrue(
            is_heartbeat_stale(
                last_success_utc=(NOW - timedelta(minutes=4)).isoformat(),
                now_utc=NOW,
                max_age_minutes=3,
            )
        )
        self.assertFalse(
            is_heartbeat_stale(
                last_success_utc=(NOW - timedelta(minutes=2)).isoformat(),
                now_utc=NOW,
                max_age_minutes=3,
            )
        )


if __name__ == "__main__":
    unittest.main()
