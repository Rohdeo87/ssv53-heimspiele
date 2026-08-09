from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone

from mower.park_only import run_park_only_cycle
from mower.runtime import CycleResult, RuntimeSettings
from mower.state_store import InMemoryStateStore

NOW = datetime(2026, 8, 9, 10, 0, tzinfo=timezone.utc)


def _result(*, decision_code: str = "WOULD_PARK", error_code: int = 0) -> CycleResult:
    return CycleResult(
        schema_version=2,
        executed_at_utc=NOW.isoformat(),
        source="test",
        control_mode="PARK_ONLY",
        past_due=False,
        decision_code=decision_code,
        command_sent=False,
        message="Testentscheidung",
        details={
            "mode": "read_only_live_dry_run",
            "decision": {
                "code": decision_code,
                "reason": "Beregnung steht an",
                "hypothetical_command": "PARK" if decision_code == "WOULD_PARK" else None,
            },
            "current_plan": {
                "parking_block": {
                    "start": (NOW + timedelta(minutes=5)).isoformat(),
                    "end": (NOW + timedelta(hours=2)).isoformat(),
                    "title": "Beregnung",
                    "source": "irrigation",
                },
                "next_block": {
                    "start": (NOW + timedelta(minutes=5)).isoformat(),
                    "end": (NOW + timedelta(hours=2)).isoformat(),
                    "title": "Beregnung",
                    "source": "irrigation",
                },
            },
            "hydrawise": {"status": "live (7 Zonen)", "error": None},
            "mower": {
                "mower_id": "mower-1",
                "activity": "MOWING" if error_code == 0 else "NOT_APPLICABLE",
                "state": "IN_OPERATION" if error_code == 0 else "ERROR",
                "error_code": error_code,
            },
            "safety": {"read_only": True, "command_functions_present": False, "command_sent": False},
        },
    )


class ParkOnlyTests(unittest.TestCase):
    def setUp(self) -> None:
        self.settings = RuntimeSettings.from_mapping({
            "CONTROL_MODE": "PARK_ONLY",
            "ENABLE_LIVE_READS": "true",
            "ENABLE_PARK_COMMANDS": "true",
        })
        self.environment = {
            "HUSQVARNA_CLIENT_ID": "client",
            "HUSQVARNA_CLIENT_SECRET": "secret",
        }

    def test_locked_gate_never_sends_command(self) -> None:
        locked = RuntimeSettings.from_mapping({
            "CONTROL_MODE": "PARK_ONLY",
            "ENABLE_LIVE_READS": "true",
            "ENABLE_PARK_COMMANDS": "false",
        })
        calls = []
        result = run_park_only_cycle(
            now_utc=NOW, settings=locked, environment=self.environment,
            past_due=False, source="test", read_only_runner=lambda **_: _result(),
            state_store_factory=lambda _env: InMemoryStateStore(),
            park_sender=lambda *_: calls.append("park") or {},
        )
        self.assertFalse(result.command_sent)
        self.assertEqual(calls, [])
        self.assertEqual(result.details["mode"], "park_only_capable_locked")

    def test_would_park_sends_only_park_and_records_ownership(self) -> None:
        store = InMemoryStateStore()
        calls = []
        result = run_park_only_cycle(
            now_utc=NOW, settings=self.settings, environment=self.environment,
            past_due=False, source="test", read_only_runner=lambda **_: _result(),
            state_store_factory=lambda _env: store,
            park_sender=lambda a, b, c: calls.append((a, b, c)) or {"accepted": True},
        )
        self.assertTrue(result.command_sent)
        self.assertEqual(result.decision_code, "PARK_COMMAND_SENT")
        self.assertEqual(len(calls), 1)
        self.assertTrue(store.load().parked_by_automation)
        self.assertFalse(result.details["park_action"]["automatic_start_possible"])
        self.assertFalse(result.details["safety"]["start_command_functions_present"])

    def test_duplicate_park_is_blocked(self) -> None:
        store = InMemoryStateStore()
        calls = []
        kwargs = dict(
            settings=self.settings, environment=self.environment, past_due=False, source="test",
            read_only_runner=lambda **_: _result(), state_store_factory=lambda _env: store,
            park_sender=lambda *_: calls.append("park") or {},
        )
        first = run_park_only_cycle(now_utc=NOW, **kwargs)
        second = run_park_only_cycle(now_utc=NOW + timedelta(minutes=1), **kwargs)
        self.assertTrue(first.command_sent)
        self.assertFalse(second.command_sent)
        self.assertEqual(second.decision_code, "DUPLICATE_COMMAND")
        self.assertEqual(calls, ["park"])

    def test_state_reservation_failure_never_sends_command(self) -> None:
        calls = []

        class BrokenStore(InMemoryStateStore):
            def save(self, state, *, expected_revision):
                raise RuntimeError("storage unavailable")

        result = run_park_only_cycle(
            now_utc=NOW,
            settings=self.settings,
            environment=self.environment,
            past_due=False,
            source="test",
            read_only_runner=lambda **_: _result(),
            state_store_factory=lambda _env: BrokenStore(),
            park_sender=lambda *_: calls.append("park") or {},
        )
        self.assertFalse(result.command_sent)
        self.assertEqual(result.decision_code, "STATE_RESERVATION_FAILED")
        self.assertEqual(calls, [])

    def test_mower_error_never_sends_command(self) -> None:
        calls = []
        result = run_park_only_cycle(
            now_utc=NOW, settings=self.settings, environment=self.environment,
            past_due=False, source="test",
            read_only_runner=lambda **_: _result(decision_code="MANUAL_ATTENTION", error_code=145),
            state_store_factory=lambda _env: InMemoryStateStore(),
            park_sender=lambda *_: calls.append("park") or {},
        )
        self.assertFalse(result.command_sent)
        self.assertEqual(calls, [])


if __name__ == "__main__":
    unittest.main()
