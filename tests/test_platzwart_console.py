from __future__ import annotations

import json
import unittest
from datetime import timedelta

from mower.state import AutomationState
from mower.state_store import InMemoryStateStore
from platzwart_console import (
    PlatzwartError,
    create_activation_hash,
    create_pin_hash,
    issue_session,
    require_session,
    verify_pin,
)
from tests.test_full_failsafe import ENV, NOW, RELAYS, result, settings, zones
from mower.full_failsafe import run_full_failsafe_cycle


SESSION_ENV = {"SSV53_PLATZWART_SESSION_SECRET": "x" * 48}


class PlatzwartAuthenticationTests(unittest.TestCase):
    def test_four_digit_pin_is_salted_and_verified(self) -> None:
        encoded = create_pin_hash("4072", salt=b"0123456789abcdef")
        self.assertNotIn("4072", encoded)
        self.assertTrue(verify_pin("4072", encoded))
        self.assertFalse(verify_pin("4073", encoded))
        with self.assertRaises(ValueError):
            create_pin_hash("12345")

    def test_session_is_bound_to_device_and_expires(self) -> None:
        token, _expires = issue_session(SESSION_ENV, NOW, "device-1")
        payload = require_session(token, SESSION_ENV, NOW + timedelta(minutes=29))
        self.assertEqual(payload["did"], "device-1")
        with self.assertRaises(PlatzwartError) as context:
            require_session(token, SESSION_ENV, NOW + timedelta(minutes=31))
        self.assertEqual(context.exception.code, "SESSION_EXPIRED")

    def test_activation_code_requires_high_entropy(self) -> None:
        self.assertEqual(len(create_activation_hash("A" * 24)), 64)
        with self.assertRaises(ValueError):
            create_activation_hash("too-short")


class PlatzwartSafetyIntegrationTests(unittest.TestCase):
    def run_cycle(self, initial: AutomationState, live_result, **senders):
        store = InMemoryStateStore(initial)
        cycle = run_full_failsafe_cycle(
            now_utc=NOW,
            settings=settings(),
            environment=ENV,
            past_due=False,
            source="test",
            read_only_runner=lambda **_kwargs: live_result,
            state_store_factory=lambda _environment: store,
            park_sender=senders.get("park_sender", lambda *_args: {"ok": True}),
            start_sender=senders.get("start_sender", lambda *_args: {"ok": True}),
            suspend_zone_sender=lambda *_args: {"ok": True},
            start_zone_sender=lambda *_args: {"ok": True},
        )
        return cycle, store.load()

    def pending(self, action: str, **values) -> AutomationState:
        return AutomationState(
            operator_request_id="request-1",
            operator_request_action=action,
            operator_requested_utc=(NOW - timedelta(seconds=10)).isoformat(),
            operator_request_expires_utc=(NOW + timedelta(minutes=10)).isoformat(),
            operator_request_status="PENDING",
            **values,
        )

    def test_operator_park_never_grants_automatic_restart(self) -> None:
        sent = []
        cycle, state = self.run_cycle(
            self.pending("PARK_MOWER"),
            result(activity="MOWING"),
            park_sender=lambda *_args: sent.append("park") or {"ok": True},
        )
        self.assertEqual(sent, ["park"])
        self.assertTrue(cycle.command_sent)
        self.assertEqual(state.operator_request_status, "COMPLETED")
        self.assertEqual(state.automation_park_source, "operator")
        self.assertFalse(state.automation_restart_allowed)

    def test_operator_park_holds_until_explicit_start(self) -> None:
        initial = AutomationState(
            parked_by_automation=True,
            automation_park_source="operator",
            automation_restart_allowed=False,
            park_confirmed_utc=(NOW - timedelta(minutes=5)).isoformat(),
            hydrawise_clear_since_utc=(NOW - timedelta(minutes=121)).isoformat(),
            last_hydrawise_success_utc=(NOW - timedelta(minutes=1)).isoformat(),
        )
        cycle, _state = self.run_cycle(initial, result(activity="CHARGING"))
        self.assertEqual(cycle.decision_code, "OPERATOR_PARK_HOLD")
        self.assertFalse(cycle.command_sent)

    def test_operator_park_is_reasserted_if_mower_unexpectedly_leaves(self) -> None:
        sent = []
        initial = AutomationState(
            parked_by_automation=True,
            automation_park_source="operator",
            automation_restart_allowed=False,
            park_command_sent_utc=(NOW - timedelta(minutes=10)).isoformat(),
            park_confirmed_utc=(NOW - timedelta(minutes=5)).isoformat(),
        )
        cycle, state = self.run_cycle(
            initial,
            result(activity="MOWING"),
            park_sender=lambda *_args: sent.append("park") or {"ok": True},
        )
        self.assertEqual(sent, ["park"])
        self.assertEqual(cycle.decision_code, "PARK_COMMAND_REASSERTED")
        self.assertTrue(state.parked_by_automation)
        self.assertEqual(state.automation_park_source, "operator")
        self.assertFalse(state.automation_restart_allowed)

    def test_explicit_start_still_obeys_safe_start_path(self) -> None:
        sent = []
        initial = self.pending(
            "START_MOWING",
            parked_by_automation=True,
            automation_park_source="operator",
            automation_restart_allowed=False,
            park_confirmed_utc=(NOW - timedelta(minutes=5)).isoformat(),
            hydrawise_clear_since_utc=(NOW - timedelta(minutes=121)).isoformat(),
            last_hydrawise_success_utc=(NOW - timedelta(minutes=1)).isoformat(),
        )
        cycle, state = self.run_cycle(
            initial,
            result(activity="CHARGING"),
            start_sender=lambda *_args: sent.append("start") or {"ok": True},
        )
        self.assertEqual(sent, ["start"])
        self.assertTrue(cycle.command_sent)
        self.assertEqual(state.operator_request_status, "COMPLETED")

    def test_manual_irrigation_first_parks_and_captures_all_seven_zones(self) -> None:
        cycle, state = self.run_cycle(
            self.pending("START_IRRIGATION"),
            result(activity="MOWING"),
        )
        self.assertTrue(cycle.command_sent)
        self.assertEqual(cycle.decision_code, "PARK_COMMAND_SENT")
        self.assertEqual(state.irrigation_phase, "PLANNED")
        plan = json.loads(state.irrigation_plan_json or "[]")
        self.assertEqual({item["relay_id"] for item in plan}, set(RELAYS))
        self.assertTrue(all(item["operator_manual"] is True for item in plan))
        self.assertEqual(state.operator_request_status, "COMPLETED")

    def test_stop_between_zones_starts_hold_without_starting_another_zone(self) -> None:
        plan = zones(start_utc=NOW + timedelta(minutes=30))
        initial = self.pending(
            "STOP_IRRIGATION_AFTER_ZONE",
            parked_by_automation=True,
            automation_park_source="irrigation",
            automation_restart_allowed=True,
            park_command_sent_utc=(NOW - timedelta(minutes=5)).isoformat(),
            park_confirmed_utc=(NOW - timedelta(minutes=2)).isoformat(),
            irrigation_phase="READY",
            irrigation_plan_id="plan",
            irrigation_plan_json=json.dumps(plan),
            irrigation_suspended_relay_ids_json=json.dumps(RELAYS),
            irrigation_suspension_until_utc=(NOW + timedelta(hours=5)).isoformat(),
            irrigation_suspension_completed_utc=(NOW - timedelta(minutes=1)).isoformat(),
            irrigation_completed_relay_ids_json="[]",
        )
        cycle, state = self.run_cycle(initial, result(activity="CHARGING"))
        self.assertEqual(cycle.decision_code, "IRRIGATION_OPERATOR_STOPPED_BETWEEN_ZONES")
        self.assertEqual(state.irrigation_phase, "COMPLETE_HOLD")
        self.assertEqual(state.operator_request_status, "COMPLETED")


if __name__ == "__main__":
    unittest.main()
