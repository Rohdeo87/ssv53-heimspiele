from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Mapping, Protocol
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen


OPEN_METEO_FORECAST_URL = "https://api.open-meteo.com/v1/forecast"
OPEN_METEO_HOURLY_FIELDS = (
    "temperature_2m",
    "relative_humidity_2m",
    "dew_point_2m",
    "precipitation_probability",
    "precipitation",
    "rain",
    "cloud_cover",
    "wind_speed_10m",
    "shortwave_radiation",
    "soil_moisture_0_to_1cm",
)


class WeatherError(RuntimeError):
    pass


def _as_bool(value: Any, default: bool = False) -> bool:
    if value is None or str(value).strip() == "":
        return default
    normalized = str(value).strip().casefold()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise WeatherError(f"Ungültiger Wahrheitswert: {value!r}")


def _as_int(
    values: Mapping[str, str],
    name: str,
    default: int,
    *,
    minimum: int,
    maximum: int,
) -> int:
    try:
        parsed = int(str(values.get(name, default)).strip())
    except ValueError as exc:
        raise WeatherError(f"{name} muss eine ganze Zahl sein.") from exc
    if not minimum <= parsed <= maximum:
        raise WeatherError(f"{name} muss zwischen {minimum} und {maximum} liegen.")
    return parsed


def _as_float(values: Mapping[str, str], name: str) -> float:
    try:
        return float(str(values.get(name, "")).strip())
    except ValueError as exc:
        raise WeatherError(f"{name} muss eine Zahl sein.") from exc


def _parse_utc(value: Any, field_name: str) -> datetime:
    text = str(value or "").strip()
    if not text:
        raise WeatherError(f"{field_name} fehlt.")
    parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


@dataclass(frozen=True)
class WeatherSettings:
    enabled: bool
    shadow_only: bool
    provider: str
    latitude: float | None
    longitude: float | None
    forecast_hours: int
    max_age_minutes: int
    minimum_fetch_interval_minutes: int
    daily_call_limit: int
    monthly_call_limit: int

    @classmethod
    def from_mapping(cls, values: Mapping[str, str]) -> "WeatherSettings":
        enabled = _as_bool(values.get("WEATHER_ENABLED"), False)
        provider = str(values.get("WEATHER_PROVIDER", "OPEN_METEO")).strip().upper()
        if provider != "OPEN_METEO":
            raise WeatherError(
                "Nur OPEN_METEO ist im kostenfreien Wetterpfad freigegeben. "
                "Kostenpflichtige Provider benötigen eine getrennte Implementierung und Freigabe."
            )
        latitude = _as_float(values, "WEATHER_LATITUDE") if enabled else None
        longitude = _as_float(values, "WEATHER_LONGITUDE") if enabled else None
        if enabled and not -90 <= float(latitude) <= 90:
            raise WeatherError("WEATHER_LATITUDE liegt außerhalb des gültigen Bereichs.")
        if enabled and not -180 <= float(longitude) <= 180:
            raise WeatherError("WEATHER_LONGITUDE liegt außerhalb des gültigen Bereichs.")
        daily_limit = _as_int(
            values,
            "WEATHER_DAILY_CALL_LIMIT",
            24,
            minimum=1,
            maximum=96,
        )
        monthly_limit = _as_int(
            values,
            "WEATHER_MONTHLY_CALL_LIMIT",
            900,
            minimum=1,
            maximum=900,
        )
        if daily_limit * 31 < monthly_limit:
            monthly_limit = daily_limit * 31
        return cls(
            enabled=enabled,
            shadow_only=_as_bool(values.get("WEATHER_SHADOW_ONLY"), True),
            provider=provider,
            latitude=latitude,
            longitude=longitude,
            forecast_hours=_as_int(
                values,
                "WEATHER_FORECAST_HOURS",
                72,
                minimum=24,
                maximum=168,
            ),
            max_age_minutes=_as_int(
                values,
                "WEATHER_FORECAST_MAX_AGE_MINUTES",
                120,
                minimum=30,
                maximum=360,
            ),
            minimum_fetch_interval_minutes=_as_int(
                values,
                "WEATHER_MINIMUM_FETCH_INTERVAL_MINUTES",
                60,
                minimum=30,
                maximum=360,
            ),
            daily_call_limit=daily_limit,
            monthly_call_limit=monthly_limit,
        )


@dataclass(frozen=True)
class WeatherPoint:
    timestamp_utc: str
    temperature_c: float | None
    relative_humidity_percent: float | None
    dew_point_c: float | None
    precipitation_probability_percent: float | None
    precipitation_mm: float | None
    rain_mm: float | None
    cloud_cover_percent: float | None
    wind_speed_kmh: float | None
    shortwave_radiation_wm2: float | None
    soil_moisture_m3m3: float | None

    @property
    def timestamp(self) -> datetime:
        return _parse_utc(self.timestamp_utc, "timestamp_utc")

    @property
    def expected_rain_mm(self) -> float:
        amount = max(0.0, float(self.precipitation_mm or self.rain_mm or 0.0))
        probability = min(
            100.0,
            max(0.0, float(self.precipitation_probability_percent or 0.0)),
        )
        return amount * probability / 100.0


@dataclass(frozen=True)
class WeatherSnapshot:
    schema_version: int
    provider: str
    fetched_at_utc: str
    latitude: float
    longitude: float
    source_cost_class: str
    points: tuple[WeatherPoint, ...]
    sunrise_utc: tuple[str, ...] = ()
    sunset_utc: tuple[str, ...] = ()

    @property
    def fetched_at(self) -> datetime:
        return _parse_utc(self.fetched_at_utc, "fetched_at_utc")

    def is_fresh(self, now_utc: datetime, max_age_minutes: int) -> bool:
        return timedelta(0) <= now_utc.astimezone(timezone.utc) - self.fetched_at <= timedelta(
            minutes=max_age_minutes
        )

    def expected_rain_mm(self, start_utc: datetime, end_utc: datetime) -> float:
        start = start_utc.astimezone(timezone.utc)
        end = end_utc.astimezone(timezone.utc)
        return round(
            sum(point.expected_rain_mm for point in self.points if start <= point.timestamp < end),
            3,
        )

    def forecast_rain_mm(self, start_utc: datetime, end_utc: datetime) -> float:
        start = start_utc.astimezone(timezone.utc)
        end = end_utc.astimezone(timezone.utc)
        return round(
            sum(
                max(0.0, float(point.precipitation_mm or point.rain_mm or 0.0))
                for point in self.points
                if start <= point.timestamp < end
            ),
            3,
        )

    def maximum_rain_probability(
        self, start_utc: datetime, end_utc: datetime
    ) -> float:
        start = start_utc.astimezone(timezone.utc)
        end = end_utc.astimezone(timezone.utc)
        values = [
            float(point.precipitation_probability_percent or 0.0)
            for point in self.points
            if start <= point.timestamp < end
        ]
        return max(values, default=0.0)

    def to_dict(self) -> dict[str, Any]:
        value = asdict(self)
        value["points"] = [asdict(point) for point in self.points]
        return value

    @classmethod
    def from_mapping(cls, values: Mapping[str, Any]) -> "WeatherSnapshot":
        raw_points = values.get("points")
        if not isinstance(raw_points, list):
            raise WeatherError("Wetter-Snapshot enthält keine gültigen Stundenwerte.")
        points = tuple(
            WeatherPoint(**point)
            for point in raw_points
            if isinstance(point, dict)
        )
        if not points:
            raise WeatherError("Wetter-Snapshot enthält keine Stundenwerte.")
        return cls(
            schema_version=int(values.get("schema_version", 1)),
            provider=str(values.get("provider") or ""),
            fetched_at_utc=_parse_utc(
                values.get("fetched_at_utc"), "fetched_at_utc"
            ).isoformat(),
            latitude=float(values.get("latitude")),
            longitude=float(values.get("longitude")),
            source_cost_class=str(values.get("source_cost_class") or "unknown"),
            points=points,
            sunrise_utc=tuple(str(value) for value in values.get("sunrise_utc", [])),
            sunset_utc=tuple(str(value) for value in values.get("sunset_utc", [])),
        )


class WeatherProvider(Protocol):
    def fetch(
        self,
        *,
        latitude: float,
        longitude: float,
        forecast_hours: int,
        now_utc: datetime,
    ) -> WeatherSnapshot:
        ...


class OpenMeteoWeatherProvider:
    """Kostenfreier, schlüssel- und zahlungsfreier Forecast-Adapter.

    Die URL ist absichtlich fest verdrahtet. Eine konfigurierbare Ziel-URL
    würde sowohl die Kosten- als auch die Netzwerksicherheitsgrenze aufweichen.
    """

    def fetch(
        self,
        *,
        latitude: float,
        longitude: float,
        forecast_hours: int,
        now_utc: datetime,
    ) -> WeatherSnapshot:
        query = urlencode(
            {
                "latitude": f"{latitude:.6f}",
                "longitude": f"{longitude:.6f}",
                "hourly": ",".join(OPEN_METEO_HOURLY_FIELDS),
                "daily": "sunrise,sunset",
                "timezone": "UTC",
                "past_hours": "12",
                "forecast_hours": str(forecast_hours),
            }
        )
        request = Request(
            f"{OPEN_METEO_FORECAST_URL}?{query}",
            method="GET",
            headers={"Accept": "application/json", "User-Agent": "SSV53-Platzpflege/1.0"},
        )
        try:
            with urlopen(request, timeout=8) as response:  # noqa: S310
                payload = response.read().decode("utf-8")
        except HTTPError as exc:
            raise WeatherError(f"Wetterabruf fehlgeschlagen: HTTP {exc.code}.") from exc
        except URLError as exc:
            raise WeatherError(f"Wetterabruf fehlgeschlagen: {exc.reason}") from exc
        try:
            data = json.loads(payload)
        except json.JSONDecodeError as exc:
            raise WeatherError("Wetterdienst lieferte kein gültiges JSON.") from exc
        if not isinstance(data, dict):
            raise WeatherError("Wetterdienst lieferte ein unerwartetes Datenformat.")
        hourly = data.get("hourly")
        if not isinstance(hourly, dict) or not isinstance(hourly.get("time"), list):
            raise WeatherError("Wetterdienst lieferte keine Stundenwerte.")
        times = hourly["time"]

        def number(field: str, index: int) -> float | None:
            values = hourly.get(field)
            if not isinstance(values, list) or index >= len(values):
                return None
            value = values[index]
            return None if value is None else float(value)

        points = tuple(
            WeatherPoint(
                timestamp_utc=_parse_utc(value, "hourly.time").isoformat(),
                temperature_c=number("temperature_2m", index),
                relative_humidity_percent=number("relative_humidity_2m", index),
                dew_point_c=number("dew_point_2m", index),
                precipitation_probability_percent=number(
                    "precipitation_probability", index
                ),
                precipitation_mm=number("precipitation", index),
                rain_mm=number("rain", index),
                cloud_cover_percent=number("cloud_cover", index),
                wind_speed_kmh=number("wind_speed_10m", index),
                shortwave_radiation_wm2=number("shortwave_radiation", index),
                soil_moisture_m3m3=number("soil_moisture_0_to_1cm", index),
            )
            for index, value in enumerate(times)
        )
        if not points:
            raise WeatherError("Wetterdienst lieferte eine leere Vorhersage.")
        daily = data.get("daily") if isinstance(data.get("daily"), dict) else {}
        sunrise = tuple(
            _parse_utc(value, "daily.sunrise").isoformat()
            for value in daily.get("sunrise", [])
        )
        sunset = tuple(
            _parse_utc(value, "daily.sunset").isoformat()
            for value in daily.get("sunset", [])
        )
        return WeatherSnapshot(
            schema_version=1,
            provider="OPEN_METEO",
            fetched_at_utc=now_utc.astimezone(timezone.utc).isoformat(),
            latitude=latitude,
            longitude=longitude,
            source_cost_class="FREE_NONCOMMERCIAL",
            points=points,
            sunrise_utc=sunrise,
            sunset_utc=sunset,
        )
