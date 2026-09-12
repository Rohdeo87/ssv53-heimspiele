from copy import deepcopy
from datetime import datetime, timedelta, timezone
import json
import re
from unittest.mock import patch

import pytest

from daily_safety_report import charging_evidence, dashboard_statistics, estimate_charging_end, parse_cycle_rows
from mower.state import AutomationState
from mower.state_store import InMemoryStateStore
from platzwart_console import _STATISTICS_CACHE, live_status
from tests.test_full_failsafe import ENV, result


NOW = datetime(2026, 9, 9, 10, tzinfo=timezone.utc)


def observation(at, battery, activity="CHARGING", **changes):
    return {
        "timestamp": at.isoformat(), "activity": activity, "mower_state": "IN_OPERATION",
        "error_code": 0, "battery_percent": battery, "mower_connected": True,
        "mower_status_timestamp_ms": int(at.timestamp() * 1000), "mower_id": "mower-1",
        **changes,
    }


def history(days=(1, 2, 3)):
    rows = []
    for day in days:
        start = NOW - timedelta(days=day, hours=2)
        rows.append(observation(start - timedelta(minutes=1), 40, "GOING_HOME"))
        rows.extend(observation(start + timedelta(minutes=minute), 40 + minute * 2) for minute in range(30))
        rows.append(observation(start + timedelta(minutes=30), 100, "LEAVING"))
    return rows


def current(start=NOW - timedelta(minutes=10), until=NOW):
    rows = [observation(start - timedelta(minutes=1), 40, "GOING_HOME")]
    rows.extend(observation(start + timedelta(minutes=minute), 40 + minute * 2)
                for minute in range(int((until - start).total_seconds() / 60) + 1))
    return rows


def live_mower(now=NOW, battery=60, **changes):
    return {"mower_id": "mower-1", "activity": "CHARGING", "state": "IN_OPERATION",
            "error_code": 0, "battery_percent": battery, "connected": True,
            "status_timestamp_ms": int(now.timestamp() * 1000), **changes}


def evidence(rows=None, at=NOW):
    return charging_evidence(parse_cycle_rows(rows if rows is not None else history() + current()), at)


def test_estimate_uses_completed_matching_charges_and_never_a_fixed_percent_rate():
    estimate = estimate_charging_end(evidence(), live_mower(), NOW)
    assert estimate is not None
    assert estimate["at"] == (NOW + timedelta(minutes=20)).isoformat()
    assert estimate["estimated"] is True
    assert estimate["sampleCount"] == 3
    assert estimate["daysCovered"] == 3
    assert estimate["currentBatteryPercent"] == 60
    # Slow only the measured charge curves; no configured battery-rate knob.
    rows = history()
    for row in rows:
        stamp = datetime.fromisoformat(row["timestamp"])
        day_start = stamp.replace(hour=8, minute=0, second=0, microsecond=0)
        if stamp >= day_start:
            # Missing telemetry minutes make this unfit, not a slower model.
            row["timestamp"] = (day_start + (stamp - day_start) * 2).isoformat()
    assert estimate_charging_end(evidence(rows + current()), live_mower(), NOW) is None


@pytest.mark.parametrize("days", [(1,), (1, 2), (1, 1, 1)])
def test_not_enough_independent_completed_sections_is_unknown(days):
    assert estimate_charging_end(evidence(history(days) + current()), live_mower(), NOW) is None


@pytest.mark.parametrize("bad_battery", [None, "", -1, 101, True, 50.5, "NaN", "inf", "broken", float("inf"), float("nan")])
def test_invalid_live_battery_is_unknown(bad_battery):
    assert estimate_charging_end(evidence(), live_mower(battery=bad_battery), NOW) is None


@pytest.mark.parametrize("changes", [
    {"connected": False}, {"connected": None}, {"error_code": 93}, {"error_code": None},
    {"state": "ERROR"}, {"activity": "PARKED_IN_CS"}, {"mower_id": "another-mower"},
    {"status_timestamp_ms": None}, {"status_timestamp_ms": int((NOW - timedelta(seconds=181)).timestamp() * 1000)},
])
def test_failed_offline_stale_or_other_device_has_no_forecast(changes):
    assert estimate_charging_end(evidence(), live_mower(**changes), NOW) is None


@pytest.mark.parametrize("change", [
    {"battery_percent": None}, {"battery_percent": 101}, {"battery_percent": 70.5},
    {"charging_battery_raw": "70.5"}, {"battery_percent": True}, {"error_code": None},
    {"battery_percent": float("inf")}, {"battery_percent": float("nan")},
    {"error_code": 93}, {"mower_connected": False}, {"mower_state": "ERROR"},
    {"mower_status_timestamp_ms": None},
])
def test_one_defective_point_discards_its_entire_historical_section(change):
    rows = history()
    rows[10].update(change)
    model = evidence(rows + current())
    assert model["validCompletedSections"] == 2
    assert estimate_charging_end(model, live_mower(), NOW) is None


def test_historical_gap_or_aborted_charge_never_becomes_a_complete_sample():
    rows = history()
    del rows[10]
    assert evidence(rows + current())["validCompletedSections"] == 2
    rows = history()
    rows[31]["battery_percent"] = 98
    assert evidence(rows + current())["validCompletedSections"] == 2


def test_current_gap_insufficient_progress_or_battery_regression_is_unknown():
    short = current(start=NOW - timedelta(minutes=2))
    assert estimate_charging_end(evidence(history() + short), live_mower(battery=44), NOW) is None
    rows = current()
    del rows[5]
    assert estimate_charging_end(evidence(history() + rows), live_mower(), NOW) is None
    assert estimate_charging_end(evidence(), live_mower(battery=50), NOW) is None


def test_stagnation_does_not_generate_a_new_later_estimate():
    rows = current()
    for point in rows[-6:]:
        point["battery_percent"] = 50
    assert estimate_charging_end(evidence(history() + rows), live_mower(battery=50), NOW) is None


def test_observed_nonlinear_tail_is_used_without_linear_extrapolation():
    pattern = [40, 42, 45, 47, 50, 52, 55, 57, 60, 60, 63, 65, 68, 70, 73,
               75, 78, 80, 82, 84, 86, 88, 90, 91, 92, 93, 94, 95, 96, 97, 98, 98, 99, 99, 100]
    rows = []
    for day in (1, 2, 3):
        start = NOW - timedelta(days=day, hours=2)
        rows.append(observation(start - timedelta(minutes=1), 40, "GOING_HOME"))
        rows.extend(observation(start + timedelta(minutes=minute), level,
                                "LEAVING" if level == 100 else "CHARGING")
                    for minute, level in enumerate(pattern))
    start = NOW - timedelta(minutes=10)
    rows.append(observation(start - timedelta(minutes=1), 40, "GOING_HOME"))
    rows.extend(observation(start + timedelta(minutes=minute), level) for minute, level in enumerate(pattern[:11]))
    estimate = estimate_charging_end(evidence(rows), live_mower(battery=63), NOW)
    # Actual observed tail is 24 minutes, rounded up to five-minute precision;
    # 37 remaining battery points must not turn into 37 invented minutes.
    assert estimate["at"] == (NOW + timedelta(minutes=25)).isoformat()


def test_passed_prediction_stays_unknown_after_cache_refresh():
    start = NOW - timedelta(minutes=10)
    first = estimate_charging_end(evidence(), live_mower(), NOW)
    deadline = datetime.fromisoformat(first["at"])
    for extra in (0, 2, 4):
        later = deadline + timedelta(minutes=extra)
        rows = current(start=start, until=later)
        # There is real reported progress, but much slower than the reference;
        # reaching a later battery percentage cannot restart the forecast clock.
        for index, point in enumerate(rows[1:]):
            point["battery_percent"] = min(99, 40 + index)
        model = evidence(history() + rows, later)
        assert estimate_charging_end(model, live_mower(later, rows[-1]["battery_percent"]), later) is None


def test_later_battery_coverage_never_replaces_an_uncovered_initial_anchor():
    rows = history()
    # Three real, completed high-battery sections do not cover this charge's
    # initial 40%. Reaching their covered range later must not create a clock.
    for point in rows:
        point["battery_percent"] = 60 + int((point["battery_percent"] - 40) * 2 / 3)
    start = NOW - timedelta(minutes=20)
    model = evidence(rows + current(start=start))
    assert model["validCompletedSections"] == 3
    assert model["ongoing"]["samples"][0]["battery"] == 40
    assert estimate_charging_end(model, live_mower(battery=80), NOW) is None


def test_midnight_cohort_expiry_cannot_revive_prediction_after_process_restart():
    old_now = datetime(2026, 9, 9, 21, 40, tzinfo=timezone.utc)  # 23:40 Berlin
    charge_start = old_now - timedelta(minutes=10)

    def section(at, initial, duration):
        points = [observation(at - timedelta(minutes=1), initial, "GOING_HOME")]
        points.extend(observation(at + timedelta(minutes=minute), initial + int((100 - initial) * minute / duration))
                      for minute in range(duration))
        return points + [observation(at + timedelta(minutes=duration), 100, "LEAVING")]

    rows = (
        section(datetime(2026, 9, 3, 10, tzinfo=timezone.utc), 40, 30)
        + section(datetime(2026, 9, 7, 10, tzinfo=timezone.utc), 40, 30)
        + section(datetime(2026, 9, 8, 10, tzinfo=timezone.utc), 40, 30)
        + section(datetime(2026, 9, 8, 15, tzinfo=timezone.utc), 70, 24)
    )
    rows.append(observation(charge_start - timedelta(minutes=1), 40, "GOING_HOME"))
    for minute in range(36):
        battery = 40 + 2 * minute if minute <= 15 else 70 + int((minute - 15) * 1.25)
        rows.append(observation(charge_start + timedelta(minutes=minute), battery))

    class Queries:
        def execute(self, query, *, timespan):
            # Honor the REAL statistics query bounds: at local midnight its
            # seven-calendar-day window excludes the oldest low-battery run.
            start_text, end_text = re.findall(r"datetime\(([^)]+)\)", query)[:2]
            start, end = (datetime.fromisoformat(value.replace("Z", "+00:00")) for value in (start_text, end_text))
            return [point for point in rows if start <= datetime.fromisoformat(point["timestamp"]) <= end]

    old_model = dashboard_statistics(old_now, {}, query_client=Queries())["_chargingEvidence"]
    first = estimate_charging_end(old_model, live_mower(old_now, 60), old_now)
    assert first["at"] == "2026-09-09T22:00:00+00:00"
    assert first["anchorAt"] == charge_start.isoformat()
    assert old_model["validCompletedSections"] == 4

    for minute, battery in ((19, 87), (21, 90), (22, 91)):
        later = old_now + timedelta(minutes=minute)
        # A fresh query object plus reconstructed JSON carries no process-local
        # forecast. This also covers restart after the first estimate expired.
        rebuilt = json.loads(json.dumps(dashboard_statistics(later, {}, query_client=Queries())["_chargingEvidence"]))
        assert rebuilt["validCompletedSections"] == (4 if minute < 20 else 3)
        assert estimate_charging_end(rebuilt, live_mower(later, battery), later) is None


def test_stale_or_expired_model_is_unknown():
    model = evidence()
    assert estimate_charging_end(model, live_mower(NOW + timedelta(minutes=6), 72), NOW + timedelta(minutes=6)) is None
    old = evidence(history(days=(8, 9, 10)) + current())
    assert old["validCompletedSections"] == 0


@pytest.mark.parametrize("include_details", [True, False])
def test_actual_live_status_uses_live_battery_with_one_cached_query_and_preserves_gates(include_details):
    _STATISTICS_CACHE.clear()
    rows = history() + current()
    queries = []

    class Queries:
        def execute(self, query, *, timespan):
            queries.append(query)
            return rows

    def stats(now, environment):
        return dashboard_statistics(now, environment, query_client=Queries())

    store = InMemoryStateStore(AutomationState(last_cycle_started_utc=NOW.isoformat()))
    cycle = deepcopy(result(activity="CHARGING"))
    cycle.details["mower"].update(live_mower())
    before = deepcopy(cycle.details)
    try:
        with (
            patch("platzwart_console.dashboard_statistics", side_effect=stats),
            patch("platzwart_console.run_read_only_cycle", return_value=cycle),
            patch("platzwart_console.AzureTableStateStore.from_environment", return_value=store),
            patch("platzwart_console._clubhouse_events", return_value={}),
            patch("platzwart_console._dashboard_irrigation_statistics", return_value={}),
        ):
            first = live_status(ENV, NOW)
            later = NOW + timedelta(minutes=4)
            cycle.details["mower"].update(live_mower(later, 68))
            second = live_status(ENV, later, include_details=include_details)
            cycle.details["mower"].update(live_mower(later, 50))
            regressed = live_status(ENV, later, include_details=include_details)
        assert len(queries) == 1
        assert first["coordination"]["chargingEndEstimate"]["currentBatteryPercent"] == 60
        assert second["coordination"]["chargingEndEstimate"]["currentBatteryPercent"] == 68
        assert second["coordination"]["chargingEndEstimate"]["at"] == first["coordination"]["chargingEndEstimate"]["at"]
        assert regressed["coordination"]["chargingEndEstimate"] is None
        assert "_chargingEvidence" not in first["statistics"]
        assert first["coordination"]["explanationOnly"] is True
        assert first["coordination"]["chargingDisplayEstimate"] is None
        assert regressed["coordination"]["chargingDisplayEstimate"] is None
        assert cycle.details["hydrawise"] == before["hydrawise"]
        assert cycle.details["current_plan"] == before["current_plan"]
        assert store.load().revision == 0
    finally:
        _STATISTICS_CACHE.clear()
