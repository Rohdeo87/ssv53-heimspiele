"""Local seven-zone timing simulation with sparse mower status updates.

All reads and writes in this test are in-memory fakes.  The test records the
decisions rather than asserting that the complete sequence must fit; this is
an incident reproduction aid for the 03:30/04:30--08:00 Europe/Berlin windows.
"""

from copy import deepcopy
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from collections import Counter
import json

import pytest

from mower.full_failsafe import run_full_failsafe_cycle
from mower.state import AutomationState
from mower.state_store import InMemoryStateStore
from tests.test_full_failsafe import ENV, RELAYS, RUN_SECONDS, settings, suspended_result, zones


SIM_START_0430 = datetime(2026, 8, 13, 2, 30, tzinfo=timezone.utc)  # 04:30 Europe/Berlin
SIM_START_0330 = datetime(2026, 8, 13, 1, 30, tzinfo=timezone.utc)  # 03:30 Europe/Berlin
SIM_END = datetime(2026, 8, 13, 6, 0, tzinfo=timezone.utc)  # 08:00 Europe/Berlin
TICK = timedelta(minutes=1)
MOWER_EVENT_PERIOD = timedelta(minutes=15)


def _simulation_state(sim_start: datetime) -> AutomationState:
    plan = zones(start_utc=sim_start)
    return AutomationState.from_mapping(
        {
            **AutomationState(
                parked_by_automation=True,
                automation_park_source="irrigation",
                automation_restart_allowed=True,
                park_command_sent_utc=(sim_start - timedelta(minutes=10)).isoformat(),
                park_confirmed_utc=(sim_start - timedelta(minutes=5)).isoformat(),
                park_confirmed_observations=2,
                last_mower_activity="PARKED_IN_CS",
                irrigation_phase="READY",
                irrigation_plan_id="simulation-plan",
                irrigation_plan_json=json.dumps(plan),
                irrigation_suspended_relay_ids_json=json.dumps(RELAYS),
                irrigation_suspension_until_utc=(sim_start + timedelta(hours=4)).isoformat(),
                irrigation_suspension_completed_utc=(sim_start - timedelta(minutes=1)).isoformat(),
                irrigation_completed_relay_ids_json="[]",
            ).to_dict(),
            "last_cycle_started_utc": (sim_start - TICK).isoformat(),
        }
    )


def _cycle(
    at: datetime,
    mower_event_at: datetime,
    active_relay: int | None,
    *,
    plan_start: datetime,
    active_start: datetime | None,
    active_end: datetime | None,
):
    cycle = deepcopy(suspended_result())
    cycle.details["mower"].update(
        {
            "activity": "PARKED_IN_CS",
            "state": "IN_OPERATION",
            "mode": "HOME",
            "override_action": "FORCE_PARK",
            "connected": True,
            "status_timestamp_ms": int(mower_event_at.timestamp() * 1000),
        }
    )
    hydra = cycle.details["hydrawise"]
    safety = hydra["safety"]
    safety.update(
        {
            "available": True,
            "fresh": True,
            "clear_now": active_relay is None,
            "observed_at_utc": at.isoformat(),
            "active_zone_count": 0 if active_relay is None else 1,
            "active_relay_ids": [] if active_relay is None else [active_relay],
            "imminent_zone_count": 0,
            "imminent_relay_ids": [],
            "relay_set_valid": True,
        }
    )
    plan = zones(start_utc=plan_start)
    observations = []
    live_zones = []
    for item in plan:
        relay = int(item["relay_id"])
        observation = {
            **item,
            "valid": True,
            "scheduled": active_relay == relay,
            "running": active_relay == relay,
            "seconds_until": (
                max(0, int((active_end - at).total_seconds()))
                if active_relay == relay and active_end is not None
                else 0
            ),
        }
        if active_relay == relay:
            observation["scheduled_start_utc"] = active_start.isoformat()
            observation["scheduled_end_utc"] = active_end.isoformat()
        else:
            observation["scheduled_start_utc"] = None
            observation["scheduled_end_utc"] = None
        observations.append(observation)
        if active_relay == relay:
            live_zones.append(observation)
    hydra["zones"] = live_zones
    hydra["zone_observations"] = observations
    return cycle


def _run_simulation(sim_start: datetime, *, park_hold=False, manual=False,
                    event_offset_minutes=0, dispatch_fault=None) -> dict[str, object]:
    store = InMemoryStateStore(_simulation_state(sim_start))
    active: dict[str, object] = {"relay": None, "started": None, "ends": None}
    starts: list[dict[str, object]] = []
    decisions: list[dict[str, object]] = []

    def start_zone(_api_key: str, relay: int, seconds: int, _controller: str | None):
        assert active["relay"] is None
        at = current_at[0]
        active["relay"] = relay
        active["started"] = at
        active["ends"] = at + timedelta(seconds=seconds)
        starts.append(
            {
                "at": at.isoformat(),
                "end": (at + timedelta(seconds=seconds)).isoformat(),
                "relay_id": relay,
                "run_seconds": seconds,
            }
        )
        return {"message_type": "info", "fake": True}

    def forbidden(*_args):
        raise AssertionError("unerwarteter Mäher- oder Suspendierungsbefehl")

    current_at = [sim_start]
    def dispatch_clock():
        if dispatch_fault == "delayed":
            return current_at[0] + timedelta(seconds=91)
        if dispatch_fault == "control_changed":
            current = store.load()
            if current.irrigation_phase == "START_RESERVED":
                store.save(replace(current, revision=current.revision + 1,
                           operator_request_id="intervening-manual-action"),
                           expected_revision=current.revision)
        return current_at[0]

    event_origin = sim_start - timedelta(minutes=event_offset_minutes)
    at = sim_start
    while at <= SIM_END:
        current_at[0] = at
        if active["ends"] is not None and at >= active["ends"]:
            active["relay"] = None
            active["started"] = None
            active["ends"] = None
        elapsed = at - event_origin
        mower_event_at = event_origin + (elapsed // MOWER_EVENT_PERIOD) * MOWER_EVENT_PERIOD
        state = store.load()
        cycle = _cycle(
            at,
            mower_event_at,
            active["relay"],
            plan_start=sim_start,
            active_start=active["started"],
            active_end=active["ends"],
        )
        output = run_full_failsafe_cycle(
            now_utc=at,
            settings=settings(manual=manual),
            environment={**ENV, "IRRIGATION_CONFIRMED_PARK_HOLD_ENABLED": str(park_hold).lower()},
            past_due=False,
            source="local-simulation",
            read_only_runner=lambda **_kwargs: deepcopy(cycle),
            state_store_factory=lambda _env: store,
            park_sender=forbidden,
            start_sender=forbidden,
            suspend_zone_sender=forbidden,
            start_zone_sender=start_zone,
            stop_zone_sender=forbidden,
            cutting_height_sender=forbidden,
            blade_usage_reset_sender=forbidden,
            command_clock=dispatch_clock,
        )
        saved = store.load()
        decisions.append(
            {
                "at": at.isoformat(),
                "decision": output.decision_code,
                "phase": saved.irrigation_phase,
                "current_relay_id": saved.irrigation_current_relay_id,
                "completed": json.loads(saved.irrigation_completed_relay_ids_json or "[]"),
                "mower_status_event": mower_event_at.isoformat(),
            }
        )
        at += TICK

    final = store.load()
    return {
        "window_utc": [sim_start.isoformat(), SIM_END.isoformat()],
        "persistent_park_hold": park_hold,
        "manual_sessions_enabled": manual,
        "event_offset_minutes": event_offset_minutes,
        "dispatch_fault": dispatch_fault,
        "window_europe_berlin": [
            (sim_start + timedelta(hours=2)).strftime("%H:%M"),
            "08:00",
        ],
        "starts": starts,
        "decision_counts": dict(Counter(item["decision"] for item in decisions)),
        "decision_transitions": [
            item
            for index, item in enumerate(decisions)
            if index == 0
            or item["decision"] != decisions[index - 1]["decision"]
            or item["completed"] != decisions[index - 1]["completed"]
        ],
        "final_state": {
            "phase": final.irrigation_phase,
            "current_relay_id": final.irrigation_current_relay_id,
            "completed": json.loads(final.irrigation_completed_relay_ids_json or "[]"),
        },
    }


@pytest.mark.parametrize(
    "sim_start",
    [SIM_START_0430, SIM_START_0330],
    ids=["start-04-30", "start-03-30"],
)
def test_full_seven_zone_run_with_fifteen_minute_mower_events(
    sim_start: datetime,
) -> None:
    summary = _run_simulation(sim_start)
    print(json.dumps(summary, indent=2))
    starts = summary["starts"]
    assert starts, "A confirmed parked mower must allow the first timely zone"
    if sim_start == SIM_START_0330:
        assert len(starts) == len(RELAYS)
        assert set(summary["final_state"]["completed"]) == set(RELAYS)
    expected_durations = dict(zip(RELAYS, RUN_SECONDS, strict=True))
    assert all(
        int(item["run_seconds"]) == expected_durations[int(item["relay_id"])]
        for item in starts
    )
    assert len({int(item["relay_id"]) for item in starts}) == len(starts)
    ordered = sorted(starts, key=lambda item: item["at"])
    assert all(
        ordered[index]["at"] >= ordered[index - 1]["end"]
        for index in range(1, len(ordered))
    )
    assert all(item["end"] <= SIM_END.isoformat() for item in starts)


@pytest.mark.parametrize("manual", [False, True])
@pytest.mark.parametrize("event_offset_minutes", [0, 7, 14])
def test_confirmed_park_hold_completes_all_seven_zones_from_0430(manual, event_offset_minutes):
    result = _run_simulation(SIM_START_0430, park_hold=True, manual=manual,
                             event_offset_minutes=event_offset_minutes)
    starts = result["starts"]
    assert len(starts) == len(RELAYS)
    assert {z["relay_id"] for z in starts} == set(RELAYS)
    assert set(result["final_state"]["completed"]) == set(RELAYS)
    assert sum(z["run_seconds"] for z in starts) == sum(RUN_SECONDS)
    assert all(z["end"] <= SIM_END.isoformat() for z in starts)
    assert all(starts[i]["at"] >= starts[i-1]["end"] for i in range(1, len(starts)))


@pytest.mark.parametrize("fault", ["delayed", "control_changed"])
def test_held_park_never_authorizes_late_or_concurrently_revoked_dispatch(fault):
    result = _run_simulation(SIM_START_0430, park_hold=True, manual=True, dispatch_fault=fault)
    assert not result["starts"]
    assert not result["final_state"]["completed"]
