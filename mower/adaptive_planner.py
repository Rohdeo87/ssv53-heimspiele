from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from datetime import datetime, time, timedelta, timezone
from typing import Any, Iterable, Mapping
from zoneinfo import ZoneInfo

from mower.planner import Block
from mower.weather import WeatherSnapshot


OCCUPANCY_SOURCES = frozenset({"training", "match", "special"})


def _bool(value: Any, default: bool = False) -> bool:
    if value is None or str(value).strip() == "":
        return default
    normalized = str(value).strip().casefold()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise ValueError(f"Ungültiger Wahrheitswert: {value!r}")


def _integer(
    values: Mapping[str, str],
    name: str,
    default: int,
    *,
    minimum: int,
    maximum: int,
) -> int:
    try:
        value = int(str(values.get(name, default)).strip())
    except ValueError as exc:
        raise ValueError(f"{name} muss eine ganze Zahl sein.") from exc
    if not minimum <= value <= maximum:
        raise ValueError(f"{name} muss zwischen {minimum} und {maximum} liegen.")
    return value


def _float(values: Mapping[str, str], name: str, default: float) -> float:
    try:
        return float(str(values.get(name, default)).strip())
    except ValueError as exc:
        raise ValueError(f"{name} muss eine Zahl sein.") from exc


def _clock(value: str) -> time:
    try:
        return time.fromisoformat(value)
    except ValueError as exc:
        raise ValueError(f"Ungültige Uhrzeit: {value!r}") from exc


def _source_parts(source: str) -> frozenset[str]:
    return frozenset(
        part.strip().casefold() for part in str(source).split("+") if part.strip()
    )


@dataclass(frozen=True)
class AdaptivePlannerSettings:
    enabled: bool
    execution_enabled: bool
    horizon_hours: int
    candidate_step_minutes: int
    park_lead_minutes: int
    post_irrigation_drying_minutes: int
    preferred_start_from: time
    preferred_start_until: time
    target_start: time
    finish_after_sunrise_minutes: int
    rain_reduce_min_mm: float
    rain_skip_min_mm: float
    rain_skip_min_probability: float

    @classmethod
    def from_mapping(cls, values: Mapping[str, str]) -> "AdaptivePlannerSettings":
        execution_enabled = _bool(values.get("ADAPTIVE_EXECUTION_ENABLED"), False)
        enabled = _bool(values.get("ADAPTIVE_PLANNING_ENABLED"), False)
        if execution_enabled and not enabled:
            raise ValueError(
                "ADAPTIVE_EXECUTION_ENABLED benötigt ADAPTIVE_PLANNING_ENABLED=true."
            )
        if execution_enabled:
            raise ValueError(
                "Adaptive Live-Ausführung ist in dieser Schattenversion absichtlich gesperrt."
            )
        return cls(
            enabled=enabled,
            execution_enabled=execution_enabled,
            horizon_hours=_integer(
                values, "PLANNING_HORIZON_HOURS", 48, minimum=24, maximum=168
            ),
            candidate_step_minutes=_integer(
                values, "IRRIGATION_CANDIDATE_STEP_MINUTES", 5, minimum=1, maximum=30
            ),
            park_lead_minutes=_integer(
                values, "MOWER_PARK_LEAD_MINUTES", 4, minimum=4, maximum=30
            ),
            post_irrigation_drying_minutes=_integer(
                values,
                "POST_IRRIGATION_DRYING_MINUTES",
                150,
                minimum=150,
                maximum=1440,
            ),
            preferred_start_from=_clock(
                str(values.get("IRRIGATION_PREFERRED_START_FROM", "01:00"))
            ),
            preferred_start_until=_clock(
                str(values.get("IRRIGATION_PREFERRED_START_UNTIL", "07:30"))
            ),
            target_start=_clock(
                str(values.get("IRRIGATION_TARGET_START_LOCAL", "04:30"))
            ),
            finish_after_sunrise_minutes=_integer(
                values,
                "IRRIGATION_FINISH_AFTER_SUNRISE_MINUTES",
                60,
                minimum=0,
                maximum=180,
            ),
            rain_reduce_min_mm=_float(values, "RAIN_REDUCE_MIN_MM", 3.0),
            rain_skip_min_mm=_float(values, "RAIN_SKIP_MIN_MM", 8.0),
            rain_skip_min_probability=_float(
                values, "RAIN_SKIP_MIN_PROBABILITY", 80.0
            ),
        )


@dataclass(frozen=True)
class IrrigationCandidate:
    park_at_utc: str
    irrigation_start_utc: str
    irrigation_end_utc: str
    earliest_mow_resume_utc: str
    drying_extension_minutes: int
    score: float
    lost_dry_mowing_minutes: int
    expected_rain_mm: float
    maximum_rain_probability: float
    score_reasons: tuple[str, ...]


@dataclass(frozen=True)
class AdaptivePlan:
    schema_version: int
    plan_id: str
    generated_at_utc: str
    valid_until_utc: str
    enabled: bool
    execution_enabled: bool
    shadow_only: bool
    status: str
    water_recommendation: str
    recommendation_reason: str
    selected: IrrigationCandidate | None
    candidates: tuple[IrrigationCandidate, ...]
    rejected_conflicts: int
    input_quality: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        value = asdict(self)
        value["selected"] = asdict(self.selected) if self.selected is not None else None
        value["candidates"] = [asdict(item) for item in self.candidates]
        return value


def _intersects(start: datetime, end: datetime, block: Block) -> bool:
    return start < block.end.astimezone(timezone.utc) and end > block.start.astimezone(
        timezone.utc
    )


def _overlap_minutes(start: datetime, end: datetime, block: Block) -> int:
    overlap_start = max(start, block.start.astimezone(timezone.utc))
    overlap_end = min(end, block.end.astimezone(timezone.utc))
    return max(0, int((overlap_end - overlap_start).total_seconds() // 60))


def _round_up(value: datetime, step_minutes: int) -> datetime:
    value = value.astimezone(timezone.utc)
    had_subminute = bool(value.second or value.microsecond)
    value = value.replace(second=0, microsecond=0)
    remainder = value.minute % step_minutes
    if remainder or had_subminute:
        value += timedelta(minutes=(step_minutes - remainder) % step_minutes or step_minutes)
    return value


def _local_minute(value: datetime, timezone_name: str) -> int:
    local = value.astimezone(ZoneInfo(timezone_name))
    return local.hour * 60 + local.minute


def _inside_preferred_window(value: datetime, settings: AdaptivePlannerSettings, timezone_name: str) -> bool:
    minute = _local_minute(value, timezone_name)
    start = settings.preferred_start_from.hour * 60 + settings.preferred_start_from.minute
    end = settings.preferred_start_until.hour * 60 + settings.preferred_start_until.minute
    if end >= start:
        return start <= minute <= end
    return minute >= start or minute <= end


def _plan_id(payload: dict[str, Any]) -> str:
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()[:24]


def _dynamic_target_minute(
    *,
    candidate_start: datetime,
    duration: timedelta,
    timezone_name: str,
    settings: AdaptivePlannerSettings,
    weather_snapshot: WeatherSnapshot | None,
    weather_fresh: bool,
) -> int:
    fallback = settings.target_start.hour * 60 + settings.target_start.minute
    if weather_snapshot is None or not weather_fresh:
        return fallback
    local_day = candidate_start.astimezone(ZoneInfo(timezone_name)).date()
    for raw in weather_snapshot.sunrise_utc:
        sunrise = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        if sunrise.tzinfo is None or sunrise.utcoffset() is None:
            continue
        sunrise_local = sunrise.astimezone(ZoneInfo(timezone_name))
        if sunrise_local.date() != local_day:
            continue
        target = (
            sunrise_local
            + timedelta(minutes=settings.finish_after_sunrise_minutes)
            - duration
        )
        return target.hour * 60 + target.minute
    return fallback


def _conservative_drying_extension_minutes(
    *,
    weather_snapshot: WeatherSnapshot | None,
    weather_fresh: bool,
    irrigation_end: datetime,
    default_release: datetime,
) -> int:
    """Ermittelt nur eine Verlängerung; die 150-Minuten-Basis wird nie gekürzt."""

    if weather_snapshot is None or not weather_fresh:
        return 0
    wet_hours = 0
    for point in weather_snapshot.points:
        if not irrigation_end <= point.timestamp < default_release:
            continue
        dew_spread = None
        if point.temperature_c is not None and point.dew_point_c is not None:
            dew_spread = point.temperature_c - point.dew_point_c
        wet = (
            float(point.precipitation_mm or point.rain_mm or 0.0) >= 0.2
            or float(point.relative_humidity_percent or 0.0) >= 90.0
            or (dew_spread is not None and dew_spread <= 2.0)
        )
        if wet:
            wet_hours += 1
    return min(180, wet_hours * 15)


def build_adaptive_plan(
    *,
    now_utc: datetime,
    timezone_name: str,
    blocks: Iterable[Block],
    zones: Iterable[Mapping[str, Any]],
    weather_snapshot: WeatherSnapshot | None,
    weather_fresh: bool,
    environment: Mapping[str, str],
) -> AdaptivePlan:
    settings = AdaptivePlannerSettings.from_mapping(environment)
    now = now_utc.astimezone(timezone.utc)
    quality = {
        "weather_available": weather_snapshot is not None,
        "weather_fresh": bool(weather_fresh),
        "weather_provider": (
            weather_snapshot.provider if weather_snapshot is not None else None
        ),
        "occupancy_fail_closed": any(
            bool(block.details.get("fail_closed")) for block in blocks
        ),
    }
    if not settings.enabled:
        return AdaptivePlan(
            schema_version=1,
            plan_id="disabled",
            generated_at_utc=now.isoformat(),
            valid_until_utc=(now + timedelta(minutes=5)).isoformat(),
            enabled=False,
            execution_enabled=False,
            shadow_only=True,
            status="DISABLED",
            water_recommendation="KEEP_BASELINE",
            recommendation_reason="Adaptive Planung ist sicher deaktiviert.",
            selected=None,
            candidates=(),
            rejected_conflicts=0,
            input_quality=quality,
        )

    normalized_zones = [dict(zone) for zone in zones]
    run_seconds = sum(max(0, int(zone.get("run_seconds") or 0)) for zone in normalized_zones)
    if not normalized_zones or run_seconds <= 0:
        return AdaptivePlan(
            schema_version=1,
            plan_id="no-irrigation-plan",
            generated_at_utc=now.isoformat(),
            valid_until_utc=(now + timedelta(minutes=5)).isoformat(),
            enabled=True,
            execution_enabled=settings.execution_enabled,
            shadow_only=True,
            status="NO_COMPLETE_IRRIGATION_PLAN",
            water_recommendation="KEEP_BASELINE",
            recommendation_reason="Es liegt kein vollständiger Beregnungsplan vor.",
            selected=None,
            candidates=(),
            rejected_conflicts=0,
            input_quality=quality,
        )

    occupancy_blocks = [
        block
        for block in blocks
        if _source_parts(block.source) & OCCUPANCY_SOURCES
    ]
    duration = timedelta(seconds=run_seconds)
    drying = timedelta(minutes=settings.post_irrigation_drying_minutes)
    horizon_end = now + timedelta(hours=settings.horizon_hours)
    start = _round_up(
        now + timedelta(minutes=settings.park_lead_minutes),
        settings.candidate_step_minutes,
    )
    candidates: list[IrrigationCandidate] = []
    rejected = 0
    while start + duration + drying <= horizon_end:
        if not _inside_preferred_window(start, settings, timezone_name):
            start += timedelta(minutes=settings.candidate_step_minutes)
            continue
        end = start + duration
        default_release = end + drying
        drying_extension = _conservative_drying_extension_minutes(
            weather_snapshot=weather_snapshot,
            weather_fresh=weather_fresh,
            irrigation_end=end,
            default_release=default_release,
        )
        release = default_release + timedelta(minutes=drying_extension)
        if release > horizon_end:
            start += timedelta(minutes=settings.candidate_step_minutes)
            continue
        if any(_intersects(start, release, block) for block in occupancy_blocks):
            rejected += 1
            start += timedelta(minutes=settings.candidate_step_minutes)
            continue
        full_wet_minutes = int((release - start).total_seconds() // 60)
        occupied_minutes = sum(
            _overlap_minutes(start, release, block) for block in occupancy_blocks
        )
        lost_dry_minutes = max(0, full_wet_minutes - occupied_minutes)
        local_minutes = _local_minute(start, timezone_name)
        target_minutes = _dynamic_target_minute(
            candidate_start=start,
            duration=duration,
            timezone_name=timezone_name,
            settings=settings,
            weather_snapshot=weather_snapshot,
            weather_fresh=weather_fresh,
        )
        time_distance = abs(local_minutes - target_minutes)
        if time_distance > 720:
            time_distance = 1440 - time_distance
        expected_rain = 0.0
        max_probability = 0.0
        if weather_snapshot is not None and weather_fresh:
            expected_rain = weather_snapshot.expected_rain_mm(start, release)
            max_probability = weather_snapshot.maximum_rain_probability(start, release)
        score = float(lost_dry_minutes * 10 + time_distance - expected_rain * 15)
        candidates.append(
            IrrigationCandidate(
                park_at_utc=(
                    start - timedelta(minutes=settings.park_lead_minutes)
                ).isoformat(),
                irrigation_start_utc=start.isoformat(),
                irrigation_end_utc=end.isoformat(),
                earliest_mow_resume_utc=release.isoformat(),
                drying_extension_minutes=drying_extension,
                score=round(score, 3),
                lost_dry_mowing_minutes=lost_dry_minutes,
                expected_rain_mm=round(expected_rain, 3),
                maximum_rain_probability=round(max_probability, 1),
                score_reasons=(
                    f"{lost_dry_minutes} potenziell verlorene trockene Mähminuten",
                    f"{time_distance} Minuten Abstand zur Zielzeit",
                    f"{expected_rain:.1f} mm erwarteter wirksamer Regen im Fenster",
                    f"{drying_extension} Minuten zusätzliche Trocknungsempfehlung",
                ),
            )
        )
        start += timedelta(minutes=settings.candidate_step_minutes)

    ordered = sorted(candidates, key=lambda item: (item.score, item.irrigation_start_utc))
    selected = ordered[0] if ordered else None
    next_twelve_hours = now + timedelta(hours=12)
    recent_rain = (
        weather_snapshot.forecast_rain_mm(now - timedelta(hours=6), now)
        if weather_snapshot is not None and weather_fresh
        else 0.0
    )
    expected_rain = (
        weather_snapshot.expected_rain_mm(now, next_twelve_hours)
        if weather_snapshot is not None and weather_fresh
        else 0.0
    )
    forecast_rain = (
        weather_snapshot.forecast_rain_mm(now, next_twelve_hours)
        if weather_snapshot is not None and weather_fresh
        else 0.0
    )
    max_probability = (
        weather_snapshot.maximum_rain_probability(now, next_twelve_hours)
        if weather_snapshot is not None and weather_fresh
        else 0.0
    )
    effective_rain_credit = recent_rain * 0.8 + expected_rain
    if not weather_fresh:
        recommendation = "KEEP_BASELINE"
        recommendation_reason = (
            "Wetterdaten fehlen oder sind veraltet; die Basisberegnung darf nicht reduziert werden."
        )
    elif (
        effective_rain_credit >= settings.rain_skip_min_mm
        and (
            recent_rain >= settings.rain_skip_min_mm
            or max_probability >= settings.rain_skip_min_probability
        )
    ):
        recommendation = "SKIP_RECOMMENDED"
        recommendation_reason = (
            f"{recent_rain:.1f} mm zuletzt und {forecast_rain:.1f} mm prognostiziert "
            f"bei bis zu {max_probability:.0f} % Wahrscheinlichkeit; "
            "nur Schattenempfehlung, keine automatische Auslassung."
        )
    elif effective_rain_credit >= settings.rain_reduce_min_mm:
        recommendation = "REDUCE_RECOMMENDED"
        recommendation_reason = (
            f"{effective_rain_credit:.1f} mm konservativ angerechneter Regen; "
            "nur Schattenempfehlung."
        )
    else:
        recommendation = "KEEP_BASELINE"
        recommendation_reason = "Der erwartete Regen reicht nicht für eine sichere Reduzierung."

    identity = {
        "now": now.isoformat(),
        "selected": asdict(selected) if selected is not None else None,
        "zone_count": len(normalized_zones),
        "run_seconds": run_seconds,
        "weather_fetched": (
            weather_snapshot.fetched_at_utc if weather_snapshot is not None else None
        ),
        "occupancy": [
            (block.start.isoformat(), block.end.isoformat(), block.source)
            for block in occupancy_blocks
        ],
    }
    return AdaptivePlan(
        schema_version=1,
        plan_id=_plan_id(identity),
        generated_at_utc=now.isoformat(),
        valid_until_utc=(
            now + timedelta(minutes=settings.candidate_step_minutes)
        ).isoformat(),
        enabled=True,
        execution_enabled=settings.execution_enabled,
        shadow_only=True,
        status="SHADOW_PLAN_READY" if selected is not None else "NO_SAFE_WINDOW",
        water_recommendation=recommendation,
        recommendation_reason=recommendation_reason,
        selected=selected,
        candidates=tuple(ordered[:8]),
        rejected_conflicts=rejected,
        input_quality=quality,
    )
