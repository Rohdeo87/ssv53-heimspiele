from dataclasses import replace
from datetime import datetime, timedelta, timezone

from mower.state import AutomationState


LAST = datetime(2026, 9, 10, 13, 52, tzinfo=timezone.utc)
DRYING = "2026-09-10T05:10:00+00:00"


def before_gap():
    state = AutomationState(
        last_hydrawise_success_utc=LAST.isoformat(),
        last_hydrawise_observed_utc=LAST.isoformat(),
        last_hydrawise_active_count=0,
        hydrawise_clear_since_utc=DRYING, hydrawise_drying_since_utc=DRYING,
        hydrawise_clear_origin="IRRIGATION_END",
        next_irrigation_start_utc="2026-09-11T02:30:00+00:00",
    )
    for minute in (1, 2):
        state = state.record_cycle(started_utc=LAST+timedelta(minutes=minute, microseconds=700),
                                   success=True, decision_code="fixture", hydrawise_clear=False)
    return state


def clear_again(state, seconds=180, active=0):
    observed = LAST + timedelta(seconds=seconds)
    return state.record_cycle(
        started_utc=observed+timedelta(microseconds=709), success=True, decision_code="fixture",
        hydrawise_success_utc=observed, hydrawise_observed_utc=observed,
        hydrawise_clear=active == 0, hydrawise_active_count=active,
    )


def test_live_three_minute_gap_does_not_gain_microseconds_from_cycle_clock():
    state = clear_again(before_gap())
    assert state.hydrawise_drying_since_utc == DRYING
    assert state.hydrawise_clear_origin == "IRRIGATION_END"
    # Data confirmation restarts; only the old physical drying end is retained.
    assert state.hydrawise_clear_since_utc == "2026-09-10T13:55:00.000709+00:00"


def test_longer_gap_keeps_full_precautionary_hold():
    state = clear_again(before_gap(), seconds=181)
    assert state.hydrawise_clear_origin == "POSSIBLE_IRRIGATION_DURING_GAP"
    assert state.hydrawise_drying_since_utc == "2026-09-10T13:55:01.000709+00:00"


def test_scheduled_water_inside_short_gap_still_requires_full_hold():
    state = clear_again(replace(before_gap(), next_irrigation_start_utc=(LAST+timedelta(minutes=1)).isoformat()))
    assert state.hydrawise_clear_origin == "POSSIBLE_IRRIGATION_DURING_GAP"
    assert state.hydrawise_drying_since_utc != DRYING


def test_observed_water_is_never_ignored_at_threshold():
    state = clear_again(before_gap(), active=1)
    assert state.hydrawise_clear_origin == "IRRIGATION_ACTIVE"
    assert state.hydrawise_clear_since_utc is None
    state = clear_again(state, seconds=240)
    assert state.hydrawise_clear_origin == "IRRIGATION_END"
    assert state.hydrawise_drying_since_utc == "2026-09-10T13:56:00.000709+00:00"
