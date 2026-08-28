from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone

from mower.weather import (
    WeatherError,
    WeatherPoint,
    WeatherSettings,
    WeatherSnapshot,
)
from mower.weather_service import resolve_weather
from mower.weather_store import InMemoryWeatherStore


NOW = datetime(2026, 8, 28, 12, 0, tzinfo=timezone.utc)


def snapshot(*, fetched_at: datetime = NOW, rain_mm: float = 0.0) -> WeatherSnapshot:
    return WeatherSnapshot(
        schema_version=1,
        provider="OPEN_METEO",
        fetched_at_utc=fetched_at.isoformat(),
        latitude=50.0,
        longitude=8.0,
        source_cost_class="FREE_NONCOMMERCIAL",
        points=tuple(
            WeatherPoint(
                timestamp_utc=(NOW + timedelta(hours=offset)).isoformat(),
                temperature_c=18.0,
                relative_humidity_percent=70.0,
                dew_point_c=12.0,
                precipitation_probability_percent=80.0 if rain_mm else 0.0,
                precipitation_mm=rain_mm,
                rain_mm=rain_mm,
                cloud_cover_percent=50.0,
                wind_speed_kmh=8.0,
                shortwave_radiation_wm2=100.0,
                soil_moisture_m3m3=0.2,
            )
            for offset in range(24)
        ),
    )


ENV = {
    "WEATHER_ENABLED": "true",
    "WEATHER_SHADOW_ONLY": "true",
    "WEATHER_PROVIDER": "OPEN_METEO",
    "WEATHER_LATITUDE": "50.0",
    "WEATHER_LONGITUDE": "8.0",
    "WEATHER_DAILY_CALL_LIMIT": "24",
    "WEATHER_MONTHLY_CALL_LIMIT": "900",
    "WEATHER_MINIMUM_FETCH_INTERVAL_MINUTES": "60",
    "WEATHER_FORECAST_MAX_AGE_MINUTES": "120",
}


class FakeProvider:
    def __init__(self, value: WeatherSnapshot) -> None:
        self.value = value
        self.calls = 0

    def fetch(self, **_kwargs) -> WeatherSnapshot:
        self.calls += 1
        return self.value


class WeatherTests(unittest.TestCase):
    def test_disabled_weather_needs_no_coordinates_or_store(self) -> None:
        resolution = resolve_weather({}, now_utc=NOW)
        self.assertFalse(resolution.enabled)
        self.assertFalse(resolution.fetch_attempted)

    def test_only_free_provider_is_accepted(self) -> None:
        with self.assertRaises(WeatherError):
            WeatherSettings.from_mapping({**ENV, "WEATHER_PROVIDER": "AZURE_MAPS"})
        resolution = resolve_weather(
            {**ENV, "WEATHER_PROVIDER": "AZURE_MAPS"},
            now_utc=NOW,
        )
        self.assertFalse(resolution.available)
        self.assertEqual(resolution.source, "configuration-error")
        self.assertFalse(resolution.fetch_attempted)

    def test_monthly_limit_cannot_exceed_cost_guard(self) -> None:
        with self.assertRaises(WeatherError):
            WeatherSettings.from_mapping(
                {**ENV, "WEATHER_MONTHLY_CALL_LIMIT": "901"}
            )

    def test_live_fetch_is_reserved_once_then_served_from_cache(self) -> None:
        store = InMemoryWeatherStore()
        provider = FakeProvider(snapshot())
        first = resolve_weather(
            ENV,
            now_utc=NOW,
            store_factory=lambda _env: store,
            provider=provider,
        )
        second = resolve_weather(
            ENV,
            now_utc=NOW + timedelta(minutes=5),
            store_factory=lambda _env: store,
            provider=provider,
        )
        self.assertTrue(first.fresh)
        self.assertEqual(first.source, "live")
        self.assertEqual(second.source, "cache")
        self.assertEqual(provider.calls, 1)
        self.assertEqual(store.month_count, 1)

    def test_exhausted_budget_never_calls_provider(self) -> None:
        store = InMemoryWeatherStore(
            month_key=NOW.strftime("%Y-%m"),
            month_count=900,
            day_key=NOW.strftime("%Y-%m-%d"),
        )
        provider = FakeProvider(snapshot())
        resolution = resolve_weather(
            ENV,
            now_utc=NOW,
            store_factory=lambda _env: store,
            provider=provider,
        )
        self.assertFalse(resolution.available)
        self.assertEqual(resolution.budget_reason, "MONTHLY_FREE_BUDGET_EXHAUSTED")
        self.assertEqual(provider.calls, 0)

    def test_stale_cache_is_never_marked_fresh_when_budget_is_exhausted(self) -> None:
        store = InMemoryWeatherStore(
            latest=snapshot(fetched_at=NOW - timedelta(hours=5)),
            month_key=NOW.strftime("%Y-%m"),
            month_count=900,
            day_key=NOW.strftime("%Y-%m-%d"),
        )
        resolution = resolve_weather(
            ENV,
            now_utc=NOW,
            store_factory=lambda _env: store,
            provider=FakeProvider(snapshot()),
        )
        self.assertTrue(resolution.available)
        self.assertFalse(resolution.fresh)
        self.assertEqual(resolution.source, "stale-cache")

    def test_expected_rain_uses_amount_and_probability(self) -> None:
        value = snapshot(rain_mm=1.0)
        self.assertEqual(
            value.expected_rain_mm(NOW, NOW + timedelta(hours=3)),
            2.4,
        )


if __name__ == "__main__":
    unittest.main()
