from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone

from mower.full_failsafe import run_full_failsafe_cycle
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
    "HYDRAWISE_EXPECTED_RELAY_IDS": ",".join(str(value) for value in RELAYS),
    "HYDRAWISE_CLEAR_CONFIRMATION_MINUTES": "90",
    "MOWER_PARK_CONFIRMATION_MINUTES": "1",
    "MOWER_PARK_PROGRESS_GRACE_MINUTES": "3",
    "IRRIGATION_FAILSAFE_DOCK_LEAD_MINUTES": "40",
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
                "zones": zones(
                    start_utc=irrigation_start or NOW + timedelta(hours=13),
                    relay_ids=observed_relay_ids,
                ),
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
                "activity": activity,
                "state": "IN_OPERATION",
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
    def _run(self, state: AutomationState, cycle: CycleResult, *, now: datetime = NOW, **senders):
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
            result(block_source="irrigation"),
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
                "irrigation_suspended_relay_ids_json": "[]",
            }
        )
        output, store = self._run(
            state,
            result(block_source="irrigation", activity="GOING_HOME"),
            suspend=lambda *args: calls.append(args) or {"message_type": "info"},
        )
        self.assertEqual(output.decision_code, "IRRIGATION_ZONE_SUSPENDED")
        self.assertEqual(len(calls), 1)
        self.assertIsNone(store.load().park_confirmed_utc)

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

    def test_irrigation_start_waits_for_training_or_match_to_clear(self) -> None:
        for source in ("irrigation+training", "irrigation+match"):
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


if __name__ == "__main__":
    unittest.main()
