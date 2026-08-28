from __future__ import annotations

import html
import json
import math
import re
import statistics
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from email.message import EmailMessage
from email.utils import formataddr
from typing import Any, Callable, Mapping, Sequence
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo

from azure.core.exceptions import ResourceExistsError, ResourceNotFoundError
from azure.data.tables import TableClient, UpdateMode
from azure.identity import ManagedIdentityCredential

from order_mail import (
    APP_BORDER,
    APP_GOLD,
    APP_LOGO_URL,
    APP_MUTED,
    EMAIL_PATTERN,
    OrderMailSettings,
    _open_authenticated_smtp,
)
from mower.irrigation_journal import read_irrigation_observations
from mower.weather import WeatherSnapshot
from mower.weather_store import (
    forecast_rain_validation,
    read_archived_weather_snapshots,
)


REPORT_PARTITION = "ssv53-daily-safety-report-v1"
REPORT_TIME_ZONE = ZoneInfo("Europe/Berlin")
MOWING_ACTIVITIES = frozenset({"MOWING", "LEAVING"})
PARKED_ACTIVITIES = frozenset({"PARKED_IN_CS", "CHARGING"})
QUERY_SCOPE = "https://api.applicationinsights.io/.default"
MAX_QUERY_ROWS = 20_000


class DailyReportError(RuntimeError):
    pass


@dataclass(frozen=True)
class CycleObservation:
    timestamp_utc: datetime
    activity: str
    mower_state: str
    error_code: int
    battery_percent: int
    decision_code: str
    command_sent: bool
    hydrawise_available: bool
    hydrawise_fresh: bool
    hydrawise_active_zones: int
    next_irrigation_start_utc: datetime | None
    blocked_source: str
    parking_source: str
    work_area_type: str
    work_area_progress: int
    work_area_last_completed: int
    weather_enabled: bool
    weather_available: bool
    weather_fresh: bool
    weather_provider: str
    adaptive_enabled: bool
    adaptive_execution_enabled: bool
    adaptive_status: str
    adaptive_recommendation: str
    adaptive_irrigation_start_utc: datetime | None
    adaptive_irrigation_end_utc: datetime | None
    adaptive_earliest_mow_resume_utc: datetime | None
    adaptive_lost_dry_mowing_minutes: int
    adaptive_drying_extension_minutes: int
    adaptive_expected_rain_mm: float


@dataclass(frozen=True)
class DailyReportSummary:
    report_date: date
    period_start_local: datetime
    period_end_local: datetime
    cycle_count_24h: int
    expected_cycles_24h: int
    gap_count_24h: int
    exception_count_24h: int
    command_count_24h: int
    mowing_minutes_7d: int
    average_daily_mowing_minutes_7d: int
    mowing_minutes_today: int
    average_return_minutes_7d: int | None
    median_return_minutes_7d: int | None
    p95_return_minutes_7d: int | None
    return_measurements_7d: int
    completed_area_cycles_7d: int
    current_work_area_progress: int
    last_completed_area_utc: datetime | None
    daily_mowing_minutes: tuple[tuple[date, int], ...]
    current_activity: str
    current_state: str
    current_error_code: int
    current_battery_percent: int
    hydrawise_available: bool
    hydrawise_fresh: bool
    hydrawise_active_zones: int
    next_irrigation_start_utc: datetime | None
    blocked_source: str
    parking_source: str
    overall_status: str
    warnings: tuple[str, ...]


@dataclass(frozen=True)
class AdaptiveShadowSummary:
    enabled: bool
    execution_enabled: bool
    latest_status: str
    latest_recommendation: str
    irrigation_start_utc: datetime | None
    irrigation_end_utc: datetime | None
    earliest_mow_resume_utc: datetime | None
    lost_dry_mowing_minutes: int
    drying_extension_minutes: int
    expected_rain_mm: float
    plan_changes_24h: int
    weather_cycles_24h: int
    weather_fresh_cycles_24h: int
    weather_fresh_percent_24h: int
    weather_provider: str
    archive_available: bool
    archived_snapshots: int
    rain_validation_samples: int
    forecast_rain_mm: float
    reported_rain_mm: float
    mean_absolute_rain_error_mm: float
    maximum_rain_error_mm: float


def _parse_utc(value: Any) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise DailyReportError("Telemetriezeit enthält keine Zeitzone.")
    return parsed.astimezone(timezone.utc)


def _as_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return str(value or "").strip().lower() == "true"


def _as_int(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _as_float(value: Any) -> float:
    try:
        return float(value or 0.0)
    except (TypeError, ValueError):
        return 0.0


def report_recipient(values: Mapping[str, str]) -> str:
    recipient = str(values.get("SSV53_DAILY_REPORT_RECIPIENT", "")).strip().lower()
    if not EMAIL_PATTERN.fullmatch(recipient):
        raise DailyReportError("Die Empfängeradresse des Tagesberichts ist ungültig.")
    return recipient


def enabled(values: Mapping[str, str]) -> bool:
    return str(values.get("SSV53_DAILY_REPORT_ENABLED", "")).strip().lower() == "true"


def should_attempt_delivery(now_utc: datetime) -> bool:
    local = now_utc.astimezone(REPORT_TIME_ZONE)
    return 7 <= local.hour <= 9


class AzureDailyReportStore:
    def __init__(self, table_client: TableClient) -> None:
        self._table = table_client

    @classmethod
    def from_environment(
        cls,
        values: Mapping[str, str],
        *,
        credential_factory=ManagedIdentityCredential,
        table_client_factory=TableClient,
    ) -> "AzureDailyReportStore":
        endpoint = str(values.get("SSV53_STORAGE_ACCOUNT_URL", "")).strip()
        table_name = str(values.get("SSV53_STATE_TABLE_NAME", "")).strip()
        client_id = str(
            values.get("SSV53_STATE_MANAGED_IDENTITY_CLIENT_ID")
            or values.get("AzureWebJobsStorage__clientId")
            or ""
        ).strip()
        if not endpoint or not table_name or not client_id:
            raise DailyReportError("Der Zustandspeicher des Tagesberichts ist unvollständig.")
        return cls(
            table_client_factory(
                endpoint=endpoint,
                table_name=table_name,
                credential=credential_factory(client_id=client_id),
            )
        )

    def claim(self, report_date: date, now_utc: datetime) -> bool:
        row_key = report_date.isoformat()
        try:
            self._table.create_entity(
                {
                    "PartitionKey": REPORT_PARTITION,
                    "RowKey": row_key,
                    "Status": "sending",
                    "CreatedAtUtc": now_utc.isoformat(),
                }
            )
            return True
        except ResourceExistsError:
            try:
                current = self._table.get_entity(REPORT_PARTITION, row_key)
            except ResourceNotFoundError:
                return False
            if str(current.get("Status") or "").lower() != "failed":
                return False
            try:
                updated = datetime.fromisoformat(
                    str(current.get("UpdatedAtUtc") or current.get("CreatedAtUtc") or "")
                )
            except ValueError:
                return False
            if updated.tzinfo is None or now_utc - updated < timedelta(minutes=30):
                return False
            self._table.update_entity(
                {
                    "PartitionKey": REPORT_PARTITION,
                    "RowKey": row_key,
                    "Status": "sending",
                    "UpdatedAtUtc": now_utc.isoformat(),
                },
                mode=UpdateMode.MERGE,
            )
            return True

    def mark(self, report_date: date, status: str, now_utc: datetime) -> None:
        self._table.update_entity(
            {
                "PartitionKey": REPORT_PARTITION,
                "RowKey": report_date.isoformat(),
                "Status": status,
                "UpdatedAtUtc": now_utc.isoformat(),
            },
            mode=UpdateMode.MERGE,
        )


class ApplicationInsightsQueryClient:
    def __init__(self, app_id: str, credential: Any) -> None:
        normalized = app_id.strip()
        if not re.fullmatch(r"[0-9a-fA-F-]{36}", normalized):
            raise DailyReportError("Die Application-Insights-App-ID ist ungültig.")
        self._app_id = normalized
        self._credential = credential

    @classmethod
    def from_environment(
        cls,
        values: Mapping[str, str],
        *,
        credential_factory=ManagedIdentityCredential,
    ) -> "ApplicationInsightsQueryClient":
        app_id = str(values.get("SSV53_APP_INSIGHTS_APP_ID", ""))
        client_id = str(
            values.get("SSV53_STATE_MANAGED_IDENTITY_CLIENT_ID")
            or values.get("AzureWebJobsStorage__clientId")
            or ""
        ).strip()
        if not client_id:
            raise DailyReportError("Die Managed Identity für den Tagesbericht fehlt.")
        return cls(app_id, credential_factory(client_id=client_id))

    def execute(self, query: str, *, timespan: str = "P8D") -> list[dict[str, Any]]:
        token = self._credential.get_token(QUERY_SCOPE).token
        body = json.dumps({"query": query, "timespan": timespan}).encode("utf-8")
        request = Request(
            f"https://api.applicationinsights.io/v1/apps/{self._app_id}/query",
            data=body,
            method="POST",
            headers={
                "Accept": "application/json",
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
                "User-Agent": "SSV53-Daily-Safety-Report/1.0",
            },
        )
        try:
            with urlopen(request, timeout=45) as response:  # noqa: S310
                payload = json.loads(response.read().decode("utf-8"))
        except HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")[:500]
            raise DailyReportError(
                f"Application Insights antwortete mit HTTP {exc.code}: {detail}"
            ) from exc
        except (URLError, TimeoutError, json.JSONDecodeError) as exc:
            raise DailyReportError("Application Insights konnte nicht gelesen werden.") from exc
        tables = payload.get("tables") if isinstance(payload, dict) else None
        if not isinstance(tables, list) or not tables:
            raise DailyReportError("Application Insights lieferte keine Tabelle.")
        table = tables[0]
        columns = [str(item.get("name") or "") for item in table.get("columns", [])]
        rows = table.get("rows", [])
        if not columns or not isinstance(rows, list):
            raise DailyReportError("Application Insights lieferte ein ungültiges Tabellenformat.")
        if len(rows) > MAX_QUERY_ROWS:
            raise DailyReportError("Application Insights lieferte unerwartet viele Zeilen.")
        return [dict(zip(columns, row, strict=False)) for row in rows]


def _cycle_query(period_start_utc: datetime, period_end_utc: datetime) -> str:
    start = period_start_utc.isoformat().replace("+00:00", "Z")
    end = period_end_utc.isoformat().replace("+00:00", "Z")
    return f"""
traces
| where timestamp between (datetime({start}) .. datetime({end}))
| where message startswith "SSV53_CONTROL_CYCLE "
| extend p=parse_json(replace_string(message, "SSV53_CONTROL_CYCLE ", ""))
| project timestamp,
    activity=tostring(p.details.mower.activity),
    mower_state=tostring(p.details.mower.state),
    error_code=toint(p.details.mower.error_code),
    battery_percent=toint(p.details.mower.battery_percent),
    decision_code=tostring(p.decision_code),
    command_sent=tobool(p.command_sent),
    hydrawise_available=tobool(p.details.hydrawise.safety.available),
    hydrawise_fresh=tobool(p.details.hydrawise.safety.fresh),
    hydrawise_active_zones=toint(p.details.hydrawise.safety.active_zone_count),
    next_irrigation_start_utc=tostring(p.details.irrigation_outage_guard.next_scheduled_start_utc),
    blocked_source=tostring(p.details.current_plan.blocked_now.source),
    parking_source=tostring(p.details.current_plan.parking_block.source),
    work_area_type=tostring(p.details.mower.target_work_area.type),
    work_area_progress=toint(p.details.mower.target_work_area.progress),
    work_area_last_completed=tolong(p.details.mower.target_work_area.last_time_completed),
    weather_enabled=tobool(p.details.weather.enabled),
    weather_available=tobool(p.details.weather.available),
    weather_fresh=tobool(p.details.weather.fresh),
    weather_provider=tostring(p.details.weather.provider),
    adaptive_enabled=tobool(p.details.adaptive_planning.enabled),
    adaptive_execution_enabled=tobool(p.details.adaptive_planning.execution_enabled),
    adaptive_status=tostring(p.details.adaptive_planning.status),
    adaptive_recommendation=tostring(p.details.adaptive_planning.water_recommendation),
    adaptive_irrigation_start_utc=tostring(p.details.adaptive_planning.selected.irrigation_start_utc),
    adaptive_irrigation_end_utc=tostring(p.details.adaptive_planning.selected.irrigation_end_utc),
    adaptive_earliest_mow_resume_utc=tostring(p.details.adaptive_planning.selected.earliest_mow_resume_utc),
    adaptive_lost_dry_mowing_minutes=toint(p.details.adaptive_planning.selected.lost_dry_mowing_minutes),
    adaptive_drying_extension_minutes=toint(p.details.adaptive_planning.selected.drying_extension_minutes),
    adaptive_expected_rain_mm=todouble(p.details.adaptive_planning.selected.expected_rain_mm)
| order by timestamp asc
""".strip()


def _exception_query(period_start_utc: datetime, period_end_utc: datetime) -> str:
    start = period_start_utc.isoformat().replace("+00:00", "Z")
    end = period_end_utc.isoformat().replace("+00:00", "Z")
    return (
        "exceptions "
        f"| where timestamp between (datetime({start}) .. datetime({end})) "
        "| where operation_Name == 'ssv53_mower_timer' "
        "| summarize exception_count=sum(itemCount)"
    )


def parse_cycle_rows(rows: Sequence[Mapping[str, Any]]) -> list[CycleObservation]:
    observations: dict[datetime, CycleObservation] = {}
    for row in rows:
        timestamp = _parse_utc(row.get("timestamp"))
        if timestamp is None:
            continue
        minute = timestamp.replace(second=0, microsecond=0)
        observations[minute] = CycleObservation(
            timestamp_utc=timestamp,
            activity=str(row.get("activity") or "UNKNOWN").upper(),
            mower_state=str(row.get("mower_state") or "UNKNOWN").upper(),
            error_code=_as_int(row.get("error_code")),
            battery_percent=_as_int(row.get("battery_percent")),
            decision_code=str(row.get("decision_code") or ""),
            command_sent=_as_bool(row.get("command_sent")),
            hydrawise_available=_as_bool(row.get("hydrawise_available")),
            hydrawise_fresh=_as_bool(row.get("hydrawise_fresh")),
            hydrawise_active_zones=_as_int(row.get("hydrawise_active_zones")),
            next_irrigation_start_utc=_parse_utc(row.get("next_irrigation_start_utc")),
            blocked_source=str(row.get("blocked_source") or ""),
            parking_source=str(row.get("parking_source") or ""),
            work_area_type=str(row.get("work_area_type") or "").upper(),
            work_area_progress=_as_int(row.get("work_area_progress")),
            work_area_last_completed=_as_int(row.get("work_area_last_completed")),
            weather_enabled=_as_bool(row.get("weather_enabled")),
            weather_available=_as_bool(row.get("weather_available")),
            weather_fresh=_as_bool(row.get("weather_fresh")),
            weather_provider=str(row.get("weather_provider") or ""),
            adaptive_enabled=_as_bool(row.get("adaptive_enabled")),
            adaptive_execution_enabled=_as_bool(
                row.get("adaptive_execution_enabled")
            ),
            adaptive_status=str(row.get("adaptive_status") or "").upper(),
            adaptive_recommendation=str(
                row.get("adaptive_recommendation") or ""
            ).upper(),
            adaptive_irrigation_start_utc=_parse_utc(
                row.get("adaptive_irrigation_start_utc")
            ),
            adaptive_irrigation_end_utc=_parse_utc(
                row.get("adaptive_irrigation_end_utc")
            ),
            adaptive_earliest_mow_resume_utc=_parse_utc(
                row.get("adaptive_earliest_mow_resume_utc")
            ),
            adaptive_lost_dry_mowing_minutes=_as_int(
                row.get("adaptive_lost_dry_mowing_minutes")
            ),
            adaptive_drying_extension_minutes=_as_int(
                row.get("adaptive_drying_extension_minutes")
            ),
            adaptive_expected_rain_mm=_as_float(
                row.get("adaptive_expected_rain_mm")
            ),
        )
    return sorted(observations.values(), key=lambda item: item.timestamp_utc)


def _return_durations_minutes(
    observations: Sequence[CycleObservation],
) -> list[float]:
    durations: list[float] = []
    going_home_started_at: datetime | None = None
    previous_activity: str | None = None
    for item in observations:
        if item.activity == "GOING_HOME":
            if previous_activity != "GOING_HOME":
                going_home_started_at = item.timestamp_utc
        elif going_home_started_at is not None:
            if item.activity in PARKED_ACTIVITIES:
                duration = (item.timestamp_utc - going_home_started_at).total_seconds() / 60
                if 0 < duration <= 60:
                    durations.append(duration)
            going_home_started_at = None
        previous_activity = item.activity
    return durations


def summarize_adaptive_shadow(
    observations: Sequence[CycleObservation],
    *,
    now_utc: datetime,
    archived_snapshots: Sequence[WeatherSnapshot] = (),
    archive_available: bool = True,
) -> AdaptiveShadowSummary:
    last_24h_start = now_utc - timedelta(hours=24)
    last_24h = [item for item in observations if item.timestamp_utc >= last_24h_start]
    latest = observations[-1] if observations else None
    weather_cycles = [item for item in last_24h if item.weather_enabled]
    weather_fresh = [
        item
        for item in weather_cycles
        if item.weather_available and item.weather_fresh
    ]
    plan_values = [
        (
            item.adaptive_recommendation,
            item.adaptive_irrigation_start_utc,
            item.adaptive_irrigation_end_utc,
            item.adaptive_earliest_mow_resume_utc,
        )
        for item in last_24h
        if item.adaptive_enabled and item.adaptive_status
    ]
    plan_changes = sum(
        1 for previous, current in zip(plan_values, plan_values[1:]) if current != previous
    )
    validation = forecast_rain_validation(
        archived_snapshots,
        period_start_utc=max(
            now_utc - timedelta(days=7),
            archived_snapshots[0].fetched_at if archived_snapshots else now_utc,
        ),
        period_end_utc=now_utc - timedelta(hours=1),
    )
    return AdaptiveShadowSummary(
        enabled=bool(latest and latest.adaptive_enabled),
        execution_enabled=bool(latest and latest.adaptive_execution_enabled),
        latest_status=latest.adaptive_status if latest else "",
        latest_recommendation=latest.adaptive_recommendation if latest else "",
        irrigation_start_utc=(
            latest.adaptive_irrigation_start_utc if latest else None
        ),
        irrigation_end_utc=latest.adaptive_irrigation_end_utc if latest else None,
        earliest_mow_resume_utc=(
            latest.adaptive_earliest_mow_resume_utc if latest else None
        ),
        lost_dry_mowing_minutes=(
            latest.adaptive_lost_dry_mowing_minutes if latest else 0
        ),
        drying_extension_minutes=(
            latest.adaptive_drying_extension_minutes if latest else 0
        ),
        expected_rain_mm=latest.adaptive_expected_rain_mm if latest else 0.0,
        plan_changes_24h=plan_changes,
        weather_cycles_24h=len(weather_cycles),
        weather_fresh_cycles_24h=len(weather_fresh),
        weather_fresh_percent_24h=(
            round(len(weather_fresh) * 100 / len(weather_cycles))
            if weather_cycles
            else 0
        ),
        weather_provider=latest.weather_provider if latest else "",
        archive_available=archive_available,
        archived_snapshots=len(archived_snapshots),
        rain_validation_samples=int(validation["sample_count"]),
        forecast_rain_mm=float(validation["forecast_total_mm"]),
        reported_rain_mm=float(validation["reported_total_mm"]),
        mean_absolute_rain_error_mm=float(validation["mean_absolute_error_mm"]),
        maximum_rain_error_mm=float(validation["maximum_error_mm"]),
    )


def summarize_report(
    observations: Sequence[CycleObservation],
    *,
    now_utc: datetime,
    exception_count_24h: int,
) -> DailyReportSummary:
    local_now = now_utc.astimezone(REPORT_TIME_ZONE)
    start_day = local_now.date() - timedelta(days=6)
    period_start_local = datetime.combine(start_day, time.min, REPORT_TIME_ZONE)
    period_start_utc = period_start_local.astimezone(timezone.utc)
    relevant = [item for item in observations if period_start_utc <= item.timestamp_utc <= now_utc]
    last_24h_start = now_utc - timedelta(hours=24)
    last_24h = [item for item in relevant if item.timestamp_utc >= last_24h_start]
    daily_minutes: dict[date, int] = defaultdict(int)
    for item in relevant:
        if item.activity in MOWING_ACTIVITIES:
            daily_minutes[item.timestamp_utc.astimezone(REPORT_TIME_ZONE).date()] += 1
    days = tuple((start_day + timedelta(days=index), daily_minutes[start_day + timedelta(days=index)]) for index in range(7))
    mowing_minutes = sum(minutes for _, minutes in days)
    completion_values = {
        item.work_area_last_completed
        for item in relevant
        if item.work_area_last_completed > 0
        and datetime.fromtimestamp(
            item.work_area_last_completed, timezone.utc
        ) >= period_start_utc - timedelta(hours=3)
    }
    completion_times = sorted(
        datetime.fromtimestamp(value, timezone.utc) for value in completion_values
    )
    # The 580 EPOS currently leaves lastTimeCompleted at 0. Its systematic-area
    # progress is integer telemetry and can therefore move 99 -> 0 between two
    # minute samples even though the app briefly shows 100 %. Treat only a
    # stable 99 % plateau followed immediately by the reset as a completion.
    high_progress_samples = 0
    last_high_progress_at: datetime | None = None
    for item in relevant:
        is_high_mowing_sample = (
            item.work_area_type == "SYSTEMATIC"
            and item.activity in MOWING_ACTIVITIES
            and item.work_area_progress >= 99
        )
        if is_high_mowing_sample:
            if (
                last_high_progress_at is not None
                and item.timestamp_utc - last_high_progress_at <= timedelta(seconds=90)
            ):
                high_progress_samples += 1
            else:
                high_progress_samples = 1
            last_high_progress_at = item.timestamp_utc
            continue
        inferred_completion = (
            high_progress_samples >= 2
            and last_high_progress_at is not None
            and item.timestamp_utc - last_high_progress_at <= timedelta(seconds=90)
            and item.work_area_progress <= 1
        )
        if inferred_completion and not any(
            abs((item.timestamp_utc - confirmed).total_seconds()) <= 10 * 60
            for confirmed in completion_times
        ):
            completion_times.append(item.timestamp_utc)
        high_progress_samples = 0
        last_high_progress_at = None
    completion_times.sort()
    completed_cycles = len(completion_times)
    last_completed_area_utc = completion_times[-1] if completion_times else None
    return_minutes = _return_durations_minutes(relevant)
    average_return_minutes = (
        round(sum(return_minutes) / len(return_minutes))
        if return_minutes
        else None
    )
    median_return_minutes = (
        round(statistics.median(return_minutes)) if return_minutes else None
    )
    p95_return_minutes = (
        round(sorted(return_minutes)[max(0, math.ceil(0.95 * len(return_minutes)) - 1)])
        if return_minutes
        else None
    )
    gaps = 0
    for previous, current in zip(last_24h, last_24h[1:]):
        if current.timestamp_utc - previous.timestamp_utc > timedelta(seconds=90):
            gaps += 1
    expected_cycles = 24 * 60
    latest = relevant[-1] if relevant else None
    warnings: list[str] = []
    latest_error_active = bool(
        latest
        and latest.mower_state in {"ERROR", "FATAL_ERROR", "ERROR_AT_POWER_UP"}
    )
    if not latest:
        warnings.append("Keine aktuelle Mähertelemetrie vorhanden")
    else:
        if latest_error_active:
            warnings.append(
                f"Mäherfehler aktiv: Zustand {latest.mower_state}, Fehlercode {latest.error_code}"
            )
        if not latest.hydrawise_available or not latest.hydrawise_fresh:
            warnings.append("Hydrawise ist nicht frisch und vollständig erreichbar")
    if len(last_24h) < expected_cycles * 0.95:
        warnings.append(
            f"Nur {len(last_24h)} von ungefähr {expected_cycles} Minutenzyklen vorhanden"
        )
    if gaps:
        warnings.append(f"{gaps} Telemetrielücke(n) über 90 Sekunden erkannt")
    if exception_count_24h:
        warnings.append(f"{exception_count_24h} Mäher-Timer-Ausnahme(n) in 24 Stunden")
    return DailyReportSummary(
        report_date=local_now.date(),
        period_start_local=period_start_local,
        period_end_local=local_now,
        cycle_count_24h=len(last_24h),
        expected_cycles_24h=expected_cycles,
        gap_count_24h=gaps,
        exception_count_24h=max(0, int(exception_count_24h)),
        command_count_24h=sum(1 for item in last_24h if item.command_sent),
        mowing_minutes_7d=mowing_minutes,
        average_daily_mowing_minutes_7d=round(mowing_minutes / 7),
        mowing_minutes_today=days[-1][1],
        average_return_minutes_7d=average_return_minutes,
        median_return_minutes_7d=median_return_minutes,
        p95_return_minutes_7d=p95_return_minutes,
        return_measurements_7d=len(return_minutes),
        completed_area_cycles_7d=completed_cycles,
        current_work_area_progress=latest.work_area_progress if latest else 0,
        last_completed_area_utc=last_completed_area_utc,
        daily_mowing_minutes=days,
        current_activity=latest.activity if latest else "UNBEKANNT",
        current_state=latest.mower_state if latest else "UNBEKANNT",
        current_error_code=(latest.error_code if latest_error_active else 0),
        current_battery_percent=latest.battery_percent if latest else 0,
        hydrawise_available=latest.hydrawise_available if latest else False,
        hydrawise_fresh=latest.hydrawise_fresh if latest else False,
        hydrawise_active_zones=latest.hydrawise_active_zones if latest else 0,
        next_irrigation_start_utc=latest.next_irrigation_start_utc if latest else None,
        blocked_source=latest.blocked_source if latest else "",
        parking_source=latest.parking_source if latest else "",
        overall_status="Abweichung" if warnings else "planmäßig",
        warnings=tuple(warnings),
    )


def dashboard_statistics(
    now_utc: datetime,
    values: Mapping[str, str],
    *,
    query_client: ApplicationInsightsQueryClient | Any | None = None,
) -> dict[str, Any]:
    """Returns the established seven-day mowing metrics without sending mail."""

    local_now = now_utc.astimezone(REPORT_TIME_ZONE)
    period_start_local = datetime.combine(
        local_now.date() - timedelta(days=6), time.min, REPORT_TIME_ZONE
    )
    client = query_client or ApplicationInsightsQueryClient.from_environment(values)
    rows = client.execute(
        _cycle_query(period_start_local.astimezone(timezone.utc), now_utc),
        timespan="P8D",
    )
    summary = summarize_report(
        parse_cycle_rows(rows),
        now_utc=now_utc,
        exception_count_24h=0,
    )
    return {
        "available": True,
        "mowingMinutes7d": summary.mowing_minutes_7d,
        "averageDailyMowingMinutes7d": summary.average_daily_mowing_minutes_7d,
        "mowingMinutesToday": summary.mowing_minutes_today,
        "averageReturnMinutes7d": summary.average_return_minutes_7d,
        "completedAreaCycles7d": summary.completed_area_cycles_7d,
        "lastCompletedAreaUtc": (
            summary.last_completed_area_utc.isoformat()
            if summary.last_completed_area_utc is not None
            else None
        ),
    }


def _irrigation_cycle_query(
    period_start_utc: datetime,
    period_end_utc: datetime,
) -> str:
    start = period_start_utc.isoformat().replace("+00:00", "Z")
    end = period_end_utc.isoformat().replace("+00:00", "Z")
    return f"""
traces
| where timestamp between (datetime({start}) .. datetime({end}))
| where message startswith "SSV53_CONTROL_CYCLE "
| extend p=parse_json(replace_string(message, "SSV53_CONTROL_CYCLE ", ""))
| project timestamp,
    decision_code=tostring(p.decision_code),
    active_relay_ids=tostring(p.details.hydrawise.safety.active_relay_ids),
    irrigation_plan_id=tostring(p.details.automation_state.irrigation_plan_id),
    irrigation_phase=tostring(p.details.automation_state.irrigation_phase),
    irrigation_completed_utc=tostring(p.details.automation_state.irrigation_completed_utc),
    completed_relay_ids=tostring(p.details.automation_state.irrigation_completed_relay_ids),
    operator_request_id=tostring(p.details.automation_state.operator_request_id),
    operator_request_action=tostring(p.details.automation_state.operator_request_action),
    operator_request_status=tostring(p.details.automation_state.operator_request_status)
| order by timestamp asc
""".strip()


def _json_int_list(value: Any) -> tuple[int, ...]:
    if isinstance(value, list):
        raw = value
    else:
        try:
            raw = json.loads(str(value or "[]"))
        except (TypeError, ValueError, json.JSONDecodeError):
            return ()
    if not isinstance(raw, list):
        return ()
    values: list[int] = []
    for item in raw:
        try:
            values.append(int(item))
        except (TypeError, ValueError):
            continue
    return tuple(sorted(set(values)))


def summarize_irrigation_statistics(
    rows: Sequence[Mapping[str, Any]],
    *,
    expected_zone_count: int = 7,
    expected_relay_ids: frozenset[int] | None = None,
) -> dict[str, Any]:
    """Verdichtet reale Minutenbeobachtungen statt Soll-Laufzeiten.

    Application Insights kann durch parallele Function-Instanzen doppelte
    Zeilen für dieselbe Minute enthalten. Je Minute gewinnt deshalb die
    zeitlich letzte Beobachtung. Vollständige Läufe werden nur gezählt, wenn
    alle erwarteten Relais im persistenten Abschlussnachweis enthalten sind.
    """

    by_minute: dict[datetime, dict[str, Any]] = {}
    for row in rows:
        timestamp = _parse_utc(row.get("timestamp"))
        if timestamp is None:
            continue
        by_minute[timestamp.replace(second=0, microsecond=0)] = {
            "timestamp": timestamp,
            "decision": str(row.get("decision_code") or "").upper(),
            "active": _json_int_list(row.get("active_relay_ids")),
            "plan": str(row.get("irrigation_plan_id") or "").strip(),
            "phase": str(row.get("irrigation_phase") or "").strip().upper(),
            "completed_at": _parse_utc(row.get("irrigation_completed_utc")),
            "completed": _json_int_list(row.get("completed_relay_ids")),
            "request_id": str(row.get("operator_request_id") or "").strip(),
            "request_action": str(row.get("operator_request_action") or "").upper(),
            "request_status": str(row.get("operator_request_status") or "").upper(),
        }
    observations = [by_minute[key] for key in sorted(by_minute)]
    zone_minutes: dict[int, int] = defaultdict(int)
    plan_active_minutes: dict[str, int] = defaultdict(int)
    for item in observations:
        active = item["active"]
        if active:
            for relay_id in active:
                zone_minutes[relay_id] += 1
            if item["plan"]:
                # Parallelbetrieb ist verboten; dennoch zählt die Dauer des
                # Laufs nur einmal je beobachteter Minute.
                plan_active_minutes[item["plan"]] += 1

    completed_runs: dict[tuple[str, datetime], dict[str, Any]] = {}
    for item in observations:
        completed_at = item["completed_at"]
        if (
            completed_at is not None
            and item["plan"]
            and len(item["completed"]) >= expected_zone_count
        ):
            completed_runs[(item["plan"], completed_at)] = item
    completed_records = [
        {
            "plan": key[0],
            "completed_at": key[1],
            "duration_minutes": plan_active_minutes.get(key[0], 0),
            "observed_only": False,
        }
        for key in completed_runs
    ]

    # Auch ein direkt in Hydrawise gestarteter Lauf ist ein realer Lauf. Er
    # besitzt jedoch keinen von unserer Automatik erzeugten Planabschluss.
    # Deshalb wird zusätzlich die binäre Relay-Folge ausgewertet: exakt die
    # freigegebenen Relays, immer nur eine Zone gleichzeitig, jede Zone in
    # genau einem zusammenhängenden Abschnitt und anschließend mindestens
    # fünf Minuten frei. Das ist deutlich strenger als eine Sollzeit und
    # verhindert zugleich Doppelzählungen mit regulären Abschlüssen.
    observed_sequences: list[dict[str, Any]] = []
    sequence: dict[str, Any] | None = None
    last_active_at: datetime | None = None

    def finish_observed_sequence() -> None:
        nonlocal sequence, last_active_at
        if sequence is None or last_active_at is None:
            sequence = None
            last_active_at = None
            return
        relays = set(sequence["relays"])
        expected = (
            set(expected_relay_ids)
            if expected_relay_ids is not None
            else None
        )
        complete_set = (
            relays == expected
            if expected is not None
            else len(relays) == expected_zone_count
        )
        if (
            not sequence["invalid"]
            and complete_set
            and len(sequence["order"]) == expected_zone_count
        ):
            observed_sequences.append(
                {
                    **sequence,
                    "completed_at": last_active_at + timedelta(minutes=1),
                }
            )
        sequence = None
        last_active_at = None

    for item in observations:
        timestamp = item["timestamp"]
        active = item["active"]
        if active:
            if (
                sequence is not None
                and last_active_at is not None
                and timestamp - last_active_at > timedelta(minutes=5)
            ):
                finish_observed_sequence()
            if sequence is None:
                sequence = {
                    "started_at": timestamp,
                    "relays": set(),
                    "order": [],
                    "plans": set(),
                    "active_minutes": 0,
                    "invalid": False,
                }
            sequence["active_minutes"] += 1
            if item["plan"]:
                sequence["plans"].add(item["plan"])
            if len(active) != 1:
                sequence["invalid"] = True
            else:
                relay_id = int(active[0])
                if not sequence["order"] or sequence["order"][-1] != relay_id:
                    if relay_id in sequence["relays"]:
                        sequence["invalid"] = True
                    sequence["order"].append(relay_id)
                sequence["relays"].add(relay_id)
            last_active_at = timestamp
        elif (
            sequence is not None
            and last_active_at is not None
            and timestamp - last_active_at > timedelta(minutes=5)
        ):
            finish_observed_sequence()
    if (
        sequence is not None
        and last_active_at is not None
        and observations
        and observations[-1]["timestamp"] - last_active_at > timedelta(minutes=5)
    ):
        finish_observed_sequence()

    inferred_complete_plans: set[str] = set()
    for observed in observed_sequences:
        completed_at = observed["completed_at"]
        duplicate = any(
            abs((record["completed_at"] - completed_at).total_seconds()) <= 10 * 60
            for record in completed_records
        )
        inferred_complete_plans.update(observed["plans"])
        if duplicate:
            continue
        completed_records.append(
            {
                "plan": "",
                "completed_at": completed_at,
                "duration_minutes": int(observed["active_minutes"]),
                "observed_only": True,
            }
        )

    ordered_completed = sorted(
        completed_records,
        key=lambda value: value["completed_at"],
    )
    last_record = ordered_completed[-1] if ordered_completed else None

    # Unsichere oder nur teilweise ausgeführte Läufe werden nicht in die
    # normalen Statistik-Kacheln gemischt. Sie werden separat für ein
    # kontextbezogenes Hinweissymbol an der Beregnungskarte bereitgestellt.
    plan_evidence: dict[str, dict[str, Any]] = {}
    for item in observations:
        plan = item["plan"]
        if not plan:
            continue
        evidence = plan_evidence.setdefault(
            plan,
            {
                "confirmed": set(),
                "active": set(),
                "first": item["timestamp"],
                "last": item["timestamp"],
                "failed": False,
                "complete": False,
            },
        )
        evidence["confirmed"].update(item["completed"])
        evidence["active"].update(item["active"])
        evidence["last"] = item["timestamp"]
        evidence["failed"] = evidence["failed"] or item["phase"] == "FAILED" or item[
            "decision"
        ] in {
            "IRRIGATION_FAILED_HOLD",
            "IRRIGATION_ZONE_END_UNCLEAR",
            "IRRIGATION_RUN_CANCELLED_EARLY",
        }
        evidence["complete"] = evidence["complete"] or (
            item["completed_at"] is not None
            and len(item["completed"]) >= expected_zone_count
        )
    for plan in inferred_complete_plans:
        if plan in plan_evidence:
            plan_evidence[plan]["complete"] = True

    gaps: list[dict[str, Any]] = []
    for previous, current in zip(observations, observations[1:]):
        missing_minutes = int(
            (current["timestamp"] - previous["timestamp"]).total_seconds() // 60
        ) - 1
        if missing_minutes < 3:
            continue
        plan = previous["plan"] if previous["plan"] == current["plan"] else ""
        evidence = plan_evidence.get(plan) if plan else None
        if not evidence or evidence["complete"]:
            continue
        if not (evidence["confirmed"] or evidence["active"] or evidence["failed"]):
            continue
        gaps.append(
            {
                "planId": plan,
                "start": previous["timestamp"].isoformat(),
                "end": current["timestamp"].isoformat(),
                "missingMinutes": missing_minutes,
            }
        )

    affected_runs: list[dict[str, Any]] = []
    for plan, evidence in plan_evidence.items():
        confirmed = sorted(evidence["confirmed"] | evidence["active"])
        if evidence["complete"] or not evidence["failed"]:
            continue
        affected_runs.append(
            {
                "planId": plan,
                "observedAt": evidence["last"].isoformat(),
                "confirmedZoneCount": len(confirmed),
                "expectedZoneCount": expected_zone_count,
                "confirmedRelayIds": confirmed,
                "status": "incomplete",
            }
        )
    affected_runs.sort(key=lambda item: item["observedAt"], reverse=True)
    attention = None
    if affected_runs or gaps:
        latest = affected_runs[0] if affected_runs else None
        attention = {
            "severity": "warning",
            "title": "Beregnung nicht vollständig bestätigt"
            if latest
            else "Beregnungsdaten waren zeitweise unvollständig",
            "summary": (
                f"{latest['confirmedZoneCount']} von "
                f"{latest['expectedZoneCount']} Zonen bestätigt."
                if latest
                else "Für einen Beregnungszeitraum liegt eine Datenlücke vor."
            ),
            "affectedRuns": affected_runs[:3],
            "dataGaps": gaps[-3:],
        }

    updated_codes = {
        "IRRIGATION_PLAN_UPDATED",
        "IRRIGATION_REMAINING_DURATIONS_UPDATED",
    }
    cancelled_codes = {
        "IRRIGATION_PLAN_CANCELLED_OR_DEFERRED",
        "IRRIGATION_OPERATOR_CANCELLED_BEFORE_RUN",
        "IRRIGATION_OPERATOR_STOPPED_BETWEEN_ZONES",
        "IRRIGATION_OPERATOR_STOPPED_AFTER_ZONE",
        "IRRIGATION_OPERATOR_STOPPED_NOW",
    }
    updated = sum(1 for item in observations if item["decision"] in updated_codes)
    cancelled = sum(1 for item in observations if item["decision"] in cancelled_codes)
    manual_request_ids = {
        item["request_id"]
        for item in observations
        if item["request_id"]
        and item["request_action"] in {"START_IRRIGATION", "START_IRRIGATION_ZONE"}
        and item["request_status"] == "SUCCESS"
    }
    manual_started = len(manual_request_ids)
    return {
        "available": True,
        "wateringMinutes7d": sum(1 for item in observations if item["active"]),
        "completedRuns7d": len(ordered_completed),
        "lastCompletedAt": (
            last_record["completed_at"].isoformat() if last_record else None
        ),
        "lastCompletedDurationMinutes": (
            last_record["duration_minutes"] if last_record else None
        ),
        "zoneMinutes7d": [
            {"relayId": relay_id, "minutes": minutes}
            for relay_id, minutes in sorted(zone_minutes.items())
        ],
        "planChanges7d": updated + cancelled + manual_started,
        "planChangeBreakdown": {
            "updated": updated,
            "cancelled": cancelled,
            "manualStarted": manual_started,
        },
        "attention": attention,
    }


def dashboard_irrigation_statistics(
    now_utc: datetime,
    values: Mapping[str, str],
    *,
    query_client: ApplicationInsightsQueryClient | Any | None = None,
    journal_reader: Callable[..., Sequence[Mapping[str, Any]]] | None = None,
) -> dict[str, Any]:
    local_now = now_utc.astimezone(REPORT_TIME_ZONE)
    period_start_local = datetime.combine(
        local_now.date() - timedelta(days=6), time.min, REPORT_TIME_ZONE
    )
    rows: list[Mapping[str, Any]] = []
    insights_failed = False
    try:
        client = query_client or ApplicationInsightsQueryClient.from_environment(values)
        rows.extend(
            client.execute(
                _irrigation_cycle_query(
                    period_start_local.astimezone(timezone.utc), now_utc
                ),
                timespan="P8D",
            )
        )
    except Exception:
        insights_failed = True
    # Das Azure-Table-Journal ist die dauerhafte Quelle. Application Insights
    # bleibt als Rückwärtskompatibilität und zur Erkennung älterer Lücken
    # erhalten. Journalzeilen gewinnen bei identischer Minute.
    reader = journal_reader
    if reader is None and query_client is None:
        reader = read_irrigation_observations
    journal_failed = False
    if reader is not None:
        try:
            rows.extend(
                reader(
                    values,
                    period_start_local.astimezone(timezone.utc),
                    now_utc,
                )
            )
        except Exception:
            # Eine vorübergehende Journal-Lesestörung darf die vorhandene
            # bestätigte Auswertung nicht vollständig ausblenden.
            journal_failed = True
    if not rows and insights_failed and (reader is None or journal_failed):
        raise RuntimeError("Beregnungsnachweise sind derzeit nicht erreichbar.")
    try:
        expected_zone_count = int(values.get("HYDRAWISE_EXPECTED_ZONE_COUNT", "7"))
    except (TypeError, ValueError):
        expected_zone_count = 7
    expected_relay_ids: frozenset[int] | None = None
    try:
        parsed_relay_ids = frozenset(
            int(value.strip())
            for value in str(values.get("HYDRAWISE_EXPECTED_RELAY_IDS", "")).split(",")
            if value.strip()
        )
        if len(parsed_relay_ids) == expected_zone_count:
            expected_relay_ids = parsed_relay_ids
    except (TypeError, ValueError):
        expected_relay_ids = None
    result = summarize_irrigation_statistics(
        rows,
        expected_zone_count=expected_zone_count,
        expected_relay_ids=expected_relay_ids,
    )
    if journal_failed:
        attention = result.get("attention") or {
            "severity": "warning",
            "title": "Beregnungsnachweis vorübergehend eingeschränkt",
            "summary": "Das dauerhafte Beregnungsjournal konnte nicht gelesen werden.",
            "affectedRuns": [],
            "dataGaps": [],
        }
        attention["sourceIssue"] = (
            "Das dauerhafte Beregnungsjournal ist momentan nicht erreichbar. "
            "Vorhandene bestätigte Minutenwerte bleiben sichtbar."
        )
        result["attention"] = attention
    return result


def _minutes(value: int) -> str:
    hours, minutes = divmod(max(0, int(value)), 60)
    return f"{hours} Std. {minutes:02d} Min."


def _local_time(value: datetime | None) -> str:
    if value is None:
        return "Nicht geplant"
    return value.astimezone(REPORT_TIME_ZONE).strftime("%d.%m.%Y, %H:%M Uhr")


def _adaptive_status_text(value: str) -> str:
    return {
        "SHADOW_PLAN_READY": "Schattenplan bereit",
        "NO_SAFE_WINDOW": "Kein sicheres Zeitfenster gefunden",
        "NO_COMPLETE_IRRIGATION_PLAN": "Kein vollständiger Sieben-Zonen-Plan",
        "DISABLED": "Deaktiviert",
    }.get(str(value or "").upper(), value or "Noch kein Messwert")


def _recommendation_text(value: str) -> str:
    return {
        "KEEP_BASELINE": "Basisberegnung beibehalten",
        "REDUCE_RECOMMENDED": "Reduzierung empfohlen – nur Beobachtung",
        "SKIP_RECOMMENDED": "Auslassen empfohlen – nur Beobachtung",
    }.get(str(value or "").upper(), value or "Noch keine Empfehlung")


def _mower_status_text(state: str, activity: str) -> str:
    mower_state = str(state or "").upper()
    mower_activity = str(activity or "").upper()
    if mower_state == "PAUSED":
        return "Pausiert"
    if mower_state == "STOPPED" or mower_activity == "STOPPED_IN_GARDEN":
        return "Manuell gestoppt"
    if mower_state in {"ERROR", "FATAL_ERROR", "ERROR_AT_POWER_UP"}:
        return "Mäherfehler"
    return {
        "MOWING": "Mäht",
        "LEAVING": "Fährt auf den Platz",
        "GOING_HOME": "Fährt zur Station",
        "PARKED_IN_CS": "Geparkt",
        "CHARGING": "Lädt",
        "SEARCHING_FOR_CHARGING_STATION": "Sucht Ladestation",
    }.get(
        mower_activity,
        "Status wird geprüft" if mower_activity == "NOT_APPLICABLE" else mower_activity or "Unbekannt",
    )


def build_message(
    settings: OrderMailSettings,
    recipient: str,
    summary: DailyReportSummary,
    adaptive: AdaptiveShadowSummary | None = None,
) -> EmailMessage:
    warning_text = " · ".join(summary.warnings) if summary.warnings else "Keine Abweichung erkannt"
    daily_rows = "".join(
        f"<tr><td style='padding:7px 0;border-bottom:1px solid {APP_BORDER};color:{APP_MUTED};'>"
        f"{day.strftime('%a, %d.%m.')}</td><td align='right' style='padding:7px 0;"
        f"border-bottom:1px solid {APP_BORDER};font-weight:700;'>{html.escape(_minutes(minutes))}</td></tr>"
        for day, minutes in summary.daily_mowing_minutes
    )
    facts = [
        (
            "Aktueller Zustand",
            _mower_status_text(summary.current_state, summary.current_activity),
        ),
        ("Akku", f"{summary.current_battery_percent} %"),
        ("Fehlercode", str(summary.current_error_code or "kein Fehler")),
        ("Minutenzyklen 24 h", f"{summary.cycle_count_24h} / ca. {summary.expected_cycles_24h}"),
        ("Lücken über 90 Sek.", str(summary.gap_count_24h)),
        ("Ausnahmen 24 h", str(summary.exception_count_24h)),
        ("Befehle 24 h", str(summary.command_count_24h)),
        ("Mähzeit 7 Tage", _minutes(summary.mowing_minutes_7d)),
        ("Ø Mähzeit pro Tag", _minutes(summary.average_daily_mowing_minutes_7d)),
        ("Mähzeit heute", _minutes(summary.mowing_minutes_today)),
        (
            "Ø Heimfahrdauer 7 Tage",
            _minutes(summary.average_return_minutes_7d)
            if summary.average_return_minutes_7d is not None
            else "noch kein Messwert",
        ),
        ("Aktueller Flächenfortschritt", f"{summary.current_work_area_progress} %"),
        ("Bestätigte Abschlüsse (7 Tage)", f"{summary.completed_area_cycles_7d}"),
        ("Letzter bestätigter Abschluss", _local_time(summary.last_completed_area_utc)),
        (
            "Hydrawise",
            "frisch und erreichbar"
            if summary.hydrawise_available and summary.hydrawise_fresh
            else "nicht sicher verfügbar",
        ),
        ("Aktive Zonen", str(summary.hydrawise_active_zones)),
        ("Nächste Beregnung", _local_time(summary.next_irrigation_start_utc)),
        ("Aktuelle Sperre", summary.blocked_source or "keine"),
        ("Parkgrund", summary.parking_source or "keiner"),
    ]
    fact_rows = "".join(
        f"<tr><td style='width:45%;padding:10px 10px 10px 0;border-bottom:1px solid {APP_BORDER};"
        f"color:{APP_MUTED};font-size:13px;'>{html.escape(label)}</td>"
        f"<td style='padding:10px 0;border-bottom:1px solid {APP_BORDER};font-weight:700;'>"
        f"{html.escape(value)}</td></tr>"
        for label, value in facts
    )
    adaptive_facts: list[tuple[str, str]] = []
    if adaptive is not None:
        irrigation_window = (
            f"{_local_time(adaptive.irrigation_start_utc)} bis "
            f"{adaptive.irrigation_end_utc.astimezone(REPORT_TIME_ZONE).strftime('%H:%M Uhr')}"
            if adaptive.irrigation_start_utc is not None
            and adaptive.irrigation_end_utc is not None
            else "Noch nicht geplant"
        )
        validation_text = (
            f"{adaptive.rain_validation_samples} Stunden · Prognose "
            f"{adaptive.forecast_rain_mm:.1f} mm · später gemeldet "
            f"{adaptive.reported_rain_mm:.1f} mm · Ø Abweichung "
            f"{adaptive.mean_absolute_rain_error_mm:.2f} mm"
            if adaptive.rain_validation_samples
            else "Noch keine ausreichende Messbasis"
        )
        adaptive_facts = [
            (
                "Betriebsart",
                "Nur Beobachtung – keine Gerätebefehle"
                if not adaptive.execution_enabled
                else "Live-Ausführung",
            ),
            ("Planstatus", _adaptive_status_text(adaptive.latest_status)),
            ("Wasserempfehlung", _recommendation_text(adaptive.latest_recommendation)),
            ("Vorgeschlagenes Fenster", irrigation_window),
            ("Früheste Mähfreigabe", _local_time(adaptive.earliest_mow_resume_utc)),
            (
                "Möglicher Verlust trockener Mähzeit",
                _minutes(adaptive.lost_dry_mowing_minutes),
            ),
            (
                "Zusätzliche Trocknung",
                f"{adaptive.drying_extension_minutes} Min.",
            ),
            (
                "Wetterdaten 24 h",
                f"{adaptive.weather_fresh_percent_24h} % frisch · "
                f"{adaptive.weather_provider or 'Provider nicht gemeldet'}",
            ),
            ("Planänderungen 24 h", str(adaptive.plan_changes_24h)),
            (
                "Prognosearchiv",
                f"{adaptive.archived_snapshots} Versionen"
                if adaptive.archive_available
                else "vorübergehend nicht lesbar",
            ),
            ("Prognosevergleich", validation_text),
            (
                "Heimfahrten 7 Tage",
                (
                    f"{summary.return_measurements_7d} Messungen · Median "
                    f"{summary.median_return_minutes_7d} Min. · P95 "
                    f"{summary.p95_return_minutes_7d} Min."
                    if summary.return_measurements_7d
                    else "Noch keine vollständige Messung"
                ),
            ),
        ]
    adaptive_rows = "".join(
        f"<tr><td style='width:45%;padding:10px 10px 10px 0;border-bottom:1px solid {APP_BORDER};"
        f"color:{APP_MUTED};font-size:13px;'>{html.escape(label)}</td>"
        f"<td style='padding:10px 0;border-bottom:1px solid {APP_BORDER};font-weight:700;'>"
        f"{html.escape(value)}</td></tr>"
        for label, value in adaptive_facts
    )
    adaptive_plain = (
        ["", "Adaptive Planung – Schattenbetrieb:"]
        + [f"{label}: {value}" for label, value in adaptive_facts]
        + [
            "Hinweis: Der später von Open-Meteo gemeldete Niederschlag ist kein Messwert "
            "eines Regenmessers direkt auf dem Sportplatz."
        ]
        if adaptive_facts
        else []
    )
    adaptive_html = (
        f"<tr><td style='padding-top:25px;font-size:13px;font-weight:800;color:#285EA7;'>"
        f"ADAPTIVE PLANUNG – SCHATTENBETRIEB</td></tr>"
        f"<tr><td><table role='presentation' width='100%' cellspacing='0' cellpadding='0' "
        f"style='border-collapse:collapse;'>{adaptive_rows}</table></td></tr>"
        f"<tr><td style='padding-top:12px;color:{APP_MUTED};font-size:12px;line-height:1.55;'>"
        "Der später von Open-Meteo gemeldete Niederschlag ist kein Messwert eines "
        "Regenmessers direkt auf dem Sportplatz.</td></tr>"
        if adaptive_rows
        else ""
    )
    status_color = "#B42318" if summary.warnings else "#157347"
    subject = f"SSV53 Sicherheitsbericht: {summary.overall_status} – {summary.report_date.strftime('%d.%m.%Y')}"
    plain = "\n".join(
        [
            "SSV53 Sicherheitsbericht",
            f"Gesamturteil: {summary.overall_status}",
            f"Hinweise: {warning_text}",
            "",
            *[f"{label}: {value}" for label, value in facts],
            *adaptive_plain,
            "",
            "Mähzeiten der letzten sieben Tage:",
            *[f"{day.strftime('%d.%m.%Y')}: {_minutes(minutes)}" for day, minutes in summary.daily_mowing_minutes],
            "",
            "Bestätigte Flächenabschlüsse werden ausschließlich aus dem Husqvarna-EPOS-Feld "
            "lastTimeCompleted ermittelt. Mähzeit und Heimfahrten gelten nicht als Abschluss.",
        ]
    )
    html_body = f"""<!doctype html>
<html lang="de"><body style="margin:0;background:#fff;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Arial,sans-serif;color:#171717;">
<table role="presentation" width="100%" cellspacing="0" cellpadding="0"><tr><td align="center" style="padding:28px 16px;">
<table role="presentation" width="100%" cellspacing="0" cellpadding="0" style="max-width:580px;">
<tr><td align="center"><img src="{APP_LOGO_URL}" alt="SSV53" width="76" style="display:block;width:76px;height:auto;"></td></tr>
<tr><td align="center" style="padding-top:15px;font-size:30px;font-weight:800;">Sicherheitsbericht</td></tr>
<tr><td style="padding:14px 55px 22px;"><div style="height:2px;background:{APP_GOLD};"></div></td></tr>
<tr><td style="padding:0 4px 18px;"><div style="border-left:4px solid {status_color};background:#F7F9FC;padding:14px 16px;">
<div style="font-size:12px;font-weight:800;color:{status_color};text-transform:uppercase;letter-spacing:.5px;">{html.escape(summary.overall_status)}</div>
<div style="padding-top:5px;line-height:1.5;">{html.escape(warning_text)}</div></div></td></tr>
<tr><td><table role="presentation" width="100%" cellspacing="0" cellpadding="0" style="border-collapse:collapse;">{fact_rows}</table></td></tr>
{adaptive_html}
<tr><td style="padding-top:25px;font-size:13px;font-weight:800;color:#285EA7;">MÄHZEITEN – LETZTE 7 TAGE</td></tr>
<tr><td><table role="presentation" width="100%" cellspacing="0" cellpadding="0" style="border-collapse:collapse;">{daily_rows}</table></td></tr>
<tr><td style="padding-top:22px;color:{APP_MUTED};font-size:12px;line-height:1.55;">Bestätigte Flächenabschlüsse werden ausschließlich aus dem Husqvarna-EPOS-Feld lastTimeCompleted ermittelt. Mähzeit, Heimfahrten und automatische Neustarts gelten nicht als Abschluss.</td></tr>
<tr><td style="padding-top:24px;color:{APP_MUTED};font-size:12px;">Automatischer, ausschließlich lesender Bericht aus Azure · Schönwalder SV 1953 e.V.</td></tr>
</table></td></tr></table></body></html>"""
    message = EmailMessage()
    message["Subject"] = subject
    message["From"] = formataddr((settings.from_name, settings.from_address))
    message["To"] = recipient
    message.set_content(plain)
    message.add_alternative(html_body, subtype="html")
    return message


MailSender = Callable[[OrderMailSettings, EmailMessage], None]


def _send_message(settings: OrderMailSettings, message: EmailMessage) -> None:
    with _open_authenticated_smtp(settings) as smtp:
        smtp.send_message(message)


def process_daily_report(
    now_utc: datetime,
    values: Mapping[str, str],
    *,
    query_client: ApplicationInsightsQueryClient | Any | None = None,
    store: AzureDailyReportStore | Any | None = None,
    mail_sender: MailSender = _send_message,
    forecast_reader: Callable[
        [Mapping[str, str], datetime, datetime], Sequence[WeatherSnapshot]
    ] = read_archived_weather_snapshots,
) -> dict[str, Any]:
    if not enabled(values):
        return {"enabled": False, "sent": False, "reason": "disabled"}
    if not should_attempt_delivery(now_utc):
        return {"enabled": True, "sent": False, "reason": "outside_delivery_window"}
    recipient = report_recipient(values)
    settings = OrderMailSettings.from_mapping(values)
    local_now = now_utc.astimezone(REPORT_TIME_ZONE)
    directory = store or AzureDailyReportStore.from_environment(values)
    if not directory.claim(local_now.date(), now_utc):
        return {"enabled": True, "sent": False, "reason": "already_claimed"}
    try:
        client = query_client or ApplicationInsightsQueryClient.from_environment(values)
        start_local = datetime.combine(
            local_now.date() - timedelta(days=6), time.min, REPORT_TIME_ZONE
        )
        cycle_rows = client.execute(
            _cycle_query(start_local.astimezone(timezone.utc), now_utc),
            timespan="P8D",
        )
        exception_rows = client.execute(
            _exception_query(now_utc - timedelta(hours=24), now_utc),
            timespan="P2D",
        )
        exception_count = _as_int(
            exception_rows[0].get("exception_count") if exception_rows else 0
        )
        observations = parse_cycle_rows(cycle_rows)
        summary = summarize_report(
            observations,
            now_utc=now_utc,
            exception_count_24h=exception_count,
        )
        archive_available = True
        try:
            archived_snapshots = list(
                forecast_reader(
                    values,
                    now_utc - timedelta(days=8),
                    now_utc,
                )
            )
        except Exception:
            # Das Prognosearchiv ist reine Beobachtung. Sein Ausfall darf den
            # Sicherheitsbericht nicht verhindern und niemals Geräteaktionen
            # beeinflussen.
            archive_available = False
            archived_snapshots = []
        adaptive = summarize_adaptive_shadow(
            observations,
            now_utc=now_utc,
            archived_snapshots=archived_snapshots,
            archive_available=archive_available,
        )
        mail_sender(
            settings,
            build_message(settings, recipient, summary, adaptive),
        )
        directory.mark(local_now.date(), "sent", now_utc)
        return {
            "enabled": True,
            "sent": True,
            "status": summary.overall_status,
            "cycles_24h": summary.cycle_count_24h,
            "mowing_minutes_7d": summary.mowing_minutes_7d,
            "completed_area_cycles_7d": summary.completed_area_cycles_7d,
            "adaptive_status": adaptive.latest_status,
            "adaptive_execution_enabled": adaptive.execution_enabled,
            "weather_fresh_percent_24h": adaptive.weather_fresh_percent_24h,
            "forecast_validation_samples": adaptive.rain_validation_samples,
        }
    except Exception:
        directory.mark(local_now.date(), "failed", now_utc)
        raise
