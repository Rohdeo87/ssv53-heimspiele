"""No-network replay of Sep 12: old dock event, no watering, old suspensions."""
from copy import deepcopy
from dataclasses import replace
from datetime import timedelta
import json

import pytest

from mower import irrigation_park_hold as hold
from mower.device_send_guard import unresolved_device_sends, load_device_send_journal
from mower.full_failsafe import _reconcile_observed_device_writes, run_full_failsafe_cycle
from mower.manual_control_api import manual_context, request_manual_control, ManualControlError
from mower.runtime import RuntimeSettings
from mower.state import AutomationState
from mower.state_store import InMemoryStateStore
from tests.test_full_failsafe import NOW, RELAYS, result
from tests.test_manual_control_api import ENV as BASE_ENV, details
from tests.test_manual_control_cycle import ready

ENV = {**BASE_ENV, hold.FLAG: "true"}


def held():
    state = replace(ready(), park_confirmed_observations=2)
    data = details()
    data["mower"].update(mode="HOME", activity="PARKED_IN_CS")
    state, _ = hold.observe(state, data["mower"], now_utc=NOW, event_fresh=True)
    for minute in range(1, 11):
        state, _ = hold.observe(state, data["mower"], now_utc=NOW+timedelta(minutes=minute),
                                event_fresh=minute <= 2)
    at = NOW+timedelta(minutes=10)
    state = replace(state, last_hydrawise_success_utc=at.isoformat())
    data["hydrawise"]["safety"]["observed_at_utc"] = at.isoformat()
    assert hold.valid(state, data["mower"], now_utc=at)
    return state, data, at


def admit(state, data, at, **extra):
    store = InMemoryStateStore(state)
    context = manual_context(state, data, ENV, at)[0]
    body = {"operation": "START", "source": "APP", "contextToken": context["contextToken"], **extra}
    updated, _ = request_manual_control(store=store, state=state, details=data, environment=ENV,
                                        now_utc=at, request_id="manual-start-held", payload=body)
    return updated


def tick(state, data, at, *, clock=None):
    store = InMemoryStateStore(AutomationState.from_mapping(state.to_dict()))
    data = deepcopy(data)
    data["hydrawise"]["safety"]["observed_at_utc"] = at.isoformat()
    cycle = replace(result(), details=data, executed_at_utc=at.isoformat())
    calls = []
    def sender(kind):
        return lambda *args: calls.append(kind) or {"accepted": True, "message_type": "info"}
    output = run_full_failsafe_cycle(now_utc=at, settings=RuntimeSettings.from_mapping(ENV), environment=ENV,
        past_due=False, source="manual-start-release-test", read_only_runner=lambda **_: cycle,
        state_store_factory=lambda _: store, command_clock=clock or (lambda: at),
        park_sender=sender("PARK"), start_sender=sender("START"),
        suspend_zone_sender=sender("SUSPEND"), start_zone_sender=sender("WATER_START"),
        stop_zone_sender=sender("WATER_STOP"))
    return output, store.load(), calls


def test_old_dock_no_watering_can_start_without_false_conflict_and_survives_restart():
    state, data, at = held()
    public = manual_context(state, data, ENV, at)[0]
    assert public["stationConfirmed"] and public["canStart"]
    assert not any(public["confirmations"][key] for key in (
        "occupancyRequired", "dryingRequired", "waterChoiceRequired"))
    requested = admit(state, data, at)
    assert hold.start_valid(requested, data["mower"], now_utc=at)
    assert not hold.valid(requested, data["mower"], now_utc=at)  # not water permission
    output, sent, calls = tick(requested, data, at+timedelta(minutes=1))
    assert calls == ["START"], (output.decision_code, output.details.get("start_action"))
    assert sent.irrigation_park_hold_json is None
    _, _, repeated = tick(sent, data, at+timedelta(seconds=90))
    assert "START" not in repeated


@pytest.mark.parametrize("change", [
    {"connected": False}, {"activity": "MOWING"}, {"activity": "GOING_HOME"},
    {"state": "STOPPED"}, {"error_code": 1}, {"mower_id": "other"},
])
def test_changed_mower_revokes_start_handoff(change):
    state, data, at = held()
    requested = admit(state, data, at)
    data["mower"].update(change)
    assert not hold.start_valid(requested, data["mower"], now_utc=at)
    _, _, calls = tick(requested, data, at+timedelta(minutes=1))
    assert "START" not in calls


@pytest.mark.parametrize("case", ["timeout", "api_failure", "new_park", "water_unknown", "water_running"])
def test_request_changes_and_water_still_block(case):
    state, data, at = held()
    requested = admit(state, data, at)
    if case == "timeout":
        at += timedelta(seconds=121)
    elif case == "api_failure":
        requested = requested.record_cycle(started_utc=at, success=False, decision_code="INPUT_UNAVAILABLE")
    elif case == "new_park":
        requested = replace(requested, operator_request_id="park-new", operator_request_action="PARK_MOWER")
    elif case == "water_unknown":
        data["hydrawise"]["safety"].update(available=False, fresh=False)
    else:
        data["hydrawise"]["safety"].update(clear_now=False, active_zone_count=1, active_relay_ids=[RELAYS[0]])
    _, _, calls = tick(requested, data, at)
    assert "START" not in calls


def test_handoff_expiry_at_final_send_fence_sends_nothing():
    state, data, at = held()
    requested = admit(state, data, at)
    output, persisted, calls = tick(requested, data, at, clock=lambda: at+timedelta(seconds=121))
    assert calls == []
    assert output.details["start_action"]["reason_code"] == "MOWER_STATUS_STALE"
    assert persisted.mower_start_pending_since_utc is None


def test_unowned_park_and_automatic_start_do_not_inherit_manual_handoff():
    state, data, at = held()
    assert not manual_context(replace(state, parked_by_automation=False), data, ENV, at)[0]["canStart"]
    _, _, calls = tick(state, data, at)
    assert "START" not in calls


def test_real_occupancy_and_drying_still_require_explicit_confirmation():
    state, data, at = held()
    data["current_plan"]["blocked_now"] = {"source": "training", "title": "Training", "resource_id": "rasen",
        "start": at.isoformat(), "end": (at+timedelta(hours=1)).isoformat()}
    with pytest.raises(ManualControlError, match="tatsächlich frei"):
        admit(state, data, at)
    data["current_plan"]["blocked_now"] = None
    data["hydrawise"]["release_confirmation"] = {"dry_until_utc": (at+timedelta(hours=1)).isoformat()}
    with pytest.raises(ManualControlError, match="ausreichend trocken"):
        admit(state, data, at)


def test_ambiguous_send_and_disabled_option_cannot_admit_old_dock_start():
    state, data, at = held()
    blocked = replace(state, device_send_journal_json=json.dumps([receipt(RELAYS[0], status="UNKNOWN")]))
    assert not manual_context(blocked, data, ENV, at)[0]["canStart"]
    public = manual_context(state, data, {**ENV, hold.FLAG: "false"}, at)[0]
    assert not public["stationConfirmed"] and not public["canStart"]


def test_new_request_cannot_reissue_or_extend_a_pending_handoff():
    state, data, at = held()
    requested = admit(state, data, at)
    next_request = replace(requested, operator_request_id="second", operator_request_session_id="second")
    projected = hold.prepare_manual_start(requested, next_request, data["mower"], now_utc=at+timedelta(seconds=60))
    assert projected.irrigation_park_hold_json == requested.irrigation_park_hold_json
    assert not hold.start_valid(projected, data["mower"], now_utc=at+timedelta(seconds=60))


def receipt(relay, *, status="SENT_UNCONFIRMED", kind="SUSPEND"):
    until = NOW+timedelta(hours=4)
    return {"version": 1, "id": str(relay), "kind": kind, "target": str(relay),
        "intent_key": f"irrigation:old-plan-{relay}:suspend:{relay}:{until.isoformat()}",
        "control_binding": "old", "reserved_at_utc": (NOW-timedelta(minutes=1)).isoformat(),
        "dispatched_at_utc": NOW.isoformat(), "deadline_utc": (NOW+timedelta(seconds=30)).isoformat(),
        "status": status}


def suspension_case():
    state = AutomationState(irrigation_plan_id="new-plan", device_send_journal_json=json.dumps([receipt(r) for r in RELAYS]))
    data = details()
    at = NOW+timedelta(hours=5)
    data["hydrawise"]["safety"]["observed_at_utc"] = at.isoformat()
    for zone in data["hydrawise"]["zone_observations"]:
        zone.update(valid=True, scheduled=True, scheduled_start_utc=(at+timedelta(days=1)).isoformat())
    return state, data, at


def test_seven_suspensions_are_resolved_across_plan_ids_with_positive_schedule_proof():
    state, data, at = suspension_case()
    assert len(unresolved_device_sends(state)) == 7
    resolved = _reconcile_observed_device_writes(state, data["mower"], data, now_utc=at)
    assert not unresolved_device_sends(resolved)
    assert all("after-suspension" in row["evidence"] for row in load_device_send_journal(resolved))
    assert not unresolved_device_sends(AutomationState.from_mapping(resolved.to_dict()))


@pytest.mark.parametrize("case", ["unknown_send", "other_kind", "missing_zone", "missing_time", "early_run", "invalid_zone", "stale", "running", "bad_intent"])
def test_no_reconciliation_without_bounded_write_and_current_complete_proof(case):
    state, data, at = suspension_case()
    entries = load_device_send_journal(state)
    if case == "unknown_send":
        for row in entries: row["status"] = "UNKNOWN"
    elif case == "other_kind":
        for row in entries: row["kind"] = "NATIVE_RESUME"
    elif case == "bad_intent":
        for row in entries: row["intent_key"] = "operator:unrelated"
    elif case == "missing_zone":
        data["hydrawise"]["safety"]["observed_relay_ids"] = RELAYS[:-1]
    elif case in {"missing_time", "early_run", "invalid_zone"}:
        for zone in data["hydrawise"]["zone_observations"]:
            if case == "invalid_zone": zone["valid"] = False
            else: zone["scheduled_start_utc"] = None if case == "missing_time" else NOW.isoformat()
    elif case == "stale": data["hydrawise"]["safety"]["fresh"] = False
    else: data["hydrawise"]["safety"].update(active_relay_ids=[RELAYS[0]], active_zone_count=1)
    state = replace(state, device_send_journal_json=json.dumps(entries))
    resolved = _reconcile_observed_device_writes(state, data["mower"], data, now_utc=at)
    assert len(unresolved_device_sends(resolved)) == 7
