import copy
from datetime import datetime, timezone

from mower.coordination_execution import (
    CONFIRMATION, consume_request, enabled, mark_terminal, reserve,
)
from mower.state import AutomationState
from test_coordination_request import fixture


def execution_input():
    cycle, previous, need, estimate = fixture()
    return cycle, {
        "schema_version": 1,
        "available": True,
        "needs": [copy.deepcopy(need)],
        "need": copy.deepcopy(need),
        "previous_cycle": previous,
        "charging_end_estimate": estimate,
        "permission_to_start": False,
        "blockers": [],
    }


def test_gate_requires_full_failsafe_and_exact_confirmation():
    values = {
        "COORDINATION_EXECUTION_ENABLED": "true",
        "COORDINATION_EXECUTION_CONFIRMATION": CONFIRMATION,
    }
    assert enabled(values, full_failsafe_gate=True)
    assert not enabled(values, full_failsafe_gate=False)
    assert not enabled({**values, "COORDINATION_EXECUTION_CONFIRMATION": "other"}, full_failsafe_gate=True)


def test_reservation_is_single_use_and_cas_state_is_restart_safe():
    cycle, handover = execution_input()
    state, outcome = reserve(
        AutomationState(), cycle=cycle, execution_input=handover,
        now_utc=datetime.fromisoformat(cycle["executed_at_utc"]),
    )
    assert outcome["accepted"] and state.revision == 1
    duplicate, repeat = reserve(
        state, cycle=cycle, execution_input=handover,
        now_utc=datetime.fromisoformat(cycle["executed_at_utc"]),
    )
    assert not repeat["accepted"]
    assert duplicate == state
    # The serialized state represents a restart. The same need remains consumed.
    restored = AutomationState.from_mapping(state.to_dict())
    terminal = mark_terminal(restored, status="REVALIDATION_FAILED", now_utc=datetime.now(timezone.utc))
    again, replay = reserve(
        terminal, cycle=cycle, execution_input=handover,
        now_utc=datetime.fromisoformat(cycle["executed_at_utc"]),
    )
    assert not replay["accepted"]
    assert again == terminal


def test_changed_authorization_cannot_turn_reservation_into_custom_next():
    cycle, handover = execution_input()
    state, outcome = reserve(
        AutomationState(), cycle=cycle, execution_input=handover,
        now_utc=datetime.fromisoformat(cycle["executed_at_utc"]),
    )
    assert outcome["accepted"]
    changed = copy.deepcopy(handover)
    changed["needs"][0]["valid_until_utc"] = "2026-09-15T03:00:00+00:00"
    _same, draft, reason = consume_request(
        state, cycle=cycle, execution_input=changed,
        now_utc=datetime.fromisoformat(cycle["executed_at_utc"]),
    )
    assert draft is None
    assert "AUTHORIZATION_CHANGED" in reason
    terminal = mark_terminal(state, status="INVALID_OR_CHANGED", now_utc=datetime.now(timezone.utc))
    assert terminal.coordination_execution_request_json is None
    assert "INVALID_OR_CHANGED" in terminal.coordination_execution_reservations_json


def test_unavailable_input_cannot_revalidate_even_with_old_approval_fields():
    cycle, handover = execution_input()
    state, outcome = reserve(
        AutomationState(), cycle=cycle, execution_input=handover,
        now_utc=datetime.fromisoformat(cycle["executed_at_utc"]),
    )
    assert outcome["accepted"]
    unavailable = {**handover, "available": False}
    _same, draft, reason = consume_request(
        state, cycle=cycle, execution_input=unavailable,
        now_utc=datetime.fromisoformat(cycle["executed_at_utc"]),
    )
    assert draft is None and reason == "COORDINATION_INPUT_UNAVAILABLE"
import json
from copy import deepcopy
from datetime import timedelta

from mower.coordination_request import canonical_schedule_id
from mower.full_failsafe import run_full_failsafe_cycle
from mower.state_store import InMemoryStateStore
from test_full_failsafe import ENV, NOW, result, settings, suspended_result


def _handover(cycle):
    zones = deepcopy(cycle.details["hydrawise"]["zones"])
    need = {
        "schema_version": 1, "need_id": "coordination-1", "demand_reference": "approved",
        "mower_id": "mower-1", "required": True, "timing_window_approved": True,
        "station_and_paths_checked": True, "source_plan_id": canonical_schedule_id(zones),
        "earliest_start_utc": (NOW + timedelta(minutes=45)).isoformat(),
        "original_start_utc": (NOW + timedelta(minutes=90)).isoformat(),
        "latest_start_utc": (NOW + timedelta(minutes=120)).isoformat(),
        "valid_until_utc": (NOW + timedelta(minutes=120)).isoformat(), "zones": zones,
    }
    previous = cycle.to_dict()
    previous["executed_at_utc"] = (NOW - timedelta(minutes=1)).isoformat()
    previous["details"]["mower"]["status_timestamp_ms"] -= 60_000
    cycle.details["coordination_shadow_input"] = {
        "schema_version": 1, "captured_at_utc": NOW.isoformat(),
        "complete_from_utc": (NOW - timedelta(hours=2)).isoformat(),
        "complete_until_utc": (NOW + timedelta(hours=12)).isoformat(),
        "occupancy_complete": True, "state_available": True, "manual_stop": False,
        "uncertain_start": False, "occupancy": [],
    }
    cycle.details["coordination_execution_input"] = {
        "schema_version": 1, "available": True, "needs": [need], "need": need,
        "previous_cycle": previous,
        "charging_end_estimate": {"estimated": True, "source": "OBSERVED_COMPLETED_CHARGING_SECTIONS",
                                    "sampleCount": 3, "daysCovered": 2,
                                    "at": (NOW + timedelta(minutes=120)).isoformat()},
        "permission_to_start": False, "blockers": [],
    }


def test_full_failsafe_reserves_coordination_before_any_sender_call():
    cycle = result(irrigation_start=NOW + timedelta(minutes=90), activity="CHARGING")
    _handover(cycle)
    store = InMemoryStateStore()
    calls = []
    environment = {**ENV, "COORDINATION_EXECUTION_ENABLED": "true",
                   "COORDINATION_EXECUTION_CONFIRMATION": "SSV53-COORDINATED-IRRIGATION-PILOT-V1"}
    output = run_full_failsafe_cycle(
        now_utc=NOW, settings=settings(), environment=environment, past_due=False, source="test",
        read_only_runner=lambda **_: cycle, state_store_factory=lambda _: store,
        park_sender=lambda *args: calls.append(("park", args)),
        suspend_zone_sender=lambda *args: calls.append(("suspend", args)),
        start_zone_sender=lambda *args: calls.append(("start", args)),
    )
    assert output.decision_code == "COORDINATION_EXECUTION_RESERVED"
    assert not output.command_sent and calls == []
    assert store.load().coordination_execution_request_json is not None


def test_full_failsafe_off_does_not_reserve_coordination():
    cycle = result(irrigation_start=NOW + timedelta(minutes=90), activity="CHARGING")
    _handover(cycle)
    store = InMemoryStateStore()
    output = run_full_failsafe_cycle(
        now_utc=NOW, settings=settings(), environment=ENV, past_due=False, source="test",
        read_only_runner=lambda **_: cycle, state_store_factory=lambda _: store,
    )
    assert output.decision_code != "COORDINATION_EXECUTION_RESERVED"
    assert store.load().coordination_execution_request_json is None


def test_reserved_coordination_requires_second_revalidation_then_uses_custom_transaction():
    from dataclasses import replace
    first = result(irrigation_start=NOW + timedelta(minutes=90), activity="CHARGING")
    _handover(first)
    store = InMemoryStateStore()
    environment = {**ENV, "COORDINATION_EXECUTION_ENABLED": "true",
                   "COORDINATION_EXECUTION_CONFIRMATION": "SSV53-COORDINATED-IRRIGATION-PILOT-V1"}
    reserved = run_full_failsafe_cycle(
        now_utc=NOW, settings=settings(), environment=environment, past_due=False, source="test",
        read_only_runner=lambda **_: first, state_store_factory=lambda _: store,
    )
    assert reserved.decision_code == "COORDINATION_EXECUTION_RESERVED"
    prior = deepcopy(first.to_dict())
    later = replace(first, executed_at_utc=(NOW + timedelta(minutes=1)).isoformat(), details=deepcopy(first.details))
    later.details["mower"]["status_timestamp_ms"] = int((NOW + timedelta(minutes=1)).timestamp() * 1000)
    later.details["hydrawise"]["safety"]["observed_at_utc"] = (NOW + timedelta(minutes=1)).isoformat()
    later.details["coordination_shadow_input"]["captured_at_utc"] = (NOW + timedelta(minutes=1)).isoformat()
    later.details["coordination_execution_input"]["previous_cycle"] = prior
    scheduled = run_full_failsafe_cycle(
        now_utc=NOW + timedelta(minutes=1), settings=settings(), environment=environment,
        past_due=False, source="test", read_only_runner=lambda **_: later,
        state_store_factory=lambda _: store,
    )
    assert scheduled.decision_code == "COORDINATION_EXECUTION_SCHEDULED", scheduled.details["coordination_execution"]
    override = json.loads(store.load().irrigation_schedule_override_json)
    assert override["kind"] == "CUSTOM_NEXT" and override["coordination_execution"] is True
    assert [zone["relay_id"] for zone in override["zones"]] == [zone["relay_id"] for zone in first.details["hydrawise"]["zones"]]
    assert store.load().coordination_execution_request_json is None


def test_renamed_need_cannot_replay_the_same_source_plan():
    cycle, handover = execution_input()
    reserved, outcome = reserve(
        AutomationState(), cycle=cycle, execution_input=handover,
        now_utc=datetime.fromisoformat(cycle["executed_at_utc"]),
    )
    assert outcome["accepted"]
    replay = copy.deepcopy(handover)
    replay["need"] = {**replay["need"], "need_id": "renamed"}
    replay["needs"] = [replay["need"]]
    same, repeated = reserve(
        mark_terminal(reserved, status="EXPIRED", now_utc=datetime.now(timezone.utc)),
        cycle=cycle, execution_input=replay,
        now_utc=datetime.fromisoformat(cycle["executed_at_utc"]),
    )
    assert not repeated["accepted"]
    assert "ALREADY_CONSUMED" in repeated["reason"]
    assert same.coordination_execution_request_json is None


def test_two_scheduler_reservations_conflict_before_any_sender():
    from mower.state_store import InMemoryStateStore, StateConflictError
    cycle, handover = execution_input()
    first, accepted = reserve(AutomationState(), cycle=cycle, execution_input=handover,
                              now_utc=datetime.fromisoformat(cycle["executed_at_utc"]))
    second, also_accepted = reserve(AutomationState(), cycle=cycle, execution_input=handover,
                                    now_utc=datetime.fromisoformat(cycle["executed_at_utc"]))
    assert accepted["accepted"] and also_accepted["accepted"]
    store = InMemoryStateStore()
    store.save(first, expected_revision=0)
    try:
        store.save(second, expected_revision=0)
    except StateConflictError:
        pass
    else:
        raise AssertionError("parallel reservation unexpectedly committed")


def test_permission_hint_cannot_enable_reservation():
    cycle, handover = execution_input()
    handover["permission_to_start"] = True
    state, outcome = reserve(AutomationState(), cycle=cycle, execution_input=handover,
                             now_utc=datetime.fromisoformat(cycle["executed_at_utc"]))
    assert not outcome["accepted"]
    assert state == AutomationState()


def test_disabled_after_reservation_keeps_dedup_but_creates_no_override():
    first = result(irrigation_start=NOW + timedelta(minutes=90), activity="CHARGING")
    _handover(first)
    store = InMemoryStateStore()
    enabled_env = {**ENV, "COORDINATION_EXECUTION_ENABLED": "true",
                   "COORDINATION_EXECUTION_CONFIRMATION": "SSV53-COORDINATED-IRRIGATION-PILOT-V1"}
    assert run_full_failsafe_cycle(
        now_utc=NOW, settings=settings(), environment=enabled_env, past_due=False, source="test",
        read_only_runner=lambda **_: first, state_store_factory=lambda _: store,
    ).decision_code == "COORDINATION_EXECUTION_RESERVED"
    later = deepcopy(first)
    later.details["mower"]["status_timestamp_ms"] = int((NOW + timedelta(minutes=1)).timestamp() * 1000)
    output = run_full_failsafe_cycle(
        now_utc=NOW + timedelta(minutes=1), settings=settings(), environment=ENV,
        past_due=False, source="test", read_only_runner=lambda **_: later,
        state_store_factory=lambda _: store,
    )
    assert output.decision_code != "COORDINATION_EXECUTION_SCHEDULED"
    assert store.load().coordination_execution_request_json is None
    assert '"status":"DISABLED"' in store.load().coordination_execution_reservations_json
    assert store.load().irrigation_schedule_override_json is None


def test_second_revalidation_checks_reserved_minute_not_newly_shifted_slot():
    from dataclasses import replace
    cycle, handover = execution_input()
    state, outcome = reserve(AutomationState(), cycle=cycle, execution_input=handover,
                             now_utc=datetime.fromisoformat(cycle["executed_at_utc"]))
    assert outcome["accepted"]
    later = copy.deepcopy(cycle)
    later["executed_at_utc"] = "2026-09-15T02:01:00+00:00"
    later["details"]["mower"]["status_timestamp_ms"] += 60_000
    later["details"]["hydrawise"]["safety"]["observed_at_utc"] = later["executed_at_utc"]
    later["details"]["coordination_shadow_input"]["captured_at_utc"] = later["executed_at_utc"]
    # Only the stored 02:45 slot becomes occupied; a newly planned 02:46
    # start would incorrectly appear free.
    later["details"]["coordination_shadow_input"]["occupancy"] = [{
        "start": "2026-09-15T02:45:00+00:00", "end": "2026-09-15T02:46:00+00:00",
        "source": "training",
    }]
    changed = copy.deepcopy(handover)
    changed["previous_cycle"] = cycle
    _same, request_value, reason = consume_request(
        state, cycle=later, execution_input=changed,
        now_utc=datetime.fromisoformat(later["executed_at_utc"]),
    )
    assert request_value is None
    assert "EXACT_SLOT_REVALIDATION_FAILED" in reason


def test_consume_rechecks_intervening_pending_operator_action():
    cycle, handover = execution_input()
    state, accepted = reserve(
        AutomationState(), cycle=cycle, execution_input=handover,
        now_utc=datetime.fromisoformat(cycle["executed_at_utc"]),
    )
    assert accepted["accepted"]
    blocked = AutomationState.from_mapping({
        **state.to_dict(), "operator_request_status": "PENDING",
        "operator_request_action": "PARK_MOWER",
    })
    _same, saved, reason = consume_request(
        blocked, cycle=cycle, execution_input=handover,
        now_utc=datetime.fromisoformat(cycle["executed_at_utc"]),
    )
    assert saved is None
    assert "MANUAL_OR_MAINTENANCE_ACTIVE" in reason


def test_empty_ledger_is_not_accepted_as_dedup_evidence():
    cycle, handover = execution_input()
    unsafe = AutomationState(coordination_execution_reservations_json="[{}]")
    unchanged, outcome = reserve(
        unsafe, cycle=cycle, execution_input=handover,
        now_utc=datetime.fromisoformat(cycle["executed_at_utc"]),
    )
    assert unchanged == unsafe
    assert not outcome["accepted"]
    assert "Koordinationsreservierungen" in outcome["reason"]


def test_start_authorization_rejects_naive_capture_time_and_future_complete_from():
    from mower.coordination_execution import start_authorized
    cycle, handover = execution_input()
    state, accepted = reserve(
        AutomationState(), cycle=cycle, execution_input=handover,
        now_utc=datetime.fromisoformat(cycle["executed_at_utc"]),
    )
    assert accepted["accepted"]
    saved = json.loads(state.coordination_execution_request_json)
    override = {
        "coordination_need_id": saved["need_id"],
        "coordination_source_plan_id": saved["source_plan_id"],
        "coordination_need_sha256": saved["need_sha256"],
    }
    plan = [{
        "scheduled_start_utc": saved["selected_start_utc"],
        "scheduled_end_utc": "2026-09-15T02:50:00+00:00",
    }]
    malformed = deepcopy(cycle)
    malformed["details"]["coordination_shadow_input"]["captured_at_utc"] = "2026-09-15T02:00:00"
    assert "Zeitzone" in start_authorized(
        override=override, cycle=malformed, execution_input=handover,
        now_utc=datetime.fromisoformat(cycle["executed_at_utc"]),
        remaining_plan=plan, projected_end_utc=datetime(2026, 9, 15, 2, 50, tzinfo=timezone.utc),
    )
    malformed["details"]["coordination_shadow_input"]["captured_at_utc"] = cycle["executed_at_utc"]
    malformed["details"]["coordination_shadow_input"]["complete_from_utc"] = "2026-09-15T03:00:00+00:00"
    assert "CAPTURE_STARTS_IN_FUTURE" in start_authorized(
        override=override, cycle=malformed, execution_input=handover,
        now_utc=datetime.fromisoformat(cycle["executed_at_utc"]),
        remaining_plan=plan, projected_end_utc=datetime(2026, 9, 15, 2, 50, tzinfo=timezone.utc),
    )


def test_projection_preserves_a_twenty_minute_gap_and_confirmation_windows():
    from mower.full_failsafe import _projected_irrigation_end
    now = datetime(2026, 9, 15, 2, 0, tzinfo=timezone.utc)
    plan = [
        {"relay_id": 1, "run_seconds": 300, "coordination_execution": True,
         "scheduled_start_utc": "2026-09-15T02:00:00+00:00"},
        {"relay_id": 2, "run_seconds": 300, "coordination_execution": True,
         "scheduled_start_utc": "2026-09-15T02:25:00+00:00"},
    ]
    # The first zone ends at 02:05 plus two-minute confirmation.  The saved
    # 20-minute gap to 02:25 must remain in the physical occupancy horizon.
    assert _projected_irrigation_end(
        plan=plan, completed_relay_ids=set(), current_relay_id=None,
        current_started_utc=None, now_utc=now, end_confirmation_minutes=2,
    ) == datetime(2026, 9, 15, 2, 32, tzinfo=timezone.utc)


def test_projection_carries_late_start_into_the_physical_pause():
    from mower.full_failsafe import _projected_irrigation_end
    plan = [
        {"relay_id": 1, "run_seconds": 300, "coordination_execution": True,
         "scheduled_start_utc": "2026-09-15T02:00:00+00:00"},
        {"relay_id": 2, "run_seconds": 300, "coordination_execution": True,
         "scheduled_start_utc": "2026-09-15T02:25:00+00:00"},
    ]
    # First run 02:03-02:08, 20m physical pause, second 02:28-02:33,
    # confirmation until 02:35. Confirmation overlaps the deliberate pause.
    args = dict(plan=plan, completed_relay_ids=set(), current_relay_id=None,
                current_started_utc=None,
                now_utc=datetime(2026, 9, 15, 2, 3, tzinfo=timezone.utc),
                end_confirmation_minutes=2)
    assert _projected_irrigation_end(**args) == datetime(2026, 9, 15, 2, 35, tzinfo=timezone.utc)
    # The legacy executor does not adopt coordinated scheduled pauses.
    for zone in plan:
        zone.pop("coordination_execution")
    assert _projected_irrigation_end(**args) == datetime(2026, 9, 15, 2, 17, tzinfo=timezone.utc)


def test_original_contiguous_program_validation_is_not_relaxed():
    import pytest
    from mower.full_failsafe import _validated_upcoming_plan
    from test_full_failsafe import RELAYS
    cycle = result(irrigation_start=NOW + timedelta(minutes=90), activity="CHARGING")
    zones = cycle.details["hydrawise"]["zones"]
    for zone in zones[1:]:
        zone["scheduled_start_utc"] = (
            datetime.fromisoformat(zone["scheduled_start_utc"]) + timedelta(minutes=20)
        ).isoformat()
    kwargs = dict(now_utc=NOW, expected_zone_count=7,
                  expected_relay_ids=frozenset(RELAYS), max_lead_minutes=1440)
    with pytest.raises(RuntimeError, match="lückenlosen"):
        _validated_upcoming_plan(cycle.details, **kwargs)
    _validated_upcoming_plan(cycle.details, **kwargs, preserve_approved_gaps=True)


def test_persisted_draft_offset_cannot_be_collapsed_before_custom_transaction():
    first = result(irrigation_start=NOW + timedelta(minutes=90), activity="CHARGING")
    _handover(first)
    store = InMemoryStateStore()
    environment = {**ENV, "COORDINATION_EXECUTION_ENABLED": "true",
                   "COORDINATION_EXECUTION_CONFIRMATION": "SSV53-COORDINATED-IRRIGATION-PILOT-V1"}
    assert run_full_failsafe_cycle(
        now_utc=NOW, settings=settings(), environment=environment, past_due=False, source="test",
        read_only_runner=lambda **_: first, state_store_factory=lambda _: store,
    ).decision_code == "COORDINATION_EXECUTION_RESERVED"
    reserved = store.load()
    draft = json.loads(reserved.coordination_execution_request_json)
    draft["zones"][1]["offset_seconds"] = 0
    tampered = AutomationState.from_mapping({
        **reserved.to_dict(), "revision": reserved.revision + 1,
        "coordination_execution_request_json": json.dumps(draft),
    })
    store.save(tampered, expected_revision=reserved.revision)
    later = deepcopy(first)
    later.details["mower"]["status_timestamp_ms"] = int((NOW + timedelta(minutes=1)).timestamp() * 1000)
    later.details["hydrawise"]["safety"]["observed_at_utc"] = (NOW + timedelta(minutes=1)).isoformat()
    later.details["coordination_shadow_input"]["captured_at_utc"] = (NOW + timedelta(minutes=1)).isoformat()
    later.details["coordination_execution_input"]["previous_cycle"] = first.to_dict()
    output = run_full_failsafe_cycle(
        now_utc=NOW + timedelta(minutes=1), settings=settings(), environment=environment, past_due=False, source="test",
        read_only_runner=lambda **_: later, state_store_factory=lambda _: store,
    )
    assert output.decision_code == "COORDINATION_EXECUTION_REVALIDATION_FAILED"
    assert store.load().irrigation_schedule_override_json is None


def test_full_failsafe_preserves_persisted_coordination_gap_before_next_zone():
    from dataclasses import replace
    plan = deepcopy(result().details["hydrawise"]["zones"])
    plan[0]["scheduled_start_utc"] = (NOW - timedelta(minutes=10)).isoformat()
    plan[0]["scheduled_end_utc"] = (NOW - timedelta(minutes=5)).isoformat()
    plan[1]["scheduled_start_utc"] = (NOW + timedelta(minutes=20)).isoformat()
    plan[1]["scheduled_end_utc"] = (NOW + timedelta(minutes=40)).isoformat()
    initial = AutomationState(
        parked_by_automation=True, automation_park_source="irrigation",
        automation_restart_allowed=True, park_command_sent_utc=(NOW - timedelta(minutes=6)).isoformat(),
        last_mower_activity="PARKED_IN_CS", park_confirmed_utc=(NOW - timedelta(minutes=5)).isoformat(),
        park_confirmed_observations=2, irrigation_phase="READY", irrigation_plan_id="gap-plan",
        irrigation_plan_json=json.dumps(plan), irrigation_suspended_relay_ids_json=json.dumps([z["relay_id"] for z in plan]),
        irrigation_suspension_until_utc=(NOW + timedelta(hours=5)).isoformat(),
        irrigation_suspension_completed_utc=(NOW - timedelta(minutes=1)).isoformat(),
        irrigation_completed_relay_ids_json=json.dumps([plan[0]["relay_id"]]),
        irrigation_schedule_override_json=json.dumps({
            "version": 1, "kind": "CUSTOM_NEXT", "status": "EXECUTING",
            "coordination_execution": True,
        }),
    )
    store = InMemoryStateStore(initial)
    output = run_full_failsafe_cycle(
        now_utc=NOW, settings=settings(), environment={**ENV, "COORDINATION_EXECUTION_ENABLED": "true",
            "COORDINATION_EXECUTION_CONFIRMATION": "SSV53-COORDINATED-IRRIGATION-PILOT-V1"},
        past_due=False, source="test", read_only_runner=lambda **_: suspended_result(),
        state_store_factory=lambda _: store,
        park_sender=lambda *_: (_ for _ in ()).throw(AssertionError("unexpected park")),
        suspend_zone_sender=lambda *_: (_ for _ in ()).throw(AssertionError("unexpected suspend")),
        start_zone_sender=lambda *_: (_ for _ in ()).throw(AssertionError("gap must not start")),
    )
    assert output.decision_code == "COORDINATION_EXECUTION_ZONE_GAP_WAIT"
