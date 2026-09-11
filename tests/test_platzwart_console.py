from __future__ import annotations

import hashlib
import json
import unittest
from datetime import timedelta
from unittest.mock import patch

from mower.state import AutomationState
from mower.state_store import InMemoryStateStore
from platzwart_console import (
    _CLUBHOUSE_CACHE,
    _clubhouse_events,
    _enrich_display_block,
    _mower_display_activity,
    _mower_display_label,
    _mower_error_active,
    _mower_error_message,
    _irrigation_intent_payload,
    _STATISTICS_CACHE,
    _restart_battery_percent,
    _protection_payload,
    _coordination_payload,
    PlatzwartError,
    create_activation_hash,
    create_pin_hash,
    issue_session,
    live_status,
    unavailable_live_status,
    request_action,
    require_session,
    verify_pin,
)
from tests.test_full_failsafe import ENV, NOW, RELAYS, result, settings, zones
from mower.full_failsafe import run_full_failsafe_cycle
from mower.runtime import RuntimeSettings


SESSION_ENV = {"SSV53_PLATZWART_SESSION_SECRET": "x" * 48}
FULL_DEVICE_CONTROL_ENV = {
    **ENV,
    "CONTROL_MODE": "FULL_FAILSAFE",
    "ENABLE_LIVE_READS": "true",
    "ENABLE_PARK_COMMANDS": "true",
    "ENABLE_START_COMMANDS": "true",
    "ENABLE_IRRIGATION_COMMANDS": "true",
    "FULL_MOWER_CONFIRMATION": "SSV53-TRAINING-MATCH-PARK-START",
    "FULL_FAILSAFE_CONFIRMATION": "SSV53-MOWER-HYDRAWISE-7-ZONES-150-MINUTES-ADAPTIVE-V1",
}


class PlatzwartAuthenticationTests(unittest.TestCase):
    def test_status_irrigation_intent_requires_complete_matching_provenance(self) -> None:
        manual_plan = zones()
        for zone in manual_plan:
            zone["operator_manual"] = True
        canonical = json.dumps(
            manual_plan, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        )
        verified = AutomationState(
            irrigation_phase="RUNNING",
            irrigation_plan_id=hashlib.sha256(canonical.encode("utf-8")).hexdigest(),
            irrigation_plan_json=canonical,
        )
        self.assertEqual(
            _irrigation_intent_payload(verified, ENV, state_available=True),
            {
                "source": "MANUAL_OPERATOR",
                "verified": True,
                "controllerManaged": True,
                "automaticWindowApplies": False,
            },
        )

        automatic_plan = zones()
        automatic_canonical = json.dumps(
            automatic_plan, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        )
        automatic = AutomationState(
            irrigation_phase="RUNNING",
            irrigation_plan_id=hashlib.sha256(automatic_canonical.encode("utf-8")).hexdigest(),
            irrigation_plan_json=automatic_canonical,
        )
        self.assertEqual(
            _irrigation_intent_payload(automatic, ENV, state_available=True),
            {
                "source": "AUTOMATIC",
                "verified": True,
                "controllerManaged": True,
                "automaticWindowApplies": True,
            },
        )

        mixed = [dict(zone) for zone in manual_plan]
        mixed[0].pop("operator_manual")
        mixed_canonical = json.dumps(
            mixed, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        )
        mixed_state = AutomationState(
            irrigation_phase="RUNNING",
            irrigation_plan_id=hashlib.sha256(mixed_canonical.encode("utf-8")).hexdigest(),
            irrigation_plan_json=mixed_canonical,
        )
        wrong_hash = AutomationState(
            irrigation_phase="RUNNING",
            irrigation_plan_id="wrong-plan-id",
            irrigation_plan_json=canonical,
        )
        for state in (
            mixed_state,
            wrong_hash,
            AutomationState(irrigation_phase="RUNNING"),
        ):
            with self.subTest(state=state.irrigation_plan_json):
                intent = _irrigation_intent_payload(state, ENV, state_available=True)
                self.assertEqual(intent["source"], "UNKNOWN")
                self.assertFalse(intent["verified"])
                self.assertIsNone(intent["automaticWindowApplies"])

    def test_dashboard_preserves_verified_drying_reason_and_time(self) -> None:
        for reason in ("IRRIGATION_END", "DATA_GAP", "POSSIBLE_IRRIGATION_DURING_GAP"):
            with self.subTest(reason=reason):
                dry_until = (NOW + timedelta(minutes=150)).isoformat()
                details = {"hydrawise": {"release_confirmation": {
                    "allowed": False, "dry_until_utc": dry_until,
                }}}
                state = AutomationState(hydrawise_clear_origin=reason)
                payload = _coordination_payload(details, state, {}, ENV, NOW, {})
                self.assertEqual(payload["dryingReason"], reason)
                self.assertEqual(payload["dryUntil"], dry_until)
                self.assertIn("DRYING_OR_CONFIRMATION", {item["code"] for item in payload["blockers"]})

    def test_stale_runtime_config_keeps_live_display_but_locks_all_controls(self) -> None:
        live_cycle = result(activity="MOWING", battery=71)
        store = InMemoryStateStore(AutomationState(continuous_mowing_owned=True))
        stale_error = RuntimeError(
            "Keine frische, validierte Laufzeitkonfiguration verfügbar; fail-closed. "
            "current/manifest.json: Konfigurationsstand ist älter als das zulässige Maximalalter."
        )
        environment = {**ENV, "SSV53_DYNAMIC_CONFIG_ENABLED": "true"}
        with patch(
            "platzwart_console.run_read_only_cycle",
            side_effect=[stale_error, live_cycle],
        ) as read_cycle, patch(
            "platzwart_console.AzureTableStateStore.from_environment",
            return_value=store,
        ), patch(
            "platzwart_console._clubhouse_events",
            return_value={"available": True, "events": [], "message": None},
        ), patch(
            "platzwart_console._dashboard_statistics",
            return_value={"available": True},
        ):
            payload = live_status(environment, NOW)

        self.assertFalse(payload["controlsAvailable"])
        self.assertEqual(payload["dataQuality"]["code"], "CONFIG_STALE")
        self.assertFalse(payload["occupancy"]["available"])
        self.assertNotIn("bleiben sicher gesperrt", payload["overall"]["message"])
        self.assertTrue(payload["dataQuality"]["displayOnly"])
        self.assertEqual(payload["mower"]["activity"], "MOWING")
        self.assertEqual(payload["mower"]["batteryPercent"], 71)
        self.assertEqual(read_cycle.call_count, 2)
        fallback_call = read_cycle.call_args_list[1]
        self.assertEqual(
            fallback_call.kwargs["environment"]["SSV53_DYNAMIC_CONFIG_ENABLED"],
            "false",
        )
        self.assertEqual(fallback_call.kwargs["source"], "platzwart-status-display-only")

    def test_unrelated_read_error_is_not_hidden_by_display_fallback(self) -> None:
        with patch(
            "platzwart_console.run_read_only_cycle",
            side_effect=RuntimeError("Husqvarna response malformed"),
        ):
            with self.assertRaisesRegex(RuntimeError, "Husqvarna response malformed"):
                live_status(ENV, NOW)

    def test_unavailable_payload_is_always_displayable_and_never_controllable(self) -> None:
        payload = unavailable_live_status(NOW)

        self.assertFalse(payload["controlsAvailable"])
        self.assertFalse(payload["deviceControlsAvailable"])
        self.assertEqual(payload["dataQuality"]["code"], "DISPLAY_UNAVAILABLE")
        self.assertFalse(payload["occupancy"]["available"])
        self.assertFalse(payload["irrigation"]["safety"]["available"])
        self.assertFalse(payload["mower"]["telemetryFresh"])
        self.assertIsNone(payload["mower"]["statusAgeSeconds"])
        self.assertEqual(payload["occupancy"]["upcoming"], [])

    def test_match_in_merged_block_gets_nominal_display_times(self) -> None:
        block = {
            "source": "match+training",
            "title": "Training C; Spiel Ü40",
            "details": {
                "items": [
                    {
                        "source": "training",
                        "title": "Training C",
                        "details": {
                            "nominal_start": "2026-08-21T17:00:00+02:00",
                            "nominal_end": "2026-08-21T18:30:00+02:00",
                        },
                    },
                    {
                        "source": "match",
                        "title": "Spiel Ü40",
                        "details": {
                            "uid": "dfb-031C84AB5K000000VS5489BUVUR5FS5A@ssv53.de"
                        },
                    },
                ]
            },
        }
        matches = {
            "dfb:031C84AB5K000000VS5489BUVUR5FS5A": {
                "kickoff": "2026-08-21T19:00:00+02:00",
                "end": "2026-08-21T20:45:00+02:00",
                "team": "Schönwalder SV (Ü40)",
                "teamCategory": "Ü40",
                "matchType": "PO",
            }
        }

        enriched = _enrich_display_block(block, matches)

        self.assertIsNotNone(enriched)
        items = enriched["details"]["items"]
        self.assertEqual(len(items), 2)
        self.assertEqual(
            items[0]["details"]["nominal_start"],
            "2026-08-21T17:00:00+02:00",
        )
        self.assertEqual(
            items[1]["details"]["kickoff"],
            "2026-08-21T19:00:00+02:00",
        )
        self.assertEqual(
            items[1]["details"]["match_end"],
            "2026-08-21T20:45:00+02:00",
        )

    def test_restart_battery_threshold_is_bounded(self) -> None:
        self.assertEqual(_restart_battery_percent({}), 90)
        self.assertEqual(_restart_battery_percent({"MOWER_RESTART_BATTERY_PERCENT": "invalid"}), 90)
        self.assertEqual(_restart_battery_percent({"MOWER_RESTART_BATTERY_PERCENT": "55"}), 60)

    def test_paused_state_never_leaks_not_applicable_to_dashboard(self) -> None:
        mower = {
            "activity": "NOT_APPLICABLE",
            "state": "PAUSED",
            "inactive_reason": "NONE",
            "error_code": 0,
        }

        self.assertEqual(_mower_display_activity(mower, AutomationState()), "PAUSED")
        self.assertEqual(_mower_display_label(mower, AutomationState()), "Pausiert")
        self.assertFalse(_mower_error_active(mower))
        self.assertIsNone(_mower_error_message(mower))

    def test_satellite_search_and_position_error_are_distinct(self) -> None:
        searching = {
            "activity": "MOWING",
            # Diese reale 580-EPOS-Kombination trat nach dem Deployment auf:
            # SEARCHING_FOR_SATELLITES muss auch RESTRICTED überstimmen.
            "state": "RESTRICTED",
            "inactive_reason": "SEARCHING_FOR_SATELLITES",
            "error_code": 0,
        }
        position_error = {
            "activity": "NOT_APPLICABLE",
            "state": "ERROR",
            "inactive_reason": "NONE",
            "error_code": 93,
        }

        self.assertEqual(
            _mower_display_label(searching, AutomationState()),
            "Sucht Satellitensignal",
        )
        self.assertFalse(_mower_error_active(searching))
        self.assertEqual(
            _mower_display_label(position_error, AutomationState()),
            "Keine genaue Satellitenposition",
        )
        self.assertTrue(_mower_error_active(position_error))
        self.assertEqual(
            _mower_error_message(position_error),
            "Keine genaue Satellitenposition",
        )

    def test_non_error_followup_state_does_not_show_retained_error_as_active(self) -> None:
        mower = {
            "activity": "NOT_APPLICABLE",
            "state": "PAUSED",
            "inactive_reason": "NONE",
            "error_code": 93,
        }

        self.assertEqual(_mower_display_label(mower, AutomationState()), "Pausiert")
        self.assertFalse(_mower_error_active(mower))
        self.assertEqual(
            _mower_error_message(mower),
            "Keine genaue Satellitenposition",
        )

    def test_live_status_exposes_next_start_and_restart_threshold(self) -> None:
        live_cycle = result(activity="CHARGING", battery=70)
        live_cycle.details["mower"]["target_work_area"]["progress"] = 40
        live_cycle.details["current_plan"]["safe_mowing_windows"] = [
            {
                "start": NOW.isoformat(),
                "end": (NOW + timedelta(hours=2)).isoformat(),
                "command_deadline": (NOW + timedelta(minutes=110)).isoformat(),
                "minimum_mowing_minutes": 30,
            }
        ]
        live_cycle.details["current_plan"]["upcoming_blocks"] = [
            {
                "start": (NOW + timedelta(hours=1)).isoformat(),
                "end": (NOW + timedelta(hours=2)).isoformat(),
                "source": "training",
                "title": "Training A",
                "details": {},
            }
        ]
        store = InMemoryStateStore()
        environment = {**ENV, "MOWER_RESTART_BATTERY_PERCENT": "92"}
        with patch("platzwart_console.run_read_only_cycle", return_value=live_cycle), patch(
            "platzwart_console.AzureTableStateStore.from_environment",
            return_value=store,
        ), patch(
            "platzwart_console._clubhouse_events",
            return_value={"available": True, "events": [], "message": None},
        ), patch(
            "platzwart_console._dashboard_statistics",
            return_value={"available": True, "completedAreaCycles7d": 3},
        ):
            payload = live_status(environment, NOW)
        self.assertEqual(payload["mower"]["restartBatteryPercent"], 92)
        self.assertEqual(payload["mower"]["model"], "Husqvarna Automower 580 EPOS")
        self.assertEqual(payload["mower"]["displayLabel"], "Lädt")
        self.assertFalse(payload["mower"]["errorActive"])
        self.assertIsNone(payload["mower"]["errorMessage"])
        self.assertEqual(len(payload["occupancy"]["safeWindows"]), 1)
        self.assertEqual(payload["occupancy"]["upcoming"][0]["title"], "Training A")
        self.assertEqual(payload["statistics"]["completedAreaCycles7d"], 3)
        self.assertEqual(payload["statistics"]["mownAreaEquivalents7d"], 3.4)
        self.assertNotIn("totalCuttingSeconds", payload["statistics"])
        self.assertNotIn("chargingCycles", payload["statistics"])

    def test_live_status_projects_verified_manual_irrigation_intent(self) -> None:
        manual_plan = zones()
        for zone in manual_plan:
            zone["operator_manual"] = True
        canonical = json.dumps(
            manual_plan, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        )
        store = InMemoryStateStore(AutomationState(
            irrigation_phase="RUNNING",
            irrigation_plan_id=hashlib.sha256(canonical.encode("utf-8")).hexdigest(),
            irrigation_plan_json=canonical,
        ))
        live_cycle = result(
            activity="PARKED_IN_CS", active_ids=[RELAYS[0]], clear=False,
        )
        with patch("platzwart_console.run_read_only_cycle", return_value=live_cycle), patch(
            "platzwart_console.AzureTableStateStore.from_environment", return_value=store,
        ), patch("platzwart_console._clubhouse_events", return_value={}), patch(
            "platzwart_console._dashboard_statistics", return_value={}
        ), patch("platzwart_console._dashboard_irrigation_statistics", return_value={}):
            payload = live_status(FULL_DEVICE_CONTROL_ENV, NOW)

        self.assertEqual(payload["irrigation"]["intent"], {
            "source": "MANUAL_OPERATOR",
            "verified": True,
            "controllerManaged": True,
            "automaticWindowApplies": False,
        })

    def test_live_status_exposes_freshness_and_only_arms_fully_enabled_device_controls(self) -> None:
        live_cycle = result(activity="PARKED_IN_CS", battery=100)
        store = InMemoryStateStore()
        with patch("platzwart_console.run_read_only_cycle", return_value=live_cycle), patch(
            "platzwart_console.AzureTableStateStore.from_environment",
            return_value=store,
        ), patch("platzwart_console._clubhouse_events", return_value={}), patch(
            "platzwart_console._dashboard_statistics", return_value={}
        ), patch("platzwart_console._dashboard_irrigation_statistics", return_value={}):
            armed = live_status(FULL_DEVICE_CONTROL_ENV, NOW)
            dry_run = live_status(
                {**FULL_DEVICE_CONTROL_ENV, "CONTROL_MODE": "DRY_RUN"}, NOW
            )
            missing_gate = live_status(
                {**FULL_DEVICE_CONTROL_ENV, "ENABLE_IRRIGATION_COMMANDS": "false"},
                NOW,
            )

        self.assertTrue(armed["controlsAvailable"])
        self.assertTrue(armed["deviceControlsAvailable"])
        self.assertTrue(armed["mower"]["telemetryFresh"])
        self.assertEqual(armed["mower"]["statusAgeSeconds"], 0)
        self.assertFalse(dry_run["deviceControlsAvailable"])
        self.assertFalse(missing_gate["deviceControlsAvailable"])

    def test_live_status_exposes_independent_automatic_and_protective_parking_flags(self) -> None:
        live_cycle = result(activity="PARKED_IN_CS", battery=100)
        store = InMemoryStateStore()
        base_kwargs = dict(
            return_value=live_cycle,
        )
        with patch("platzwart_console.run_read_only_cycle", **base_kwargs), patch(
            "platzwart_console.AzureTableStateStore.from_environment", return_value=store
        ), patch("platzwart_console._clubhouse_events", return_value={}), patch(
            "platzwart_console._dashboard_statistics", return_value={}
        ), patch("platzwart_console._dashboard_irrigation_statistics", return_value={}):
            full = live_status(FULL_DEVICE_CONTROL_ENV, NOW)
            dry = live_status({**FULL_DEVICE_CONTROL_ENV, "CONTROL_MODE": "DRY_RUN"}, NOW)
            failsafe_without_water = live_status({
                **FULL_DEVICE_CONTROL_ENV,
                "ENABLE_IRRIGATION_COMMANDS": "false",
            }, NOW)
            operator_settings = RuntimeSettings.from_mapping({
                **FULL_DEVICE_CONTROL_ENV,
                "CONTROL_MODE": "OPERATOR_ONLY",
                "ENABLE_START_COMMANDS": "false",
                "ENABLE_IRRIGATION_COMMANDS": "false",
                "FULL_MOWER_CONFIRMATION": "",
                "FULL_FAILSAFE_CONFIRMATION": "",
                "ENABLE_OPERATOR_SAFETY_GUARD": "true",
                "OPERATOR_CONTROL_CONFIRMATION": "SSV53-OPERATOR-PARK-HEIGHT-V1",
            })
        self.assertEqual(full["protection"], {
            "automaticStartEnabled": True,
            "protectiveParkingEnabled": True,
        })
        self.assertEqual(failsafe_without_water["protection"], {
            "automaticStartEnabled": False,
            "protectiveParkingEnabled": True,
        })
        self.assertEqual(dry["protection"], {
            "automaticStartEnabled": False,
            "protectiveParkingEnabled": False,
        })
        self.assertEqual(_protection_payload(operator_settings), {
            "automaticStartEnabled": False,
            "protectiveParkingEnabled": True,
        })

    def test_stale_connected_mower_is_visible_with_telemetry_blocker_but_open_stop_gate(self) -> None:
        live_cycle = result(activity="PARKED_IN_CS", battery=100)
        live_cycle.details["mower"]["status_timestamp_ms"] = int(
            (NOW - timedelta(seconds=181)).timestamp() * 1000
        )
        store = InMemoryStateStore()
        with patch("platzwart_console.run_read_only_cycle", return_value=live_cycle), patch(
            "platzwart_console.AzureTableStateStore.from_environment",
            return_value=store,
        ), patch("platzwart_console._clubhouse_events", return_value={}), patch(
            "platzwart_console._dashboard_statistics", return_value={}
        ), patch("platzwart_console._dashboard_irrigation_statistics", return_value={}):
            payload = live_status(FULL_DEVICE_CONTROL_ENV, NOW)

        self.assertTrue(payload["mower"]["connected"])
        self.assertFalse(payload["mower"]["telemetryFresh"])
        self.assertEqual(payload["mower"]["statusAgeSeconds"], 181)
        self.assertTrue(payload["deviceControlsAvailable"])
        self.assertIn(
            "MOWER_TELEMETRY",
            {item["code"] for item in payload["coordination"]["blockers"]},
        )

    def test_restricted_force_park_station_report_keeps_station_activity(self) -> None:
        for activity, expected_label in (
            ("PARKED_IN_CS", "In der Station"),
            ("CHARGING", "Lädt"),
        ):
            with self.subTest(activity=activity):
                live_cycle = result(activity=activity, battery=100)
                live_cycle.details["mower"].update(
                    {
                        "state": "RESTRICTED",
                        "restricted_reason": "WEEK_SCHEDULE",
                        "override_action": "FORCE_PARK",
                    }
                )
                with patch("platzwart_console.run_read_only_cycle", return_value=live_cycle), patch(
                    "platzwart_console.AzureTableStateStore.from_environment",
                    return_value=InMemoryStateStore(),
                ), patch("platzwart_console._clubhouse_events", return_value={}), patch(
                    "platzwart_console._dashboard_statistics", return_value={}
                ), patch("platzwart_console._dashboard_irrigation_statistics", return_value={}):
                    payload = live_status(FULL_DEVICE_CONTROL_ENV, NOW)

                self.assertEqual(payload["mower"]["displayActivity"], activity)
                self.assertEqual(payload["mower"]["displayLabel"], expected_label)

    def test_live_status_exposes_all_seven_irrigation_zone_statistics(self) -> None:
        live_cycle = result(activity="PARKED_IN_CS", battery=100)
        store = InMemoryStateStore()
        with patch("platzwart_console.run_read_only_cycle", return_value=live_cycle), patch(
            "platzwart_console.AzureTableStateStore.from_environment",
            return_value=store,
        ), patch(
            "platzwart_console._clubhouse_events",
            return_value={"available": True, "events": [], "message": None},
        ), patch(
            "platzwart_console._dashboard_statistics",
            return_value={"available": True},
        ), patch(
            "platzwart_console._dashboard_irrigation_statistics",
            return_value={
                "available": True,
                "wateringMinutes7d": 12,
                "zoneMinutes7d": [
                    {"relayId": RELAYS[0], "minutes": 12},
                ],
            },
        ):
            payload = live_status(ENV, NOW)

        statistics = payload["irrigationStatistics"]
        self.assertEqual(statistics["wateringMinutes7d"], 12)
        self.assertEqual(len(statistics["zoneMinutes7d"]), 7)
        self.assertEqual(statistics["zoneMinutes7d"][0]["minutes"], 12)
        self.assertEqual(statistics["zoneMinutes7d"][1]["minutes"], 0)
        self.assertTrue(all(item["name"] for item in statistics["zoneMinutes7d"]))

    def test_epos_home_search_is_exposed_as_satellite_search(self) -> None:
        live_cycle = result(activity="NOT_APPLICABLE")
        live_cycle.details["mower"].update(
            {
                "state": "IN_OPERATION",
                "mode": "HOME",
                "override_action": "FORCE_PARK",
            }
        )
        store = InMemoryStateStore(
            AutomationState(
                parked_by_automation=True,
                automation_park_source="hydrawise_unconfirmed",
                automation_restart_allowed=True,
            )
        )
        with patch("platzwart_console.run_read_only_cycle", return_value=live_cycle), patch(
            "platzwart_console.AzureTableStateStore.from_environment",
            return_value=store,
        ), patch(
            "platzwart_console._clubhouse_events",
            return_value={"available": True, "events": [], "message": None},
        ), patch(
            "platzwart_console._dashboard_statistics",
            return_value={"available": True},
        ):
            payload = live_status(ENV, NOW)
        self.assertEqual(
            payload["mower"]["displayActivity"],
            "SEARCHING_FOR_POSITION",
        )
        self.assertTrue(payload["mower"]["connected"])

    def test_epos_inactive_reason_overrides_coarse_mowing_activity(self) -> None:
        live_cycle = result(activity="MOWING")
        live_cycle.details["mower"]["inactive_reason"] = "SEARCHING_FOR_SATELLITES"
        store = InMemoryStateStore(AutomationState(continuous_mowing_owned=True))
        with patch("platzwart_console.run_read_only_cycle", return_value=live_cycle), patch(
            "platzwart_console.AzureTableStateStore.from_environment",
            return_value=store,
        ), patch(
            "platzwart_console._clubhouse_events",
            return_value={"available": True, "events": [], "message": None},
        ), patch(
            "platzwart_console._dashboard_statistics",
            return_value={"available": True},
        ):
            payload = live_status(ENV, NOW)
        self.assertEqual(payload["mower"]["activity"], "MOWING")
        self.assertEqual(payload["mower"]["inactiveReason"], "SEARCHING_FOR_SATELLITES")
        self.assertEqual(payload["mower"]["displayActivity"], "SEARCHING_FOR_POSITION")

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
    def test_non_failsafe_modes_reject_armed_flags_before_any_request_state_read_or_write(self) -> None:
        for control_mode in ("OFF", "DRY_RUN", "PARK_ONLY", "FULL_MOWER"):
            with self.subTest(control_mode=control_mode):
                environment = {
                    **FULL_DEVICE_CONTROL_ENV,
                    "CONTROL_MODE": control_mode,
                }
                with patch(
                    "platzwart_console.AzureTableStateStore.from_environment"
                ) as state_factory, patch(
                    "platzwart_console.ConsoleTableStore.from_environment"
                ) as audit_factory, self.assertRaises(PlatzwartError) as error:
                    request_action(
                        "START_MOWING",
                        f"{control_mode.lower()}-rejected",
                        "START_MOWING",
                        environment,
                        NOW,
                    )

                self.assertEqual(error.exception.code, "AUTOMATION_LOCKED")
                state_factory.assert_not_called()
                audit_factory.assert_not_called()

    def test_manual_irrigation_at_any_hour_queues_request(self) -> None:
        for action, moment, extra in (
            ("START_IRRIGATION", NOW.replace(hour=1, minute=29), {}),
            ("START_IRRIGATION", NOW.replace(hour=12), {}),
            ("START_IRRIGATION_ZONE", NOW.replace(hour=5, minute=40), {"zone": 3, "run_seconds": 1200}),
        ):
            with self.subTest(action=action, moment=moment):
                store = InMemoryStateStore()
                with patch("platzwart_console.AzureTableStateStore.from_environment", return_value=store), patch("platzwart_console.RuntimeSettings.from_mapping", return_value=settings()), patch("platzwart_console.ConsoleTableStore.from_environment"), patch("platzwart_console.run_read_only_cycle") as read:
                    accepted = request_action(action, f"any-hour-{action}", action, ENV, moment, **extra)
                self.assertEqual(accepted["status"], "PENDING")
                self.assertEqual(store.load().operator_request_action, action)
                read.assert_not_called()

    def test_manual_zone_request_keeps_duration_and_remains_pending(self) -> None:
        store = InMemoryStateStore()
        with patch("platzwart_console.AzureTableStateStore.from_environment", return_value=store), patch("platzwart_console.RuntimeSettings.from_mapping", return_value=settings()), patch("platzwart_console.ConsoleTableStore.from_environment"), patch("platzwart_console.run_read_only_cycle") as read:
            accepted = request_action("START_IRRIGATION_ZONE", "fits-window", "START_IRRIGATION_ZONE", ENV, NOW, zone=3, run_seconds=1500)
        self.assertEqual(accepted["status"], "PENDING")
        self.assertEqual(store.load().operator_request_run_seconds, 1500)
        read.assert_not_called()

    def test_custom_irrigation_window_error_is_actionable(self) -> None:
        with self.assertRaises(PlatzwartError) as error:
            request_action("CUSTOMIZE_NEXT_IRRIGATION", "custom-outside", "CUSTOMIZE_NEXT_IRRIGATION", ENV, NOW, irrigation_schedule={
                "desiredStart": NOW.replace(hour=5, minute=45).isoformat(),
                "zones": [{"zone": index, "runSeconds": 1200, "selected": True} for index in range(1, 8)],
            })
        self.assertEqual(error.exception.code, "IRRIGATION_WINDOW_CANNOT_FIT")

    def run_cycle(self, initial: AutomationState, live_result, **senders):
        store = InMemoryStateStore(initial)
        cycle = run_full_failsafe_cycle(
            now_utc=NOW,
            command_clock=lambda: NOW,
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

    def test_schedule_request_is_validated_and_persisted_without_direct_command(self) -> None:
        store = InMemoryStateStore()

        class AuditStore:
            def audit(self, *_args, **_kwargs):
                return None

        payload = {
            "pauseUntil": (NOW + timedelta(days=3)).isoformat(),
        }
        with patch(
            "platzwart_console.AzureTableStateStore.from_environment",
            return_value=store,
        ), patch(
            "platzwart_console.ConsoleTableStore.from_environment",
            return_value=AuditStore(),
        ), patch(
            "platzwart_console.RuntimeSettings.from_mapping",
            return_value=settings(),
        ):
            accepted = request_action(
                "PAUSE_IRRIGATION_UNTIL",
                "pause-1",
                "PAUSE_IRRIGATION_UNTIL",
                ENV,
                NOW,
                irrigation_schedule=payload,
            )
        self.assertEqual(accepted["status"], "PENDING")
        saved = store.load()
        self.assertEqual(saved.operator_request_action, "PAUSE_IRRIGATION_UNTIL")
        self.assertEqual(
            json.loads(saved.operator_request_irrigation_schedule_json or "{}"),
            payload,
        )

    def test_schedule_change_is_rejected_during_irrigation_sequence(self) -> None:
        store = InMemoryStateStore(AutomationState(irrigation_phase="RUNNING"))

        class AuditStore:
            def audit(self, *_args, **_kwargs):
                return None

        with patch(
            "platzwart_console.AzureTableStateStore.from_environment",
            return_value=store,
        ), patch(
            "platzwart_console.ConsoleTableStore.from_environment",
            return_value=AuditStore(),
        ), patch(
            "platzwart_console.RuntimeSettings.from_mapping",
            return_value=settings(),
        ), self.assertRaises(PlatzwartError) as context:
            request_action(
                "SKIP_NEXT_IRRIGATION",
                "skip-active",
                "SKIP_NEXT_IRRIGATION",
                ENV,
                NOW,
            )
        self.assertEqual(context.exception.code, "IRRIGATION_SEQUENCE_ACTIVE")

    def test_mower_start_is_rejected_until_schedule_change_is_stable(self) -> None:
        class AuditStore:
            def audit(self, *_args, **_kwargs):
                return None

        for status in (
            "PARKING",
            "VERIFYING",
            "APPLYING",
            "CONFIRMING",
            "EXECUTING",
            "POST_RUN",
        ):
            with self.subTest(status=status):
                store = InMemoryStateStore(
                    AutomationState(
                        irrigation_schedule_override_json=json.dumps(
                            {"kind": "SKIP_NEXT", "status": status}
                        )
                    )
                )
                with patch(
                    "platzwart_console.AzureTableStateStore.from_environment",
                    return_value=store,
                ), patch(
                    "platzwart_console.ConsoleTableStore.from_environment",
                    return_value=AuditStore(),
                ), patch(
                    "platzwart_console.RuntimeSettings.from_mapping",
                    return_value=settings(),
                ), self.assertRaises(PlatzwartError) as context:
                    request_action(
                        "START_MOWING",
                        f"start-{status.lower()}",
                        "START_MOWING",
                        ENV,
                        NOW,
                    )
                self.assertEqual(
                    context.exception.code,
                    "IRRIGATION_SCHEDULE_CHANGE_PENDING",
                )
                self.assertIsNone(store.load().operator_request_id)

    def test_stable_schedule_change_allows_a_fresh_mower_start(self) -> None:
        store = InMemoryStateStore(
            AutomationState(
                irrigation_schedule_override_json=json.dumps(
                    {"kind": "SKIP_NEXT", "status": "ACTIVE"}
                )
            )
        )

        class AuditStore:
            def audit(self, *_args, **_kwargs):
                return None

        with patch(
            "platzwart_console.AzureTableStateStore.from_environment",
            return_value=store,
        ), patch(
            "platzwart_console.ConsoleTableStore.from_environment",
            return_value=AuditStore(),
        ), patch(
            "platzwart_console.RuntimeSettings.from_mapping",
            return_value=settings(),
        ):
            accepted = request_action(
                "START_MOWING",
                "start-after-skip",
                "START_MOWING",
                ENV,
                NOW,
            )
        self.assertEqual(accepted["status"], "PENDING")
        self.assertEqual(store.load().operator_request_action, "START_MOWING")

    def test_schedule_change_does_not_block_an_operator_park_request(self) -> None:
        store = InMemoryStateStore(
            AutomationState(
                irrigation_schedule_override_json=json.dumps(
                    {"kind": "SKIP_NEXT", "status": "CONFIRMING"}
                )
            )
        )

        class AuditStore:
            def audit(self, *_args, **_kwargs):
                return None

        with patch(
            "platzwart_console.AzureTableStateStore.from_environment",
            return_value=store,
        ), patch(
            "platzwart_console.ConsoleTableStore.from_environment",
            return_value=AuditStore(),
        ), patch(
            "platzwart_console.RuntimeSettings.from_mapping",
            return_value=settings(),
        ):
            accepted = request_action(
                "PARK_MOWER",
                "park-during-schedule-change",
                "PARK_MOWER",
                ENV,
                NOW,
            )
        self.assertEqual(accepted["status"], "PENDING")
        self.assertEqual(store.load().operator_request_action, "PARK_MOWER")

    def test_invalid_schedule_state_fails_closed_for_mower_start(self) -> None:
        store = InMemoryStateStore(
            AutomationState(irrigation_schedule_override_json="not-json")
        )

        class AuditStore:
            def audit(self, *_args, **_kwargs):
                return None

        with patch(
            "platzwart_console.AzureTableStateStore.from_environment",
            return_value=store,
        ), patch(
            "platzwart_console.ConsoleTableStore.from_environment",
            return_value=AuditStore(),
        ), patch(
            "platzwart_console.RuntimeSettings.from_mapping",
            return_value=settings(),
        ), self.assertRaises(PlatzwartError) as context:
            request_action(
                "START_MOWING",
                "start-with-invalid-schedule-state",
                "START_MOWING",
                ENV,
                NOW,
            )
        self.assertEqual(
            context.exception.code,
            "IRRIGATION_SCHEDULE_STATE_INVALID",
        )
        self.assertIsNone(store.load().operator_request_id)

    def test_cutting_height_request_is_validated_and_persisted_in_mm(self) -> None:
        for invalid in (19, 61):
            with self.subTest(invalid=invalid), self.assertRaises(PlatzwartError) as context:
                request_action(
                    "SET_CUTTING_HEIGHT",
                    "height-invalid",
                    "SET_CUTTING_HEIGHT",
                    ENV,
                    NOW,
                    cutting_height_mm=invalid,
                )
            self.assertEqual(context.exception.code, "CUTTING_HEIGHT_INVALID")

        store = InMemoryStateStore()

        class AuditStore:
            def audit(self, *_args, **_kwargs):
                return None

        with patch(
            "platzwart_console.AzureTableStateStore.from_environment",
            return_value=store,
        ), patch(
            "platzwart_console.ConsoleTableStore.from_environment",
            return_value=AuditStore(),
        ), patch(
            "platzwart_console.RuntimeSettings.from_mapping",
            return_value=settings(),
        ):
            accepted = request_action(
                "SET_CUTTING_HEIGHT",
                "height-valid",
                "SET_CUTTING_HEIGHT",
                ENV,
                NOW,
                cutting_height_mm=30,
            )
        self.assertTrue(accepted["accepted"])
        self.assertEqual(store.load().operator_request_cutting_height_mm, 30)
        self.assertEqual(store.load().operator_request_action, "SET_CUTTING_HEIGHT")

    def test_blade_usage_reset_requires_exact_confirmation_and_is_queued(self) -> None:
        with self.assertRaises(PlatzwartError) as context:
            request_action(
                "RESET_BLADE_USAGE", "blade-invalid", "RESET", ENV, NOW
            )
        self.assertEqual(context.exception.code, "CONFIRMATION_INVALID")
        store = InMemoryStateStore()

        class AuditStore:
            def audit(self, *_args, **_kwargs):
                return None

        with patch(
            "platzwart_console.AzureTableStateStore.from_environment",
            return_value=store,
        ), patch(
            "platzwart_console.ConsoleTableStore.from_environment",
            return_value=AuditStore(),
        ), patch(
            "platzwart_console.RuntimeSettings.from_mapping",
            return_value=settings(),
        ):
            accepted = request_action(
                "RESET_BLADE_USAGE",
                "blade-valid",
                "RESET_BLADE_USAGE",
                ENV,
                NOW,
            )
        self.assertTrue(accepted["accepted"])
        self.assertEqual(store.load().operator_request_action, "RESET_BLADE_USAGE")

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

    def test_occupancy_override_requires_distinct_confirmation(self) -> None:
        store = InMemoryStateStore()

        class AuditStore:
            def audit(self, *_args, **_kwargs):
                return None

        with patch(
            "platzwart_console.AzureTableStateStore.from_environment",
            return_value=store,
        ), patch(
            "platzwart_console.ConsoleTableStore.from_environment",
            return_value=AuditStore(),
        ), patch(
            "platzwart_console.RuntimeSettings.from_mapping",
            return_value=settings(),
        ):
            with self.assertRaises(PlatzwartError) as context:
                request_action(
                    "START_MOWING",
                    "occupied-start-wrong-confirmation",
                    "START_MOWING",
                    ENV,
                    NOW,
                    occupancy_override_key="start|end|training",
                )
            self.assertEqual(context.exception.code, "CONFIRMATION_INVALID")
            accepted = request_action(
                "START_MOWING",
                "occupied-start-confirmed",
                "START_MOWING_OCCUPANCY_OVERRIDE",
                ENV,
                NOW,
                occupancy_override_key="start|end|training",
            )
        self.assertTrue(accepted["accepted"])
        self.assertEqual(
            store.load().operator_request_occupancy_override_key,
            "start|end|training",
        )

    def test_confirmed_current_training_can_start_but_only_for_that_block(self) -> None:
        live = result(command="PARK", block_source="training", activity="CHARGING")
        block = {
            "start": (NOW - timedelta(minutes=30)).isoformat(),
            "end": (NOW + timedelta(hours=2)).isoformat(),
            "title": "Training A",
            "source": "training",
        }
        live.details["current_plan"]["blocked_now"] = block
        live.details["current_plan"]["parking_block"] = block
        key = "|".join((block["start"], block["end"], block["source"]))
        sent = []
        initial = self.pending(
            "START_MOWING",
            operator_request_occupancy_override_key=key,
            parked_by_automation=True,
            automation_park_source="training",
            automation_restart_allowed=True,
            park_confirmed_utc=(NOW - timedelta(minutes=5)).isoformat(),
            hydrawise_clear_since_utc=(NOW - timedelta(minutes=121)).isoformat(),
            last_hydrawise_success_utc=(NOW - timedelta(minutes=1)).isoformat(),
        )
        cycle, state = self.run_cycle(
            initial,
            live,
            start_sender=lambda *_args: sent.append(_args) or {"ok": True},
        )
        self.assertEqual(len(sent), 1)
        self.assertEqual(cycle.decision_code, "CONTINUOUS_MOWING_START_SENT")
        self.assertTrue(cycle.command_sent)
        self.assertTrue(state.continuous_mowing_owned)
        self.assertEqual(state.operator_occupancy_override_key, key)
        self.assertEqual(
            state.operator_occupancy_override_until_utc,
            (NOW + timedelta(hours=2)).isoformat(),
        )
        self.assertEqual(state.operator_request_status, "COMPLETED")

    def test_occupancy_override_never_overrides_irrigation_or_changed_block(self) -> None:
        for source, changed_key in (("training+irrigation", False), ("training", True)):
            with self.subTest(source=source, changed_key=changed_key):
                live = result(command="PARK", block_source=source, activity="CHARGING")
                block = {
                    "start": (NOW - timedelta(minutes=30)).isoformat(),
                    "end": (NOW + timedelta(hours=2)).isoformat(),
                    "title": "Training A",
                    "source": source,
                }
                live.details["current_plan"]["blocked_now"] = block
                live.details["current_plan"]["parking_block"] = block
                actual_key = "|".join((block["start"], block["end"], block["source"]))
                requested_key = "anderer|block|training" if changed_key else actual_key
                calls = []
                initial = self.pending(
                    "START_MOWING",
                    operator_request_occupancy_override_key=requested_key,
                    hydrawise_clear_since_utc=(NOW - timedelta(minutes=121)).isoformat(),
                    last_hydrawise_success_utc=(NOW - timedelta(minutes=1)).isoformat(),
                )
                cycle, state = self.run_cycle(
                    initial,
                    live,
                    start_sender=lambda *_args: calls.append(_args) or {"ok": True},
                )
                self.assertEqual(calls, [])
                self.assertEqual(cycle.decision_code, "OPERATOR_OCCUPANCY_OVERRIDE_REJECTED")
                self.assertEqual(state.operator_request_status, "REJECTED")

    def test_active_occupancy_override_is_not_reparked_but_a_new_block_is(self) -> None:
        block = {
            "start": (NOW - timedelta(minutes=30)).isoformat(),
            "end": (NOW + timedelta(hours=2)).isoformat(),
            "title": "Training A",
            "source": "training",
        }
        key = "|".join((block["start"], block["end"], block["source"]))
        initial = AutomationState(
            continuous_mowing_owned=True,
            continuous_mowing_work_area_id=849199,
            continuous_mowing_window_end_utc=block["end"],
            operator_occupancy_override_key=key,
            operator_occupancy_override_until_utc=block["end"],
            hydrawise_clear_since_utc=(NOW - timedelta(minutes=121)).isoformat(),
            last_hydrawise_success_utc=(NOW - timedelta(minutes=1)).isoformat(),
        )
        live = result(command="PARK", block_source="training", activity="MOWING")
        live.details["current_plan"]["blocked_now"] = block
        live.details["current_plan"]["parking_block"] = block
        park_calls = []
        cycle, state = self.run_cycle(
            initial,
            live,
            park_sender=lambda *_args: park_calls.append(_args) or {"ok": True},
        )
        self.assertEqual(park_calls, [])
        self.assertEqual(cycle.decision_code, "CONTINUOUS_MOWING_ACTIVE")
        self.assertEqual(state.operator_occupancy_override_key, key)

        changed = dict(block)
        changed["start"] = (NOW - timedelta(minutes=5)).isoformat()
        changed["end"] = (NOW + timedelta(hours=3)).isoformat()
        changed["title"] = "Anderer Termin"
        live_changed = result(command="PARK", block_source="training", activity="MOWING")
        live_changed.details["current_plan"]["blocked_now"] = changed
        live_changed.details["current_plan"]["parking_block"] = changed
        park_calls = []
        cycle, state = self.run_cycle(
            initial,
            live_changed,
            park_sender=lambda *_args: park_calls.append(_args) or {"ok": True},
        )
        self.assertEqual(len(park_calls), 1)
        self.assertEqual(cycle.decision_code, "PARK_COMMAND_SENT")
        self.assertIsNone(state.operator_occupancy_override_key)

    def test_confirmed_occupancy_override_still_waits_for_hydrawise_release(self) -> None:
        live = result(
            command="PARK",
            block_source="training",
            activity="CHARGING",
            clear=False,
        )
        block = {
            "start": (NOW - timedelta(minutes=30)).isoformat(),
            "end": (NOW + timedelta(hours=2)).isoformat(),
            "title": "Training A",
            "source": "training",
        }
        live.details["current_plan"]["blocked_now"] = block
        live.details["current_plan"]["parking_block"] = block
        key = "|".join((block["start"], block["end"], block["source"]))
        start_calls = []
        cycle, state = self.run_cycle(
            self.pending(
                "START_MOWING",
                operator_request_occupancy_override_key=key,
            ),
            live,
            start_sender=lambda *_args: start_calls.append(_args) or {"ok": True},
        )
        self.assertEqual(start_calls, [])
        self.assertEqual(cycle.decision_code, "HYDRAWISE_CLEAR_CONFIRMATION_HOLD")
        self.assertEqual(state.operator_request_status, "PENDING")

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
            park_confirmed_observations=2,
            last_mower_activity="PARKED_IN_CS",
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
            park_confirmed_observations=2,
            last_mower_activity="PARKED_IN_CS",
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
