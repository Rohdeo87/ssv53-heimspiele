from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Mapping

from mower.weather import (
    OpenMeteoWeatherProvider,
    WeatherProvider,
    WeatherSettings,
    WeatherSnapshot,
)
from mower.weather_store import AzureTableWeatherStore, WeatherStore


@dataclass(frozen=True)
class WeatherResolution:
    enabled: bool
    shadow_only: bool
    available: bool
    fresh: bool
    source: str
    snapshot: WeatherSnapshot | None
    fetch_attempted: bool
    budget_reason: str | None
    budget: dict[str, int | str]
    error: str | None

    def to_dict(self) -> dict[str, Any]:
        return {
            "enabled": self.enabled,
            "shadow_only": self.shadow_only,
            "available": self.available,
            "fresh": self.fresh,
            "source": self.source,
            "fetched_at_utc": (
                self.snapshot.fetched_at_utc if self.snapshot is not None else None
            ),
            "provider": self.snapshot.provider if self.snapshot is not None else None,
            "source_cost_class": (
                self.snapshot.source_cost_class if self.snapshot is not None else None
            ),
            "point_count": len(self.snapshot.points) if self.snapshot is not None else 0,
            "fetch_attempted": self.fetch_attempted,
            "budget_reason": self.budget_reason,
            "budget": self.budget,
            "error": self.error,
        }


def resolve_weather(
    environment: Mapping[str, str],
    *,
    now_utc: datetime,
    store_factory: Callable[[Mapping[str, str]], WeatherStore] = (
        AzureTableWeatherStore.from_environment
    ),
    provider: WeatherProvider | None = None,
) -> WeatherResolution:
    try:
        settings = WeatherSettings.from_mapping(environment)
    except Exception as exc:
        return WeatherResolution(
            enabled=str(environment.get("WEATHER_ENABLED", "false")).strip().casefold()
            in {"1", "true", "yes", "on"},
            shadow_only=True,
            available=False,
            fresh=False,
            source="configuration-error",
            snapshot=None,
            fetch_attempted=False,
            budget_reason=None,
            budget={},
            error=f"{type(exc).__name__}: {exc}",
        )
    if not settings.enabled:
        return WeatherResolution(
            enabled=False,
            shadow_only=True,
            available=False,
            fresh=False,
            source="disabled",
            snapshot=None,
            fetch_attempted=False,
            budget_reason=None,
            budget={},
            error=None,
        )
    now = now_utc.astimezone(timezone.utc)
    cached: WeatherSnapshot | None = None
    try:
        store = store_factory(environment)
        cached = store.load_latest()
        cache_fresh = bool(
            cached is not None and cached.is_fresh(now, settings.max_age_minutes)
        )
        cache_inside_fetch_interval = bool(
            cached is not None
            and timedelta(0)
            <= now - cached.fetched_at
            < timedelta(minutes=settings.minimum_fetch_interval_minutes)
        )
        if cache_fresh and cache_inside_fetch_interval:
            return WeatherResolution(
                enabled=True,
                shadow_only=settings.shadow_only,
                available=True,
                fresh=True,
                source="cache",
                snapshot=cached,
                fetch_attempted=False,
                budget_reason="CACHE_FRESH",
                budget={},
                error=None,
            )
        reserved, reason, budget = store.reserve_fetch(
            now_utc=now,
            minimum_interval_minutes=settings.minimum_fetch_interval_minutes,
            daily_limit=settings.daily_call_limit,
            monthly_limit=settings.monthly_call_limit,
        )
        if not reserved:
            cache_fresh = bool(
                cached is not None and cached.is_fresh(now, settings.max_age_minutes)
            )
            return WeatherResolution(
                enabled=True,
                shadow_only=settings.shadow_only,
                available=cached is not None,
                fresh=cache_fresh,
                source=(
                    "cache"
                    if cache_fresh
                    else "stale-cache" if cached is not None else "unavailable"
                ),
                snapshot=cached,
                fetch_attempted=False,
                budget_reason=reason,
                budget=budget,
                error=(
                    None
                    if cache_fresh
                    else "Wetterbudget oder Mindestabstand verhindert einen neuen Abruf; "
                    "veraltete Daten dürfen keine Beregnung reduzieren."
                ),
            )
        selected_provider = provider or OpenMeteoWeatherProvider()
        snapshot = selected_provider.fetch(
            latitude=float(settings.latitude),
            longitude=float(settings.longitude),
            forecast_hours=settings.forecast_hours,
            now_utc=now,
        )
        store.save_latest(snapshot)
        return WeatherResolution(
            enabled=True,
            shadow_only=settings.shadow_only,
            available=True,
            fresh=True,
            source="live",
            snapshot=snapshot,
            fetch_attempted=True,
            budget_reason=reason,
            budget=budget,
            error=None,
        )
    except Exception as exc:
        cache_fresh = bool(
            cached is not None and cached.is_fresh(now, settings.max_age_minutes)
        )
        return WeatherResolution(
            enabled=True,
            shadow_only=settings.shadow_only,
            available=cached is not None,
            fresh=cache_fresh,
            source=(
                "cache-refresh-error"
                if cache_fresh
                else "stale-cache" if cached is not None else "unavailable"
            ),
            snapshot=cached,
            fetch_attempted=False,
            budget_reason=None,
            budget={},
            error=f"{type(exc).__name__}: {exc}",
        )
