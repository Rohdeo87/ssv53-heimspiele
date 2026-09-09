from __future__ import annotations

import json
from copy import deepcopy
from dataclasses import replace
from datetime import datetime, timedelta, timezone

from mower.coordination_request import canonical_schedule_id
from mower.full_failsafe import run_full_failsafe_cycle
from mower.state import AutomationState
from mower.state_store import InMemoryStateStore
from test_full_failsafe import ENV, NOW, RELAYS, RUN_SECONDS, result, settings


EXECUTION_ENV = {
    **ENV,
    "COORDINATION_EXECUTION_ENABLED": "true",
    "COORDINATION_EXECUTION_CONFIRMATION": "SSV53-COORDINATED-IRRIGATION-PILOT-V1",
}


def _original_zones() -> list[dict]:
    """Measured seven-zone source occurrence: no invented inter-zone pause."""
    # The modeled 04:55 Berlin occurrence contains no invented native pause
    # and still completes before the mandatory 08:00 boundary. The prior
    # 05:30 fixture ended after 08:00 and is rightly rejected.
    cursor = NOW + timedelta(minutes=55)
    zones = []
    for index, (relay_id, run_seconds) in enumerate(zip(RELAYS, RUN_SECONDS, strict=True)):
        end = cursor + timedelta(seconds=run_seconds)
        zones.append({
            "relay_id": relay_id,
            "zone": index + 1,
            "name": f"Zone {index + 1}",
            "running": False,
            "run_seconds": run_seconds,
            "scheduled_start_utc": cursor.isoformat(),
            "scheduled_end_utc": end.isoformat(),
        })
        cursor = end
    return zones


class ReplayPlant:
    """Minute-resolution mower/Hydrawise plant; every controller edge is real."""

    def __init__(
        self,
        *,
        lose_first_start_response: bool = False,
        safe_mower_delay_minutes: int = 0,
    ) -> None:
        self.original_zones = _original_zones()
        self.provider_zones = deepcopy(self.original_zones)
        self.need = {
            "schema_version": 1,
            "need_id": "coordination-replay-1",
            "demand_reference": "approved-replay",
            "mower_id": "mower-1",
            "required": True,
            "timing_window_approved": True,
            "station_and_paths_checked": True,
            "source_plan_id": canonical_schedule_id(self.original_zones),
            # Ten minutes ahead of the unchanged source satisfies the
            # execution pilot's minimum field-gain criterion.
            "earliest_start_utc": (NOW + timedelta(minutes=45)).isoformat(),
            "original_start_utc": self.original_zones[0]["scheduled_start_utc"],
            "latest_start_utc": (NOW + timedelta(minutes=120)).isoformat(),
            # Authorization remains current for the whole approved occurrence;
            # start_authorized still rechecks that this exact need is present.
            "valid_until_utc": (NOW + timedelta(hours=12)).isoformat(),
            "zones": deepcopy(self.original_zones),
        }
        self.estimate = {
            "estimated": True,
            "source": "OBSERVED_COMPLETED_CHARGING_SECTIONS",
            "sampleCount": 3,
            "daysCovered": 2,
            "at": (NOW + timedelta(minutes=120)).isoformat(),
        }
        self.capture_complete_until = NOW + timedelta(hours=24)
        self.now = NOW
        self.mower_activity = "CHARGING"
        self.running_relay: int | None = None
        self.running_until: datetime | None = None
        self.previous_cycle = self._seed_previous_cycle()
        self.approval_revoked = False
        self.lose_first_start_response = lose_first_start_response
        self.safe_mower_delay_minutes = safe_mower_delay_minutes
        self.lost_response_raised = False
        self.park_calls: list[datetime] = []
        self.suspend_calls: list[dict] = []
        self.start_calls: list[dict] = []
        self.physical_runs: list[dict] = []
        self.native_suppressed: list[dict] = []
        self.native_physical_starts: list[dict] = []
        self._native_processed: set[int] = set()
        self.trace: list[dict] = []

    def _seed_previous_cycle(self) -> dict:
        previous = result(irrigation_start=NOW + timedelta(minutes=90), activity="CHARGING")
        previous = replace(previous, executed_at_utc=(NOW - timedelta(minutes=1)).isoformat())
        previous.details["hydrawise"]["zones"] = deepcopy(self.original_zones)
        previous.details["mower"]["status_timestamp_ms"] = int(
            (NOW - timedelta(minutes=1)).timestamp() * 1000
        )
        previous.details["hydrawise"]["safety"]["observed_at_utc"] = (
            NOW - timedelta(minutes=1)
        ).isoformat()
        return previous.to_dict()

    def _refresh_physics(self) -> None:
        if self.running_until is not None and self.now >= self.running_until:
            self.running_relay = None
            self.running_until = None

    def _process_native_due_starts(self) -> None:
        """Synthetic Hydrawise behavior for the untouched source occurrence."""
        for zone in self.original_zones:
            relay_id = int(zone["relay_id"])
            scheduled = datetime.fromisoformat(zone["scheduled_start_utc"])
            if relay_id in self._native_processed or self.now < scheduled:
                continue
            self._native_processed.add(relay_id)
            suspension = next(
                (call for call in reversed(self.suspend_calls)
                 if call["relay_id"] == relay_id and call["until"] >= scheduled),
                None,
            )
            occurrence = {"relay_id": relay_id, "scheduled_at": scheduled, "seen_at": self.now}
            if suspension is not None:
                self.native_suppressed.append({**occurrence, "suspend_until": suspension["until"]})
                continue
            self.native_physical_starts.append(occurrence)
            if self.running_relay is not None:
                raise AssertionError("unsuppressed native occurrence overlapped coordinated watering")
            self.running_relay = relay_id
            self.running_until = self.now + timedelta(seconds=int(zone["run_seconds"]))

    def read(self, **_kwargs):
        self._refresh_physics()
        self._process_native_due_starts()
        cycle = result(activity=self.mower_activity)
        details = cycle.details
        observations = []
        live_zones = deepcopy(self.provider_zones)
        for zone in live_zones:
            running = int(zone["relay_id"]) == self.running_relay
            zone["running"] = running
            observations.append({**zone, "valid": True, "scheduled": True})
        details["hydrawise"]["zones"] = live_zones
        details["hydrawise"]["zone_observations"] = observations
        safety = details["hydrawise"]["safety"]
        active = [] if self.running_relay is None else [self.running_relay]
        safety.update({
            "available": True,
            "fresh": True,
            "clear_now": not active,
            "observed_at_utc": self.now.isoformat(),
            "selected_zone_count": len(RELAYS),
            "observed_relay_ids": list(RELAYS),
            "expected_relay_ids": list(RELAYS),
            "relay_set_valid": True,
            "active_zone_count": len(active),
            "active_relay_ids": active,
            "imminent_zone_count": 0,
            "reason": "läuft" if active else "frei",
        })
        details["mower"]["status_timestamp_ms"] = int(self.now.timestamp() * 1000)
        details["mower"]["activity"] = self.mower_activity
        details["mower"]["override_action"] = "FORCE_PARK"
        desired_start = datetime.fromisoformat(self.need["earliest_start_utc"])
        details["mower"]["connected"] = not (
            desired_start
            <= self.now
            < desired_start + timedelta(minutes=self.safe_mower_delay_minutes)
        )
        details["coordination_shadow_input"] = {
            "schema_version": 1,
            "captured_at_utc": self.now.isoformat(),
            "complete_from_utc": (NOW - timedelta(hours=2)).isoformat(),
            "complete_until_utc": self.capture_complete_until.isoformat(),
            "occupancy_complete": True,
            "state_available": True,
            "manual_stop": False,
            "uncertain_start": False,
            "occupancy": [],
        }
        authorized_needs = [] if self.approval_revoked else [deepcopy(self.need)]
        details["coordination_execution_input"] = {
            "schema_version": 1,
            "available": True,
            "needs": authorized_needs,
            "need": deepcopy(self.need) if authorized_needs else None,
            "previous_cycle": deepcopy(self.previous_cycle),
            "charging_end_estimate": deepcopy(self.estimate),
            "permission_to_start": False,
            "blockers": [],
        }
        cycle = replace(cycle, executed_at_utc=self.now.isoformat(), details=details)
        # The producer only retains the prior mower observation. Keeping a
        # whole handover inside the next handover would create recursive test
        # data that the real read-only observation buffer never produces.
        self.previous_cycle = {
            "executed_at_utc": cycle.executed_at_utc,
            "command_sent": cycle.command_sent,
            "details": {"mower": deepcopy(details["mower"])},
        }
        return cycle

    def park(self, *_args):
        self.park_calls.append(self.now)
        self.mower_activity = "PARKED_IN_CS"
        return {"ok": True, "activity": self.mower_activity}

    def suspend(self, _api_key, relay_id, until_epoch, _controller_id):
        until = datetime.fromtimestamp(until_epoch, tz=timezone.utc)
        self.suspend_calls.append({"relay_id": relay_id, "at": self.now, "until": until})
        cursor = until + timedelta(days=1)
        for zone in self.provider_zones:
            if int(zone["relay_id"]) == int(relay_id):
                index = RELAYS.index(relay_id)
                shifted = cursor + timedelta(minutes=index)
                zone["scheduled_start_utc"] = shifted.isoformat()
                zone["scheduled_end_utc"] = (
                    shifted + timedelta(seconds=int(zone["run_seconds"]))
                ).isoformat()
                return {"ok": True, "relay_id": relay_id}
        raise AssertionError(f"unknown relay {relay_id}")

    def start_zone(self, _api_key, relay_id, run_seconds, _controller_id):
        if self.running_relay is not None:
            raise AssertionError("controller attempted overlapping water starts")
        call = {"relay_id": relay_id, "run_seconds": run_seconds, "at": self.now}
        self.start_calls.append(call)
        self.running_relay = relay_id
        self.running_until = self.now + timedelta(seconds=run_seconds)
        self.physical_runs.append({**call, "until": self.running_until})
        if self.lose_first_start_response and not self.lost_response_raised:
            self.lost_response_raised = True
            raise TimeoutError("simulated lost Hydrawise response after physical start")
        return {"ok": True, "relay_id": relay_id, "run_seconds": run_seconds}

    @staticmethod
    def unexpected_sender(*_args):
        raise AssertionError("unexpected device sender call")


class Replay:
    def __init__(self, plant: ReplayPlant) -> None:
        self.plant = plant
        self.store = InMemoryStateStore()

    def restart(self) -> None:
        persisted = AutomationState.from_mapping(self.store.load().to_dict())
        self.store = InMemoryStateStore(persisted)

    def minute(self, *, expect_timeout: bool = False):
        try:
            output = run_full_failsafe_cycle(
                now_utc=self.plant.now,
                settings=settings(),
                environment=EXECUTION_ENV,
                past_due=False,
                source="coordination-replay",
                read_only_runner=self.plant.read,
                state_store_factory=lambda _environment: self.store,
                park_sender=self.plant.park,
                start_sender=self.plant.unexpected_sender,
                suspend_zone_sender=self.plant.suspend,
                start_zone_sender=self.plant.start_zone,
                stop_zone_sender=self.plant.unexpected_sender,
                cutting_height_sender=self.plant.unexpected_sender,
                blade_usage_reset_sender=self.plant.unexpected_sender,
                command_clock=lambda: self.plant.now,
            )
        except TimeoutError:
            if not expect_timeout:
                raise
            self.plant.trace.append({
                "at": self.plant.now.isoformat(),
                "decision": "SIMULATED_LOST_START_RESPONSE",
                "phase": self.store.load().irrigation_phase,
            })
            self.plant.now += timedelta(minutes=1)
            return None
        if expect_timeout:
            raise AssertionError("expected the fake sender response to be lost")
        state = self.store.load()
        self.plant.trace.append({
            "at": self.plant.now.isoformat(),
            "decision": output.decision_code,
            "phase": state.irrigation_phase,
            "relay": state.irrigation_current_relay_id,
        })
        self.plant.now += timedelta(minutes=1)
        return output

    def run_until(self, predicate, *, limit: int = 500):
        for _ in range(limit):
            output = self.minute()
            if predicate(output, self.store.load()):
                return output
        tail = json.dumps(self.plant.trace[-20:], indent=2)
        raise AssertionError(f"replay did not reach target; trace tail:\n{tail}")


def _assert_successful_physical_run(plant: ReplayPlant, replay: Replay) -> None:
    decisions = [item["decision"] for item in plant.trace]
    assert [call["relay_id"] for call in plant.suspend_calls] == sorted(RELAYS)
    assert decisions.count("IRRIGATION_SCHEDULE_ZONE_UPDATED") == len(RELAYS)
    assert [call["relay_id"] for call in plant.start_calls] == RELAYS
    assert [call["run_seconds"] for call in plant.start_calls] == RUN_SECONDS
    assert len({call["relay_id"] for call in plant.start_calls}) == len(RELAYS)
    assert len(plant.park_calls) == 1
    first, second = plant.physical_runs[:2]
    # No synthetic native pause belongs in this measured source fixture; the
    # controller must still wait its configured end confirmation.
    assert second["at"] - first["until"] >= timedelta(minutes=2)
    assert first["at"] == NOW + timedelta(minutes=45)
    assert first["at"] == datetime.fromisoformat(plant.need["original_start_utc"]) - timedelta(minutes=10)
    assert "PARK_COMMAND_SENT" in decisions
    assert "IRRIGATION_ZONE_CONFIRMED_RUNNING" in decisions
    assert decisions[-1] == "IRRIGATION_ALL_ZONES_CONFIRMED_COMPLETE"
    final_state = replay.store.load()
    assert final_state.irrigation_phase == "COMPLETE_HOLD"
    assert final_state.park_confirmed_utc is not None
    assert final_state.park_confirmed_observations >= 2
    source_end = max(datetime.fromisoformat(zone["scheduled_end_utc"])
                     for zone in plant.original_zones)
    assert plant.capture_complete_until >= source_end + timedelta(minutes=150)
    assert all(
        datetime.fromisoformat(zone["scheduled_start_utc"]) > call["until"]
        for zone, call in zip(plant.provider_zones, plant.suspend_calls, strict=True)
    )
    assert plant.now > datetime.fromisoformat(plant.need["original_start_utc"])


def test_actual_fsm_replays_coordinated_seven_zone_occurrence_across_restarts():
    plant = ReplayPlant()
    replay = Replay(plant)

    reserved = replay.run_until(
        lambda output, _state: output.decision_code == "COORDINATION_EXECUTION_RESERVED"
    )
    assert reserved.decision_code == "COORDINATION_EXECUTION_RESERVED"
    assert not plant.park_calls and not plant.suspend_calls and not plant.start_calls
    replay.restart()

    scheduled = replay.minute()
    assert scheduled.decision_code == "COORDINATION_EXECUTION_SCHEDULED"
    replay.restart()

    replay.run_until(lambda _out, state: state.irrigation_phase == "COMPLETE_HOLD")
    _assert_successful_physical_run(plant, replay)

    # Let the synthetic provider evaluate every due source occurrence. Each
    # one must be suppressed by the accepted per-relay suspend horizon.
    last_original_start = max(
        datetime.fromisoformat(zone["scheduled_start_utc"])
        for zone in plant.original_zones
    )
    while plant.now <= last_original_start:
        replay.minute()
    assert [call["relay_id"] for call in plant.start_calls] == RELAYS
    assert plant.native_physical_starts == []
    assert [item["relay_id"] for item in plant.native_suppressed] == RELAYS


def test_lost_start_response_is_not_duplicated_after_serialized_restart():
    plant = ReplayPlant(lose_first_start_response=True)
    replay = Replay(plant)
    replay.run_until(lambda _out, state: state.irrigation_phase == "READY")
    while plant.now <= NOW + timedelta(minutes=45):
        if plant.now == NOW + timedelta(minutes=45):
            replay.minute(expect_timeout=True)
            break
        replay.minute()

    assert replay.store.load().irrigation_phase == "START_RESERVED"
    assert [call["relay_id"] for call in plant.start_calls] == [RELAYS[0]]
    replay.restart()
    confirmed = replay.minute()
    assert confirmed.decision_code == "IRRIGATION_ZONE_CONFIRMED_RUNNING"
    assert [call["relay_id"] for call in plant.start_calls] == [RELAYS[0]]


def test_revoked_need_before_zone_two_blocks_every_new_start():
    plant = ReplayPlant()
    replay = Replay(plant)
    replay.run_until(lambda _out, _state: len(plant.start_calls) == 1)
    first_run = plant.physical_runs[0]
    plant.approval_revoked = True

    blocked = replay.run_until(
        lambda out, _state: out.decision_code == "COORDINATION_EXECUTION_START_BLOCKED"
    )
    assert blocked.details["coordination_execution"]["start_revalidation"] is False
    assert plant.now >= first_run["until"] + timedelta(minutes=2)
    assert [call["relay_id"] for call in plant.start_calls] == [RELAYS[0]]
    for _ in range(25):
        replay.minute()
    assert [call["relay_id"] for call in plant.start_calls] == [RELAYS[0]]


def test_delayed_first_start_preserves_end_confirmation_across_between_zone_restart():
    plant = ReplayPlant(safe_mower_delay_minutes=3)
    replay = Replay(plant)
    replay.run_until(lambda _out, _state: len(plant.start_calls) == 1)
    first = plant.physical_runs[0]
    assert first["at"] == NOW + timedelta(minutes=48)

    replay.run_until(
        lambda out, _state: out.decision_code == "IRRIGATION_ZONE_CONFIRMED_COMPLETE"
    )
    replay.restart()
    replay.run_until(lambda _out, _state: len(plant.start_calls) == 2)

    second = plant.physical_runs[1]
    assert second["at"] - first["until"] >= timedelta(minutes=2)
