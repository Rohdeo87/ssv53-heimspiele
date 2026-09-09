from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from mower.adaptive_planner import build_adaptive_plan
from mower.planner import Block, merge_blocks
from mower.weather import WeatherPoint, WeatherSnapshot


NOW = datetime(2026, 8, 28, 20, 0, tzinfo=timezone.utc)
TZ = ZoneInfo("Europe/Berlin")
RUN_SECONDS = [1200, 1200, 1200, 1200, 1200, 1800, 1800]


def zones() -> list[dict]:
    return [
        {"zone": index, "relay_id": index, "run_seconds": run_seconds}
        for index, run_seconds in enumerate(RUN_SECONDS, start=1)
    ]


def weather(
    *,
    rain_mm: float,
    probability: float,
    sunrise_utc: tuple[str, ...] = (),
) -> WeatherSnapshot:
    return WeatherSnapshot(
        schema_version=1,
        provider="OPEN_METEO",
        fetched_at_utc=NOW.isoformat(),
        latitude=50.0,
        longitude=8.0,
        source_cost_class="FREE_NONCOMMERCIAL",
        points=tuple(
            WeatherPoint(
                timestamp_utc=(NOW + timedelta(hours=offset)).isoformat(),
                temperature_c=16.0,
                relative_humidity_percent=80.0,
                dew_point_c=12.0,
                precipitation_probability_percent=probability,
                precipitation_mm=rain_mm,
                rain_mm=rain_mm,
                cloud_cover_percent=70.0,
                wind_speed_kmh=5.0,
                shortwave_radiation_wm2=0.0,
                soil_moisture_m3m3=0.25,
            )
            for offset in range(48)
        ),
        sunrise_utc=sunrise_utc,
    )


ENV = {
    "ADAPTIVE_PLANNING_ENABLED": "true",
    "ADAPTIVE_EXECUTION_ENABLED": "false",
    "PLANNING_HORIZON_HOURS": "48",
    "IRRIGATION_CANDIDATE_STEP_MINUTES": "5",
    "MOWER_PARK_LEAD_MINUTES": "4",
    "POST_IRRIGATION_DRYING_MINUTES": "150",
    "IRRIGATION_PREFERRED_START_FROM": "01:00",
    "IRRIGATION_PREFERRED_START_UNTIL": "07:30",
    "IRRIGATION_TARGET_START_LOCAL": "04:30",
    "RAIN_REDUCE_MIN_MM": "3",
    "RAIN_SKIP_MIN_MM": "8",
    "RAIN_SKIP_MIN_PROBABILITY": "80",
}


class AdaptivePlannerTests(unittest.TestCase):
    def _plan(self, blocks):
        return build_adaptive_plan(
            now_utc=NOW, timezone_name="Europe/Berlin", blocks=blocks,
            zones=zones(), weather_snapshot=None, weather_fresh=False, environment=ENV,
        )

    def test_generator_preserves_full_horizon_occupancy(self) -> None:
        block = Block(NOW, NOW + timedelta(hours=48), "training", "Belegt")
        self.assertEqual(self._plan(iter([block])).status, "NO_SAFE_WINDOW")

    def test_generator_preserves_sperre_after_fail_closed_quality_check(self) -> None:
        block = Block(NOW, NOW + timedelta(hours=48), "special", "Unbekannt", {"fail_closed": True})
        plan = self._plan(iter([block]))
        self.assertTrue(plan.input_quality["occupancy_fail_closed"])
        self.assertEqual(plan.status, "NO_SAFE_WINDOW")

    def test_merged_irrigation_keeps_exact_occupancy_without_old_water_margin(self) -> None:
        # A 48h old-water interval is deliberately extreme to make the
        # distinction visible. The actual one-hour sports block never moves.
        water = Block(NOW, NOW + timedelta(hours=48), "irrigation", "Altes Wasserfenster")
        training = Block(NOW + timedelta(hours=15), NOW + timedelta(hours=16), "training", "Training")
        plan = self._plan(merge_blocks([water, training]))
        exact = self._plan([training])
        self.assertEqual(plan.selected, exact.selected)
        self.assertEqual(plan.rejected_conflicts, exact.rejected_conflicts)

    def test_invalid_or_incomplete_merged_provenance_retains_full_sperre(self) -> None:
        for details in ({}, {"items": []}, {"items": [{"start": "bad"}]},
                        {"fail_closed": True, "items": []}):
            with self.subTest(details=details):
                block = Block(NOW, NOW + timedelta(hours=48), "irrigation+training", "Belegt", details)
                self.assertEqual(self._plan([block]).status, "NO_SAFE_WINDOW")

    def test_provenance_with_gap_cannot_remove_merged_occupancy(self) -> None:
        water = Block(NOW, NOW + timedelta(hours=48), "irrigation", "Wasser")
        training = Block(NOW + timedelta(hours=15), NOW + timedelta(hours=16), "training", "Training")
        merged = merge_blocks([water, training])[0]
        merged.details["items"][0]["end"] = (NOW + timedelta(hours=1)).isoformat()
        self.assertEqual(self._plan([merged]).status, "NO_SAFE_WINDOW")

    def test_merged_fail_closed_provenance_retains_full_sperre_and_quality_flag(self) -> None:
        water = Block(NOW, NOW + timedelta(hours=48), "irrigation", "Wasser")
        unknown = Block(NOW + timedelta(hours=15), NOW + timedelta(hours=16), "special", "Unbekannt", {"fail_closed": True})
        plan = self._plan(merge_blocks([water, unknown]))
        self.assertEqual(plan.status, "NO_SAFE_WINDOW")
        self.assertTrue(plan.input_quality["occupancy_fail_closed"])

    def test_adaptive_execution_cannot_be_enabled_in_shadow_package(self) -> None:
        with self.assertRaises(ValueError):
            build_adaptive_plan(
                now_utc=NOW,
                timezone_name="Europe/Berlin",
                blocks=[],
                zones=zones(),
                weather_snapshot=None,
                weather_fresh=False,
                environment={**ENV, "ADAPTIVE_EXECUTION_ENABLED": "true"},
            )

    def test_selected_candidate_has_four_minute_park_lead_and_150_minute_drying(self) -> None:
        plan = build_adaptive_plan(
            now_utc=NOW,
            timezone_name="Europe/Berlin",
            blocks=[],
            zones=zones(),
            weather_snapshot=None,
            weather_fresh=False,
            environment=ENV,
        )
        self.assertEqual(plan.status, "SHADOW_PLAN_READY")
        self.assertTrue(plan.shadow_only)
        selected = plan.selected
        self.assertIsNotNone(selected)
        start = datetime.fromisoformat(selected.irrigation_start_utc)
        park = datetime.fromisoformat(selected.park_at_utc)
        end = datetime.fromisoformat(selected.irrigation_end_utc)
        release = datetime.fromisoformat(selected.earliest_mow_resume_utc)
        self.assertEqual(start - park, timedelta(minutes=4))
        self.assertEqual(release - end, timedelta(minutes=150))
        self.assertEqual(start.astimezone(TZ).strftime("%H:%M"), "04:30")

    def test_special_occupancy_is_a_hard_candidate_conflict(self) -> None:
        blocked = Block(
            start=datetime(2026, 8, 29, 1, 0, tzinfo=timezone.utc),
            end=datetime(2026, 8, 29, 10, 0, tzinfo=timezone.utc),
            source="special",
            title="Manuelle Rasenbelegung",
        )
        plan = build_adaptive_plan(
            now_utc=NOW,
            timezone_name="Europe/Berlin",
            blocks=[blocked],
            zones=zones(),
            weather_snapshot=None,
            weather_fresh=False,
            environment=ENV,
        )
        self.assertGreater(plan.rejected_conflicts, 0)
        self.assertIsNotNone(plan.selected)
        selected_start = datetime.fromisoformat(plan.selected.irrigation_start_utc)
        self.assertGreaterEqual(selected_start, blocked.end.astimezone(timezone.utc))

    def test_stale_weather_can_never_reduce_baseline(self) -> None:
        plan = build_adaptive_plan(
            now_utc=NOW,
            timezone_name="Europe/Berlin",
            blocks=[],
            zones=zones(),
            weather_snapshot=weather(rain_mm=2.0, probability=100.0),
            weather_fresh=False,
            environment=ENV,
        )
        self.assertEqual(plan.water_recommendation, "KEEP_BASELINE")

    def test_fresh_sunrise_moves_target_while_remaining_in_shadow_mode(self) -> None:
        # Sonnenaufgang 06:30 Uhr lokal; Ende soll standardmäßig ungefähr
        # 60 Minuten später liegen. Bei 160 Minuten Laufzeit ergibt das 04:50.
        plan = build_adaptive_plan(
            now_utc=NOW,
            timezone_name="Europe/Berlin",
            blocks=[],
            zones=zones(),
            weather_snapshot=weather(
                rain_mm=0.0,
                probability=0.0,
                sunrise_utc=("2026-08-29T04:30:00+00:00",),
            ),
            weather_fresh=True,
            environment=ENV,
        )
        self.assertIsNotNone(plan.selected)
        start = datetime.fromisoformat(plan.selected.irrigation_start_utc)
        self.assertEqual(start.astimezone(TZ).strftime("%H:%M"), "04:50")
        self.assertTrue(plan.shadow_only)

    def test_strong_rain_is_only_a_shadow_skip_recommendation(self) -> None:
        plan = build_adaptive_plan(
            now_utc=NOW,
            timezone_name="Europe/Berlin",
            blocks=[],
            zones=zones(),
            weather_snapshot=weather(rain_mm=1.0, probability=100.0),
            weather_fresh=True,
            environment=ENV,
        )
        self.assertEqual(plan.water_recommendation, "SKIP_RECOMMENDED")
        self.assertTrue(plan.shadow_only)
        self.assertFalse(plan.execution_enabled)
        self.assertIsNotNone(plan.selected)
        self.assertGreater(plan.selected.drying_extension_minutes, 0)

    def test_full_occupancy_horizon_has_no_safe_window(self) -> None:
        blocked = Block(
            start=NOW,
            end=NOW + timedelta(hours=48),
            source="training+match+special",
            title="Rasen gesperrt",
        )
        plan = build_adaptive_plan(
            now_utc=NOW,
            timezone_name="Europe/Berlin",
            blocks=[blocked],
            zones=zones(),
            weather_snapshot=None,
            weather_fresh=False,
            environment=ENV,
        )
        self.assertEqual(plan.status, "NO_SAFE_WINDOW")
        self.assertIsNone(plan.selected)


if __name__ == "__main__":
    unittest.main()
