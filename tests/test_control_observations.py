import unittest
from datetime import datetime
from zoneinfo import ZoneInfo
from scripts.analyze_control_observations import summarize, utc


class ObservationTests(unittest.TestCase):
    def test_travel_and_charging_are_not_productive_and_gaps_are_unknown(self):
        rows = [{"timestamp": t, "activity": a} for t, a in (
            ("2026-09-09T00:00:00Z", "MOWING"),
            ("2026-09-09T00:01:00Z", "LEAVING"),
            ("2026-09-09T00:02:00Z", "CHARGING"),
            ("2026-09-09T00:07:00Z", "GOING_HOME"),
        )]
        result = summarize(rows, utc("2026-09-09T00:00:00Z"), utc("2026-09-09T00:08:00Z"))
        self.assertEqual(result["minutes_by_activity"]["PRODUCTIVE_MOWING_ESTIMATE"], 1)
        self.assertEqual(result["unobserved_minutes"], 4)
        self.assertEqual(sum(result["minutes_by_activity"].values()), 4)

    def test_error_and_irrigation_are_not_counted_twice_as_idle(self):
        row = {"timestamp": "2026-09-09T00:00:00Z", "activity": "MOWING", "error": 93,
               "hydraAvailable": True, "hydraFresh": True, "activeZones": 2, "code": "WATER"}
        result = summarize([row, row], utc(row["timestamp"]), utc("2026-09-09T00:01:00Z"))
        self.assertEqual(result["rows"], 1)
        self.assertEqual(result["minutes_by_activity"], {"FAULT": 1})
        self.assertEqual(result["idle_minutes_by_primary_reason"], {"DEVICE_ERROR": 1})
        self.assertEqual(result["irrigation_active_minutes_estimate"], 1)

    def test_dst_fold_is_two_different_physical_minutes(self):
        start, end = utc("2026-10-25T02:30:00+02:00"), utc("2026-10-25T02:30:00+01:00")
        result = summarize([], start, end)
        self.assertEqual(result["unobserved_minutes"], 60)

    def test_direct_berlin_fold_inputs_are_compared_as_utc_instants(self):
        local = datetime(2026, 10, 25, 2, 30, tzinfo=ZoneInfo("Europe/Berlin"))
        self.assertEqual(summarize([], local.replace(fold=0), local.replace(fold=1))["period_minutes"], 60)
        with self.assertRaises(ValueError):
            summarize([], local.replace(minute=10, fold=1), local.replace(minute=50, fold=0))
