import json
from datetime import datetime, timedelta, timezone

import pytest

from daily_safety_report import (
    DailyReportError,
    dashboard_irrigation_statistics,
    dashboard_statistics,
    parse_cycle_rows,
    process_daily_report,
    report_recipient,
    should_attempt_delivery,
    summarize_adaptive_shadow,
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
    assert value["attention"] is None


def test_irrigation_statistics_separates_partial_run_and_related_gap():
    rows = [
        {"timestamp": "2026-08-21T02:40:01Z", "decision_code": "IRRIGATION_ZONE_RUNNING", "active_relay_ids": "[101]", "irrigation_plan_id": "plan-gap", "irrigation_phase": "RUNNING", "irrigation_completed_utc": "", "completed_relay_ids": "[100]"},
        {"timestamp": "2026-08-21T02:41:01Z", "decision_code": "IRRIGATION_ZONE_RUNNING", "active_relay_ids": "[101]", "irrigation_plan_id": "plan-gap", "irrigation_phase": "RUNNING", "irrigation_completed_utc": "", "completed_relay_ids": "[100]"},
        {"timestamp": "2026-08-21T07:40:01Z", "decision_code": "IRRIGATION_FAILED_HOLD", "active_relay_ids": "[]", "irrigation_plan_id": "plan-gap", "irrigation_phase": "FAILED", "irrigation_completed_utc": "", "completed_relay_ids": "[100,101]"},
    ]

    value = summarize_irrigation_statistics(rows)

    assert value["completedRuns7d"] == 0
    assert value["attention"]["summary"] == "2 von 7 Zonen bestätigt."
    assert value["attention"]["affectedRuns"][0]["confirmedRelayIds"] == [100, 101]
    assert value["attention"]["dataGaps"][0]["missingMinutes"] == 298


def test_irrigation_statistics_recognizes_complete_external_seven_zone_run():
    relays = [101, 102, 103, 104, 105, 106, 107]
    rows = []
    minute = 0
    for relay_id in relays:
        for _sample in range(2):
            rows.append(
                {
                    "timestamp": (
                        datetime(2026, 8, 27, 2, 15, tzinfo=timezone.utc)
                        + timedelta(minutes=minute)
                    ).isoformat(),
                    "decision_code": "IRRIGATION_FAILED_HOLD",
                    "active_relay_ids": json.dumps([relay_id]),
                    "irrigation_plan_id": "stale-failed-plan",
                    "irrigation_phase": "FAILED",
                    "irrigation_completed_utc": "",
                    "completed_relay_ids": "[]",
                }
            )
            minute += 1
    for _sample in range(7):
        rows.append(
            {
                "timestamp": (
                    datetime(2026, 8, 27, 2, 15, tzinfo=timezone.utc)
                    + timedelta(minutes=minute)
                ).isoformat(),
                "decision_code": "IRRIGATION_FAILED_HOLD",
                "active_relay_ids": "[]",
                "irrigation_plan_id": "stale-failed-plan",
                "irrigation_phase": "FAILED",
                "irrigation_completed_utc": "",
                "completed_relay_ids": "[]",
            }
        )
        minute += 1

    value = summarize_irrigation_statistics(
        rows,
        expected_relay_ids=frozenset(relays),
    )

    assert value["completedRuns7d"] == 1
    assert value["lastCompletedDurationMinutes"] == 14
    assert value["lastCompletedAt"] == "2026-08-27T02:29:00+00:00"
    assert value["attention"] is None


def test_irrigation_statistics_never_promotes_incomplete_external_run():
    relays = [101, 102, 103, 104, 105, 106, 107]
    rows = [
        {
            "timestamp": (
                datetime(2026, 8, 27, 2, 15, tzinfo=timezone.utc)
                + timedelta(minutes=index)
            ).isoformat(),
            "decision_code": "IRRIGATION_FAILED_HOLD",
            "active_relay_ids": json.dumps([relay_id]),
            "irrigation_plan_id": "partial-plan",
            "irrigation_phase": "FAILED",
            "irrigation_completed_utc": "",
            "completed_relay_ids": "[]",
        }
        for index, relay_id in enumerate(relays[:-1])
    ]
    rows.extend(
        {
            "timestamp": (
                datetime(2026, 8, 27, 2, 21, tzinfo=timezone.utc)
                + timedelta(minutes=index)
            ).isoformat(),
            "decision_code": "IRRIGATION_FAILED_HOLD",
            "active_relay_ids": "[]",
            "irrigation_plan_id": "partial-plan",
            "irrigation_phase": "FAILED",
            "irrigation_completed_utc": "",
            "completed_relay_ids": "[]",
        }
        for index in range(7)
    )

    value = summarize_irrigation_statistics(
        rows,
        expected_relay_ids=frozenset(relays),
    )

    assert value["completedRuns7d"] == 0
    assert value["attention"]["summary"] == "6 von 7 Zonen bestätigt."


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


def test_dashboard_irrigation_statistics_survives_insights_outage_with_journal():
    class BrokenInsights:
        def execute(self, query, *, timespan):
            raise RuntimeError("insights unavailable")

    def journal(values, start, end):
        return [
            {"timestamp": "2026-08-20T04:02:01Z", "decision_code": "IRRIGATION_ALL_ZONES_CONFIRMED_COMPLETE", "active_relay_ids": "[]", "irrigation_plan_id": "plan-journal", "irrigation_phase": "COMPLETE_HOLD", "irrigation_completed_utc": "2026-08-20T04:02:00Z", "completed_relay_ids": "[1,2,3,4,5,6,7]"},
        ]

    value = dashboard_irrigation_statistics(
        datetime(2026, 8, 20, 5, 0, tzinfo=timezone.utc),
        {},
        query_client=BrokenInsights(),
        journal_reader=journal,
    )
    assert value["completedRuns7d"] == 1
    assert value["attention"] is None


def test_dashboard_irrigation_statistics_marks_unavailable_journal_separately():
    class Insights:
        def execute(self, query, *, timespan):
            return []

    def broken_journal(values, start, end):
        raise RuntimeError("table unavailable")

    value = dashboard_irrigation_statistics(
        datetime(2026, 8, 20, 5, 0, tzinfo=timezone.utc),
        {},
        query_client=Insights(),
        journal_reader=broken_journal,
    )
    assert value["available"] is True
    assert "sourceIssue" in value["attention"]


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
    assert summary.median_return_minutes_7d == 4
    assert summary.p95_return_minutes_7d == 5
    assert summary.return_measurements_7d == 2


def test_paused_mower_does_not_turn_delayed_error_code_into_active_report_error():
    item = row("2026-08-20T04:59:01Z", error=93)
    item["mower_state"] = "PAUSED"
    summary = summarize_report(
        parse_cycle_rows([item]),
        now_utc=datetime(2026, 8, 20, 5, 0, tzinfo=timezone.utc),
        exception_count_24h=0,
    )
    assert summary.current_error_code == 0
    assert not any("Mäherfehler aktiv" in warning for warning in summary.warnings)


def test_adaptive_shadow_summary_reports_freshness_and_real_plan_changes():
    rows = []
    for minute, start, fresh in [
        (0, "2026-08-21T02:30:00Z", True),
        (1, "2026-08-21T02:30:00Z", True),
        (2, "2026-08-21T03:00:00Z", True),
        (3, "2026-08-21T03:00:00Z", False),
    ]:
        item = row(f"2026-08-20T04:{minute:02d}:01Z")
        item.update(
            weather_enabled=True,
            weather_available=True,
            weather_fresh=fresh,
            weather_provider="OPEN_METEO",
            adaptive_enabled=True,
            adaptive_execution_enabled=False,
            adaptive_status="SHADOW_PLAN_READY",
            adaptive_recommendation="KEEP_BASELINE",
            adaptive_irrigation_start_utc=start,
            adaptive_irrigation_end_utc="2026-08-21T05:40:00Z",
            adaptive_earliest_mow_resume_utc="2026-08-21T08:10:00Z",
            adaptive_lost_dry_mowing_minutes=310,
            adaptive_drying_extension_minutes=0,
            adaptive_expected_rain_mm=0.4,
        )
        rows.append(item)
    summary = summarize_adaptive_shadow(
        parse_cycle_rows(rows),
        now_utc=datetime(2026, 8, 20, 5, 0, tzinfo=timezone.utc),
        archived_snapshots=[],
    )
    assert summary.enabled is True
    assert summary.execution_enabled is False
    assert summary.latest_status == "SHADOW_PLAN_READY"
    assert summary.weather_fresh_percent_24h == 75
    assert summary.plan_changes_24h == 1
    assert summary.irrigation_start_utc == datetime(
        2026, 8, 21, 3, 0, tzinfo=timezone.utc
    )
    assert summary.rain_validation_samples == 0


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
        value = row("2026-08-20T04:59:01Z")
        value.update(
            weather_enabled=True,
            weather_available=True,
            weather_fresh=True,
            weather_provider="OPEN_METEO",
            adaptive_enabled=True,
            adaptive_execution_enabled=False,
            adaptive_status="SHADOW_PLAN_READY",
            adaptive_recommendation="KEEP_BASELINE",
            adaptive_irrigation_start_utc="2026-08-21T02:30:00Z",
            adaptive_irrigation_end_utc="2026-08-21T05:10:00Z",
            adaptive_earliest_mow_resume_utc="2026-08-21T07:40:00Z",
            adaptive_lost_dry_mowing_minutes=310,
            adaptive_drying_extension_minutes=0,
            adaptive_expected_rain_mm=0.0,
        )
        return [value]


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
    plain = sent[0].get_body(preferencelist=("plain",)).get_content()
    assert "Bestätigte Abschlüsse (7 Tage)" in plain
    assert "Adaptive Planung – Schattenbetrieb" in plain
    assert "keine Gerätebefehle" in plain
    assert "Schattenplan bereit" in plain
    assert "Basisberegnung beibehalten" in plain
    assert "Prognosearchiv: vorübergehend nicht lesbar" in plain
    assert result["adaptive_execution_enabled"] is False

    duplicate = process_daily_report(
        datetime(2026, 8, 20, 6, 0, tzinfo=timezone.utc),
        values,
        query_client=Queries(),
        store=Store(claim=False),
        mail_sender=lambda settings, message: pytest.fail("duplicate mail"),
        forecast_reader=lambda _values, _start, _end: [],
    )
    assert duplicate["reason"] == "already_claimed"
