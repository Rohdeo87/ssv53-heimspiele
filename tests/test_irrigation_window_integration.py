from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from dataclasses import replace
from datetime import datetime, timedelta, timezone

from mower.full_failsafe import _irrigation_operating_window, run_full_failsafe_cycle
from mower.state import AutomationState
from mower.state_store import InMemoryStateStore
from tests.test_full_failsafe import ENV, NOW, RELAYS, irrigation_state, observed_cycle, result, settings


UTC = timezone.utc


def _plan(start: datetime, *, pauses: list[int] | None = None) -> list[dict]:
    pauses = pauses or [0] * 6
    cursor = start
    zones = []
    for index, relay_id in enumerate(RELAYS, start=1):
        end = cursor + timedelta(minutes=20)
        zones.append({
            "relay_id": relay_id, "zone": index, "name": f"Zone {index}",
            "run_seconds": 1200, "scheduled_start_utc": cursor.isoformat(),
            "scheduled_end_utc": end.isoformat(),
        })
        cursor = end + timedelta(seconds=pauses[index - 1] if index < len(RELAYS) else 0)
    return zones


def _cycle_for(plan: list[dict], *, now: datetime):
    cycle = deepcopy(result(block_source="irrigation"))
    cycle.details["mower"]["status_timestamp_ms"] = int(now.timestamp() * 1000)
    cycle.details["hydrawise"]["zones"] = deepcopy(plan)
    cycle.details["hydrawise"]["zone_observations"] = [
        {**zone, "valid": True, "scheduled": True} for zone in plan
    ]
    return cycle


def _run_ready(
    state: AutomationState, cycle, *, now: datetime, zone_calls: list,
    store: InMemoryStateStore | None = None, command_clock=None,
):
    store = store or InMemoryStateStore(state)
    output = run_full_failsafe_cycle(
        now_utc=now, settings=settings(), environment=ENV, past_due=False, source="test",
        read_only_runner=lambda **kwargs: observed_cycle(cycle, kwargs["now_utc"]),
        state_store_factory=lambda _environment: store,
        park_sender=lambda *_: {"accepted": True},
        start_sender=lambda *_: {"accepted": True},
        suspend_zone_sender=lambda *_: {"accepted": True},
        start_zone_sender=lambda *args: zone_calls.append(args) or {"accepted": True},
        command_clock=command_clock or (lambda: now),
    )
    return output, store


def test_before_0330_waits_without_starting_or_releasing_native_suppression():
    now = NOW - timedelta(hours=1)  # 03:00 Europe/Berlin
    plan = _plan(NOW + timedelta(minutes=30))
    state = replace(
        irrigation_state(phase="READY"),
        park_command_sent_utc=(now - timedelta(minutes=10)).isoformat(),
        park_confirmed_utc=(now - timedelta(minutes=5)).isoformat(),
        irrigation_suspension_completed_utc=(now - timedelta(minutes=1)).isoformat(),
        irrigation_plan_json=json.dumps(plan),
    )
    cycle = _cycle_for(plan, now=now)
    calls: list[tuple] = []

    output, stored = _run_ready(state, cycle, now=now, zone_calls=calls)

    assert output.decision_code == "IRRIGATION_OPERATING_WINDOW"
    assert calls == []
    assert stored.load().irrigation_phase == "READY"
    assert stored.load().irrigation_suspended_relay_ids_json == json.dumps(RELAYS)


def test_manual_all_zone_run_starts_before_0330_with_safety_interlocks_intact():
    now = NOW - timedelta(hours=1)  # 03:00 Europe/Berlin
    plan = _plan(NOW + timedelta(minutes=30))
    for zone in plan:
        zone["operator_manual"] = True
    canonical = json.dumps(plan, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    state = replace(
        irrigation_state(phase="READY"),
        park_command_sent_utc=(now - timedelta(minutes=10)).isoformat(),
        park_confirmed_utc=(now - timedelta(minutes=5)).isoformat(),
        irrigation_suspension_completed_utc=(now - timedelta(minutes=1)).isoformat(),
        irrigation_suspension_until_utc=(now + timedelta(hours=5)).isoformat(),
        irrigation_plan_id=hashlib.sha256(canonical.encode("utf-8")).hexdigest(),
        irrigation_plan_json=canonical,
    )
    calls: list[tuple] = []

    output, stored = _run_ready(
        state, _cycle_for(plan, now=now), now=now, zone_calls=calls,
    )

    assert output.decision_code == "IRRIGATION_ZONE_START_SENT"
    assert len(calls) == 1
    assert stored.load().irrigation_phase == "START_RESERVED"
    assert output.details["irrigation_operating_window"]["intent"] == "MANUAL_OPERATOR"
    assert output.details["irrigation_operating_window"]["automatic_window_applies"] is False


def test_remaining_whole_run_that_cannot_finish_by_0800_never_posts():
    now = datetime(2026, 8, 13, 4, 30, tzinfo=UTC)  # 06:30 Europe/Berlin
    plan = _plan(now + timedelta(minutes=1))
    state = replace(
        irrigation_state(phase="READY"),
        irrigation_plan_json=json.dumps(plan),
        irrigation_suspension_completed_utc=(now - timedelta(minutes=1)).isoformat(),
        irrigation_suspension_until_utc=(now + timedelta(hours=4)).isoformat(),
    )
    calls: list[tuple] = []

    output, stored = _run_ready(state, _cycle_for(plan, now=now), now=now, zone_calls=calls)

    assert output.decision_code == "IRRIGATION_WINDOW_CANNOT_FIT"
    assert calls == []
    assert stored.load().irrigation_phase == "READY"


def test_manual_single_zone_run_starts_after_0800_with_requested_runtime():
    now = datetime(2026, 8, 13, 8, 30, tzinfo=UTC)  # 10:30 Europe/Berlin
    plan = _plan(now + timedelta(minutes=30))
    for zone in plan:
        zone["operator_manual"] = True
        zone["operator_single_zone"] = True
        zone["selected"] = zone["zone"] == 3
    plan[2]["run_seconds"] = 25 * 60
    plan[2]["scheduled_end_utc"] = (
        datetime.fromisoformat(plan[2]["scheduled_start_utc"]) + timedelta(minutes=25)
    ).isoformat()
    canonical = json.dumps(plan, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    state = replace(
        irrigation_state(phase="READY"),
        park_command_sent_utc=(now - timedelta(minutes=10)).isoformat(),
        park_confirmed_utc=(now - timedelta(minutes=5)).isoformat(),
        irrigation_suspension_completed_utc=(now - timedelta(minutes=1)).isoformat(),
        irrigation_suspension_until_utc=(now + timedelta(hours=5)).isoformat(),
        irrigation_plan_id=hashlib.sha256(canonical.encode("utf-8")).hexdigest(),
        irrigation_plan_json=canonical,
    )
    calls: list[tuple] = []

    output, stored = _run_ready(
        state, _cycle_for(plan, now=now), now=now, zone_calls=calls,
    )

    assert output.decision_code == "IRRIGATION_ZONE_START_SENT"
    assert [(args[1], args[2]) for args in calls] == [(RELAYS[2], 25 * 60)]
    assert stored.load().irrigation_current_relay_id == RELAYS[2]


def test_native_pause_is_preserved_in_projected_window():
    now = datetime(2026, 10, 25, 2, 30, tzinfo=UTC)  # 03:30 CET, DST fallback day
    plan = _plan(now, pauses=[20 * 60, 0, 0, 0, 0, 0])

    window = _irrigation_operating_window(
        plan=plan, completed_relay_ids=set(), now_utc=now,
        end_confirmation_minutes=2,
    )

    assert window.code == "OK"
    # Seven 20-minute zones and a native 20-minute pause remain present. One
    # existing two-minute confirmation wait is absorbed by that larger pause;
    # the other six remain part of the conservative projection.
    assert window.projected_end_utc == now + timedelta(minutes=172)


def test_dst_spring_forward_uses_0330_berlin_bound():
    now = datetime(2026, 3, 29, 1, 30, tzinfo=UTC)  # 03:30 CEST
    window = _irrigation_operating_window(
        plan=_plan(now), completed_relay_ids=set(), now_utc=now,
        end_confirmation_minutes=2,
    )

    assert window.code == "OK"
    assert window.earliest_start_utc == now


def test_malformed_persisted_zone_is_rejected_without_throwing():
    window = _irrigation_operating_window(
        plan=[{
            "relay_id": True, "run_seconds": 1200,
            "scheduled_start_utc": NOW.isoformat(),
        }],
        completed_relay_ids=set(), now_utc=NOW,
        end_confirmation_minutes=2,
    )

    assert window.code == "INVALID"

    invalid_confirmation = _irrigation_operating_window(
        plan=_plan(NOW), completed_relay_ids=set(), now_utc=NOW,
        end_confirmation_minutes=True,
    )
    assert invalid_confirmation.code == "INVALID"


def test_delayed_final_state_read_expires_hydrawise_proof_before_post():
    plan = _plan(NOW + timedelta(minutes=30))
    state = replace(
        irrigation_state(phase="READY"),
        irrigation_plan_json=json.dumps(plan),
        irrigation_suspension_completed_utc=(NOW - timedelta(minutes=1)).isoformat(),
    )
    calls: list[tuple] = []

    output, stored = _run_ready(
        state, _cycle_for(plan, now=NOW), now=NOW, zone_calls=calls,
        command_clock=lambda: NOW + timedelta(seconds=181),
    )

    assert output.decision_code == "IRRIGATION_OPERATING_WINDOW"
    assert output.details["irrigation_action"]["reason_code"] in {
        "MOWER_STATUS_STALE", "HYDRAWISE_STATUS_STALE_OR_ACTIVE",
    }
    assert calls == []
    assert stored.load().irrigation_phase == "READY"
    assert stored.load().last_decision_code == "IRRIGATION_OPERATING_WINDOW"


def test_delayed_final_state_read_past_window_reopens_without_post():
    plan = _plan(NOW + timedelta(minutes=30))
    state = replace(
        irrigation_state(phase="READY"),
        irrigation_plan_json=json.dumps(plan),
        irrigation_suspension_completed_utc=(NOW - timedelta(minutes=1)).isoformat(),
    )
    calls: list[tuple] = []

    output, stored = _run_ready(
        state, _cycle_for(plan, now=NOW), now=NOW, zone_calls=calls,
        command_clock=lambda: NOW + timedelta(hours=6),
    )

    assert output.decision_code == "IRRIGATION_WINDOW_CANNOT_FIT"
    assert calls == []
    assert stored.load().irrigation_phase == "READY"
    assert stored.load().last_decision_code == "IRRIGATION_WINDOW_CANNOT_FIT"


def test_changed_state_after_reservation_never_posts_or_overwrites_owner():
    class ChangedAfterReservationStore(InMemoryStateStore):
        armed = False

        def save(self, state, *, expected_revision):
            super().save(state, expected_revision=expected_revision)
            if state.irrigation_phase == "START_RESERVED":
                self.armed = True

        def load(self):
            current = super().load()
            if self.armed:
                self.armed = False
                # Model a writer whose current revision owns a maintenance
                # hold while the final remote GET was in flight.
                changed = replace(
                    current, revision=current.revision + 1,
                    maintenance_mode=True, last_decision_code="MAINTENANCE_MODE",
                )
                self._state = AutomationState.from_mapping(changed.to_dict())
                return super().load()
            return current

    plan = _plan(NOW + timedelta(minutes=30))
    state = replace(
        irrigation_state(phase="READY"), irrigation_plan_json=json.dumps(plan),
        irrigation_suspension_completed_utc=(NOW - timedelta(minutes=1)).isoformat(),
    )
    store = ChangedAfterReservationStore(state)
    calls: list[tuple] = []

    output, stored = _run_ready(
        state, _cycle_for(plan, now=NOW), now=NOW, zone_calls=calls, store=store,
    )

    assert output.decision_code == "IRRIGATION_OPERATING_WINDOW"
    assert output.details["irrigation_action"]["reason_code"] == "IRRIGATION_START_RESERVATION_CHANGED"
    assert calls == []
    assert stored.load().maintenance_mode is True
    assert stored.load().last_decision_code == "MAINTENANCE_MODE"


def test_actual_active_water_outside_window_is_diagnostic_hold_without_stop():
    now = NOW - timedelta(hours=1)  # 03:00 Europe/Berlin
    cycle = _cycle_for(_plan(NOW + timedelta(minutes=30)), now=now)
    cycle.details["hydrawise"]["safety"].update(
        clear_now=False, active_zone_count=1, active_relay_ids=[RELAYS[0]],
    )
    state = AutomationState()
    store = InMemoryStateStore(state)
    stops: list[tuple] = []

    output = run_full_failsafe_cycle(
        now_utc=now, settings=settings(), environment=ENV, past_due=False, source="test",
        read_only_runner=lambda **kwargs: observed_cycle(cycle, kwargs["now_utc"]),
        state_store_factory=lambda _environment: store,
        park_sender=lambda *_: {"accepted": True}, start_sender=lambda *_: {"accepted": True},
        suspend_zone_sender=lambda *_: {"accepted": True},
        stop_zone_sender=lambda *args: stops.append(args) or {"accepted": True},
        start_zone_sender=lambda *_: {"accepted": True}, command_clock=lambda: now,
    )

    assert output.decision_code == "IRRIGATION_ACTIVE_OUTSIDE_OPERATING_WINDOW"
    assert output.details["irrigation_operating_window"]["automatic_stop_sent"] is False
    assert stops == []
    assert store.load().irrigation_phase == "FAILED"


def test_active_manual_multi_zone_water_after_0800_remains_owned():
    now = datetime(2026, 8, 13, 8, 30, tzinfo=UTC)  # 10:30 Europe/Berlin
    plan = _plan(now + timedelta(minutes=30))
    for zone in plan:
        zone["operator_manual"] = True
    canonical = json.dumps(plan, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    cycle = _cycle_for(plan, now=now)
    cycle.details["hydrawise"]["safety"].update(
        clear_now=False, active_zone_count=1, active_relay_ids=[RELAYS[0]],
    )
    state = replace(
        irrigation_state(phase="RUNNING", current=RELAYS[0]),
        irrigation_plan_id=hashlib.sha256(canonical.encode("utf-8")).hexdigest(),
        irrigation_plan_json=canonical,
        irrigation_zone_start_reserved_utc=(now - timedelta(minutes=6)).isoformat(),
        irrigation_zone_started_utc=(now - timedelta(minutes=5)).isoformat(),
        irrigation_suspension_until_utc=(now + timedelta(hours=5)).isoformat(),
    )
    store = InMemoryStateStore(state)

    output, stored = _run_ready(
        state, cycle, now=now, zone_calls=[], store=store,
    )

    assert output.decision_code == "IRRIGATION_ZONE_RUNNING"
    assert stored.load().irrigation_phase == "RUNNING"


def test_native_water_after_completed_manual_run_is_not_exempted():
    now = datetime(2026, 8, 13, 8, 30, tzinfo=UTC)
    plan = _plan(now + timedelta(minutes=30))
    for zone in plan:
        zone["operator_manual"] = True
    canonical = json.dumps(plan, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    cycle = _cycle_for(plan, now=now)
    cycle.details["hydrawise"]["safety"].update(
        clear_now=False, active_zone_count=1, active_relay_ids=[RELAYS[0]],
    )
    state = replace(
        irrigation_state(phase="COMPLETE_HOLD"),
        irrigation_plan_id=hashlib.sha256(canonical.encode("utf-8")).hexdigest(),
        irrigation_plan_json=canonical,
        irrigation_current_relay_id=None,
    )

    output, stored = _run_ready(
        state, cycle, now=now, zone_calls=[],
    )

    assert output.decision_code == "IRRIGATION_ACTIVE_OUTSIDE_OPERATING_WINDOW"
    assert stored.load().irrigation_phase == "FAILED"


def test_unrelated_active_relay_during_manual_run_is_not_exempted():
    now = datetime(2026, 8, 13, 8, 30, tzinfo=UTC)
    plan = _plan(now + timedelta(minutes=30))
    for zone in plan:
        zone["operator_manual"] = True
    canonical = json.dumps(plan, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    cycle = _cycle_for(plan, now=now)
    cycle.details["hydrawise"]["safety"].update(
        clear_now=False, active_zone_count=1, active_relay_ids=[RELAYS[1]],
    )
    state = replace(
        irrigation_state(phase="RUNNING", current=RELAYS[0]),
        irrigation_plan_id=hashlib.sha256(canonical.encode("utf-8")).hexdigest(),
        irrigation_plan_json=canonical,
        irrigation_zone_start_reserved_utc=(now - timedelta(minutes=6)).isoformat(),
        irrigation_zone_started_utc=(now - timedelta(minutes=5)).isoformat(),
        irrigation_suspension_until_utc=(now + timedelta(hours=5)).isoformat(),
    )

    output, stored = _run_ready(
        state, cycle, now=now, zone_calls=[],
    )

    assert output.decision_code == "IRRIGATION_ACTIVE_OUTSIDE_OPERATING_WINDOW"
    assert stored.load().irrigation_phase == "FAILED"


def test_next_day_ready_plan_is_expired_before_any_water_restart():
    next_day = NOW + timedelta(days=1)
    old_plan = _plan(NOW + timedelta(minutes=30))
    state = replace(
        irrigation_state(phase="READY"),
        irrigation_plan_json=json.dumps(old_plan),
        irrigation_suspension_completed_utc=(NOW - timedelta(minutes=1)).isoformat(),
        irrigation_suspension_until_utc=(NOW + timedelta(hours=4)).isoformat(),
    )
    calls: list[tuple] = []

    output, stored = _run_ready(
        state, _cycle_for(old_plan, now=next_day), now=next_day, zone_calls=calls,
    )

    assert output.decision_code == "IRRIGATION_PLAN_LEASE_EXPIRED"
    assert calls == []
    assert stored.load().irrigation_phase == "FAILED"
