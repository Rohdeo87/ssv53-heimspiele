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
from mower.weather_store import (
    FORECAST_ARCHIVE_DAYS,
    FORECAST_ARCHIVE_PARTITION,
    AzureTableWeatherStore,
    InMemoryWeatherStore,
    forecast_archive_entity,
    forecast_rain_validation,
    read_archived_weather_snapshots,
)


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

    def test_fresh_cache_is_refreshed_after_minimum_fetch_interval(self) -> None:
        later = NOW + timedelta(minutes=61)
        store = InMemoryWeatherStore(
            latest=snapshot(fetched_at=NOW),
            month_key=NOW.strftime("%Y-%m"),
            month_count=1,
            day_key=NOW.strftime("%Y-%m-%d"),
            day_count=1,
            last_reserved_utc=NOW,
        )
        provider = FakeProvider(snapshot(fetched_at=later))
        resolution = resolve_weather(
            ENV,
            now_utc=later,
            store_factory=lambda _env: store,
            provider=provider,
        )
        self.assertEqual(resolution.source, "live")
        self.assertTrue(resolution.fresh)
        self.assertEqual(provider.calls, 1)
        self.assertEqual(len(store.archive), 1)

    def test_failed_hourly_refresh_keeps_still_fresh_cache_usable(self) -> None:
        class BrokenProvider:
            def fetch(self, **_kwargs):
                raise RuntimeError("provider unavailable")

        later = NOW + timedelta(minutes=61)
        store = InMemoryWeatherStore(
            latest=snapshot(fetched_at=NOW),
            month_key=NOW.strftime("%Y-%m"),
            month_count=1,
            day_key=NOW.strftime("%Y-%m-%d"),
            day_count=1,
            last_reserved_utc=NOW,
        )
        resolution = resolve_weather(
            ENV,
            now_utc=later,
            store_factory=lambda _env: store,
            provider=BrokenProvider(),
        )
        self.assertTrue(resolution.available)
        self.assertTrue(resolution.fresh)
        self.assertEqual(resolution.source, "cache-refresh-error")
        self.assertIn("provider unavailable", resolution.error)

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

    def test_forecast_archive_is_a_bounded_three_week_ring(self) -> None:
        store = InMemoryWeatherStore()
        for day in range(FORECAST_ARCHIVE_DAYS + 5):
            store.save_latest(snapshot(fetched_at=NOW + timedelta(days=day)))
        self.assertEqual(len(store.archive), FORECAST_ARCHIVE_DAYS)
        self.assertEqual(store.latest.fetched_at, NOW + timedelta(days=FORECAST_ARCHIVE_DAYS + 4))

    def test_compressed_archive_roundtrips_and_rejects_expired_ring_slot(self) -> None:
        current = snapshot()
        expired = snapshot(fetched_at=NOW - timedelta(days=22))

        class Client:
            def query_entities(self, **kwargs):
                self.kwargs = kwargs
                return [forecast_archive_entity(expired), forecast_archive_entity(current)]

        values = read_archived_weather_snapshots(
            {},
            NOW - timedelta(days=7),
            NOW + timedelta(minutes=1),
            table_client=Client(),
        )
        self.assertEqual([item.fetched_at for item in values], [NOW])
        encoded = forecast_archive_entity(current)["SnapshotGzipBase64"]
        self.assertLess(len(encoded), 64_000)

    def test_live_snapshot_is_archived_before_latest_pointer_is_advanced(self) -> None:
        class Client:
            def __init__(self):
                self.calls = []

            def upsert_entity(self, **kwargs):
                self.calls.append(kwargs)

        client = Client()
        AzureTableWeatherStore(client).save_latest(snapshot())
        self.assertEqual(len(client.calls), 2)
        self.assertEqual(
            client.calls[0]["entity"]["PartitionKey"],
            FORECAST_ARCHIVE_PARTITION,
        )
        self.assertEqual(client.calls[1]["entity"]["RowKey"], "latest")
        self.assertEqual([call["timeout"] for call in client.calls], [5, 5])

    def test_forecast_validation_distinguishes_forecast_from_later_report(self) -> None:
        target = NOW

        def value(fetched: datetime, rain: float) -> WeatherSnapshot:
            point = WeatherPoint(
                timestamp_utc=target.isoformat(),
                temperature_c=18.0,
                relative_humidity_percent=70.0,
                dew_point_c=12.0,
                precipitation_probability_percent=80.0,
                precipitation_mm=rain,
                rain_mm=rain,
                cloud_cover_percent=50.0,
                wind_speed_kmh=8.0,
                shortwave_radiation_wm2=100.0,
                soil_moisture_m3m3=0.2,
            )
            return WeatherSnapshot(
                schema_version=1,
                provider="OPEN_METEO",
                fetched_at_utc=fetched.isoformat(),
                latitude=50.0,
                longitude=8.0,
                source_cost_class="FREE_NONCOMMERCIAL",
                points=(point,),
            )

        result = forecast_rain_validation(
            [value(NOW - timedelta(hours=4), 1.0), value(NOW + timedelta(hours=2), 2.5)],
            period_start_utc=target,
            period_end_utc=target + timedelta(hours=1),
        )
        self.assertEqual(result["sample_count"], 1)
        self.assertEqual(result["forecast_total_mm"], 1.0)
        self.assertEqual(result["reported_total_mm"], 2.5)
        self.assertEqual(result["mean_absolute_error_mm"], 1.5)


if __name__ == "__main__":
    unittest.main()
