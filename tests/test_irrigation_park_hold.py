from copy import deepcopy
from dataclasses import replace
from datetime import timedelta
import json

import pytest

from mower import irrigation_park_hold as hold
from mower.full_failsafe import _water_park_authorized
from mower.state import AutomationState
from tests.test_full_failsafe import ENV, NOW, irrigation_state, suspended_result


def mower(at=NOW):
    value = deepcopy(suspended_result().details["mower"])
    value["status_timestamp_ms"] = int(at.timestamp() * 1000)
    return value


def advance(state, at, value=None, *, fresh=False):
    return hold.observe(state, value or mower(), now_utc=at, event_fresh=fresh)[0]


def confirmed():
    state = advance(irrigation_state(phase="READY"), NOW, fresh=True)
    assert not hold.valid(state, mower(), now_utc=NOW)
    state = advance(state, NOW+timedelta(minutes=1), fresh=True)
    assert hold.valid(state, mower(), now_utc=NOW+timedelta(minutes=1))
    return state


def test_same_docked_event_is_reused_for_hours_with_continuous_successful_reads():
    state = confirmed()
    for minute in range(2, 241):
        at = NOW+timedelta(minutes=minute)
        state = advance(state, at)
        assert hold.valid(state, mower(), now_utc=at)
    # A quick restart preserves the durable identity, but grants no additional time.
    restored = AutomationState.from_mapping(state.to_dict())
    assert hold.valid(restored, mower(), now_utc=at+timedelta(seconds=60))
    assert not hold.valid(restored, mower(), now_utc=at+timedelta(seconds=91))


@pytest.mark.parametrize("change", [
    {"activity": "LEAVING"}, {"activity": "MOWING"}, {"mode": "MAIN_AREA"},
    {"connected": False}, {"connected": None}, {"error_code": 1},
    {"error_code": None}, {"state": "ERROR"}, {"state": "UNKNOWN"},
    {"mower_id": "another-mower"}, {"status_timestamp_ms": 0},
])
def test_changed_device_state_invalidates_and_same_old_event_cannot_revive_it(change):
    state = confirmed()
    at = NOW+timedelta(minutes=2)
    changed = {**mower(), **change}
    state = advance(state, at, changed)
    assert not hold.valid(state, changed, now_utc=at)
    state = advance(state, at+timedelta(minutes=1), mower(), fresh=True)
    assert not hold.valid(state, mower(), now_utc=at+timedelta(minutes=1))


@pytest.mark.parametrize("change", [
    {"park_command_sent_utc": (NOW+timedelta(seconds=30)).isoformat()},
    {"last_start_command_utc": NOW.isoformat()}, {"parked_by_automation": False},
    {"maintenance_mode": True}, {"continuous_mowing_owned": True},
    {"mower_start_pending_since_utc": NOW.isoformat()},
    {"operator_request_id": "new-action"}, {"operator_request_status": "PENDING"},
    {"operator_request_status": "COMPLETED"}, {"automation_park_source": "operator"},
    {"automation_restart_allowed": False},
])
def test_control_changes_revoke_proof_even_at_final_dispatch(change):
    state = confirmed()
    edited = replace(state, **change)
    assert not hold.valid(edited, mower(), now_utc=NOW+timedelta(minutes=1),
                          expected_json=state.irrigation_park_hold_json)


def test_gap_demands_a_new_fresh_event_and_two_reads():
    state = confirmed()
    state = advance(state, NOW+timedelta(minutes=4))
    state = advance(state, NOW+timedelta(minutes=5), fresh=True)
    assert not hold.valid(state, mower(), now_utc=NOW+timedelta(minutes=5))
    fresh = mower(NOW+timedelta(minutes=6))
    state = advance(state, NOW+timedelta(minutes=6), fresh, fresh=True)
    assert not hold.valid(state, fresh, now_utc=NOW+timedelta(minutes=6))
    state = advance(state, NOW+timedelta(minutes=7), fresh, fresh=True)
    assert hold.valid(state, fresh, now_utc=NOW+timedelta(minutes=7))


def test_input_failure_persists_revocation_across_restart():
    state = confirmed().record_cycle(started_utc=NOW+timedelta(minutes=2),
                                    success=False, decision_code="INPUT_UNAVAILABLE")
    state = AutomationState.from_mapping(state.to_dict())
    state = replace(state, park_confirmed_utc=NOW.isoformat(), park_confirmed_observations=3)
    state = advance(state, NOW+timedelta(minutes=3), fresh=True)
    assert not hold.valid(state, mower(), now_utc=NOW+timedelta(minutes=3))


def test_duplicate_cycle_cannot_count_as_second_confirmation():
    state = advance(irrigation_state(phase="READY"), NOW, fresh=True)
    state = advance(state, NOW, fresh=True)
    assert not hold.valid(state, mower(), now_utc=NOW)


def test_old_event_cannot_create_hold_and_dispatch_identity_is_checked():
    state = advance(irrigation_state(phase="READY"), NOW, fresh=False)
    assert not hold.valid(state, mower(), now_utc=NOW)
    state = confirmed()
    assert not hold.valid(state, mower(), now_utc=NOW+timedelta(minutes=1), expected_json="different")


def test_old_event_before_own_park_and_future_events_are_rejected():
    for event in (NOW-timedelta(hours=1), NOW+timedelta(minutes=2)):
        state = advance(irrigation_state(phase="READY"), NOW, mower(event), fresh=True)
        assert not hold.valid(state, mower(event), now_utc=NOW)


def test_flag_off_preserves_legacy_freshness_and_flag_on_requires_the_hold():
    state = confirmed()
    for minute in range(2, 8):
        state = advance(state, NOW+timedelta(minutes=minute))
    at = NOW+timedelta(minutes=7)
    assert not _water_park_authorized(state, mower(), now_utc=at, environment=ENV, max_age_seconds=180)
    assert _water_park_authorized(state, mower(), now_utc=at,
                                  environment={**ENV, hold.FLAG: "true"}, max_age_seconds=180)


@pytest.mark.parametrize("change", [
    {"since_utc": "broken"}, {"checked_at_utc": None}, {"source_at_utc": "broken"},
    {"observations": "2"}, {"observations": 0}, {"binding": "invalid"},
    {"status": "UNKNOWN"}, {"version": True},
    {"status": "INVALID", "not_before_utc": "broken"},
])
def test_corrupt_persisted_proof_is_revoked_without_reviving_the_old_event(change):
    state = confirmed()
    proof = json.loads(state.irrigation_park_hold_json)
    state = replace(state, irrigation_park_hold_json=json.dumps({**proof, **change}))
    at = NOW + timedelta(minutes=2)
    assert not hold.valid(state, mower(), now_utc=at)
    state = advance(state, at, fresh=True)
    assert json.loads(state.irrigation_park_hold_json)["status"] == "INVALID"
    state = advance(state, at + timedelta(seconds=30), fresh=True)
    assert not hold.valid(state, mower(), now_utc=at + timedelta(seconds=30))


@pytest.mark.parametrize("failure", ["inputs", "transport"])
def test_real_failed_read_path_revokes_before_protective_guard_or_exception(failure):
    from functools import partial
    from mower.config_source import InputUnavailable
    from mower.full_failsafe import run_full_failsafe_cycle
    from mower.input_failure_guard import run_input_failure_guard
    from mower.state_store import InMemoryStateStore
    from tests.test_input_failure_guard import mower_item
    from tests.test_full_failsafe import settings

    store = InMemoryStateStore(confirmed())
    at = NOW + timedelta(seconds=90)
    def broken(**_):
        raise InputUnavailable("injected") if failure == "inputs" else TimeoutError("injected")
    def forbidden(*_):
        raise AssertionError("No device command on failed input")
    kwargs = dict(now_utc=at, settings=settings(manual=True),
        environment={**ENV, hold.FLAG: "true"}, past_due=False, source="test",
        read_only_runner=broken, state_store_factory=lambda _: store,
        park_sender=forbidden, start_sender=forbidden, start_zone_sender=forbidden,
        input_failure_runner=partial(run_input_failure_guard,
            mower_fetcher=lambda *_: [mower_item(activity="PARKED_IN_CS", mode="HOME", observed=at)],
            clock=lambda: at))
    if failure == "transport":
        with pytest.raises(TimeoutError):
            run_full_failsafe_cycle(**kwargs)
    else:
        result = run_full_failsafe_cycle(**kwargs)
        assert result.details["irrigation_park_hold"]["status"] == "REVOKED"
        assert not result.command_sent
    restored = AutomationState.from_mapping(store.load().to_dict())
    assert json.loads(restored.irrigation_park_hold_json)["status"] == "INVALID"
    restored = advance(restored, NOW + timedelta(minutes=2), fresh=True)
    assert not hold.valid(restored, mower(), now_utc=NOW + timedelta(minutes=2))
