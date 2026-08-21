from datetime import datetime, timezone

import pytest

from daily_safety_report import (
    DailyReportError,
    dashboard_irrigation_statistics,
    dashboard_statistics,
    parse_cycle_rows,
    process_daily_report,
    report_recipient,
    should_attempt_delivery,
    summarize_report,
    summarize_irrigation_statistics,
)


def row(when: str, *, activity="MOWING", decision="", sent=False, error=0, progress=0, completed=0):
    return {
        "timestamp": when,
        "activity": activity,
        "mower_state": "IN_OPERATION" if not error else "ERROR",
        "error_code": error,
        "battery_percent": 72,
        "decision_code": decision,
        "command_sent": sent,
        "hydrawise_available": True,
        "hydrawise_fresh": True,
        "hydrawise_active_zones": 0,
        "next_irrigation_start_utc": "2026-08-21T02:00:00Z",
        "blocked_source": "",
        "parking_source": "",
        "work_area_type": "SYSTEMATIC",
        "work_area_progress": progress,
        "work_area_last_completed": completed,
    }


def test_delivery_window_tracks_berlin_summer_and_winter_time():
    assert should_attempt_delivery(datetime(2026, 8, 20, 5, 0, tzinfo=timezone.utc))
    assert should_attempt_delivery(datetime(2026, 12, 20, 6, 0, tzinfo=timezone.utc))
    assert not should_attempt_delivery(datetime(2026, 8, 20, 4, 59, tzinfo=timezone.utc))


def test_summary_counts_only_unique_epos_confirmed_completions():
    observations = parse_cycle_rows(
        [
            row("2026-08-19T05:00:01Z"),
            row("2026-08-19T05:00:40Z"),  # same telemetry minute
            row("2026-08-19T05:01:01Z", activity="LEAVING"),
            row(
                "2026-08-19T05:02:01Z",
                activity="PARKED_IN_CS",
                decision="CONTINUOUS_MOWING_TURNAROUND_SENT",
                sent=True,
                progress=100,
                completed=1787115600,
            ),
            row(
                "2026-08-19T05:03:01Z",
                activity="PARKED_IN_CS",
                decision="CONTINUOUS_MOWING_TURNAROUND_SENT",
                sent=False,
                progress=12,
                completed=1787115600,
            ),
        ]
    )
    summary = summarize_report(
        observations,
        now_utc=datetime(2026, 8, 20, 5, 0, tzinfo=timezone.utc),
        exception_count_24h=0,
    )
    assert summary.mowing_minutes_7d == 2
    assert summary.completed_area_cycles_7d == 1
    assert summary.current_work_area_progress == 12
    assert summary.last_completed_area_utc is not None
    assert summary.average_daily_mowing_minutes_7d == 0


def test_dashboard_statistics_reuses_confirmed_seven_day_metrics():
    class DashboardQueries:
        def execute(self, query, *, timespan):
            assert "work_area_last_completed" in query
            assert timespan == "P8D"
            return [
                row("2026-08-20T04:58:01Z", completed=1787198400, progress=100),
                row("2026-08-20T04:59:01Z", completed=1787198400, progress=8),
            ]

    value = dashboard_statistics(
        datetime(2026, 8, 20, 5, 0, tzinfo=timezone.utc),
        {},
        query_client=DashboardQueries(),
    )
    assert value["available"] is True
    assert value["completedAreaCycles7d"] == 1
    assert value["mowingMinutes7d"] == 2
    assert value["mowingMinutesToday"] == 2
    assert value["averageReturnMinutes7d"] is None
    assert value["lastCompletedAreaUtc"] is not None


def test_irrigation_statistics_use_actual_zones_and_confirmed_full_runs():
    rows = [
        {"timestamp": "2026-08-20T04:00:01Z", "decision_code": "IRRIGATION_ZONE_RUNNING", "active_relay_ids": "[101]", "irrigation_plan_id": "plan-a", "irrigation_completed_utc": "", "completed_relay_ids": "[]"},
        {"timestamp": "2026-08-20T04:00:40Z", "decision_code": "IRRIGATION_ZONE_RUNNING", "active_relay_ids": "[101]", "irrigation_plan_id": "plan-a", "irrigation_completed_utc": "", "completed_relay_ids": "[]"},
        {"timestamp": "2026-08-20T04:01:01Z", "decision_code": "IRRIGATION_ZONE_RUNNING", "active_relay_ids": "[102]", "irrigation_plan_id": "plan-a", "irrigation_completed_utc": "", "completed_relay_ids": "[101]"},
        {"timestamp": "2026-08-20T04:02:01Z", "decision_code": "IRRIGATION_ALL_ZONES_CONFIRMED_COMPLETE", "active_relay_ids": "[]", "irrigation_plan_id": "plan-a", "irrigation_completed_utc": "2026-08-20T04:02:00Z", "completed_relay_ids": "[101,102,103,104,105,106,107]"},
        {"timestamp": "2026-08-20T05:00:01Z", "decision_code": "IRRIGATION_PLAN_UPDATED", "active_relay_ids": "[]", "irrigation_plan_id": "plan-b", "irrigation_completed_utc": "", "completed_relay_ids": "[]", "operator_request_id": "manual-1", "operator_request_action": "START_IRRIGATION", "operator_request_status": "SUCCESS"},
        {"timestamp": "2026-08-20T05:01:01Z", "decision_code": "IRRIGATION_PLAN_CANCELLED_OR_DEFERRED", "active_relay_ids": "[]", "irrigation_plan_id": "plan-b", "irrigation_completed_utc": "", "completed_relay_ids": "[]", "operator_request_id": "manual-1", "operator_request_action": "START_IRRIGATION", "operator_request_status": "SUCCESS"},
    ]
    value = summarize_irrigation_statistics(rows)
    assert value["wateringMinutes7d"] == 2
    assert value["completedRuns7d"] == 1
    assert value["lastCompletedDurationMinutes"] == 2
    assert value["zoneMinutes7d"] == [
        {"relayId": 101, "minutes": 1},
        {"relayId": 102, "minutes": 1},
    ]
    assert value["planChanges7d"] == 3
    assert value["planChangeBreakdown"] == {
        "updated": 1,
        "cancelled": 1,
        "manualStarted": 1,
    }


def test_dashboard_irrigation_statistics_queries_eight_day_window():
    class DashboardQueries:
        def execute(self, query, *, timespan):
            assert "active_relay_ids" in query
            assert "irrigation_completed_utc" in query
            assert timespan == "P8D"
            return []

    value = dashboard_irrigation_statistics(
        datetime(2026, 8, 20, 5, 0, tzinfo=timezone.utc),
        {},
        query_client=DashboardQueries(),
    )
    assert value["available"] is True
    assert value["completedRuns7d"] == 0
    assert value["wateringMinutes7d"] == 0


def test_summary_infers_epos_completion_from_stable_99_to_zero_transition():
    observations = parse_cycle_rows(
        [
            row("2026-08-20T16:00:01Z", progress=98),
            row("2026-08-20T16:01:01Z", progress=99),
            row("2026-08-20T16:02:01Z", progress=99),
            row("2026-08-20T16:03:01Z", progress=99, decision="PARK_COMMAND_SENT", sent=True),
            row("2026-08-20T16:04:01Z", progress=0, activity="GOING_HOME"),
            row("2026-08-20T16:05:01Z", progress=0, activity="GOING_HOME"),
        ]
    )
    summary = summarize_report(
        observations,
        now_utc=datetime(2026, 8, 20, 17, 0, tzinfo=timezone.utc),
        exception_count_24h=0,
    )
    assert summary.completed_area_cycles_7d == 1
    assert summary.last_completed_area_utc == datetime(
        2026, 8, 20, 16, 4, 1, tzinfo=timezone.utc
    )
    assert summary.current_work_area_progress == 0


def test_summary_does_not_infer_completion_from_98_or_single_99_sample():
    observations = parse_cycle_rows(
        [
            row("2026-08-20T15:58:01Z", progress=98),
            row("2026-08-20T15:59:01Z", progress=0, activity="GOING_HOME"),
            row("2026-08-20T16:01:01Z", progress=99),
            row("2026-08-20T16:02:01Z", progress=0, activity="GOING_HOME"),
        ]
    )
    summary = summarize_report(
        observations,
        now_utc=datetime(2026, 8, 20, 17, 0, tzinfo=timezone.utc),
        exception_count_24h=0,
    )
    assert summary.completed_area_cycles_7d == 0
    assert summary.last_completed_area_utc is None


def test_inferred_and_device_confirmed_completion_are_not_counted_twice():
    completed = int(datetime(2026, 8, 20, 16, 4, tzinfo=timezone.utc).timestamp())
    observations = parse_cycle_rows(
        [
            row("2026-08-20T16:01:01Z", progress=99),
            row("2026-08-20T16:02:01Z", progress=99),
            row("2026-08-20T16:03:01Z", progress=0, activity="GOING_HOME", completed=completed),
            row("2026-08-20T16:04:01Z", progress=0, activity="GOING_HOME", completed=completed),
        ]
    )
    summary = summarize_report(
        observations,
        now_utc=datetime(2026, 8, 20, 17, 0, tzinfo=timezone.utc),
        exception_count_24h=0,
    )
    assert summary.completed_area_cycles_7d == 1


def test_summary_calculates_today_mowing_and_average_return_duration():
    observations = parse_cycle_rows(
        [
            row("2026-08-20T08:00:01Z", activity="MOWING"),
            row("2026-08-20T08:01:01Z", activity="MOWING"),
            row("2026-08-20T08:02:01Z", activity="GOING_HOME"),
            row("2026-08-20T08:05:01Z", activity="PARKED_IN_CS"),
            row("2026-08-20T10:00:01Z", activity="GOING_HOME"),
            row("2026-08-20T10:05:01Z", activity="CHARGING"),
        ]
    )
    summary = summarize_report(
        observations,
        now_utc=datetime(2026, 8, 20, 12, 0, tzinfo=timezone.utc),
        exception_count_24h=0,
    )
    assert summary.mowing_minutes_today == 2
    assert summary.average_return_minutes_7d == 4


def test_recipient_is_required_and_validated():
    assert report_recipient({"SSV53_DAILY_REPORT_RECIPIENT": "Thomas.Rohde@ssv53.de"}) == "thomas.rohde@ssv53.de"
    with pytest.raises(DailyReportError):
        report_recipient({"SSV53_DAILY_REPORT_RECIPIENT": "not-an-email"})


class Store:
    def __init__(self, claim=True):
        self.claim_result = claim
        self.marks = []

    def claim(self, report_date, now_utc):
        return self.claim_result

    def mark(self, report_date, status, now_utc):
        self.marks.append(status)


class Queries:
    def __init__(self):
        self.calls = 0

    def execute(self, query, *, timespan):
        self.calls += 1
        if query.startswith("exceptions"):
            return [{"exception_count": 0}]
        return [row("2026-08-20T04:59:01Z")]


def test_process_sends_once_and_marks_success(monkeypatch):
    values = {
        "SSV53_DAILY_REPORT_ENABLED": "true",
        "SSV53_DAILY_REPORT_RECIPIENT": "thomas.rohde@ssv53.de",
        "SSV53_ORDER_MAIL_SMTP_HOST": "smtp.example.test",
        "SSV53_ORDER_MAIL_SMTP_PORT": "587",
        "SSV53_ORDER_MAIL_SMTP_USERNAME": "info@ssv53.de",
        "SSV53_ORDER_MAIL_SMTP_PASSWORD": "secret",
        "SSV53_ORDER_MAIL_FROM_ADDRESS": "info@ssv53.de",
        "SSV53_ORDER_MAIL_FROM_NAME": "Schönwalder SV 1953 e.V.",
    }
    sent = []
    store = Store()
    result = process_daily_report(
        datetime(2026, 8, 20, 5, 0, tzinfo=timezone.utc),
        values,
        query_client=Queries(),
        store=store,
        mail_sender=lambda settings, message: sent.append(message),
    )
    assert result["sent"] is True
    assert store.marks == ["sent"]
    assert sent[0]["To"] == "thomas.rohde@ssv53.de"
    assert "Bestätigte Abschlüsse (7 Tage)" in sent[0].get_body(preferencelist=("plain",)).get_content()

    duplicate = process_daily_report(
        datetime(2026, 8, 20, 6, 0, tzinfo=timezone.utc),
        values,
        query_client=Queries(),
        store=Store(claim=False),
        mail_sender=lambda settings, message: pytest.fail("duplicate mail"),
    )
    assert duplicate["reason"] == "already_claimed"
