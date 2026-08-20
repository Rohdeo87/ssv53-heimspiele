from __future__ import annotations

import json
import unittest
from datetime import timedelta
from unittest.mock import patch

from mower.state import AutomationState
from mower.state_store import InMemoryStateStore
from platzwart_console import (
    _CLUBHOUSE_CACHE,
    _clubhouse_events,
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

    def test_clubhouse_reservations_return_content_and_display_name_only(self) -> None:
        class Response:
            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return None

            def geturl(self):
                return "https://application.appack.de/resource-management/de?component=component-1&jwt=" + "x" * 80

        _CLUBHOUSE_CACHE.update({"expires": None, "events": [], "available": False})
        graphql_results = [
            {"findBookingResources": [{"id": "resource-1", "name": "Vereinsheim"}]},
            {"findBookingCalendar": {"items": ["FULLY_BOOKED", "AVAILABLE"]}},
            {
                "day0": [
                    {
                        "slots": [
                            {
                                "start": "2026-08-13T05:00:00Z",
                                "end": "2026-08-13T21:45:00Z",
                                "available": False,
                                "blocked": False,
                                "bookingId": "booking-secret",
                                "booking": {
                                    "profileName": "  Erika   Musterfrau  ",
                                    "comment": "  Vorstandssitzung   Jugend  ",
                                    "profileMail": "must-not-leak@example.invalid",
                                    "profileId": "profile-secret",
                                },
                            }
                        ]
                    }
                ]
            },
        ]
        with patch("platzwart_console.urllib.request.urlopen", return_value=Response()), patch(
            "platzwart_console._appack_graphql", side_effect=graphql_results
        ) as graphql:
            result = _clubhouse_events(
                {"SSV53_CLUBHOUSE_RESERVATION_URL": "https://example.invalid/embed"},
                NOW,
            )
        self.assertTrue(result["available"], msg=result)
        self.assertEqual([item["title"] for item in result["events"]], ["Vorstandssitzung Jugend"])
        self.assertEqual([item["bookedBy"] for item in result["events"]], ["Erika Musterfrau"])
        self.assertNotIn("booking-secret", json.dumps(result))
        self.assertNotIn("must-not-leak", json.dumps(result))
        self.assertNotIn("profile-secret", json.dumps(result))
        plans_query = graphql.call_args_list[2].args[1]
        self.assertIn("profileName comment", plans_query)
        self.assertNotIn("profileMail", plans_query)
        self.assertNotIn("profileId", plans_query)

    def test_clubhouse_hour_slots_of_one_booking_are_merged(self) -> None:
        class Response:
            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return None

            def geturl(self):
                return "https://application.appack.de/resource-management/de?component=component-1&jwt=" + "x" * 80

        def slot(start_hour: int, end_hour: int, booking_id: str) -> dict:
            return {
                "start": f"2026-09-05T{start_hour:02d}:00:00Z",
                "end": f"2026-09-05T{end_hour:02d}:00:00Z",
                "available": False,
                "blocked": False,
                "bookingId": booking_id,
                "booking": {
                    "profileName": "Julian Böhm",
                    "comment": "Handballsaison Start",
                },
            }

        _CLUBHOUSE_CACHE.update({"expires": None, "events": [], "available": False})
        statuses = ["AVAILABLE"] * 23 + ["MOSTLY_BOOKED"]
        graphql_results = [
            {"findBookingResources": [{"id": "resource-1", "name": "Vereinsheim"}]},
            {"findBookingCalendar": {"items": statuses}},
            {
                "day0": [
                    {
                        "slots": [
                            slot(5, 6, "one-booking"),
                            slot(6, 7, "one-booking"),
                            slot(7, 8, "one-booking"),
                            slot(8, 9, "one-booking"),
                            slot(9, 10, "other-booking"),
                        ]
                    }
                ]
            },
        ]
        with patch("platzwart_console.urllib.request.urlopen", return_value=Response()), patch(
            "platzwart_console._appack_graphql", side_effect=graphql_results
        ):
            result = _clubhouse_events(
                {"SSV53_CLUBHOUSE_RESERVATION_URL": "https://example.invalid/embed"},
                NOW,
            )

        self.assertTrue(result["available"], msg=result)
        self.assertEqual(len(result["events"]), 2)
        first, second = result["events"]
        self.assertEqual(first["start"], "2026-09-05T05:00:00+00:00")
        self.assertEqual(first["end"], "2026-09-05T09:00:00+00:00")
        self.assertEqual(second["start"], "2026-09-05T09:00:00+00:00")
        self.assertEqual(second["end"], "2026-09-05T10:00:00+00:00")
        serialized = json.dumps(result)
        self.assertNotIn("one-booking", serialized)
        self.assertNotIn("other-booking", serialized)

    def test_clubhouse_booking_after_more_than_eight_occupied_days_is_not_lost(self) -> None:
        class Response:
            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return None

            def geturl(self):
                return "https://application.appack.de/resource-management/de?component=component-1&jwt=" + "x" * 80

        _CLUBHOUSE_CACHE.update({"expires": None, "events": [], "available": False})
        plans = {f"day{index}": [] for index in range(9)}
        plans["day8"] = [
            {
                "slots": [
                    {
                        "start": "2026-08-21T17:00:00Z",
                        "end": "2026-08-21T18:00:00Z",
                        "available": False,
                        "bookingId": "ninth-day-booking",
                        "booking": {"profileName": "Erika Musterfrau", "comment": "Kurze Buchung"},
                    }
                ]
            }
        ]
        graphql_results = [
            {"findBookingResources": [{"id": "resource-1", "name": "Vereinsheim"}]},
            {"findBookingCalendar": {"items": ["MOSTLY_BOOKED"] * 9}},
            plans,
        ]
        with patch("platzwart_console.urllib.request.urlopen", return_value=Response()), patch(
            "platzwart_console._appack_graphql", side_effect=graphql_results
        ) as graphql:
            result = _clubhouse_events(
                {"SSV53_CLUBHOUSE_RESERVATION_URL": "https://example.invalid/embed"},
                NOW,
            )

        self.assertTrue(result["available"], msg=result)
        self.assertEqual([item["title"] for item in result["events"]], ["Kurze Buchung"])
        plans_query = graphql.call_args_list[2].args[1]
        self.assertIn("day8:findResourcePlans", plans_query)
        self.assertNotIn("ninth-day-booking", json.dumps(result))


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
            suspend_zone_sender=senders.get("suspend_zone_sender", lambda *_args: {"ok": True}),
            start_zone_sender=senders.get("start_zone_sender", lambda *_args: {"ok": True}),
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

    def test_explicit_start_adopts_already_mowing_external_override(self) -> None:
        sent = []
        initial = self.pending(
            "START_MOWING",
            hydrawise_clear_since_utc=(NOW - timedelta(minutes=121)).isoformat(),
            last_hydrawise_success_utc=(NOW - timedelta(minutes=1)).isoformat(),
        )
        cycle, state = self.run_cycle(
            initial,
            result(activity="MOWING", override_action="FORCE_MOW"),
            start_sender=lambda *_args: sent.append("start") or {"ok": True},
        )
        self.assertEqual(sent, [])
        self.assertFalse(cycle.command_sent)
        self.assertEqual(cycle.decision_code, "CONTINUOUS_MOWING_ACTIVE")
        self.assertTrue(state.continuous_mowing_owned)
        self.assertEqual(state.last_decision_code, "CONTINUOUS_MOWING_ACTIVE")
        self.assertIsNotNone(state.continuous_mowing_work_area_id)
        self.assertIsNotNone(state.continuous_mowing_window_end_utc)
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

    def test_manual_single_zone_keeps_all_safety_relays_but_runs_only_selection(self) -> None:
        cycle, state = self.run_cycle(
            self.pending(
                "START_IRRIGATION_ZONE",
                operator_request_zone=3,
                operator_request_run_seconds=25 * 60,
            ),
            result(activity="MOWING"),
        )
        self.assertTrue(cycle.command_sent)
        self.assertEqual(cycle.decision_code, "PARK_COMMAND_SENT")
        plan = json.loads(state.irrigation_plan_json or "[]")
        self.assertEqual({item["relay_id"] for item in plan}, set(RELAYS))
        selected = [item for item in plan if item.get("selected")]
        self.assertEqual(len(selected), 1)
        self.assertEqual(selected[0]["zone"], 3)
        self.assertEqual(selected[0]["run_seconds"], 25 * 60)
        self.assertTrue(selected[0]["operator_single_zone"])
        self.assertEqual(state.operator_request_status, "COMPLETED")

    def test_manual_single_zone_starts_only_selected_zone_with_selected_duration(self) -> None:
        plan = zones(start_utc=NOW + timedelta(minutes=30))
        for item in plan:
            item["selected"] = item["zone"] == 3
            item["operator_single_zone"] = True
        plan[2]["run_seconds"] = 25 * 60
        sent = []
        initial = AutomationState(
            parked_by_automation=True,
            automation_park_source="irrigation",
            automation_restart_allowed=True,
            park_command_sent_utc=(NOW - timedelta(minutes=5)).isoformat(),
            park_confirmed_utc=(NOW - timedelta(minutes=2)).isoformat(),
            irrigation_phase="READY",
            irrigation_plan_id="single-zone-plan",
            irrigation_plan_json=json.dumps(plan),
            irrigation_suspended_relay_ids_json=json.dumps(RELAYS),
            irrigation_suspension_until_utc=(NOW + timedelta(hours=5)).isoformat(),
            irrigation_suspension_completed_utc=(NOW - timedelta(minutes=1)).isoformat(),
            irrigation_completed_relay_ids_json="[]",
        )
        cycle, state = self.run_cycle(
            initial,
            result(activity="CHARGING"),
            start_zone_sender=lambda _key, relay, seconds, _controller: sent.append(
                (relay, seconds)
            ) or {"ok": True},
        )
        self.assertEqual(
            sent,
            [(RELAYS[2], 25 * 60)],
            msg=(cycle.decision_code, cycle.message, cycle.details),
        )
        self.assertEqual(cycle.decision_code, "IRRIGATION_ZONE_START_SENT")
        self.assertEqual(state.irrigation_phase, "START_RESERVED")
        self.assertEqual(state.irrigation_current_relay_id, RELAYS[2])

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
