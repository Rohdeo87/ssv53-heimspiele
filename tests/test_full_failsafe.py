from __future__ import annotations

import json
import unittest
from copy import deepcopy
from datetime import datetime, timedelta, timezone

from mower.full_failsafe import (
    EXPIRED_IRRIGATION_PLAN_LEASE_FAILURE,
    PARTIAL_IRRIGATION_WINDOW_FAILURE,
    run_full_failsafe_cycle,
)
from mower.hydrawise import HydrawiseError
from mower.runtime import CycleResult, RuntimeSettings
from mower.safety import CommandIntent
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
            "FULL_FAILSAFE_CONFIRMATION": "SSV53-MOWER-HYDRAWISE-7-ZONES-150-MINUTES-ADAPTIVE-V1" if live else "LOCKED",
            "PARK_LOOKAHEAD_MINUTES": "10",
        }
    )


ENV = {
    "HUSQVARNA_CLIENT_ID": "client",
    "HUSQVARNA_CLIENT_SECRET": "secret",
    "HYDRAWISE_API_KEY": "key",
    "HYDRAWISE_CONTROLLER_ID": "controller",
    "HYDRAWISE_EXPECTED_ZONE_COUNT": "7",
    "HYDRAWISE_EXPECTED_RELAY_IDS": ",".join(str(value) for value in RELAYS),
    "HYDRAWISE_CLEAR_CONFIRMATION_MINUTES": "2",
    "HYDRAWISE_DATA_GAP_CONFIRMATION_MINUTES": "2",
    "POST_IRRIGATION_DRYING_MINUTES": "150",
    "MOWER_PARK_CONFIRMATION_CYCLES": "2",
    "MOWER_STATUS_MAX_AGE_SECONDS": "180",
    "MOWER_PARK_CONFIRMATION_MINUTES": "1",
    "MOWER_PARK_PROGRESS_GRACE_MINUTES": "3",
    "IRRIGATION_FAILSAFE_DOCK_LEAD_MINUTES": "40",
    "IRRIGATION_PLAN_LEASE_MINUTES": "3",
    "IRRIGATION_PLAN_CHANGE_CONFIRMATION_MINUTES": "2",
    "IRRIGATION_EARLY_STOP_TOLERANCE_SECONDS": "120",
    "MAX_AUTOMATIC_START_MINUTES": "720",
    "MOWER_CONTINUE_MIN_BATTERY_PERCENT": "60",
    "MOWER_RESTART_BATTERY_PERCENT": "90",
}


def zones(
    *,
    start_utc: datetime | None = None,
    relay_ids: list[int] | None = None,
) -> list[dict]:
    start = start_utc or NOW + timedelta(minutes=30)
    selected_relay_ids = RELAYS if relay_ids is None else relay_ids
    result = []
    for index, (relay_id, run_seconds) in enumerate(
        zip(selected_relay_ids, RUN_SECONDS, strict=True),
        start=1,
    ):
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
    mower_state: str = "IN_OPERATION",
    battery: int = 100,
    active_ids: list[int] | None = None,
    clear: bool = True,
    override_action: str | None = None,
    external_reason_id: int | None = 253053,
    window_end: datetime | None = None,
    irrigation_start: datetime | None = None,
    relay_ids: list[int] | None = None,
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
    observed_relay_ids = RELAYS if relay_ids is None else relay_ids
    zone_schedule = zones(
        start_utc=irrigation_start or NOW + timedelta(hours=13),
        relay_ids=observed_relay_ids,
    )
    zone_observations = [
        {
            **zone,
            "valid": True,
            "scheduled": True,
        }
        for zone in zone_schedule
    ]
    relay_set_valid = (
        len(observed_relay_ids) == len(set(observed_relay_ids))
        and set(observed_relay_ids) == set(RELAYS)
    )
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
                    "end": (window_end or NOW + timedelta(hours=12)).isoformat(),
                },
            },
            "hydrawise": {
                "status": "live (7 Zonen)",
                "error": None,
                "zones": zone_schedule,
                "zone_observations": zone_observations,
                "safety": {
                    "available": True,
                    "fresh": True,
                    "clear_now": clear,
                    "observed_at_utc": NOW.isoformat(),
                    "selected_zone_count": len(observed_relay_ids),
                    "observed_relay_ids": observed_relay_ids,
                    "expected_relay_ids": RELAYS,
                    "relay_set_valid": relay_set_valid,
                    "active_zone_count": len(active_ids),
                    "active_relay_ids": active_ids,
                    "reason": "frei" if clear else "läuft",
                },
            },
            "mower": {
                "mower_id": "mower-1",
                "model": "Husqvarna Automower 580 EPOS",
                "connected": True,
                "activity": activity,
                "state": mower_state,
                "override_action": (
                    override_action
                    if override_action is not None
                    else (
                        "FORCE_PARK"
                        if activity in {"PARKED_IN_CS", "CHARGING"}
                        else "NOT_ACTIVE"
                    )
                ),
                "external_reason_id": external_reason_id,
                "error_code": 0,
                "status_timestamp_ms": int(NOW.timestamp() * 1000),
                "battery_percent": battery,
                "target_work_area": {"id": 849199, "name": "Rasenfläche", "enabled": True, "cutting_height_percent": 12, "use_global_cutting_height": False},
            },
            "safety": {"read_only": True, "command_sent": False},
        },
    )


def suspended_result(*, activity: str = "PARKED_IN_CS") -> CycleResult:
    cycle = deepcopy(result(activity=activity))
    hydrawise = cycle.details["hydrawise"]
    observations = []
    for zone in zones(start_utc=NOW + timedelta(hours=13)):
        observations.append(
            {
                **zone,
                "valid": True,
                "scheduled": False,
                "running": False,
                "seconds_until": 0,
                "scheduled_start_utc": None,
                "scheduled_end_utc": None,
            }
        )
    hydrawise["zones"] = []
    hydrawise["zone_observations"] = observations
    hydrawise["safety"]["clear_now"] = True
    hydrawise["safety"]["active_zone_count"] = 0
    hydrawise["safety"]["active_relay_ids"] = []
    return cycle


def irrigation_state(*, phase: str, current: int | None = None) -> AutomationState:
    plan = zones()
    return AutomationState(
        parked_by_automation=True,
        automation_park_source="irrigation",
        automation_restart_allowed=True,
        park_command_sent_utc=(NOW - timedelta(minutes=10)).isoformat(),
        park_confirmed_utc=(NOW - timedelta(minutes=5)).isoformat(),
        park_confirmed_observations=2,
        last_mower_activity="PARKED_IN_CS",
        irrigation_phase=phase,
        irrigation_plan_id="plan",
        irrigation_plan_json=__import__("json").dumps(plan),
        irrigation_suspended_relay_ids_json=__import__("json").dumps(RELAYS),
        irrigation_suspension_until_utc=(
            (NOW + timedelta(hours=4)).isoformat()
            if phase not in {"PLANNED", "SUSPENDING", "FAILED"}
            else None
        ),
        irrigation_suspension_completed_utc=(
            (NOW - timedelta(minutes=1)).isoformat()
            if phase not in {"PLANNED", "SUSPENDING", "FAILED"}
            else None
        ),
        irrigation_completed_relay_ids_json="[]",
        irrigation_current_relay_id=current,
        irrigation_zone_start_reserved_utc=(NOW - timedelta(minutes=1)).isoformat() if current else None,
        irrigation_zone_started_utc=(
            (NOW - timedelta(minutes=5)).isoformat()
            if phase == "RUNNING" and current
            else None
        ),
    )


class FullFailsafeTests(unittest.TestCase):
    def _run(self, state: AutomationState, cycle: CycleResult, *, now: datetime = NOW, **senders):
        cycle = deepcopy(cycle)
        cycle.details["mower"]["status_timestamp_ms"] = int(now.timestamp() * 1000)
        store = InMemoryStateStore(state)
        output = run_full_failsafe_cycle(
            now_utc=now,
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
            stop_zone_sender=senders.get("stop_zone", lambda *_: {"message_type": "info"}),
            cutting_height_sender=senders.get("height", lambda *_: {"accepted": True}),
            blade_usage_reset_sender=senders.get("blade_reset", lambda *_: {"accepted": True}),
        )
        return output, store

    def _continue(self, store, cycle: CycleResult, *, now: datetime, **senders):
        cycle = deepcopy(cycle)
        cycle.details["mower"]["status_timestamp_ms"] = int(now.timestamp() * 1000)
        output = run_full_failsafe_cycle(
            now_utc=now,
            settings=settings(),
            environment=ENV,
            past_due=False,
            source="test",
            read_only_runner=lambda **_: cycle,
            state_store_factory=lambda _env: store,
            park_sender=senders.get("park", lambda *_: {"accepted": True}),
            start_sender=senders.get("start", lambda *_: {"accepted": True}),
            suspend_zone_sender=senders.get(
                "suspend", lambda *_: {"message_type": "info"}
            ),
            start_zone_sender=senders.get("zone", lambda *_: {"message_type": "info"}),
            stop_zone_sender=senders.get(
                "stop_zone", lambda *_: {"message_type": "info"}
            ),
        )
        return output, store

    def test_missing_relay_allowlist_fails_before_reads_or_commands(self) -> None:
        calls = {"read": [], "park": [], "start": [], "suspend": [], "zone": []}
        environment = dict(ENV)
        environment.pop("HYDRAWISE_EXPECTED_RELAY_IDS")
        with self.assertRaises(HydrawiseError):
            run_full_failsafe_cycle(
                now_utc=NOW,
                settings=settings(),
                environment=environment,
                past_due=False,
                source="test",
                read_only_runner=lambda **kwargs: calls["read"].append(kwargs),
                state_store_factory=lambda _env: InMemoryStateStore(),
                park_sender=lambda *args: calls["park"].append(args),
                start_sender=lambda *args: calls["start"].append(args),
                suspend_zone_sender=lambda *args: calls["suspend"].append(args),
                start_zone_sender=lambda *args: calls["zone"].append(args),
            )
        self.assertEqual(calls, {"read": [], "park": [], "start": [], "suspend": [], "zone": []})

    def test_cutting_height_targets_verified_work_area_without_other_commands(self) -> None:
        calls = []
        initial = AutomationState(
            operator_request_id="height-1",
            operator_request_action="SET_CUTTING_HEIGHT",
            operator_requested_utc=(NOW - timedelta(seconds=10)).isoformat(),
            operator_request_expires_utc=(NOW + timedelta(minutes=10)).isoformat(),
            operator_request_status="PENDING",
            operator_request_cutting_height_mm=30,
        )
        output, store = self._run(
            initial,
            result(activity="MOWING"),
            height=lambda *args: calls.append(args) or {"accepted": True},
            park=lambda *_: self.fail("Kein Parkbefehl erwartet"),
            start=lambda *_: self.fail("Kein Startbefehl erwartet"),
        )
        self.assertEqual(calls, [("client", "secret", "mower-1", 849199, 25)])
        self.assertEqual(output.decision_code, "CUTTING_HEIGHT_SET")
        self.assertEqual(store.load().operator_request_status, "COMPLETED")

    def test_safety_park_has_priority_over_cutting_height(self) -> None:
        height_calls = []
        park_calls = []
        initial = AutomationState(
            operator_request_id="height-2",
            operator_request_action="SET_CUTTING_HEIGHT",
            operator_requested_utc=(NOW - timedelta(seconds=10)).isoformat(),
            operator_request_expires_utc=(NOW + timedelta(minutes=10)).isoformat(),
            operator_request_status="PENDING",
            operator_request_cutting_height_mm=24,
        )
        output, store = self._run(
            initial,
            result(activity="MOWING", block_source="training", command="PARK"),
            height=lambda *args: height_calls.append(args),
            park=lambda *args: park_calls.append(args) or {"accepted": True},
        )
        self.assertEqual(height_calls, [])
        self.assertEqual(len(park_calls), 1)
        self.assertEqual(output.decision_code, "PARK_COMMAND_SENT")
        self.assertEqual(store.load().operator_request_status, "PENDING")

    def test_blade_usage_reset_is_sent_once_in_clear_cycle(self) -> None:
        calls = []
        initial = AutomationState(
            operator_request_id="blade-1",
            operator_request_action="RESET_BLADE_USAGE",
            operator_requested_utc=(NOW - timedelta(seconds=10)).isoformat(),
            operator_request_expires_utc=(NOW + timedelta(minutes=10)).isoformat(),
            operator_request_status="PENDING",
        )
        output, store = self._run(
            initial,
            result(activity="MOWING"),
            blade_reset=lambda *args: calls.append(args) or {"accepted": True},
        )
        self.assertEqual(calls, [("client", "secret", "mower-1")])
        self.assertEqual(output.decision_code, "BLADE_USAGE_RESET")
        self.assertEqual(store.load().operator_request_status, "COMPLETED")

    def test_safety_park_has_priority_over_blade_usage_reset(self) -> None:
        reset_calls = []
        park_calls = []
        initial = AutomationState(
            operator_request_id="blade-2",
            operator_request_action="RESET_BLADE_USAGE",
            operator_requested_utc=(NOW - timedelta(seconds=10)).isoformat(),
            operator_request_expires_utc=(NOW + timedelta(minutes=10)).isoformat(),
            operator_request_status="PENDING",
        )
        output, store = self._run(
            initial,
            result(activity="MOWING", block_source="training", command="PARK"),
            blade_reset=lambda *args: reset_calls.append(args),
            park=lambda *args: park_calls.append(args) or {"accepted": True},
        )
        self.assertEqual(reset_calls, [])
        self.assertEqual(len(park_calls), 1)
        self.assertEqual(output.decision_code, "PARK_COMMAND_SENT")
        self.assertEqual(store.load().operator_request_status, "PENDING")

    def test_blade_usage_reset_runs_during_occupancy_when_already_parked(self) -> None:
        calls = []
        initial = AutomationState(
            operator_request_id="blade-parked",
            operator_request_action="RESET_BLADE_USAGE",
            operator_requested_utc=(NOW - timedelta(seconds=10)).isoformat(),
            operator_request_expires_utc=(NOW + timedelta(minutes=10)).isoformat(),
            operator_request_status="PENDING",
        )
        output, store = self._run(
            initial,
            result(activity="PARKED_IN_CS", block_source="training"),
            blade_reset=lambda *args: calls.append(args) or {"accepted": True},
            park=lambda *_: self.fail("Bereits geparkter Mäher darf keinen Parkbefehl erhalten"),
        )
        self.assertEqual(calls, [("client", "secret", "mower-1")])
        self.assertEqual(output.decision_code, "BLADE_USAGE_RESET")
        self.assertEqual(store.load().operator_request_status, "COMPLETED")

    def test_external_station_park_is_not_taken_over_for_training_or_match(self) -> None:
        for source in ("training", "match", "training+match"):
            for activity in ("PARKED_IN_CS", "CHARGING"):
                with self.subTest(source=source, activity=activity):
                    calls = []
                    state = AutomationState(
                        continuous_mowing_owned=True,
                        continuous_mowing_work_area_id=849199,
                        continuous_mowing_window_end_utc=(
                            NOW + timedelta(hours=12)
                        ).isoformat(),
                    )
                    output, store = self._run(
                        state,
                        result(block_source=source, activity=activity),
                        park=lambda *args: calls.append(args) or {"accepted": True},
                    )
                    saved = store.load()
                    self.assertEqual(
                        output.decision_code,
                        "EXTERNAL_PARK_RESPECTED_DURING_OCCUPANCY",
                    )
                    self.assertEqual(calls, [])
                    self.assertFalse(saved.parked_by_automation)
                    self.assertFalse(saved.automation_restart_allowed)
                    self.assertFalse(saved.continuous_mowing_owned)

    def test_external_homeward_override_is_not_replaced_by_automation_park(self) -> None:
        calls = []
        output, store = self._run(
            AutomationState(),
            result(
                block_source="training",
                activity="GOING_HOME",
                override_action="PARK_UNTIL_FURTHER_NOTICE",
                external_reason_id=None,
            ),
            park=lambda *args: calls.append(args) or {"accepted": True},
        )
        self.assertEqual(
            output.decision_code,
            "EXTERNAL_PARK_RESPECTED_DURING_OCCUPANCY",
        )
        self.assertEqual(calls, [])
        self.assertFalse(store.load().parked_by_automation)

    def test_external_park_never_creates_a_later_start_right(self) -> None:
        park_calls = []
        start_calls = []
        _blocked, store = self._run(
            AutomationState(
                continuous_mowing_owned=True,
                continuous_mowing_work_area_id=849199,
                continuous_mowing_window_end_utc=(NOW + timedelta(hours=12)).isoformat(),
                hydrawise_clear_since_utc=(NOW - timedelta(minutes=120)).isoformat(),
                last_hydrawise_success_utc=(NOW - timedelta(minutes=1)).isoformat(),
            ),
            result(block_source="training", activity="PARKED_IN_CS"),
            park=lambda *args: park_calls.append(args) or {"accepted": True},
            start=lambda *args: start_calls.append(args) or {"accepted": True},
        )
        released = run_full_failsafe_cycle(
            now_utc=NOW + timedelta(minutes=1),
            settings=settings(),
            environment=ENV,
            past_due=False,
            source="test",
            read_only_runner=lambda **_: result(activity="PARKED_IN_CS"),
            state_store_factory=lambda _env: store,
            park_sender=lambda *args: park_calls.append(args) or {"accepted": True},
            start_sender=lambda *args: start_calls.append(args) or {"accepted": True},
        )
        self.assertEqual(released.decision_code, "MANUAL_OR_ERROR_HOLD")
        self.assertEqual(park_calls, [])
        self.assertEqual(start_calls, [])
        self.assertFalse(store.load().parked_by_automation)

    def test_unowned_homeward_mower_without_external_park_override_is_secured(self) -> None:
        calls = []
        output, store = self._run(
            AutomationState(),
            result(
                block_source="training",
                activity="GOING_HOME",
                override_action="NOT_ACTIVE",
                external_reason_id=None,
            ),
            park=lambda *args: calls.append(args) or {"accepted": True},
        )
        self.assertEqual(output.decision_code, "PARK_COMMAND_SENT")
        self.assertEqual(len(calls), 1)
        self.assertTrue(store.load().parked_by_automation)

    def test_owned_battery_charge_is_not_misclassified_as_manual_park(self) -> None:
        calls = []
        state = AutomationState(
            continuous_mowing_owned=True,
            continuous_mowing_work_area_id=849199,
            continuous_mowing_window_end_utc=(NOW + timedelta(hours=12)).isoformat(),
        )
        output, store = self._run(
            state,
            result(
                block_source="training",
                activity="CHARGING",
                override_action="FORCE_MOW",
                external_reason_id=253053,
            ),
            park=lambda *args: calls.append(args) or {"accepted": True},
        )
        self.assertEqual(output.decision_code, "PARK_COMMAND_SENT")
        self.assertEqual(len(calls), 1)
        self.assertTrue(store.load().parked_by_automation)
        self.assertTrue(store.load().automation_restart_allowed)

    def test_reasserts_owned_park_if_mower_reenters_training_or_match(self) -> None:
        for source in ("training", "match"):
            with self.subTest(source=source):
                calls = []
                state = AutomationState(
                    parked_by_automation=True,
                    automation_park_source=source,
                    automation_restart_allowed=True,
                    automation_park_until_utc=(NOW + timedelta(hours=4)).isoformat(),
                    park_command_sent_utc=(NOW - timedelta(minutes=20)).isoformat(),
                    park_confirmed_utc=(NOW - timedelta(minutes=15)).isoformat(),
                )
                output, saved = self._run(
                    state,
                    result(block_source=source, activity="MOWING"),
                    park=lambda *args: calls.append(args) or {"accepted": True},
                )
                self.assertEqual(output.decision_code, "PARK_COMMAND_REASSERTED")
                self.assertEqual(len(calls), 1)
                self.assertTrue(output.details["park_action"]["reasserted"])
                self.assertIsNone(saved.load().park_confirmed_utc)

    def test_reasserts_owned_park_during_active_irrigation(self) -> None:
        calls = []
        output, _store = self._run(
            irrigation_state(phase="RUNNING", current=RELAYS[0]),
            result(activity="LEAVING", active_ids=[RELAYS[0]], clear=False),
            park=lambda *args: calls.append(args) or {"accepted": True},
        )
        self.assertEqual(output.decision_code, "PARK_COMMAND_REASSERTED")
        self.assertEqual(len(calls), 1)

    def test_reasserts_owned_park_while_irrigation_is_failed(self) -> None:
        calls = []
        state = irrigation_state(phase="FAILED")
        state = AutomationState.from_mapping(
            {**state.to_dict(), "irrigation_failed_reason": "Testfehler"}
        )
        output, _store = self._run(
            state,
            result(block_source="irrigation", activity="MOWING"),
            park=lambda *args: calls.append(args) or {"accepted": True},
        )
        self.assertEqual(output.decision_code, "PARK_COMMAND_REASSERTED")
        self.assertEqual(len(calls), 1)

    def test_fresh_unconfirmed_park_gets_status_grace_without_command_spam(self) -> None:
        calls = []
        state = AutomationState(
            parked_by_automation=True,
            automation_park_source="training",
            automation_restart_allowed=True,
            automation_park_until_utc=(NOW + timedelta(hours=4)).isoformat(),
            park_command_sent_utc=(NOW - timedelta(minutes=2)).isoformat(),
        )
        output, _store = self._run(
            state,
            result(block_source="training", activity="MOWING"),
            park=lambda *args: calls.append(args) or {"accepted": True},
        )
        self.assertEqual(output.decision_code, "PARK_REASSERT_GRACE")
        self.assertEqual(calls, [])

    def test_reasserted_park_is_not_sent_again_during_grace(self) -> None:
        calls = []
        state = AutomationState(
            parked_by_automation=True,
            automation_park_source="training",
            automation_restart_allowed=True,
            automation_park_until_utc=(NOW + timedelta(hours=4)).isoformat(),
            park_command_sent_utc=(NOW - timedelta(minutes=20)).isoformat(),
            park_confirmed_utc=(NOW - timedelta(minutes=15)).isoformat(),
        )
        _first, store = self._run(
            state,
            result(block_source="training", activity="MOWING"),
            park=lambda *args: calls.append(args) or {"accepted": True},
        )
        second = run_full_failsafe_cycle(
            now_utc=NOW + timedelta(minutes=1),
            settings=settings(),
            environment=ENV,
            past_due=False,
            source="test",
            read_only_runner=lambda **_: result(block_source="training", activity="MOWING"),
            state_store_factory=lambda _env: store,
            park_sender=lambda *args: calls.append(args) or {"accepted": True},
        )
        self.assertEqual(second.decision_code, "PARK_REASSERT_GRACE")
        self.assertEqual(len(calls), 1)
        third = run_full_failsafe_cycle(
            now_utc=NOW + timedelta(minutes=3),
            settings=settings(),
            environment=ENV,
            past_due=False,
            source="test",
            read_only_runner=lambda **_: result(block_source="training", activity="MOWING"),
            state_store_factory=lambda _env: store,
            park_sender=lambda *args: calls.append(args) or {"accepted": True},
        )
        self.assertEqual(third.decision_code, "PARK_COMMAND_REASSERTED")
        self.assertEqual(len(calls), 2)

    def test_manual_stop_is_never_overridden_by_park_guard(self) -> None:
        calls = []
        state = AutomationState(
            parked_by_automation=True,
            automation_park_source="training",
            automation_restart_allowed=True,
            automation_park_until_utc=(NOW + timedelta(hours=4)).isoformat(),
            park_command_sent_utc=(NOW - timedelta(minutes=20)).isoformat(),
            park_confirmed_utc=(NOW - timedelta(minutes=15)).isoformat(),
        )
        output, _store = self._run(
            state,
            result(block_source="training", activity="STOPPED_IN_GARDEN"),
            park=lambda *args: calls.append(args) or {"accepted": True},
        )
        self.assertEqual(output.decision_code, "OCCUPANCY_OR_IRRIGATION_HOLD")
        self.assertEqual(calls, [])

    def test_irrigation_plan_parks_before_any_zone_command(self) -> None:
        park_calls = []
        zone_calls = []
        output, store = self._run(
            AutomationState(),
            result(
                command="PARK",
                block_source="irrigation",
                activity="MOWING",
                clear=False,
                irrigation_start=NOW + timedelta(minutes=30),
            ),
            park=lambda *args: park_calls.append(args) or {"accepted": True},
            zone=lambda *args: zone_calls.append(args) or {},
        )
        self.assertEqual(output.decision_code, "PARK_COMMAND_SENT")
        self.assertEqual(len(park_calls), 1)
        self.assertEqual(zone_calls, [])
        self.assertEqual(store.load().irrigation_phase, "PLANNED")

    def test_mower_error_cannot_block_suspension_of_local_irrigation_schedule(self) -> None:
        park_calls = []
        suspend_calls = []
        zone_calls = []
        cycle = result(
            command="PARK",
            block_source="irrigation",
            activity="NOT_APPLICABLE",
            mower_state="ERROR",
            irrigation_start=NOW + timedelta(minutes=45),
        )
        cycle.details["mower"]["error_code"] = 93

        first, store = self._run(
            AutomationState(),
            cycle,
            park=lambda *args: park_calls.append(args) or {"accepted": True},
            suspend=lambda *args: suspend_calls.append(args) or {"message_type": "info"},
            zone=lambda *args: zone_calls.append(args) or {"message_type": "info"},
        )

        self.assertEqual(first.decision_code, "IRRIGATION_ZONE_SUSPENDED")
        self.assertEqual(park_calls, [])
        self.assertEqual(len(suspend_calls), 1)
        self.assertEqual(zone_calls, [])
        self.assertEqual(store.load().irrigation_phase, "SUSPENDING")
        self.assertTrue(
            first.details["park_command_unavailable"][
                "irrigation_schedule_capture_continues"
            ]
        )

        for minute in range(1, len(RELAYS)):
            output, _ = self._continue(
                store,
                cycle,
                now=NOW + timedelta(minutes=minute),
                park=lambda *args: park_calls.append(args) or {"accepted": True},
                suspend=lambda *args: suspend_calls.append(args)
                or {"message_type": "info"},
                zone=lambda *args: zone_calls.append(args) or {"message_type": "info"},
            )
            self.assertEqual(output.decision_code, "IRRIGATION_ZONE_SUSPENDED")

        saved = store.load()
        self.assertEqual(saved.irrigation_phase, "READY")
        self.assertEqual(
            set(json.loads(saved.irrigation_suspended_relay_ids_json or "[]")),
            set(RELAYS),
        )
        self.assertEqual(len(suspend_calls), len(RELAYS))
        self.assertEqual(park_calls, [])
        self.assertEqual(zone_calls, [])

        held, _ = self._continue(
            store,
            cycle,
            now=NOW + timedelta(minutes=len(RELAYS)),
            park=lambda *args: park_calls.append(args) or {"accepted": True},
            suspend=lambda *args: suspend_calls.append(args) or {"message_type": "info"},
            zone=lambda *args: zone_calls.append(args) or {"message_type": "info"},
        )
        self.assertEqual(held.decision_code, "MOWER_NOT_SAFE_FOR_PARK_COMMAND")
        self.assertEqual(len(suspend_calls), len(RELAYS))
        self.assertEqual(park_calls, [])
        self.assertEqual(zone_calls, [])

    def test_lost_suspend_response_is_retried_without_false_progress(self) -> None:
        cycle = result(
            command="PARK",
            block_source="irrigation",
            activity="NOT_APPLICABLE",
            mower_state="ERROR",
            irrigation_start=NOW + timedelta(minutes=45),
        )
        cycle.details["mower"]["error_code"] = 93
        store = InMemoryStateStore()
        calls = []

        def fail(*args):
            calls.append(args)
            raise RuntimeError("Hydrawise-Antwort verloren")

        with self.assertRaisesRegex(RuntimeError, "Antwort verloren"):
            run_full_failsafe_cycle(
                now_utc=NOW,
                settings=settings(),
                environment=ENV,
                past_due=False,
                source="test",
                read_only_runner=lambda **_: cycle,
                state_store_factory=lambda _env: store,
                park_sender=lambda *_: self.fail("Mäher darf keinen Parkbefehl erhalten"),
                suspend_zone_sender=fail,
                start_zone_sender=lambda *_: self.fail("Beregnung darf nicht starten"),
            )

        self.assertIsNone(store.load().irrigation_phase)
        self.assertEqual([call[1] for call in calls], [RELAYS[0]])

        recovered, _ = self._continue(
            store,
            cycle,
            now=NOW + timedelta(seconds=10),
            park=lambda *_: self.fail("Mäher darf keinen Parkbefehl erhalten"),
            suspend=lambda *args: calls.append(args) or {"message_type": "info"},
            zone=lambda *_: self.fail("Beregnung darf nicht starten"),
        )
        self.assertEqual(recovered.decision_code, "IRRIGATION_ZONE_SUSPENDED")
        self.assertEqual([call[1] for call in calls], [RELAYS[0], RELAYS[0]])
        self.assertEqual(
            json.loads(store.load().irrigation_suspended_relay_ids_json or "[]"),
            [RELAYS[0]],
        )

    def test_stale_plan_is_cleared_even_while_mower_error_persists(self) -> None:
        state = irrigation_state(phase="PLANNED")
        state = AutomationState.from_mapping(
            {
                **state.to_dict(),
                "parked_by_automation": False,
                "automation_park_source": None,
                "automation_restart_allowed": False,
                "park_command_sent_utc": None,
                "park_confirmed_utc": None,
                "park_confirmed_observations": 0,
                "irrigation_suspended_relay_ids_json": "[]",
            }
        )
        cycle = suspended_result(activity="NOT_APPLICABLE")
        cycle.details["mower"]["state"] = "ERROR"
        cycle.details["mower"]["error_code"] = 93
        suspend_calls = []
        zone_calls = []

        first, store = self._run(
            state,
            cycle,
            suspend=lambda *args: suspend_calls.append(args),
            zone=lambda *args: zone_calls.append(args),
        )
        self.assertEqual(first.decision_code, "IRRIGATION_PLAN_CHANGE_CONFIRMING")

        second, _ = self._continue(
            store,
            cycle,
            now=NOW + timedelta(minutes=1),
            suspend=lambda *args: suspend_calls.append(args),
            zone=lambda *args: zone_calls.append(args),
        )
        self.assertEqual(second.decision_code, "IRRIGATION_PLAN_CHANGE_CONFIRMING")

        cleared, _ = self._continue(
            store,
            cycle,
            now=NOW + timedelta(minutes=2),
            suspend=lambda *args: suspend_calls.append(args),
            zone=lambda *args: zone_calls.append(args),
        )
        self.assertEqual(cleared.decision_code, "IRRIGATION_PLAN_CANCELLED_OR_DEFERRED")
        self.assertIsNone(store.load().irrigation_phase)
        self.assertEqual(suspend_calls, [])
        self.assertEqual(zone_calls, [])

    def test_substituted_relay_fails_plan_capture_and_only_parks_mower(self) -> None:
        park_calls = []
        suspend_calls = []
        zone_calls = []
        substituted = [*RELAYS[:-1], 999999]
        output, store = self._run(
            AutomationState(),
            result(
                command="PARK",
                block_source="irrigation",
                activity="MOWING",
                clear=False,
                irrigation_start=NOW + timedelta(minutes=30),
                relay_ids=substituted,
            ),
            park=lambda *args: park_calls.append(args) or {"accepted": True},
            suspend=lambda *args: suspend_calls.append(args) or {},
            zone=lambda *args: zone_calls.append(args) or {},
        )
        self.assertEqual(output.decision_code, "PARK_COMMAND_SENT")
        self.assertEqual(len(park_calls), 1)
        self.assertEqual(suspend_calls, [])
        self.assertEqual(zone_calls, [])
        self.assertEqual(store.load().irrigation_phase, "FAILED")
        self.assertIn("Relay-ID-Liste", store.load().irrigation_failed_reason)
        self.assertFalse(output.details["hydrawise_relay_allowlist"]["valid"])

    def test_stored_plan_with_substituted_relay_fails_before_zone_action(self) -> None:
        suspend_calls = []
        zone_calls = []
        state = irrigation_state(phase="PLANNED")
        substituted_plan = zones(relay_ids=[*RELAYS[:-1], 999999])
        state = AutomationState.from_mapping(
            {
                **state.to_dict(),
                "irrigation_plan_json": __import__("json").dumps(substituted_plan),
                "irrigation_suspended_relay_ids_json": "[]",
            }
        )
        output, store = self._run(
            state,
            result(block_source="irrigation"),
            suspend=lambda *args: suspend_calls.append(args) or {},
            zone=lambda *args: zone_calls.append(args) or {},
        )
        self.assertEqual(output.decision_code, "IRRIGATION_PLAN_INVALID")
        self.assertEqual(suspend_calls, [])
        self.assertEqual(zone_calls, [])
        self.assertEqual(store.load().irrigation_phase, "FAILED")

    def test_relay_allowlist_mismatch_prevents_mower_start_and_parks(self) -> None:
        park_calls = []
        start_calls = []
        substituted = [*RELAYS[:-1], 999999]
        output, _store = self._run(
            AutomationState(hydrawise_clear_since_utc=(NOW - timedelta(hours=4)).isoformat()),
            result(activity="MOWING", clear=False, relay_ids=substituted),
            park=lambda *args: park_calls.append(args) or {"accepted": True},
            start=lambda *args: start_calls.append(args) or {},
        )
        self.assertEqual(
            output.decision_code,
            "PARK_COMMAND_SENT_FOR_HYDRAWISE_HOLD",
        )
        self.assertEqual(len(park_calls), 1)
        self.assertEqual(start_calls, [])
        self.assertFalse(output.details["hydrawise_relay_allowlist"]["valid"])

    def test_suspends_one_scheduled_zone_per_cycle(self) -> None:
        calls = []
        # Helper state already contains all suspended IDs; use an empty list.
        state = irrigation_state(phase="PLANNED")
        state = AutomationState.from_mapping({**state.to_dict(), "irrigation_suspended_relay_ids_json": "[]"})
        output, store = self._run(
            state,
            result(
                block_source="irrigation",
                irrigation_start=NOW + timedelta(minutes=30),
            ),
            suspend=lambda *args: calls.append(args) or {"message_type": "info"},
        )
        self.assertEqual(output.decision_code, "IRRIGATION_ZONE_SUSPENDED")
        self.assertEqual(calls[-1][1], RELAYS[0])
        self.assertEqual(len(__import__("json").loads(store.load().irrigation_suspended_relay_ids_json)), 1)

    def test_suspends_regular_schedule_while_mower_is_still_going_home(self) -> None:
        calls = []
        state = irrigation_state(phase="PLANNED")
        state = AutomationState.from_mapping(
            {
                **state.to_dict(),
                "park_confirmed_utc": None,
                "irrigation_plan_json": __import__("json").dumps(
                    zones(start_utc=NOW + timedelta(hours=2))
                ),
                "irrigation_suspended_relay_ids_json": "[]",
            }
        )
        output, store = self._run(
            state,
            result(
                block_source="irrigation",
                activity="GOING_HOME",
                irrigation_start=NOW + timedelta(hours=2),
            ),
            suspend=lambda *args: calls.append(args) or {"message_type": "info"},
        )
        self.assertEqual(output.decision_code, "IRRIGATION_ZONE_SUSPENDED")
        self.assertEqual(len(calls), 1)
        self.assertIsNone(store.load().park_confirmed_utc)

    def test_three_day_suspension_is_confirmed_then_releases_unused_window(self) -> None:
        suspend_calls = []
        zone_calls = []
        state = irrigation_state(phase="PLANNED")
        state = AutomationState.from_mapping(
            {**state.to_dict(), "irrigation_suspended_relay_ids_json": "[]"}
        )
        cycle = result(block_source="irrigation")
        details = dict(cycle.details)
        observations = [
            {
                **observation,
                "scheduled": False,
                "scheduled_start_utc": None,
                "scheduled_end_utc": None,
                "seconds_until": 0,
            }
            for observation in details["hydrawise"]["zone_observations"]
        ]
        details["hydrawise"] = {
            **details["hydrawise"],
            "zones": [],
            "zone_observations": observations,
        }
        cycle = __import__("dataclasses").replace(cycle, details=details)
        first, store = self._run(
            state,
            cycle,
            suspend=lambda *args: suspend_calls.append(args) or {},
            zone=lambda *args: zone_calls.append(args) or {},
        )
        self.assertEqual(first.decision_code, "IRRIGATION_PLAN_CHANGE_CONFIRMING")
        second = run_full_failsafe_cycle(
            now_utc=NOW + timedelta(minutes=1),
            settings=settings(),
            environment=ENV,
            past_due=False,
            source="test",
            read_only_runner=lambda **_: cycle,
            state_store_factory=lambda _env: store,
            suspend_zone_sender=lambda *args: suspend_calls.append(args) or {},
            start_zone_sender=lambda *args: zone_calls.append(args) or {},
        )
        self.assertEqual(second.decision_code, "IRRIGATION_PLAN_CHANGE_CONFIRMING")
        third = run_full_failsafe_cycle(
            now_utc=NOW + timedelta(minutes=2),
            settings=settings(),
            environment=ENV,
            past_due=False,
            source="test",
            read_only_runner=lambda **_: cycle,
            state_store_factory=lambda _env: store,
            suspend_zone_sender=lambda *args: suspend_calls.append(args) or {},
            start_zone_sender=lambda *args: zone_calls.append(args) or {},
        )
        self.assertEqual(third.decision_code, "IRRIGATION_PLAN_CANCELLED_OR_DEFERRED")
        self.assertEqual(suspend_calls, [])
        self.assertEqual(zone_calls, [])
        self.assertIsNone(store.load().irrigation_phase)
        self.assertEqual(
            store.load().irrigation_cancelled_without_run_utc,
            (NOW + timedelta(minutes=2)).isoformat(),
        )

    def test_changed_pending_zone_duration_is_confirmed_and_adopted(self) -> None:
        suspend_calls = []
        state = irrigation_state(phase="SUSPENDING")
        state = AutomationState.from_mapping(
            {
                **state.to_dict(),
                "irrigation_suspended_relay_ids_json": __import__("json").dumps(RELAYS[:3]),
            }
        )
        live = zones(start_utc=NOW + timedelta(minutes=30))
        live[3] = {**live[3], "run_seconds": live[3]["run_seconds"] + 60}
        cycle = result(
            block_source="irrigation",
            irrigation_start=NOW + timedelta(minutes=30),
        )
        details = dict(cycle.details)
        observations = [
            {**zone, "valid": True, "scheduled": True}
            for zone in live
        ]
        details["hydrawise"] = {
            **details["hydrawise"],
            "zones": live,
            "zone_observations": observations,
        }
        cycle = __import__("dataclasses").replace(cycle, details=details)
        first, store = self._run(
            state,
            cycle,
            suspend=lambda *args: suspend_calls.append(args) or {},
        )
        self.assertEqual(first.decision_code, "IRRIGATION_PLAN_CHANGE_CONFIRMING")
        second = run_full_failsafe_cycle(
            now_utc=NOW + timedelta(minutes=2),
            settings=settings(),
            environment=ENV,
            past_due=False,
            source="test",
            read_only_runner=lambda **_: cycle,
            state_store_factory=lambda _env: store,
            suspend_zone_sender=lambda *args: suspend_calls.append(args) or {},
        )
        self.assertEqual(second.decision_code, "IRRIGATION_PLAN_UPDATED")
        self.assertEqual(suspend_calls, [])
        saved_plan = __import__("json").loads(store.load().irrigation_plan_json)
        self.assertEqual(saved_plan[3]["run_seconds"], RUN_SECONDS[3] + 60)
        self.assertEqual(store.load().irrigation_phase, "SUSPENDING")

    def test_confirmed_unused_irrigation_window_can_mow_without_drying_hold(self) -> None:
        start_calls = []
        state = AutomationState(
            parked_by_automation=True,
            automation_park_source="irrigation",
            automation_restart_allowed=True,
            park_command_sent_utc=(NOW - timedelta(minutes=10)).isoformat(),
            park_confirmed_utc=(NOW - timedelta(minutes=5)).isoformat(),
            irrigation_cancelled_without_run_utc=NOW.isoformat(),
            hydrawise_clear_since_utc=NOW.isoformat(),
        )
        output, store = self._run(
            state,
            result(activity="PARKED_IN_CS", battery=100),
            start=lambda *args: start_calls.append(args) or {"accepted": True},
        )
        self.assertEqual(output.decision_code, "CONTINUOUS_MOWING_START_SENT")
        self.assertEqual(len(start_calls), 1)
        self.assertTrue(
            output.details["hydrawise_release_gate"][
                "cancelled_without_run_release"
            ]
        )
        self.assertIsNone(store.load().irrigation_cancelled_without_run_utc)

    def test_running_sequence_adopts_changed_future_duration_after_confirmation(self) -> None:
        for delta_seconds in (-300, 300):
            with self.subTest(delta_seconds=delta_seconds):
                state = irrigation_state(phase="RUNNING", current=RELAYS[0])
                cycle = result(
                    block_source="irrigation",
                    active_ids=[RELAYS[0]],
                    clear=False,
                )
                details = dict(cycle.details)
                observations = list(details["hydrawise"]["zone_observations"])
                observations[1] = {
                    **observations[1],
                    "run_seconds": RUN_SECONDS[1] + delta_seconds,
                }
                details["hydrawise"] = {
                    **details["hydrawise"],
                    "zone_observations": observations,
                }
                cycle = __import__("dataclasses").replace(cycle, details=details)
                first, store = self._run(state, cycle)
                self.assertEqual(
                    first.decision_code,
                    "IRRIGATION_DURATION_CHANGE_CONFIRMING",
                )
                confirmed = run_full_failsafe_cycle(
                    now_utc=NOW + timedelta(minutes=2),
                    settings=settings(),
                    environment=ENV,
                    past_due=False,
                    source="test",
                    read_only_runner=lambda **_: cycle,
                    state_store_factory=lambda _env: store,
                )
                self.assertEqual(
                    confirmed.decision_code,
                    "IRRIGATION_REMAINING_DURATIONS_UPDATED",
                )
                saved_plan = __import__("json").loads(
                    store.load().irrigation_plan_json
                )
                self.assertEqual(
                    saved_plan[1]["run_seconds"],
                    RUN_SECONDS[1] + delta_seconds,
                )
                self.assertEqual(
                    saved_plan[0]["run_seconds"],
                    RUN_SECONDS[0],
                )

                ready = AutomationState.from_mapping(
                    {
                        **store.load().to_dict(),
                        "irrigation_phase": "READY",
                        "irrigation_completed_relay_ids_json": __import__("json").dumps(
                            [RELAYS[0]]
                        ),
                        "irrigation_current_relay_id": None,
                        "irrigation_zone_start_reserved_utc": None,
                        "irrigation_zone_started_utc": None,
                    }
                )
                zone_calls = []
                ready_details = dict(cycle.details)
                ready_hydrawise = dict(ready_details["hydrawise"])
                ready_hydrawise["safety"] = {
                    **ready_hydrawise["safety"],
                    "clear_now": True,
                    "active_zone_count": 0,
                    "active_relay_ids": [],
                }
                ready_details["hydrawise"] = ready_hydrawise
                ready_cycle = __import__("dataclasses").replace(
                    cycle,
                    details=ready_details,
                )
                started, _ = self._run(
                    ready,
                    ready_cycle,
                    now=NOW + timedelta(minutes=3),
                    zone=lambda *args: zone_calls.append(args) or {},
                )
                self.assertEqual(started.decision_code, "IRRIGATION_ZONE_START_SENT")
                self.assertEqual(
                    zone_calls[0][1:3],
                    (RELAYS[1], RUN_SECONDS[1] + delta_seconds),
                )

    def test_confirmed_early_manual_stop_cancels_remaining_zones_and_starts_hold(self) -> None:
        state = irrigation_state(phase="RUNNING", current=RELAYS[0])
        state = AutomationState.from_mapping(
            {
                **state.to_dict(),
                "irrigation_zone_started_utc": (NOW - timedelta(minutes=5)).isoformat(),
                "irrigation_zone_clear_since_utc": (NOW - timedelta(minutes=3)).isoformat(),
            }
        )
        zone_calls = []
        output, store = self._run(
            state,
            result(block_source="irrigation"),
            zone=lambda *args: zone_calls.append(args) or {},
        )
        saved = store.load()
        self.assertEqual(output.decision_code, "IRRIGATION_RUN_CANCELLED_EARLY")
        self.assertEqual(saved.irrigation_phase, "COMPLETE_HOLD")
        self.assertEqual(saved.hydrawise_clear_since_utc, NOW.isoformat())
        self.assertEqual(zone_calls, [])

        held, _ = self._run(
            saved,
            result(),
            now=NOW + timedelta(minutes=89),
            start=lambda *args: zone_calls.append(args) or {},
        )
        self.assertEqual(held.decision_code, "HYDRAWISE_CLEAR_CONFIRMATION_HOLD")
        self.assertEqual(zone_calls, [])

    def test_excessive_duration_extension_fails_before_any_future_zone_start(self) -> None:
        state = irrigation_state(phase="RUNNING", current=RELAYS[0])
        cycle = result(
            block_source="irrigation",
            active_ids=[RELAYS[0]],
            clear=False,
        )
        details = dict(cycle.details)
        observations = [
            (
                {**observation, "run_seconds": 7200}
                if int(observation["relay_id"]) != RELAYS[0]
                else observation
            )
            for observation in details["hydrawise"]["zone_observations"]
        ]
        details["hydrawise"] = {
            **details["hydrawise"],
            "zone_observations": observations,
        }
        cycle = __import__("dataclasses").replace(cycle, details=details)
        first, store = self._run(state, cycle)
        self.assertEqual(
            first.decision_code,
            "IRRIGATION_DURATION_CHANGE_CONFIRMING",
        )
        confirmed = run_full_failsafe_cycle(
            now_utc=NOW + timedelta(minutes=2),
            settings=settings(),
            environment=ENV,
            past_due=False,
            source="test",
            read_only_runner=lambda **_: cycle,
            state_store_factory=lambda _env: store,
        )
        self.assertEqual(
            confirmed.decision_code,
            "IRRIGATION_DURATION_CHANGE_UNSAFE",
        )
        self.assertEqual(store.load().irrigation_phase, "FAILED")

    def test_shifted_start_time_is_confirmed_and_adopted_before_suspend(self) -> None:
        state = irrigation_state(phase="PLANNED")
        state = AutomationState.from_mapping(
            {**state.to_dict(), "irrigation_suspended_relay_ids_json": "[]"}
        )
        shifted = zones(start_utc=NOW + timedelta(minutes=40))
        cycle = result(
            block_source="irrigation",
            irrigation_start=NOW + timedelta(minutes=40),
        )
        first, store = self._run(state, cycle)
        self.assertEqual(first.decision_code, "IRRIGATION_PLAN_CHANGE_CONFIRMING")
        confirmed = run_full_failsafe_cycle(
            now_utc=NOW + timedelta(minutes=2),
            settings=settings(),
            environment=ENV,
            past_due=False,
            source="test",
            read_only_runner=lambda **_: cycle,
            state_store_factory=lambda _env: store,
        )
        self.assertEqual(confirmed.decision_code, "IRRIGATION_PLAN_UPDATED")
        saved_plan = __import__("json").loads(store.load().irrigation_plan_json)
        self.assertEqual(
            saved_plan[0]["scheduled_start_utc"],
            shifted[0]["scheduled_start_utc"],
        )

    def test_unstable_plan_edits_restart_confirmation_and_send_no_command(self) -> None:
        calls = []
        state = irrigation_state(phase="PLANNED")
        state = AutomationState.from_mapping(
            {**state.to_dict(), "irrigation_suspended_relay_ids_json": "[]"}
        )
        first_cycle = result(
            block_source="irrigation",
            irrigation_start=NOW + timedelta(minutes=35),
        )
        first, store = self._run(
            state,
            first_cycle,
            suspend=lambda *args: calls.append(args) or {},
        )
        first_hash = store.load().irrigation_change_candidate_hash
        second_cycle = result(
            block_source="irrigation",
            irrigation_start=NOW + timedelta(minutes=40),
        )
        second = run_full_failsafe_cycle(
            now_utc=NOW + timedelta(minutes=1),
            settings=settings(),
            environment=ENV,
            past_due=False,
            source="test",
            read_only_runner=lambda **_: second_cycle,
            state_store_factory=lambda _env: store,
            suspend_zone_sender=lambda *args: calls.append(args) or {},
        )
        self.assertEqual(first.decision_code, "IRRIGATION_PLAN_CHANGE_CONFIRMING")
        self.assertEqual(second.decision_code, "IRRIGATION_PLAN_CHANGE_CONFIRMING")
        self.assertNotEqual(store.load().irrigation_change_candidate_hash, first_hash)
        self.assertEqual(
            store.load().irrigation_change_candidate_since_utc,
            (NOW + timedelta(minutes=1)).isoformat(),
        )
        self.assertEqual(calls, [])

    def test_last_suspend_creates_short_lived_start_proof(self) -> None:
        suspend_calls = []
        state = irrigation_state(phase="SUSPENDING")
        state = AutomationState.from_mapping(
            {
                **state.to_dict(),
                "irrigation_suspended_relay_ids_json": __import__("json").dumps(RELAYS[:-1]),
                "irrigation_suspension_completed_utc": None,
            }
        )
        output, store = self._run(
            state,
            result(
                block_source="irrigation",
                irrigation_start=NOW + timedelta(minutes=30),
            ),
            suspend=lambda *args: suspend_calls.append(args) or {"message_type": "info"},
        )
        saved = store.load()
        self.assertEqual(output.decision_code, "IRRIGATION_ZONE_SUSPENDED")
        self.assertEqual(len(suspend_calls), 1)
        self.assertEqual(saved.irrigation_phase, "READY")
        self.assertEqual(saved.irrigation_suspension_completed_utc, NOW.isoformat())

    def test_expired_proof_with_reenabled_live_schedule_never_starts_zone(self) -> None:
        zone_calls = []
        state = irrigation_state(phase="READY")
        state = AutomationState.from_mapping(
            {
                **state.to_dict(),
                "irrigation_suspension_completed_utc": (NOW - timedelta(minutes=4)).isoformat(),
            }
        )
        cycle = result(block_source="irrigation")
        cycle.details["hydrawise"]["zone_observations"][0][
            "scheduled_start_utc"
        ] = (NOW + timedelta(minutes=10)).isoformat()
        output, store = self._run(
            state,
            cycle,
            zone=lambda *args: zone_calls.append(args) or {},
        )
        self.assertEqual(output.decision_code, "IRRIGATION_PLAN_LEASE_EXPIRED")
        self.assertEqual(zone_calls, [])
        self.assertEqual(store.load().irrigation_phase, "FAILED")

    def test_recovered_mower_is_parked_then_expired_suspension_is_revalidated(self) -> None:
        initial = irrigation_state(phase="READY")
        initial = AutomationState.from_mapping(
            {
                **initial.to_dict(),
                "parked_by_automation": False,
                "automation_park_source": None,
                "automation_restart_allowed": False,
                "park_command_sent_utc": None,
                "park_confirmed_utc": None,
                "park_confirmed_observations": 0,
                "irrigation_suspension_completed_utc": (
                    NOW - timedelta(minutes=10)
                ).isoformat(),
            }
        )
        park_calls = []
        zone_calls = []

        park_sent, store = self._run(
            initial,
            suspended_result(activity="MOWING"),
            park=lambda *args: park_calls.append(args) or {"accepted": True},
            zone=lambda *args: zone_calls.append(args) or {"message_type": "info"},
        )
        self.assertEqual(park_sent.decision_code, "PARK_COMMAND_SENT")
        self.assertEqual(len(park_calls), 1)
        self.assertEqual(zone_calls, [])

        going_home, _ = self._continue(
            store,
            suspended_result(activity="GOING_HOME"),
            now=NOW + timedelta(minutes=1),
            zone=lambda *args: zone_calls.append(args) or {"message_type": "info"},
        )
        self.assertEqual(
            going_home.decision_code,
            "IRRIGATION_WAIT_FOR_CONFIRMED_PARK",
        )

        first_dock, _ = self._continue(
            store,
            suspended_result(activity="PARKED_IN_CS"),
            now=NOW + timedelta(minutes=2),
            zone=lambda *args: zone_calls.append(args) or {"message_type": "info"},
        )
        self.assertEqual(first_dock.decision_code, "IRRIGATION_WAIT_FOR_CONFIRMED_PARK")

        first_proof, _ = self._continue(
            store,
            suspended_result(activity="PARKED_IN_CS"),
            now=NOW + timedelta(minutes=3),
            zone=lambda *args: zone_calls.append(args) or {"message_type": "info"},
        )
        self.assertEqual(
            first_proof.decision_code,
            "IRRIGATION_SUSPENSION_REVALIDATING",
        )
        self.assertEqual(
            store.load().irrigation_suspension_revalidation_observations,
            1,
        )

        renewed, _ = self._continue(
            store,
            suspended_result(activity="PARKED_IN_CS"),
            now=NOW + timedelta(minutes=4),
            zone=lambda *args: zone_calls.append(args) or {"message_type": "info"},
        )
        self.assertEqual(renewed.decision_code, "IRRIGATION_SUSPENSION_REVALIDATED")
        self.assertEqual(
            store.load().irrigation_suspension_completed_utc,
            (NOW + timedelta(minutes=4)).isoformat(),
        )
        self.assertEqual(zone_calls, [])

        started, _ = self._continue(
            store,
            suspended_result(activity="PARKED_IN_CS"),
            now=NOW + timedelta(minutes=5),
            zone=lambda *args: zone_calls.append(args) or {"message_type": "info"},
        )
        self.assertEqual(started.decision_code, "IRRIGATION_ZONE_START_SENT")
        self.assertEqual(len(zone_calls), 1)
        self.assertEqual(len(park_calls), 1)

    def test_revalidation_requires_consecutive_fresh_cycles(self) -> None:
        state = irrigation_state(phase="READY")
        state = AutomationState.from_mapping(
            {
                **state.to_dict(),
                "irrigation_suspension_completed_utc": (
                    NOW - timedelta(minutes=10)
                ).isoformat(),
            }
        )
        first, store = self._run(state, suspended_result())
        self.assertEqual(first.decision_code, "IRRIGATION_SUSPENSION_REVALIDATING")

        after_gap, _ = self._continue(
            store,
            suspended_result(),
            now=NOW + timedelta(minutes=2),
        )
        self.assertEqual(
            after_gap.decision_code,
            "IRRIGATION_SUSPENSION_REVALIDATING",
        )
        self.assertEqual(
            store.load().irrigation_suspension_revalidation_observations,
            1,
        )

    def test_stale_hydrawise_cannot_revalidate_or_start(self) -> None:
        zone_calls = []
        state = irrigation_state(phase="READY")
        state = AutomationState.from_mapping(
            {
                **state.to_dict(),
                "irrigation_suspension_completed_utc": (
                    NOW - timedelta(minutes=10)
                ).isoformat(),
            }
        )
        cycle = suspended_result()
        cycle.details["hydrawise"]["safety"]["fresh"] = False
        output, store = self._run(
            state,
            cycle,
            zone=lambda *args: zone_calls.append(args) or {},
        )
        self.assertEqual(output.decision_code, "IRRIGATION_STATUS_NOT_SAFE")
        self.assertEqual(zone_calls, [])
        self.assertEqual(store.load().irrigation_phase, "READY")

    def test_revalidated_run_still_must_fit_inside_suspension_window(self) -> None:
        state = irrigation_state(phase="READY")
        state = AutomationState.from_mapping(
            {
                **state.to_dict(),
                "irrigation_suspension_completed_utc": (
                    NOW - timedelta(minutes=10)
                ).isoformat(),
                "irrigation_suspension_until_utc": (
                    NOW + timedelta(minutes=60)
                ).isoformat(),
            }
        )
        zone_calls = []
        first, store = self._run(
            state,
            suspended_result(),
            zone=lambda *args: zone_calls.append(args) or {},
        )
        self.assertEqual(first.decision_code, "IRRIGATION_SUSPENSION_REVALIDATING")
        renewed, _ = self._continue(
            store,
            suspended_result(),
            now=NOW + timedelta(minutes=1),
            zone=lambda *args: zone_calls.append(args) or {},
        )
        self.assertEqual(renewed.decision_code, "IRRIGATION_SUSPENSION_REVALIDATED")
        too_late, _ = self._continue(
            store,
            suspended_result(),
            now=NOW + timedelta(minutes=2),
            zone=lambda *args: zone_calls.append(args) or {},
        )
        self.assertEqual(too_late.decision_code, "IRRIGATION_DURATION_CHANGE_UNSAFE")
        self.assertEqual(store.load().irrigation_phase, "FAILED")
        self.assertEqual(zone_calls, [])

    def test_signal_recovery_during_suspension_sends_park_before_more_zone_commands(self) -> None:
        state = irrigation_state(phase="SUSPENDING")
        state = AutomationState.from_mapping(
            {
                **state.to_dict(),
                "parked_by_automation": False,
                "automation_park_source": None,
                "automation_restart_allowed": False,
                "park_command_sent_utc": None,
                "park_confirmed_utc": None,
                "park_confirmed_observations": 0,
                "irrigation_suspended_relay_ids_json": json.dumps(RELAYS[:3]),
                "irrigation_suspension_completed_utc": None,
            }
        )
        park_calls = []
        suspend_calls = []
        output, store = self._run(
            state,
            result(
                command="PARK",
                block_source="irrigation",
                activity="MOWING",
                irrigation_start=NOW + timedelta(minutes=30),
            ),
            park=lambda *args: park_calls.append(args) or {"accepted": True},
            suspend=lambda *args: suspend_calls.append(args) or {},
        )
        self.assertEqual(output.decision_code, "PARK_COMMAND_SENT")
        self.assertEqual(len(park_calls), 1)
        self.assertEqual(suspend_calls, [])
        self.assertTrue(store.load().parked_by_automation)

    def test_active_zone_during_suspension_fails_without_hydrawise_command(self) -> None:
        for phase, activity in (("PLANNED", "GOING_HOME"), ("SUSPENDING", "PARKED_IN_CS")):
            with self.subTest(phase=phase, activity=activity):
                suspend_calls = []
                zone_calls = []
                state = irrigation_state(phase=phase)
                state = AutomationState.from_mapping(
                    {
                        **state.to_dict(),
                        "park_confirmed_utc": None if activity == "GOING_HOME" else state.park_confirmed_utc,
                        "irrigation_suspended_relay_ids_json": "[]",
                    }
                )
                output, store = self._run(
                    state,
                    result(
                        block_source="irrigation",
                        activity=activity,
                        active_ids=[RELAYS[0]],
                        clear=False,
                    ),
                    suspend=lambda *args: suspend_calls.append(args) or {},
                    zone=lambda *args: zone_calls.append(args) or {},
                )
                self.assertEqual(
                    output.decision_code,
                    "IRRIGATION_ACTIVE_DURING_SUSPENSION",
                )
                self.assertFalse(output.command_sent)
                self.assertEqual(suspend_calls, [])
                self.assertEqual(zone_calls, [])
                self.assertEqual(store.load().irrigation_phase, "FAILED")
                self.assertIn("läuft bereits", store.load().irrigation_failed_reason)
                self.assertEqual(
                    output.details["irrigation_active_during_suspension"]["active_relay_ids"],
                    [RELAYS[0]],
                )

    def test_unknown_active_zone_also_fails_closed_during_suspension(self) -> None:
        suspend_calls = []
        zone_calls = []
        state = irrigation_state(phase="PLANNED")
        state = AutomationState.from_mapping(
            {**state.to_dict(), "irrigation_suspended_relay_ids_json": "[]"}
        )
        output, store = self._run(
            state,
            result(active_ids=[999999], clear=False),
            suspend=lambda *args: suspend_calls.append(args) or {},
            zone=lambda *args: zone_calls.append(args) or {},
        )
        self.assertEqual(output.decision_code, "IRRIGATION_ACTIVE_DURING_SUSPENSION")
        self.assertEqual(suspend_calls, [])
        self.assertEqual(zone_calls, [])
        self.assertEqual(store.load().irrigation_phase, "FAILED")
        self.assertEqual(
            output.details["irrigation_active_during_suspension"]["active_relay_ids"],
            [999999],
        )

    def test_irrigation_start_waits_for_every_rasen_occupancy_to_clear(self) -> None:
        for source in (
            "irrigation+training",
            "irrigation+match",
            "irrigation+special",
            "irrigation+future-occupancy-type",
        ):
            with self.subTest(source=source):
                zone_calls = []
                state = irrigation_state(phase="READY")
                state = AutomationState.from_mapping(
                    {**state.to_dict(), "automation_park_source": source}
                )
                output, store = self._run(
                    state,
                    result(block_source=source, activity="PARKED_IN_CS"),
                    zone=lambda *args: zone_calls.append(args) or {"message_type": "info"},
                )
                self.assertEqual(
                    output.decision_code,
                    "IRRIGATION_WAIT_FOR_OCCUPANCY_CLEAR",
                )
                self.assertEqual(zone_calls, [])
                self.assertEqual(store.load().irrigation_phase, "READY")

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

    def test_water_start_requires_connected_and_fresh_mower_status(self) -> None:
        for condition in ("disconnected", "stale"):
            with self.subTest(condition=condition):
                cycle = result(block_source="irrigation")
                if condition == "disconnected":
                    cycle.details["mower"]["connected"] = False
                    at = NOW
                else:
                    cycle.details["mower"]["status_timestamp_ms"] = int(
                        (NOW - timedelta(minutes=10)).timestamp() * 1000
                    )
                    at = NOW
                store = InMemoryStateStore(irrigation_state(phase="READY"))
                calls = []
                output = run_full_failsafe_cycle(
                    now_utc=at,
                    settings=settings(),
                    environment=ENV,
                    past_due=False,
                    source="test",
                    read_only_runner=lambda **_: cycle,
                    state_store_factory=lambda _env: store,
                    park_sender=lambda *_: {"accepted": True},
                    start_sender=lambda *_: {"accepted": True},
                    suspend_zone_sender=lambda *_: {"message_type": "info"},
                    start_zone_sender=lambda *args: calls.append(args)
                    or {"message_type": "info"},
                )
                self.assertEqual(
                    output.decision_code,
                    "IRRIGATION_WAIT_FOR_CONFIRMED_PARK",
                )
                self.assertEqual(calls, [])

    def test_started_zone_tracking_continues_with_unchanged_park_event_timestamp(self) -> None:
        cases = (
            ("START_RESERVED", "IRRIGATION_ZONE_CONFIRMED_RUNNING"),
            ("RUNNING", "IRRIGATION_ZONE_RUNNING"),
        )
        for phase, expected_decision in cases:
            with self.subTest(phase=phase):
                state = irrigation_state(phase=phase, current=RELAYS[0])
                cycle = result(
                    block_source="irrigation",
                    active_ids=[RELAYS[0]],
                    clear=False,
                )
                cycle.details["mower"]["status_timestamp_ms"] = int(
                    (NOW - timedelta(minutes=10)).timestamp() * 1000
                )
                store = InMemoryStateStore(state)

                output = run_full_failsafe_cycle(
                    now_utc=NOW,
                    settings=settings(),
                    environment=ENV,
                    past_due=False,
                    source="test",
                    read_only_runner=lambda **_: cycle,
                    state_store_factory=lambda _env: store,
                    park_sender=lambda *_: {"accepted": True},
                    start_sender=lambda *_: {"accepted": True},
                    suspend_zone_sender=lambda *_: {"message_type": "info"},
                    start_zone_sender=lambda *_: {"message_type": "info"},
                )

                self.assertEqual(output.decision_code, expected_decision)
                self.assertNotEqual(
                    output.decision_code,
                    "IRRIGATION_WAIT_FOR_CONFIRMED_PARK",
                )

    def test_zone_end_is_recorded_with_unchanged_park_event_timestamp(self) -> None:
        state = irrigation_state(phase="RUNNING", current=RELAYS[0])
        state = AutomationState.from_mapping(
            {
                **state.to_dict(),
                "irrigation_zone_started_utc": (
                    NOW - timedelta(minutes=25)
                ).isoformat(),
                "irrigation_zone_clear_since_utc": (
                    NOW - timedelta(minutes=3)
                ).isoformat(),
            }
        )
        cycle = result(block_source="irrigation")
        cycle.details["mower"]["status_timestamp_ms"] = int(
            (NOW - timedelta(minutes=10)).timestamp() * 1000
        )
        store = InMemoryStateStore(state)

        output = run_full_failsafe_cycle(
            now_utc=NOW,
            settings=settings(),
            environment=ENV,
            past_due=False,
            source="test",
            read_only_runner=lambda **_: cycle,
            state_store_factory=lambda _env: store,
            park_sender=lambda *_: {"accepted": True},
            start_sender=lambda *_: {"accepted": True},
            suspend_zone_sender=lambda *_: {"message_type": "info"},
            start_zone_sender=lambda *_: {"message_type": "info"},
        )

        self.assertEqual(output.decision_code, "IRRIGATION_ZONE_CONFIRMED_COMPLETE")
        self.assertEqual(store.load().irrigation_phase, "READY")
        self.assertEqual(
            json.loads(store.load().irrigation_completed_relay_ids_json),
            [RELAYS[0]],
        )

    def test_water_start_requires_two_distinct_parked_cycles(self) -> None:
        initial = irrigation_state(phase="READY")
        initial = AutomationState.from_mapping(
            {**initial.to_dict(), "park_confirmed_utc": None}
        )
        store = InMemoryStateStore(initial)
        zone_calls = []

        def run(at: datetime):
            return run_full_failsafe_cycle(
                now_utc=at,
                settings=settings(),
                environment=ENV,
                past_due=False,
                source="test",
                read_only_runner=lambda **_: result(block_source="irrigation"),
                state_store_factory=lambda _env: store,
                park_sender=lambda *_: {"accepted": True},
                start_sender=lambda *_: {"accepted": True},
                suspend_zone_sender=lambda *_: {"message_type": "info"},
                start_zone_sender=lambda *args: zone_calls.append(args)
                or {"message_type": "info"},
            )

        first = run(NOW)
        self.assertEqual(first.decision_code, "IRRIGATION_WAIT_FOR_CONFIRMED_PARK")
        self.assertEqual(zone_calls, [])
        self.assertEqual(store.load().park_confirmed_utc, NOW.isoformat())

        second = run(NOW + timedelta(minutes=1))
        self.assertEqual(second.decision_code, "IRRIGATION_ZONE_START_SENT")
        self.assertEqual(len(zone_calls), 1)

    def test_direct_stop_targets_running_zone_and_starts_hold_only_after_confirmation(self) -> None:
        initial = irrigation_state(phase="RUNNING", current=RELAYS[0])
        initial = AutomationState.from_mapping(
            {
                **initial.to_dict(),
                "operator_request_id": "stop-now-1",
                "operator_request_action": "STOP_IRRIGATION_NOW",
                "operator_requested_utc": (NOW - timedelta(seconds=10)).isoformat(),
                "operator_request_expires_utc": (NOW + timedelta(minutes=10)).isoformat(),
                "operator_request_status": "PENDING",
            }
        )
        store = InMemoryStateStore(initial)
        stop_calls = []

        def run(at: datetime, *, active: bool):
            return run_full_failsafe_cycle(
                now_utc=at,
                settings=settings(),
                environment=ENV,
                past_due=False,
                source="test",
                read_only_runner=lambda **_: result(
                    block_source="irrigation",
                    active_ids=[RELAYS[0]] if active else [],
                    clear=not active,
                ),
                state_store_factory=lambda _env: store,
                stop_zone_sender=lambda _key, relay, controller: stop_calls.append(
                    (relay, controller)
                ) or {"message_type": "info"},
            )

        sent = run(NOW, active=True)
        self.assertEqual(sent.decision_code, "IRRIGATION_ZONE_STOP_SENT")
        self.assertTrue(sent.command_sent)
        self.assertEqual(stop_calls, [(RELAYS[0], "controller")])
        self.assertEqual(store.load().irrigation_phase, "STOPPING")

        first_clear = run(NOW + timedelta(minutes=1), active=False)
        self.assertEqual(first_clear.decision_code, "IRRIGATION_CONFIRM_DIRECT_STOP")
        self.assertEqual(store.load().irrigation_phase, "STOPPING")
        self.assertEqual(stop_calls, [(RELAYS[0], "controller")])

        confirmed = run(NOW + timedelta(minutes=3), active=False)
        saved = store.load()
        self.assertEqual(confirmed.decision_code, "IRRIGATION_OPERATOR_STOPPED_NOW")
        self.assertEqual(saved.irrigation_phase, "COMPLETE_HOLD")
        self.assertEqual(saved.operator_request_status, "COMPLETED")
        self.assertEqual(saved.hydrawise_clear_since_utc, (NOW + timedelta(minutes=3)).isoformat())
        self.assertEqual(stop_calls, [(RELAYS[0], "controller")])

    def test_next_irrigation_plan_replaces_stale_complete_hold_at_capture_lead(self) -> None:
        state = irrigation_state(phase="COMPLETE_HOLD")
        state = AutomationState.from_mapping(
            {
                **state.to_dict(),
                "irrigation_completed_relay_ids_json": __import__("json").dumps(RELAYS),
                "irrigation_completed_utc": (NOW - timedelta(hours=20)).isoformat(),
                "hydrawise_clear_since_utc": (NOW - timedelta(hours=20)).isoformat(),
            }
        )
        suspend_calls = []
        output, store = self._run(
            state,
            result(irrigation_start=NOW + timedelta(minutes=45)),
            suspend=lambda *args: suspend_calls.append(args) or {"message_type": "info"},
        )
        self.assertEqual(output.decision_code, "IRRIGATION_ZONE_SUSPENDED")
        self.assertEqual(len(suspend_calls), 1)
        self.assertEqual(store.load().irrigation_phase, "SUSPENDING")
        self.assertTrue(output.details["irrigation_state_rollover"]["cleared"])

    def test_unexpected_active_zone_fails_closed(self) -> None:
        output, store = self._run(
            irrigation_state(phase="START_RESERVED", current=RELAYS[0]),
            result(block_source="irrigation", active_ids=[RELAYS[1]], clear=False),
        )
        self.assertEqual(output.decision_code, "IRRIGATION_UNEXPECTED_ACTIVE_ZONE")
        self.assertEqual(store.load().irrigation_phase, "FAILED")

    def test_seventh_confirmed_zone_enters_complete_hold(self) -> None:
        state = irrigation_state(phase="RUNNING", current=RELAYS[-1])
        state = AutomationState.from_mapping(
            {
                **state.to_dict(),
                "irrigation_completed_relay_ids_json": __import__("json").dumps(RELAYS[:-1]),
                "irrigation_zone_started_utc": (NOW - timedelta(minutes=30)).isoformat(),
                "irrigation_zone_clear_since_utc": (NOW - timedelta(minutes=3)).isoformat(),
            }
        )
        output, store = self._run(state, result(block_source="irrigation"))
        saved = store.load()
        self.assertEqual(output.decision_code, "IRRIGATION_ALL_ZONES_CONFIRMED_COMPLETE")
        self.assertEqual(saved.irrigation_phase, "COMPLETE_HOLD")
        self.assertEqual(
            set(__import__("json").loads(saved.irrigation_completed_relay_ids_json)),
            set(RELAYS),
        )
        self.assertEqual(saved.hydrawise_clear_since_utc, NOW.isoformat())

    def test_complete_hold_is_never_recaptured_during_original_schedule(self) -> None:
        state = irrigation_state(phase="COMPLETE_HOLD")
        state = AutomationState.from_mapping(
            {
                **state.to_dict(),
                "irrigation_completed_relay_ids_json": __import__("json").dumps(RELAYS),
                "irrigation_completed_utc": NOW.isoformat(),
                "hydrawise_clear_since_utc": NOW.isoformat(),
            }
        )
        calls = {"park": [], "start": [], "suspend": [], "zone": []}
        output, store = self._run(
            state,
            result(block_source="irrigation"),
            park=lambda *args: calls["park"].append(args) or {},
            start=lambda *args: calls["start"].append(args) or {},
            suspend=lambda *args: calls["suspend"].append(args) or {},
            zone=lambda *args: calls["zone"].append(args) or {},
        )
        self.assertEqual(output.decision_code, "OCCUPANCY_OR_IRRIGATION_HOLD")
        self.assertEqual(store.load().irrigation_phase, "COMPLETE_HOLD")
        self.assertEqual(calls, {"park": [], "start": [], "suspend": [], "zone": []})

    def test_one_hundred_twenty_minute_hold_blocks_mower_start(self) -> None:
        state = irrigation_state(phase="COMPLETE_HOLD")
        state = AutomationState.from_mapping(
            {
                **state.to_dict(),
                "irrigation_completed_utc": (NOW - timedelta(minutes=119)).isoformat(),
                "hydrawise_clear_since_utc": (NOW - timedelta(minutes=119)).isoformat(),
            }
        )
        calls = []
        output, _store = self._run(
            state,
            result(),
            start=lambda *args: calls.append(args) or {},
        )
        self.assertEqual(output.decision_code, "HYDRAWISE_CLEAR_CONFIRMATION_HOLD")
        self.assertEqual(calls, [])

    def test_harmless_data_gap_uses_two_minute_confirmation_not_irrigation_hold(self) -> None:
        base = AutomationState(
            parked_by_automation=True,
            automation_park_source="training",
            automation_restart_allowed=True,
            park_command_sent_utc=(NOW - timedelta(hours=2)).isoformat(),
            park_confirmed_utc=(NOW - timedelta(hours=2)).isoformat(),
            hydrawise_clear_since_utc=(NOW - timedelta(minutes=1)).isoformat(),
            hydrawise_clear_origin="DATA_GAP",
            last_hydrawise_success_utc=(NOW - timedelta(minutes=1)).isoformat(),
            last_hydrawise_active_count=0,
        )
        calls = []
        held, _ = self._run(
            base,
            result(),
            start=lambda *args: calls.append(args) or {},
        )
        self.assertEqual(held.decision_code, "HYDRAWISE_CLEAR_CONFIRMATION_HOLD")
        self.assertEqual(
            held.details["hydrawise_release_gate"]["required_clear_minutes"],
            2,
        )
        self.assertEqual(calls, [])

        confirmed = AutomationState.from_mapping(
            {
                **base.to_dict(),
                "hydrawise_clear_since_utc": (NOW - timedelta(minutes=2)).isoformat(),
            }
        )
        started, _ = self._run(
            confirmed,
            result(),
            start=lambda *args: calls.append(args) or {"accepted": True},
        )
        self.assertEqual(started.decision_code, "CONTINUOUS_MOWING_START_SENT")
        self.assertEqual(len(calls), 1)

    def test_possible_irrigation_during_gap_keeps_full_hold(self) -> None:
        state = AutomationState(
            parked_by_automation=True,
            automation_park_source="irrigation_outage",
            automation_restart_allowed=True,
            hydrawise_clear_since_utc=(NOW - timedelta(minutes=10)).isoformat(),
            hydrawise_clear_origin="POSSIBLE_IRRIGATION_DURING_GAP",
            last_hydrawise_success_utc=(NOW - timedelta(minutes=1)).isoformat(),
            last_hydrawise_active_count=0,
        )
        output, _ = self._run(state, result())
        self.assertEqual(output.decision_code, "HYDRAWISE_CLEAR_CONFIRMATION_HOLD")
        self.assertEqual(
            output.details["hydrawise_release_gate"]["required_clear_minutes"],
            150,
        )

    def test_owned_irrigation_park_can_restart_in_cancelled_training_window(self) -> None:
        state = irrigation_state(phase="COMPLETE_HOLD")
        state = AutomationState.from_mapping(
            {
                **state.to_dict(),
                "irrigation_completed_utc": (NOW - timedelta(minutes=150)).isoformat(),
                "hydrawise_clear_since_utc": (NOW - timedelta(minutes=150)).isoformat(),
                "last_hydrawise_success_utc": (NOW - timedelta(minutes=1)).isoformat(),
            }
        )
        calls = []
        output, store = self._run(
            state,
            result(
                activity="PARKED_IN_CS",
                override_action="FORCE_MOW",
                external_reason_id=None,
            ),
            start=lambda *args: calls.append(args) or {"accepted": True},
        )
        self.assertEqual(output.decision_code, "CONTINUOUS_MOWING_START_SENT")
        self.assertEqual(len(calls), 1)
        self.assertTrue(store.load().continuous_mowing_owned)
        self.assertIsNone(store.load().irrigation_phase)

    def test_recovered_external_irrigation_failure_restarts_owned_park(self) -> None:
        state = AutomationState(
            parked_by_automation=True,
            automation_park_source="irrigation",
            automation_restart_allowed=True,
            park_command_sent_utc=(NOW - timedelta(hours=5)).isoformat(),
            park_confirmed_utc=(NOW - timedelta(hours=5)).isoformat(),
            hydrawise_clear_since_utc=(NOW - timedelta(minutes=150)).isoformat(),
            hydrawise_clear_origin="IRRIGATION_END",
            last_hydrawise_success_utc=(NOW - timedelta(minutes=1)).isoformat(),
        )
        calls = []
        output, store = self._run(
            state,
            result(
                activity="PARKED_IN_CS",
                override_action="FORCE_MOW",
                external_reason_id=None,
            ),
            start=lambda *args: calls.append(args) or {"accepted": True},
        )
        self.assertEqual(output.decision_code, "CONTINUOUS_MOWING_START_SENT")
        self.assertEqual(len(calls), 1)
        self.assertTrue(store.load().continuous_mowing_owned)

    def test_manual_pause_still_blocks_restart_after_owned_irrigation_park(self) -> None:
        state = irrigation_state(phase="COMPLETE_HOLD")
        state = AutomationState.from_mapping(
            {
                **state.to_dict(),
                "irrigation_completed_utc": (NOW - timedelta(minutes=150)).isoformat(),
                "hydrawise_clear_since_utc": (NOW - timedelta(minutes=150)).isoformat(),
                "last_hydrawise_success_utc": (NOW - timedelta(minutes=1)).isoformat(),
            }
        )
        calls = []
        output, _store = self._run(
            state,
            result(
                activity="NOT_APPLICABLE",
                mower_state="PAUSED",
                override_action="FORCE_MOW",
                external_reason_id=None,
            ),
            start=lambda *args: calls.append(args) or {"accepted": True},
        )
        self.assertEqual(output.decision_code, "MANUAL_OR_ERROR_HOLD")
        self.assertEqual(calls, [])

    def test_explicit_operator_start_can_release_owned_manual_pause(self) -> None:
        state = AutomationState(
            parked_by_automation=True,
            automation_park_source="continuous",
            automation_restart_allowed=True,
            park_command_sent_utc=(NOW - timedelta(hours=1)).isoformat(),
            last_mower_activity="NOT_APPLICABLE",
            last_mower_state="PAUSED",
            hydrawise_clear_since_utc=(NOW - timedelta(minutes=120)).isoformat(),
            hydrawise_clear_origin="DATA_GAP",
            last_hydrawise_success_utc=(NOW - timedelta(minutes=1)).isoformat(),
            operator_request_id="start-paused-owned",
            operator_request_action="START_MOWING",
            operator_requested_utc=(NOW - timedelta(seconds=10)).isoformat(),
            operator_request_expires_utc=(NOW + timedelta(minutes=10)).isoformat(),
            operator_request_status="PENDING",
        )
        calls = []

        output, store = self._run(
            state,
            result(
                activity="NOT_APPLICABLE",
                mower_state="PAUSED",
                override_action="NOT_ACTIVE",
                external_reason_id=None,
            ),
            start=lambda *args: calls.append(args) or {"accepted": True},
        )

        self.assertEqual(output.decision_code, "CONTINUOUS_MOWING_START_SENT")
        self.assertEqual(len(calls), 1)
        self.assertEqual(store.load().operator_request_status, "COMPLETED")
        self.assertTrue(store.load().continuous_mowing_owned)

    def test_explicit_operator_start_does_not_adopt_unowned_manual_pause(self) -> None:
        state = AutomationState(
            hydrawise_clear_since_utc=(NOW - timedelta(minutes=120)).isoformat(),
            hydrawise_clear_origin="DATA_GAP",
            last_hydrawise_success_utc=(NOW - timedelta(minutes=1)).isoformat(),
            operator_request_id="start-paused-unowned",
            operator_request_action="START_MOWING",
            operator_requested_utc=(NOW - timedelta(seconds=10)).isoformat(),
            operator_request_expires_utc=(NOW + timedelta(minutes=10)).isoformat(),
            operator_request_status="PENDING",
        )
        calls = []

        output, store = self._run(
            state,
            result(
                activity="NOT_APPLICABLE",
                mower_state="PAUSED",
                override_action="NOT_ACTIVE",
                external_reason_id=None,
            ),
            start=lambda *args: calls.append(args) or {"accepted": True},
        )

        self.assertEqual(output.decision_code, "MANUAL_OR_ERROR_HOLD")
        self.assertEqual(calls, [])
        self.assertEqual(store.load().operator_request_status, "PENDING")

    def test_paused_status_after_observed_dock_can_unlock_irrigation(self) -> None:
        initial = irrigation_state(phase="READY")
        initial = AutomationState.from_mapping(
            {
                **initial.to_dict(),
                "park_confirmed_utc": None,
                "park_confirmed_observations": 0,
                "last_mower_activity": "GOING_HOME",
                "last_mower_state": "IN_OPERATION",
            }
        )
        zone_calls = []

        first, store = self._run(
            initial,
            result(block_source="irrigation", activity="PARKED_IN_CS"),
            zone=lambda *args: zone_calls.append(args) or {"message_type": "info"},
        )
        self.assertEqual(first.decision_code, "IRRIGATION_WAIT_FOR_CONFIRMED_PARK")
        self.assertEqual(store.load().park_confirmed_observations, 1)

        paused_cycle = result(
            block_source="irrigation",
            activity="NOT_APPLICABLE",
            mower_state="PAUSED",
            override_action="NOT_ACTIVE",
            external_reason_id=None,
        )
        second, _ = self._continue(
            store,
            paused_cycle,
            now=NOW + timedelta(minutes=1),
            zone=lambda *args: zone_calls.append(args) or {"message_type": "info"},
        )

        self.assertEqual(second.decision_code, "IRRIGATION_ZONE_START_SENT")
        self.assertEqual(len(zone_calls), 1)
        self.assertEqual(store.load().park_confirmed_observations, 2)

    def test_expired_never_started_ready_plan_is_released_only_after_confirmation(self) -> None:
        expired_plan = zones(start_utc=NOW - timedelta(hours=4))
        initial = irrigation_state(phase="READY")
        initial = AutomationState.from_mapping(
            {
                **initial.to_dict(),
                "irrigation_plan_json": json.dumps(expired_plan),
                "irrigation_suspension_until_utc": (
                    NOW - timedelta(minutes=10)
                ).isoformat(),
                "hydrawise_clear_since_utc": (
                    NOW - timedelta(minutes=10)
                ).isoformat(),
                "hydrawise_clear_origin": "DATA_GAP",
                "last_hydrawise_success_utc": (
                    NOW - timedelta(minutes=1)
                ).isoformat(),
            }
        )
        cycle = suspended_result(activity="PARKED_IN_CS")
        park_calls = []
        start_calls = []
        zone_calls = []

        first, store = self._run(
            initial,
            cycle,
            park=lambda *args: park_calls.append(args),
            start=lambda *args: start_calls.append(args),
            zone=lambda *args: zone_calls.append(args),
        )
        self.assertEqual(first.decision_code, "IRRIGATION_EXPIRED_READY_CONFIRMING")
        self.assertEqual(store.load().irrigation_phase, "READY")

        second, _ = self._continue(
            store,
            cycle,
            now=NOW + timedelta(minutes=2),
            park=lambda *args: park_calls.append(args),
            start=lambda *args: start_calls.append(args),
            zone=lambda *args: zone_calls.append(args),
        )
        self.assertEqual(second.decision_code, "IRRIGATION_EXPIRED_READY_RELEASED")
        self.assertIsNone(store.load().irrigation_phase)
        self.assertEqual(
            store.load().irrigation_cancelled_without_run_utc,
            (NOW + timedelta(minutes=2)).isoformat(),
        )
        self.assertEqual(park_calls, [])
        self.assertEqual(start_calls, [])
        self.assertEqual(zone_calls, [])

    def test_expired_ready_plan_is_not_released_while_a_zone_is_active(self) -> None:
        expired_plan = zones(start_utc=NOW - timedelta(hours=4))
        initial = irrigation_state(phase="READY")
        initial = AutomationState.from_mapping(
            {
                **initial.to_dict(),
                "irrigation_plan_json": json.dumps(expired_plan),
                "irrigation_suspension_until_utc": (
                    NOW - timedelta(minutes=10)
                ).isoformat(),
                "hydrawise_clear_since_utc": None,
            }
        )
        zone_calls = []

        output, store = self._run(
            initial,
            result(active_ids=[RELAYS[0]], clear=False),
            zone=lambda *args: zone_calls.append(args),
        )

        self.assertNotEqual(output.decision_code, "IRRIGATION_EXPIRED_READY_RELEASED")
        self.assertEqual(store.load().irrigation_phase, "FAILED")
        self.assertEqual(zone_calls, [])

    def test_ready_plan_is_not_released_before_suspension_expiry(self) -> None:
        expired_plan = zones(start_utc=NOW - timedelta(hours=4))
        initial = irrigation_state(phase="READY")
        initial = AutomationState.from_mapping(
            {
                **initial.to_dict(),
                "irrigation_plan_json": json.dumps(expired_plan),
                "irrigation_suspension_until_utc": (
                    NOW + timedelta(minutes=10)
                ).isoformat(),
                "park_confirmed_utc": None,
                "park_confirmed_observations": 0,
            }
        )

        output, store = self._run(
            initial,
            suspended_result(activity="GOING_HOME"),
        )

        self.assertEqual(output.decision_code, "IRRIGATION_WAIT_FOR_CONFIRMED_PARK")
        self.assertEqual(store.load().irrigation_phase, "READY")
        self.assertIsNone(store.load().irrigation_cancelled_without_run_utc)

    def test_expired_ready_plan_with_any_start_evidence_stays_fail_closed(self) -> None:
        expired_plan = zones(start_utc=NOW - timedelta(hours=4))
        for evidence in (
            {"irrigation_zone_start_reserved_utc": (NOW - timedelta(hours=3)).isoformat()},
            {
                "hydrawise_clear_origin": "IRRIGATION_END",
                "hydrawise_clear_since_utc": (NOW - timedelta(minutes=10)).isoformat(),
            },
            {"last_hydrawise_active_count": 1},
        ):
            with self.subTest(evidence=evidence):
                initial = irrigation_state(phase="READY")
                initial = AutomationState.from_mapping(
                    {
                        **initial.to_dict(),
                        "irrigation_plan_json": json.dumps(expired_plan),
                        "irrigation_suspension_until_utc": (
                            NOW - timedelta(minutes=10)
                        ).isoformat(),
                        "hydrawise_clear_since_utc": (
                            NOW - timedelta(minutes=10)
                        ).isoformat(),
                        "hydrawise_clear_origin": "DATA_GAP",
                        "last_hydrawise_success_utc": (
                            NOW - timedelta(minutes=1)
                        ).isoformat(),
                        **evidence,
                    }
                )
                cycle = suspended_result(activity="PARKED_IN_CS")
                cycle.details["mower"]["connected"] = False

                output, store = self._run(initial, cycle)

                self.assertNotEqual(
                    output.decision_code,
                    "IRRIGATION_EXPIRED_READY_RELEASED",
                )
                self.assertNotEqual(
                    output.decision_code,
                    "IRRIGATION_EXPIRED_READY_CONFIRMING",
                )
                self.assertEqual(store.load().irrigation_phase, "READY")
                self.assertIsNone(
                    store.load().irrigation_cancelled_without_run_utc
                )

    def test_expired_lease_failure_without_water_recovers_then_mows(self) -> None:
        expired_plan = zones(start_utc=NOW - timedelta(hours=4))
        initial = irrigation_state(phase="FAILED")
        initial = AutomationState.from_mapping(
            {
                **initial.to_dict(),
                "irrigation_plan_json": json.dumps(expired_plan),
                "irrigation_failed_reason": EXPIRED_IRRIGATION_PLAN_LEASE_FAILURE,
                "irrigation_suspension_until_utc": (
                    NOW - timedelta(minutes=10)
                ).isoformat(),
                "hydrawise_clear_since_utc": (
                    NOW - timedelta(minutes=10)
                ).isoformat(),
                "hydrawise_clear_origin": "DATA_GAP",
                "last_hydrawise_success_utc": (
                    NOW - timedelta(minutes=1)
                ).isoformat(),
                "last_hydrawise_active_count": 0,
            }
        )
        cycle = result(activity="PARKED_IN_CS")
        park_calls = []
        start_calls = []
        zone_calls = []

        first, store = self._run(
            initial,
            cycle,
            park=lambda *args: park_calls.append(args),
            start=lambda *args: start_calls.append(args) or {"accepted": True},
            zone=lambda *args: zone_calls.append(args),
        )
        self.assertEqual(
            first.decision_code,
            "IRRIGATION_EXPIRED_FAILED_CONFIRMING",
        )
        self.assertEqual(store.load().irrigation_phase, "FAILED")

        released, _ = self._continue(
            store,
            cycle,
            now=NOW + timedelta(minutes=2),
            park=lambda *args: park_calls.append(args),
            start=lambda *args: start_calls.append(args) or {"accepted": True},
            zone=lambda *args: zone_calls.append(args),
        )
        self.assertEqual(
            released.decision_code,
            "IRRIGATION_EXPIRED_FAILED_RELEASED",
        )
        self.assertIsNone(store.load().irrigation_phase)
        self.assertIsNotNone(store.load().irrigation_cancelled_without_run_utc)
        self.assertEqual(park_calls, [])
        self.assertEqual(start_calls, [])
        self.assertEqual(zone_calls, [])

        resumed, _ = self._continue(
            store,
            cycle,
            now=NOW + timedelta(minutes=3),
            start=lambda *args: start_calls.append(args) or {"accepted": True},
        )
        self.assertEqual(resumed.decision_code, "CONTINUOUS_MOWING_START_SENT")
        self.assertEqual(len(start_calls), 1)

    def test_expired_lease_failure_with_start_evidence_never_auto_recovers(self) -> None:
        expired_plan = zones(start_utc=NOW - timedelta(hours=4))
        for evidence in (
            {"irrigation_zone_start_reserved_utc": (NOW - timedelta(hours=3)).isoformat()},
            {"irrigation_completed_relay_ids_json": json.dumps([RELAYS[0]])},
            {
                "hydrawise_clear_origin": "IRRIGATION_END",
                "hydrawise_clear_since_utc": (NOW - timedelta(hours=3)).isoformat(),
            },
        ):
            with self.subTest(evidence=evidence):
                initial = irrigation_state(phase="FAILED")
                initial = AutomationState.from_mapping(
                    {
                        **initial.to_dict(),
                        "irrigation_plan_json": json.dumps(expired_plan),
                        "irrigation_failed_reason": (
                            EXPIRED_IRRIGATION_PLAN_LEASE_FAILURE
                        ),
                        "irrigation_suspension_until_utc": (
                            NOW - timedelta(minutes=10)
                        ).isoformat(),
                        "hydrawise_clear_since_utc": (
                            NOW - timedelta(minutes=10)
                        ).isoformat(),
                        "hydrawise_clear_origin": "DATA_GAP",
                        "last_hydrawise_success_utc": (
                            NOW - timedelta(minutes=1)
                        ).isoformat(),
                        **evidence,
                    }
                )

                output, store = self._run(
                    initial,
                    result(activity="PARKED_IN_CS"),
                )

                self.assertEqual(output.decision_code, "IRRIGATION_FAILED_HOLD")
                self.assertEqual(store.load().irrigation_phase, "FAILED")
                self.assertIsNone(
                    store.load().irrigation_cancelled_without_run_utc
                )

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

    def test_start_command_expires_at_the_park_command_deadline(self) -> None:
        state = AutomationState(
            parked_by_automation=True,
            automation_park_source="continuous",
            automation_restart_allowed=True,
            park_command_sent_utc=(NOW - timedelta(hours=1)).isoformat(),
            park_confirmed_utc=(NOW - timedelta(minutes=2)).isoformat(),
            hydrawise_clear_since_utc=(NOW - timedelta(minutes=120)).isoformat(),
            last_hydrawise_success_utc=(NOW - timedelta(minutes=1)).isoformat(),
        )
        calls = []
        output, store = self._run(
            state,
            result(
                activity="PARKED_IN_CS",
                battery=100,
                window_end=NOW + timedelta(minutes=60),
                irrigation_start=NOW + timedelta(hours=4),
            ),
            start=lambda *args: calls.append(args) or {"accepted": True},
        )
        self.assertEqual(output.decision_code, "CONTINUOUS_MOWING_START_SENT")
        self.assertEqual(calls[0][-1], 50)
        self.assertEqual(
            store.load().continuous_mowing_window_end_utc,
            (NOW + timedelta(minutes=50)).isoformat(),
        )

    def test_regular_irrigation_start_imposes_a_hard_forty_minute_deadline(self) -> None:
        irrigation_start = NOW + timedelta(minutes=120)
        state = AutomationState(
            parked_by_automation=True,
            automation_park_source="continuous",
            automation_restart_allowed=True,
            park_command_sent_utc=(NOW - timedelta(hours=1)).isoformat(),
            park_confirmed_utc=(NOW - timedelta(minutes=2)).isoformat(),
            hydrawise_clear_since_utc=(NOW - timedelta(minutes=120)).isoformat(),
            last_hydrawise_success_utc=(NOW - timedelta(minutes=1)).isoformat(),
        )
        calls = []
        output, store = self._run(
            state,
            result(
                activity="PARKED_IN_CS",
                battery=100,
                window_end=NOW + timedelta(minutes=180),
                irrigation_start=irrigation_start,
            ),
            start=lambda *args: calls.append(args) or {"accepted": True},
        )
        expected_deadline = irrigation_start - timedelta(minutes=40)
        self.assertEqual(output.decision_code, "CONTINUOUS_MOWING_START_SENT")
        self.assertEqual(calls[0][-1], 80)
        self.assertEqual(
            store.load().continuous_mowing_window_end_utc,
            expected_deadline.isoformat(),
        )
        self.assertEqual(
            output.details["mower_outage_guard"]["command_deadline_utc"],
            expected_deadline.isoformat(),
        )

    def test_running_mower_gets_a_shorter_device_command_if_deadline_changed(self) -> None:
        irrigation_start = NOW + timedelta(minutes=120)
        calls = []
        state = AutomationState(
            continuous_mowing_owned=True,
            continuous_mowing_work_area_id=849199,
            continuous_mowing_window_end_utc=(NOW + timedelta(minutes=180)).isoformat(),
            last_mower_activity="MOWING",
            hydrawise_clear_since_utc=(NOW - timedelta(minutes=120)).isoformat(),
            last_hydrawise_success_utc=(NOW - timedelta(minutes=1)).isoformat(),
        )
        output, store = self._run(
            state,
            result(
                activity="MOWING",
                window_end=NOW + timedelta(minutes=180),
                irrigation_start=irrigation_start,
            ),
            start=lambda *args: calls.append(args) or {"accepted": True},
        )
        expected_deadline = irrigation_start - timedelta(minutes=40)
        self.assertEqual(
            output.decision_code,
            "CONTINUOUS_MOWING_FAILSAFE_REFRESHED",
        )
        self.assertEqual(calls[0][-1], 80)
        self.assertTrue(output.details["start_action"]["failsafe_refresh"])
        self.assertEqual(
            store.load().continuous_mowing_window_end_utc,
            expected_deadline.isoformat(),
        )

    def test_already_bounded_running_mower_is_not_recommanded(self) -> None:
        calls = []
        state = AutomationState(
            continuous_mowing_owned=True,
            continuous_mowing_work_area_id=849199,
            continuous_mowing_window_end_utc=(NOW + timedelta(minutes=60)).isoformat(),
            last_mower_activity="MOWING",
            hydrawise_clear_since_utc=(NOW - timedelta(minutes=120)).isoformat(),
            last_hydrawise_success_utc=(NOW - timedelta(minutes=1)).isoformat(),
        )
        output, _store = self._run(
            state,
            result(
                activity="MOWING",
                window_end=NOW + timedelta(minutes=180),
                irrigation_start=NOW + timedelta(minutes=120),
            ),
            start=lambda *args: calls.append(args) or {"accepted": True},
        )
        self.assertEqual(output.decision_code, "CONTINUOUS_MOWING_ACTIVE")
        self.assertEqual(calls, [])

    def test_irrigation_deadline_forces_park_even_if_planner_block_is_missing(self) -> None:
        park_calls = []
        start_calls = []
        output, store = self._run(
            AutomationState(
                continuous_mowing_owned=True,
                continuous_mowing_work_area_id=849199,
                continuous_mowing_window_end_utc=(NOW + timedelta(hours=12)).isoformat(),
            ),
            result(
                activity="MOWING",
                window_end=NOW + timedelta(hours=12),
                irrigation_start=NOW + timedelta(minutes=40),
            ),
            park=lambda *args: park_calls.append(args) or {"accepted": True},
            start=lambda *args: start_calls.append(args) or {"accepted": True},
        )
        self.assertEqual(output.decision_code, "PARK_COMMAND_SENT")
        self.assertEqual(len(park_calls), 1)
        self.assertEqual(start_calls, [])
        self.assertEqual(store.load().automation_park_source, "irrigation")

    def test_irrigation_deadline_holds_a_charging_mower_in_the_dock(self) -> None:
        park_calls = []
        output, store = self._run(
            AutomationState(
                continuous_mowing_owned=True,
                continuous_mowing_work_area_id=849199,
                continuous_mowing_window_end_utc=(NOW + timedelta(hours=12)).isoformat(),
            ),
            result(
                activity="CHARGING",
                override_action="FORCE_MOW",
                window_end=NOW + timedelta(hours=12),
                irrigation_start=NOW + timedelta(minutes=40),
            ),
            park=lambda *args: park_calls.append(args) or {"accepted": True},
        )
        self.assertEqual(output.decision_code, "PARK_COMMAND_SENT")
        self.assertEqual(len(park_calls), 1)
        self.assertEqual(store.load().automation_park_source, "irrigation")
        self.assertTrue(store.load().parked_by_automation)

    def test_owned_mower_turns_around_before_dock_when_battery_is_sufficient(self) -> None:
        window_end = NOW + timedelta(hours=12)
        clear_since = NOW - timedelta(minutes=120)
        initial_start = CommandIntent(
            action="START",
            target="mower-1",
            reason=f"continuous|{window_end.isoformat()}|hydrawise-clear:{clear_since.isoformat()}",
            valid_until_utc=window_end,
        )
        state = AutomationState(
            continuous_mowing_owned=True,
            continuous_mowing_work_area_id=849199,
            continuous_mowing_window_end_utc=window_end.isoformat(),
            last_mower_activity="MOWING",
            hydrawise_clear_since_utc=clear_since.isoformat(),
            last_hydrawise_success_utc=(NOW - timedelta(minutes=1)).isoformat(),
            last_command_fingerprint=initial_start.fingerprint,
            last_command_utc=(NOW - timedelta(minutes=1)).isoformat(),
        )
        calls = []
        output, store = self._run(
            state,
            result(activity="GOING_HOME", battery=65),
            start=lambda *args: calls.append(args) or {"accepted": True},
        )
        self.assertEqual(output.decision_code, "CONTINUOUS_MOWING_TURNAROUND_SENT")
        self.assertEqual(len(calls), 1)
        self.assertTrue(output.details["start_action"]["turnaround_before_dock"])
        self.assertTrue(store.load().continuous_mowing_owned)

    def test_low_battery_homeward_mower_is_never_turned_around(self) -> None:
        state = AutomationState(
            continuous_mowing_owned=True,
            continuous_mowing_work_area_id=849199,
            continuous_mowing_window_end_utc=(NOW + timedelta(hours=12)).isoformat(),
            last_mower_activity="MOWING",
            hydrawise_clear_since_utc=(NOW - timedelta(minutes=120)).isoformat(),
            last_hydrawise_success_utc=(NOW - timedelta(minutes=1)).isoformat(),
        )
        calls = []
        output, _store = self._run(
            state,
            result(activity="GOING_HOME", battery=59),
            start=lambda *args: calls.append(args) or {"accepted": True},
        )
        self.assertEqual(output.decision_code, "MOWER_LOW_BATTERY_HOME_ALLOWED")
        self.assertEqual(calls, [])

    def test_unowned_homeward_mower_is_not_interrupted(self) -> None:
        state = AutomationState(
            hydrawise_clear_since_utc=(NOW - timedelta(minutes=120)).isoformat(),
            last_hydrawise_success_utc=(NOW - timedelta(minutes=1)).isoformat(),
        )
        calls = []
        output, _store = self._run(
            state,
            result(activity="GOING_HOME", battery=100),
            start=lambda *args: calls.append(args) or {"accepted": True},
        )
        self.assertEqual(output.decision_code, "WAIT_FOR_MOWER_AT_STATION")
        self.assertEqual(calls, [])

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

    def test_pause_is_sent_once_per_zone_and_only_active_after_two_fresh_confirmations(self) -> None:
        initial = AutomationState(
            operator_request_id="pause-1",
            operator_request_action="PAUSE_IRRIGATION_UNTIL",
            operator_requested_utc=(NOW - timedelta(seconds=10)).isoformat(),
            operator_request_expires_utc=(NOW + timedelta(minutes=10)).isoformat(),
            operator_request_status="PENDING",
            operator_request_irrigation_schedule_json=json.dumps(
                {"pauseUntil": (NOW + timedelta(days=3)).isoformat()}
            ),
        )
        accepted, store = self._run(initial, result())
        self.assertEqual(accepted.decision_code, "IRRIGATION_SCHEDULE_REQUEST_ACCEPTED")
        self.assertEqual(
            json.loads(store.load().irrigation_schedule_override_json or "{}")["status"],
            "APPLYING",
        )
        calls = []
        for minute in range(1, 8):
            self._continue(
                store,
                result(),
                now=NOW + timedelta(minutes=minute),
                suspend=lambda *args: calls.append(args) or {"message_type": "info"},
            )
        self.assertEqual([call[1] for call in calls], sorted(RELAYS))
        self.assertEqual(len(calls), 7)
        self.assertEqual(
            json.loads(store.load().irrigation_schedule_override_json or "{}")["status"],
            "CONFIRMING",
        )
        for minute in (8, 9):
            self._continue(
                store,
                suspended_result(),
                now=NOW + timedelta(minutes=minute),
                suspend=lambda *_: self.fail("Nach allen sieben Befehlen kein weiterer Schreibzugriff"),
            )
            self.assertNotEqual(
                json.loads(store.load().irrigation_schedule_override_json or "{}")["status"],
                "ACTIVE",
            )
        self._continue(
            store,
            suspended_result(),
            now=NOW + timedelta(minutes=10),
            suspend=lambda *_: self.fail("Bestätigung darf keinen Befehl senden"),
        )
        self.assertEqual(
            json.loads(store.load().irrigation_schedule_override_json or "{}")["status"],
            "ACTIVE",
        )

    def test_changed_target_is_rejected_before_any_suspend_command(self) -> None:
        original_zones = zones(start_utc=NOW + timedelta(hours=13))
        import hashlib

        source_id = hashlib.sha256(
            json.dumps(
                original_zones,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()
        override = {
            "version": 1,
            "kind": "SKIP_NEXT",
            "status": "VERIFYING",
            "verify_since_utc": NOW.isoformat(),
            "source_plan_id": source_id,
            "source_start_utc": (NOW + timedelta(hours=13)).isoformat(),
            "source_end_utc": (NOW + timedelta(hours=15)).isoformat(),
            "suspend_until_utc": (NOW + timedelta(hours=16)).isoformat(),
            "source_zones": original_zones,
            "commanded_relay_ids": [],
        }
        calls = []
        output, store = self._run(
            AutomationState(irrigation_schedule_override_json=json.dumps(override)),
            result(irrigation_start=NOW + timedelta(hours=14)),
            now=NOW + timedelta(minutes=2),
            suspend=lambda *args: calls.append(args),
        )
        self.assertEqual(output.decision_code, "IRRIGATION_SCHEDULE_TARGET_CHANGED")
        self.assertEqual(calls, [])
        self.assertEqual(
            json.loads(store.load().irrigation_schedule_override_json or "{}")["status"],
            "REJECTED",
        )

    def test_three_write_failures_stop_without_false_activation(self) -> None:
        override = {
            "version": 1,
            "kind": "PAUSE",
            "status": "APPLYING",
            "suspend_until_utc": (NOW + timedelta(days=2)).isoformat(),
            "commanded_relay_ids": [],
        }
        store = InMemoryStateStore(
            AutomationState(irrigation_schedule_override_json=json.dumps(override))
        )
        calls = []

        def fail(*args):
            calls.append(args)
            raise RuntimeError("offline")

        outputs = []
        for minute in range(3):
            output, _ = self._continue(
                store,
                result(),
                now=NOW + timedelta(minutes=minute),
                suspend=fail,
            )
            outputs.append(output.decision_code)
        self.assertEqual(len(calls), 3)
        self.assertEqual(outputs[-1], "IRRIGATION_SCHEDULE_APPLY_FAILED")
        saved_override = json.loads(store.load().irrigation_schedule_override_json or "{}")
        self.assertEqual(saved_override["status"], "REJECTED")
        self.assertEqual(saved_override["commanded_relay_ids"], [])

    def test_training_park_always_precedes_schedule_write(self) -> None:
        override = {
            "version": 1,
            "kind": "PAUSE",
            "status": "APPLYING",
            "suspend_until_utc": (NOW + timedelta(days=2)).isoformat(),
            "commanded_relay_ids": [],
        }
        suspend_calls = []
        park_calls = []
        output, _store = self._run(
            AutomationState(irrigation_schedule_override_json=json.dumps(override)),
            result(activity="MOWING", block_source="training", command="PARK"),
            suspend=lambda *args: suspend_calls.append(args) or {"ok": True},
            park=lambda *args: park_calls.append(args) or {"ok": True},
        )
        self.assertEqual(output.decision_code, "PARK_COMMAND_SENT")
        self.assertEqual(len(park_calls), 1)
        self.assertEqual(suspend_calls, [])

    def test_early_resume_parks_mower_before_lifting_suspension(self) -> None:
        active_pause = {
            "version": 1,
            "kind": "PAUSE",
            "status": "ACTIVE",
            "suspend_until_utc": (NOW + timedelta(days=3)).isoformat(),
            "commanded_relay_ids": RELAYS,
        }
        initial = AutomationState(
            operator_request_id="resume-1",
            operator_request_action="RESUME_IRRIGATION_SCHEDULE",
            operator_requested_utc=(NOW - timedelta(seconds=10)).isoformat(),
            operator_request_expires_utc=(NOW + timedelta(minutes=10)).isoformat(),
            operator_request_status="PENDING",
            operator_request_irrigation_schedule_json="{}",
            irrigation_schedule_override_json=json.dumps(active_pause),
        )
        suspend_calls = []
        park_calls = []
        _accepted, store = self._run(
            initial,
            suspended_result(activity="MOWING"),
            suspend=lambda *args: suspend_calls.append(args),
            park=lambda *args: park_calls.append(args) or {"accepted": True},
        )
        self.assertEqual(suspend_calls, [])
        self._continue(
            store,
            suspended_result(activity="MOWING"),
            now=NOW + timedelta(minutes=1),
            suspend=lambda *args: suspend_calls.append(args),
            park=lambda *args: park_calls.append(args) or {"accepted": True},
        )
        self.assertEqual(len(park_calls), 1)
        self.assertEqual(suspend_calls, [])
        self._continue(
            store,
            suspended_result(activity="PARKED_IN_CS"),
            now=NOW + timedelta(minutes=2),
            suspend=lambda *args: suspend_calls.append(args),
        )
        self.assertEqual(suspend_calls, [])
        self._continue(
            store,
            suspended_result(activity="PARKED_IN_CS"),
            now=NOW + timedelta(minutes=3),
            suspend=lambda *args: suspend_calls.append(args) or {"message_type": "info"},
        )
        self.assertEqual(len(suspend_calls), 1)

    def test_custom_next_run_parks_early_waits_for_selected_time_and_keeps_zone_choices(self) -> None:
        requested_zones = [
            {
                "zone": zone,
                "runSeconds": 600 + zone * 60,
                "selected": zone != 2,
            }
            for zone in range(1, 8)
        ]
        initial = AutomationState(
            operator_request_id="custom-1",
            operator_request_action="CUSTOMIZE_NEXT_IRRIGATION",
            operator_requested_utc=(NOW - timedelta(seconds=10)).isoformat(),
            operator_request_expires_utc=(NOW + timedelta(minutes=10)).isoformat(),
            operator_request_status="PENDING",
            operator_request_irrigation_schedule_json=json.dumps(
                {
                    "desiredStart": (NOW + timedelta(minutes=60)).isoformat(),
                    "zones": requested_zones,
                }
            ),
        )
        _accepted, store = self._run(initial, result())
        suspend_calls = []
        for minute in range(1, 9):
            self._continue(
                store,
                result(),
                now=NOW + timedelta(minutes=minute),
                suspend=lambda *args: suspend_calls.append(args) or {"ok": True},
            )
        self.assertEqual(len(suspend_calls), 7)
        for minute in (9, 10, 11):
            self._continue(
                store,
                suspended_result(activity="MOWING"),
                now=NOW + timedelta(minutes=minute),
                suspend=lambda *_: self.fail("Bestätigung darf keine Zone erneut ändern"),
            )
        active_override = json.loads(
            store.load().irrigation_schedule_override_json or "{}"
        )
        self.assertEqual(active_override["status"], "ACTIVE")
        self.assertFalse(active_override["zones"][1]["selected"])

        park_calls = []
        zone_calls = []
        parked, _ = self._continue(
            store,
            suspended_result(activity="MOWING"),
            now=NOW + timedelta(minutes=15),
            park=lambda *args: park_calls.append(args) or {"ok": True},
            zone=lambda *args: zone_calls.append(args) or {"ok": True},
        )
        self.assertEqual(parked.decision_code, "PARK_COMMAND_SENT")
        self.assertEqual(len(park_calls), 1)
        self.assertEqual(zone_calls, [])
        confirming_park, _ = self._continue(
            store,
            suspended_result(activity="PARKED_IN_CS"),
            now=NOW + timedelta(minutes=16),
            zone=lambda *args: zone_calls.append(args) or {"ok": True},
        )
        self.assertEqual(confirming_park.decision_code, "IRRIGATION_WAIT_FOR_CONFIRMED_PARK")
        waiting, _ = self._continue(
            store,
            suspended_result(activity="PARKED_IN_CS"),
            now=NOW + timedelta(minutes=17),
            zone=lambda *args: zone_calls.append(args) or {"ok": True},
        )
        self.assertEqual(waiting.decision_code, "IRRIGATION_CUSTOM_START_WAIT")
        self.assertEqual(zone_calls, [])

        reserved, _ = self._continue(
            store,
            suspended_result(activity="PARKED_IN_CS"),
            now=NOW + timedelta(minutes=60),
            zone=lambda *args: zone_calls.append(args) or {"ok": True},
        )
        if reserved.decision_code == "IRRIGATION_ZONE_START_RESERVED":
            started, _ = self._continue(
                store,
                suspended_result(activity="PARKED_IN_CS"),
                now=NOW + timedelta(minutes=61),
                zone=lambda *args: zone_calls.append(args) or {"ok": True},
            )
            self.assertEqual(started.decision_code, "IRRIGATION_ZONE_START_SENT")
        else:
            self.assertEqual(reserved.decision_code, "IRRIGATION_ZONE_START_SENT")
        self.assertEqual(len(zone_calls), 1)
        self.assertEqual(zone_calls[0][1], RELAYS[0])
        self.assertEqual(zone_calls[0][2], 660)

    def test_short_pause_parks_before_any_schedule_write(self) -> None:
        override = {
            "version": 1,
            "kind": "PAUSE",
            "status": "APPLYING",
            "suspend_until_utc": (NOW + timedelta(minutes=30)).isoformat(),
            "commanded_relay_ids": [],
        }
        suspend_calls = []
        park_calls = []
        output, _store = self._run(
            AutomationState(irrigation_schedule_override_json=json.dumps(override)),
            result(activity="MOWING"),
            suspend=lambda *args: suspend_calls.append(args) or {"ok": True},
            park=lambda *args: park_calls.append(args) or {"ok": True},
        )
        self.assertEqual(output.decision_code, "PARK_COMMAND_SENT")
        self.assertEqual(len(park_calls), 1)
        self.assertEqual(suspend_calls, [])

    def test_active_irrigation_parks_before_any_schedule_write(self) -> None:
        override = {
            "version": 1,
            "kind": "PAUSE",
            "status": "APPLYING",
            "suspend_until_utc": (NOW + timedelta(days=2)).isoformat(),
            "commanded_relay_ids": [],
        }
        active = result(activity="MOWING")
        active.details["hydrawise"]["active_relay_ids"] = [RELAYS[0]]
        active.details["hydrawise"]["safety"]["active_relay_ids"] = [RELAYS[0]]
        active.details["hydrawise"]["safety"]["clear_now"] = False
        suspend_calls = []
        park_calls = []
        output, _store = self._run(
            AutomationState(irrigation_schedule_override_json=json.dumps(override)),
            active,
            suspend=lambda *args: suspend_calls.append(args) or {"ok": True},
            park=lambda *args: park_calls.append(args) or {"ok": True},
        )
        self.assertEqual(
            output.decision_code,
            "PARK_COMMAND_SENT_FOR_HYDRAWISE_HOLD",
        )
        self.assertEqual(len(park_calls), 1)
        self.assertEqual(suspend_calls, [])

    def test_already_running_external_irrigation_is_observed_not_failed(self) -> None:
        active = result(
            activity="PARKED_IN_CS",
            active_ids=[RELAYS[0]],
            clear=False,
            irrigation_start=NOW - timedelta(minutes=5),
        )
        active.details["hydrawise"]["zones"][0]["running"] = True
        active.details["hydrawise"]["zone_observations"][0]["running"] = True
        suspend_calls = []
        zone_calls = []

        output, store = self._run(
            AutomationState(),
            active,
            suspend=lambda *args: suspend_calls.append(args),
            zone=lambda *args: zone_calls.append(args),
        )

        self.assertNotEqual(output.decision_code, "IRRIGATION_FAILED_HOLD")
        self.assertIsNone(store.load().irrigation_phase)
        self.assertIsNone(store.load().irrigation_failed_reason)
        self.assertTrue(output.details["external_irrigation_observation"]["active"])
        self.assertEqual(suspend_calls, [])
        self.assertEqual(zone_calls, [])

    def test_external_zone_transition_gap_is_observed_not_failed(self) -> None:
        transition = result(
            activity="PARKED_IN_CS",
            active_ids=[],
            clear=True,
            irrigation_start=NOW - timedelta(minutes=20),
        )
        state = AutomationState(
            hydrawise_clear_since_utc=NOW.isoformat(),
            hydrawise_clear_origin="IRRIGATION_END",
            last_hydrawise_success_utc=(NOW - timedelta(minutes=1)).isoformat(),
        )

        output, store = self._run(state, transition)

        self.assertNotEqual(output.decision_code, "IRRIGATION_FAILED_HOLD")
        self.assertIsNone(store.load().irrigation_phase)
        self.assertTrue(output.details["external_irrigation_observation"]["active"])

    def test_specific_stale_external_plan_failure_recovers_only_after_full_hold(self) -> None:
        state = AutomationState(
            parked_by_automation=True,
            automation_park_source="irrigation",
            automation_restart_allowed=False,
            park_command_sent_utc=(NOW - timedelta(hours=3)).isoformat(),
            park_confirmed_utc=(NOW - timedelta(hours=3)).isoformat(),
            irrigation_phase="FAILED",
            irrigation_failed_reason=(
                "RuntimeError: Hydrawise-Zonenplan enthält ungültige Werte."
            ),
            hydrawise_clear_since_utc=(NOW - timedelta(minutes=151)).isoformat(),
            hydrawise_clear_origin="IRRIGATION_END",
            last_hydrawise_success_utc=(NOW - timedelta(minutes=1)).isoformat(),
        )

        output, store = self._run(state, result())

        self.assertNotEqual(output.decision_code, "IRRIGATION_FAILED_HOLD")
        self.assertIsNone(store.load().irrigation_phase)
        self.assertIsNone(store.load().irrigation_failed_reason)
        self.assertTrue(output.details["irrigation_failure_recovery"]["recovered"])

    def test_partial_run_that_no_longer_fits_enters_drying_hold_and_then_restarts(self) -> None:
        state = irrigation_state(phase="READY")
        state = AutomationState.from_mapping(
            {
                **state.to_dict(),
                "irrigation_completed_relay_ids_json": json.dumps(RELAYS[:3]),
                "irrigation_suspension_until_utc": (
                    NOW + timedelta(minutes=10)
                ).isoformat(),
                "hydrawise_clear_since_utc": (
                    NOW - timedelta(minutes=2)
                ).isoformat(),
                "hydrawise_clear_origin": "IRRIGATION_END",
                "last_hydrawise_success_utc": (
                    NOW - timedelta(minutes=1)
                ).isoformat(),
            }
        )
        start_calls = []
        zone_calls = []

        stopped, store = self._run(
            state,
            result(activity="PARKED_IN_CS"),
            start=lambda *args: start_calls.append(args) or {"accepted": True},
            zone=lambda *args: zone_calls.append(args),
        )

        self.assertEqual(
            stopped.decision_code,
            "IRRIGATION_PARTIAL_RUN_COMPLETE_HOLD",
        )
        self.assertEqual(store.load().irrigation_phase, "COMPLETE_HOLD")
        self.assertIsNone(store.load().irrigation_failed_reason)
        self.assertEqual(start_calls, [])
        self.assertEqual(zone_calls, [])
        self.assertEqual(
            stopped.details["irrigation_partial_end"]["remaining_relay_ids"],
            RELAYS[3:],
        )

        held_state = AutomationState.from_mapping(
            {
                **store.load().to_dict(),
                "hydrawise_clear_since_utc": (
                    NOW - timedelta(minutes=149)
                ).isoformat(),
                "last_hydrawise_success_utc": (
                    NOW - timedelta(minutes=1)
                ).isoformat(),
            }
        )
        held, _ = self._run(
            held_state,
            result(activity="PARKED_IN_CS"),
            start=lambda *args: start_calls.append(args) or {"accepted": True},
        )
        self.assertEqual(held.decision_code, "HYDRAWISE_CLEAR_CONFIRMATION_HOLD")
        self.assertEqual(start_calls, [])

        resumable_state = AutomationState.from_mapping(
            {
                **store.load().to_dict(),
                "hydrawise_clear_since_utc": (
                    NOW - timedelta(minutes=150)
                ).isoformat(),
                "last_hydrawise_success_utc": (
                    NOW - timedelta(minutes=1)
                ).isoformat(),
            }
        )
        resumed, resumed_store = self._run(
            resumable_state,
            result(activity="PARKED_IN_CS"),
            start=lambda *args: start_calls.append(args) or {"accepted": True},
        )
        self.assertEqual(resumed.decision_code, "CONTINUOUS_MOWING_START_SENT")
        self.assertEqual(len(start_calls), 1)
        self.assertIsNone(resumed_store.load().irrigation_phase)

    def test_partial_run_end_never_releases_on_incomplete_safety_proof(self) -> None:
        base = irrigation_state(phase="READY")
        base = AutomationState.from_mapping(
            {
                **base.to_dict(),
                "irrigation_completed_relay_ids_json": json.dumps(RELAYS[:3]),
                "irrigation_suspension_until_utc": (
                    NOW + timedelta(minutes=10)
                ).isoformat(),
                "hydrawise_clear_since_utc": (
                    NOW - timedelta(minutes=5)
                ).isoformat(),
                "hydrawise_clear_origin": "IRRIGATION_END",
                "last_hydrawise_success_utc": (
                    NOW - timedelta(minutes=1)
                ).isoformat(),
            }
        )
        cases = {
            "no completed zone": {
                "state": {"irrigation_completed_relay_ids_json": "[]"},
                "cycle": {},
            },
            "active zone": {
                "state": {},
                "cycle": {"active_ids": [RELAYS[3]], "clear": False},
            },
            "imminent zone": {
                "state": {},
                "cycle": {"imminent": True},
            },
            "relay set incomplete": {"state": {}, "cycle": {"incomplete": True}},
            "clear origin uncertain": {
                "state": {"hydrawise_clear_origin": "DATA_GAP"},
                "cycle": {},
            },
            "pending zone state": {
                "state": {
                    "irrigation_current_relay_id": RELAYS[3],
                    "irrigation_zone_start_reserved_utc": NOW.isoformat(),
                },
                "cycle": {},
            },
        }
        for label, case in cases.items():
            with self.subTest(label=label):
                state = AutomationState.from_mapping(
                    {**base.to_dict(), **case["state"]}
                )
                cycle_options = {
                    key: value
                    for key, value in case["cycle"].items()
                    if key not in {"imminent", "incomplete"}
                }
                cycle = result(activity="PARKED_IN_CS", **cycle_options)
                if case["cycle"].get("imminent"):
                    cycle.details["hydrawise"]["safety"].update(
                        {
                            "imminent_zone_count": 1,
                            "imminent_relay_ids": [RELAYS[3]],
                        }
                    )
                if case["cycle"].get("incomplete"):
                    cycle.details["hydrawise"]["zones"] = cycle.details[
                        "hydrawise"
                    ]["zones"][:-1]
                    cycle.details["hydrawise"]["zone_observations"] = cycle.details[
                        "hydrawise"
                    ]["zone_observations"][:-1]
                    cycle.details["hydrawise"]["safety"].update(
                        {
                            "selected_zone_count": 6,
                            "observed_relay_ids": RELAYS[:-1],
                            "relay_set_valid": False,
                        }
                    )
                start_calls = []
                zone_calls = []
                output, store = self._run(
                    state,
                    cycle,
                    start=lambda *args: start_calls.append(args),
                    zone=lambda *args: zone_calls.append(args),
                )
                self.assertNotEqual(
                    output.decision_code,
                    "IRRIGATION_PARTIAL_RUN_COMPLETE_HOLD",
                )
                self.assertNotEqual(store.load().irrigation_phase, "COMPLETE_HOLD")
                self.assertEqual(start_calls, [])
                self.assertEqual(zone_calls, [])

    def test_existing_partial_window_failure_recovers_through_full_drying_gate(self) -> None:
        state = irrigation_state(phase="FAILED")
        state = AutomationState.from_mapping(
            {
                **state.to_dict(),
                "irrigation_failed_reason": PARTIAL_IRRIGATION_WINDOW_FAILURE,
                "irrigation_plan_json": json.dumps(zones()),
                "irrigation_completed_relay_ids_json": json.dumps(RELAYS[:3]),
                "irrigation_suspended_relay_ids_json": json.dumps(RELAYS),
                "irrigation_suspension_completed_utc": (
                    NOW - timedelta(hours=3)
                ).isoformat(),
                "irrigation_suspension_until_utc": (
                    NOW - timedelta(hours=1)
                ).isoformat(),
                "hydrawise_clear_since_utc": (
                    NOW - timedelta(minutes=151)
                ).isoformat(),
                "hydrawise_clear_origin": "IRRIGATION_END",
                "last_hydrawise_success_utc": (
                    NOW - timedelta(minutes=1)
                ).isoformat(),
            }
        )
        start_calls = []

        output, store = self._run(
            state,
            result(activity="PARKED_IN_CS"),
            start=lambda *args: start_calls.append(args) or {"accepted": True},
        )

        self.assertEqual(output.decision_code, "CONTINUOUS_MOWING_START_SENT")
        self.assertTrue(
            output.details["irrigation_partial_end_recovery"]["recovered"]
        )
        self.assertEqual(len(start_calls), 1)
        self.assertIsNone(store.load().irrigation_phase)

    def test_occupancy_still_blocks_after_safe_partial_run_end(self) -> None:
        state = irrigation_state(phase="READY")
        state = AutomationState.from_mapping(
            {
                **state.to_dict(),
                "irrigation_completed_relay_ids_json": json.dumps(RELAYS[:3]),
                "irrigation_suspension_until_utc": (
                    NOW + timedelta(minutes=10)
                ).isoformat(),
                "hydrawise_clear_since_utc": (
                    NOW - timedelta(minutes=2)
                ).isoformat(),
                "hydrawise_clear_origin": "IRRIGATION_END",
                "last_hydrawise_success_utc": (
                    NOW - timedelta(minutes=1)
                ).isoformat(),
            }
        )
        _, store = self._run(state, result(activity="PARKED_IN_CS"))
        blocked_state = AutomationState.from_mapping(
            {
                **store.load().to_dict(),
                "hydrawise_clear_since_utc": (
                    NOW - timedelta(minutes=150)
                ).isoformat(),
                "last_hydrawise_success_utc": (
                    NOW - timedelta(minutes=1)
                ).isoformat(),
            }
        )
        start_calls = []

        output, blocked_store = self._run(
            blocked_state,
            result(activity="PARKED_IN_CS", block_source="training"),
            start=lambda *args: start_calls.append(args),
        )

        self.assertNotEqual(output.decision_code, "CONTINUOUS_MOWING_START_SENT")
        self.assertEqual(blocked_store.load().irrigation_phase, "COMPLETE_HOLD")
        self.assertEqual(start_calls, [])

    def test_unknown_irrigation_failure_never_recovers_automatically(self) -> None:
        state = AutomationState(
            parked_by_automation=True,
            automation_park_source="irrigation",
            automation_restart_allowed=True,
            park_command_sent_utc=(NOW - timedelta(hours=3)).isoformat(),
            park_confirmed_utc=(NOW - timedelta(hours=3)).isoformat(),
            irrigation_phase="FAILED",
            irrigation_failed_reason="Unbekannter Gerätefehler",
            hydrawise_clear_since_utc=(NOW - timedelta(hours=4)).isoformat(),
            hydrawise_clear_origin="IRRIGATION_END",
            last_hydrawise_success_utc=(NOW - timedelta(minutes=1)).isoformat(),
        )

        output, store = self._run(state, result())

        self.assertEqual(output.decision_code, "IRRIGATION_FAILED_HOLD")
        self.assertEqual(store.load().irrigation_phase, "FAILED")

    def test_direct_hydrawise_resume_supersedes_pause_after_two_cycles(self) -> None:
        pause = {
            "version": 1,
            "kind": "PAUSE",
            "status": "ACTIVE",
            "suspend_until_utc": (NOW + timedelta(days=3)).isoformat(),
            "commanded_relay_ids": RELAYS,
        }
        store = InMemoryStateStore(
            AutomationState(irrigation_schedule_override_json=json.dumps(pause))
        )
        suspend_calls = []

        first, _ = self._continue(
            store,
            result(),
            now=NOW,
            suspend=lambda *args: suspend_calls.append(args),
        )
        self.assertEqual(
            first.decision_code,
            "IRRIGATION_SCHEDULE_EXTERNAL_RESUME_CONFIRMING",
        )
        self.assertIsNotNone(store.load().irrigation_schedule_override_json)

        second, _ = self._continue(
            store,
            result(),
            now=NOW + timedelta(minutes=1),
            suspend=lambda *args: suspend_calls.append(args),
        )
        self.assertEqual(
            second.decision_code,
            "IRRIGATION_SCHEDULE_EXTERNAL_RESUME_CONFIRMING",
        )
        self._continue(
            store,
            result(),
            now=NOW + timedelta(minutes=2),
            suspend=lambda *args: suspend_calls.append(args),
        )

        self.assertIsNone(store.load().irrigation_schedule_override_json)
        history = json.loads(store.load().irrigation_schedule_history_json or "[]")
        self.assertEqual(history[-1]["status"], "SUPERSEDED_EXTERNALLY")
        self.assertEqual(suspend_calls, [])

    def test_confirmation_gap_restarts_proof_without_false_activation(self) -> None:
        override = {
            "version": 1,
            "kind": "PAUSE",
            "status": "CONFIRMING",
            "suspend_until_utc": (NOW + timedelta(days=2)).isoformat(),
            "commanded_relay_ids": RELAYS,
            "confirm_since_utc": (NOW - timedelta(minutes=20)).isoformat(),
            "confirm_last_seen_utc": (NOW - timedelta(minutes=10)).isoformat(),
        }
        output, store = self._run(
            AutomationState(irrigation_schedule_override_json=json.dumps(override)),
            suspended_result(),
        )
        saved = json.loads(store.load().irrigation_schedule_override_json or "{}")
        self.assertEqual(output.decision_code, "IRRIGATION_SCHEDULE_CONFIRMING")
        self.assertEqual(saved["status"], "CONFIRMING")
        self.assertEqual(saved["confirm_since_utc"], NOW.isoformat())

    def test_skip_is_rejected_when_next_run_is_too_close_for_safe_application(self) -> None:
        initial = AutomationState(
            operator_request_id="skip-close",
            operator_request_action="SKIP_NEXT_IRRIGATION",
            operator_requested_utc=(NOW - timedelta(seconds=10)).isoformat(),
            operator_request_expires_utc=(NOW + timedelta(minutes=10)).isoformat(),
            operator_request_status="PENDING",
            operator_request_irrigation_schedule_json="{}",
        )
        calls = []
        output, store = self._run(
            initial,
            result(irrigation_start=NOW + timedelta(minutes=11)),
            suspend=lambda *args: calls.append(args),
        )
        self.assertEqual(output.decision_code, "IRRIGATION_SCHEDULE_REQUEST_REJECTED")
        self.assertEqual(store.load().operator_request_status, "REJECTED")
        self.assertEqual(calls, [])


if __name__ == "__main__":
    unittest.main()
