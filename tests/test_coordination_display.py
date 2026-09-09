from dataclasses import replace
from datetime import timedelta
import unittest

from mower.state import AutomationState
from platzwart_console import _coordination_payload
from tests.test_full_failsafe import result, NOW, ENV


class CoordinationDisplayTests(unittest.TestCase):
    def test_unconfirmed_start_is_a_stop_latch_even_after_docking(self):
        state = AutomationState(last_cycle_started_utc=NOW.isoformat(), mower_start_pending_since_utc=(NOW - timedelta(minutes=5)).isoformat())
        value = _coordination_payload(result(activity="PARKED_IN_CS").details, state, {}, ENV, NOW, {})
        self.assertEqual(value["primaryBlocker"]["code"], "START_UNCONFIRMED")
        self.assertIn("Parkmeldung allein", value["primaryBlocker"]["resolution"])

    def test_simultaneous_blockers_and_physical_deadline_are_not_a_start_permit(self):
        details = result(activity="CHARGING").details
        details["hydrawise"]["release_confirmation"] = {
            "allowed": False, "telemetry_confirmed": False,
            "dry_until_utc": (NOW + timedelta(minutes=40)).isoformat(),
            "release_at_utc": (NOW + timedelta(minutes=42)).isoformat(),
        }
        state = AutomationState(last_cycle_started_utc=NOW.isoformat(), maintenance_mode=True)
        plan = {"blocked_now": {"title": "Training", "end": (NOW + timedelta(hours=2)).isoformat()}}
        value = _coordination_payload(details, state, plan, ENV, NOW, {})
        self.assertTrue(value["explanationOnly"])
        self.assertEqual(value["primaryBlocker"]["code"], "MANUAL_STOP")
        self.assertEqual(value["dryingMinutes"], 150)
        self.assertEqual(value["dryUntil"], details["hydrawise"]["release_confirmation"]["dry_until_utc"])
        self.assertEqual({item["code"] for item in value["blockers"]}, {"MANUAL_STOP", "OCCUPANCY", "DRYING_OR_CONFIRMATION", "CHARGING"})
        self.assertIsNone(value["chargingEndEstimate"])

    def test_missing_timestamps_are_unknown_and_sent_is_not_physical_completion(self):
        state = AutomationState(operator_request_action="PARK_MOWER", operator_request_status="COMPLETE")
        value = _coordination_payload({}, state, {}, ENV, NOW, {})
        self.assertIsNone(value["dataAgeSeconds"]["mower"])
        self.assertFalse(value["lastAction"]["deviceExecutionConfirmed"])
        self.assertIn("CONTROLLER_STALE", [item["code"] for item in value["blockers"]])

    def test_freshly_generated_display_does_not_refresh_old_decision(self):
        state = AutomationState(last_cycle_started_utc=(NOW - timedelta(minutes=5)).isoformat())
        value = _coordination_payload(result().details, state, {}, ENV, NOW, {})
        self.assertEqual(value["dataAgeSeconds"]["controller"], 300)
        self.assertIn("CONTROLLER_STALE", [item["code"] for item in value["blockers"]])
