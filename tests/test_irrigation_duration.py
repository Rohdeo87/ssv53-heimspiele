from datetime import datetime, timedelta, timezone

import pytest

from daily_safety_report import summarize_irrigation_statistics
from mower.irrigation_duration import runtime_segments, duration_summary
from mower.irrigation_journal import observation_entity, read_irrigation_observations
from mower.runtime import CycleResult


BASE = datetime(2026, 9, 13, 2, tzinfo=timezone.utc)


def run(relay=1, minutes=20, offset=0, completed=()):
    """Synthetic countdown with one-second transport delay and timer jitter."""
    rows = []
    begin = BASE + timedelta(minutes=offset, seconds=2)
    end = begin + timedelta(minutes=minutes)
    for i in range(minutes + 3):
        cycle = BASE + timedelta(minutes=offset + i)
        if i == minutes - 1:
            cycle -= timedelta(microseconds=1)
        observed = BASE + timedelta(minutes=offset + i, seconds=2)
        active = 0 < i < minutes
        rows.append({
            "timestamp": cycle.isoformat(), "hydrawise_observed_at_utc": observed.isoformat(),
            "hydrawise_available": True, "hydrawise_fresh": True,
            "irrigation_plan_id": "run-a", "irrigation_phase": "RUNNING",
            "decision_code": "IRRIGATION_ZONE_START_SENT" if i == 0 else "IRRIGATION_ZONE_RUNNING",
            "command_sent": i == 0, "active_relay_ids": [relay] if active else [],
            "completed_relay_ids": list(completed) + ([relay] if i > minutes else []),
            "irrigation_action": {"type": "StartZone", "relay_id": relay, "run_seconds": minutes * 60,
                "response": {"message_type": "info"}} if i == 0 else {},
            "zone_observations": [{"relay_id": relay, "running": True, "valid": True,
                "run_seconds": int((end - observed).total_seconds()),
                "scheduled_end_utc": end.isoformat()}] if active else [],
        })
    return rows


def summarize(rows):
    return duration_summary(runtime_segments(rows))


def test_seven_zone_run_is_160_minutes_without_gaps_or_minute_collision_loss():
    rows, offset = [], 0
    for relay, minutes in enumerate([20] * 5 + [30] * 2, 1):
        rows.extend(run(relay, minutes, offset, range(1, relay)))
        offset += minutes + 3
    rows[-1].update(irrigation_completed_utc=rows[-1]["timestamp"],
                    decision_code="IRRIGATION_ALL_ZONES_CONFIRMED_COMPLETE")
    stats = summarize_irrigation_statistics(rows)
    assert stats["wateringMinutes7d"] == 160
    assert stats["lastCompletedDurationMinutes"] == 160
    assert stats["wateringDurationEstimated"] is False
    assert stats["completedRuns7d"] == 1
    assert [z["minutes"] for z in stats["zoneMinutes7d"]] == [20] * 5 + [30] * 2


def test_journal_trace_duplicates_old_rows_and_reversed_order_do_not_change_duration():
    rows = run()
    old = [{k: v for k, v in row.items() if k not in
            {"zone_observations", "irrigation_action", "command_sent"}} for row in rows]
    for merged in [rows + old + rows, list(reversed(old + rows))]:
        assert summarize(merged) == (20, False)


@pytest.mark.parametrize("change", ["early_stop", "missing_middle", "missing_end",
                                     "failed_start", "no_start", "changed_countdown", "stale"])
def test_insufficient_evidence_never_uses_full_command_duration(change):
    rows = run()
    if change == "early_stop":
        rows = rows[:9] + [dict(rows[-1], timestamp=(BASE + timedelta(minutes=9)).isoformat(),
                              hydrawise_observed_at_utc=(BASE + timedelta(minutes=9, seconds=2)).isoformat())]
    elif change == "missing_middle":
        rows = rows[:6] + rows[14:]
    elif change == "missing_end":
        rows = rows[:10]
    elif change == "failed_start":
        rows[0]["irrigation_action"]["response"]["message_type"] = "error"
    elif change == "no_start":
        rows = rows[1:]
    elif change == "changed_countdown":
        rows[8]["zone_observations"][0]["scheduled_end_utc"] = (BASE + timedelta(minutes=25)).isoformat()
    elif change == "stale":
        for row in rows[6:14]:
            row["hydrawise_fresh"] = False
    minutes, estimated = summarize(rows)
    assert minutes < 20
    assert estimated is True


def test_command_and_completion_without_running_are_not_watering():
    rows = run()
    assert summarize([rows[0], rows[-1]]) == (0, False)


def test_two_runs_same_zone_count_each_once():
    assert summarize(run() + run(offset=30)) == (40, False)


def test_parallel_relays_are_union_time_but_each_has_own_duration():
    rows = run()
    for row in rows:
        if row["active_relay_ids"]:
            row["active_relay_ids"].append(2)
    assert summarize(rows)[0] == 20
    stats = summarize_irrigation_statistics(rows)
    assert stats["zoneMinutes7d"] == [{"relayId": 1, "minutes": 20}, {"relayId": 2, "minutes": 19}]
    assert stats["wateringDurationEstimated"] is True


def test_window_clips_at_boundary_without_inventing_pre_window_minutes():
    segments = runtime_segments(run(), period_start=BASE + timedelta(minutes=10, seconds=2),
                                period_end=BASE + timedelta(minutes=15, seconds=2))
    assert duration_summary(segments) == (5, False)


def test_observations_in_same_clock_minute_survive_and_same_cycle_is_idempotent():
    first = CycleResult(schema_version=2, executed_at_utc=BASE.isoformat(), source="test",
        control_mode="FULL_FAILSAFE", past_due=False, decision_code="IRRIGATION_ZONE_RUNNING",
        command_sent=False, message="", details={})
    from dataclasses import replace
    second = replace(first, executed_at_utc=(BASE + timedelta(seconds=59, microseconds=999999)).isoformat())
    assert observation_entity(first)["RowKey"] != observation_entity(second)["RowKey"]
    assert observation_entity(first) == observation_entity(first)


def test_rich_journal_roundtrip_keeps_runtime_evidence():
    rows = run()
    entities = []
    for row in rows:
        result = CycleResult(schema_version=2, executed_at_utc=row["timestamp"], source="test",
            control_mode="FULL_FAILSAFE", past_due=False, decision_code=row["decision_code"],
            command_sent=row["command_sent"], message="", details={
                "irrigation_action": row["irrigation_action"],
                "automation_state": {"irrigation_plan_id": row["irrigation_plan_id"],
                    "irrigation_completed_relay_ids": row["completed_relay_ids"]},
                "hydrawise": {"zone_observations": row["zone_observations"], "safety": {
                    "available": True, "fresh": True, "active_relay_ids": row["active_relay_ids"],
                    "observed_at_utc": row["hydrawise_observed_at_utc"]}}})
        entities.append(observation_entity(result))
    class Client:
        def query_entities(self, **kwargs):
            return entities
    restored = read_irrigation_observations({}, BASE, BASE + timedelta(hours=1), table_client=Client())
    assert summarize(restored) == (20, False)


def test_clear_timestamp_rounding_of_two_seconds_does_not_drop_entire_run():
    rows = run()
    for row in rows[20:]:
        row["hydrawise_observed_at_utc"] = row["timestamp"]
    assert summarize(rows) == (20, False)


def test_reused_plan_id_does_not_double_last_run_duration():
    first, second = run(), run(offset=30)
    for rows in (first, second):
        rows[-1]["irrigation_completed_utc"] = rows[-1]["timestamp"]
    stats = summarize_irrigation_statistics(first + second, expected_zone_count=1)
    assert stats["completedRuns7d"] == 2
    assert stats["wateringMinutes7d"] == 40
    assert stats["lastCompletedDurationMinutes"] == 20


def test_completion_without_runtime_evidence_is_unknown_not_zero():
    row = run()[-1]
    row["irrigation_completed_utc"] = row["timestamp"]
    stats = summarize_irrigation_statistics([row], expected_zone_count=1)
    assert stats["lastCompletedDurationMinutes"] is None


def test_dashboard_window_does_not_count_lookback_water():
    rows = run()
    rows[-1]["irrigation_completed_utc"] = rows[-1]["timestamp"]
    stats = summarize_irrigation_statistics(rows, expected_zone_count=1,
        period_start_utc=BASE + timedelta(minutes=10, seconds=2),
        period_end_utc=BASE + timedelta(hours=1))
    assert stats["wateringMinutes7d"] == 10
    assert stats["lastCompletedDurationMinutes"] == 20


def test_plan_change_between_zones_preserves_entire_last_run():
    first, second = run(), run(relay=2, offset=23, completed=[1])
    for row in second:
        row["irrigation_plan_id"] = "run-b"
    second[-1]["irrigation_completed_utc"] = second[-1]["timestamp"]
    stats = summarize_irrigation_statistics(first + second, expected_zone_count=2)
    assert stats["lastCompletedDurationMinutes"] == 40
    assert stats["wateringMinutes7d"] == 40


def test_lookback_does_not_count_old_plan_changes_or_external_completions():
    rows = run()
    rows[0]["decision_code"] = "IRRIGATION_PLAN_UPDATED"
    rows[-1]["irrigation_completed_utc"] = rows[-1]["timestamp"]
    stats = summarize_irrigation_statistics(rows, expected_zone_count=1,
        period_start_utc=BASE + timedelta(hours=1), period_end_utc=BASE + timedelta(hours=2))
    assert stats["planChanges7d"] == 0
    assert stats["completedRuns7d"] == 0
    assert stats["wateringMinutes7d"] == 0


def test_completed_operator_status_counts_manual_start():
    row = dict(run()[0], operator_request_id="manual-test", operator_request_action="START_IRRIGATION",
               operator_request_status="COMPLETED")
    assert summarize_irrigation_statistics([row])["planChangeBreakdown"]["manualStarted"] == 1


def test_missing_entire_zone_never_presents_remaining_duration_as_exact_full_run():
    rows = run()
    rows[-1].update(irrigation_completed_utc=rows[-1]["timestamp"], completed_relay_ids=[1, 2])
    stats = summarize_irrigation_statistics(rows, expected_zone_count=2)
    assert stats["lastCompletedDurationMinutes"] == 20
    assert stats["lastCompletedDurationEstimated"] is True
    assert stats["wateringDurationEstimated"] is True
