from __future__ import annotations

import base64
import gzip
import json
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Mapping, Protocol, Sequence

from azure.core import MatchConditions
from azure.core.exceptions import HttpResponseError, ResourceExistsError, ResourceNotFoundError
from azure.data.tables import TableClient, UpdateMode
from azure.identity import ManagedIdentityCredential

from mower.weather import WeatherSnapshot


FORECAST_ARCHIVE_PARTITION = "ssv53-weather-forecast-v1"
FORECAST_ARCHIVE_DAYS = 21


def _archive_row_key(fetched_at: datetime) -> str:
    """Maps an hourly fetch into a bounded 21-day ring buffer."""

    value = fetched_at.astimezone(timezone.utc)
    return f"slot-{value.date().toordinal() % FORECAST_ARCHIVE_DAYS:02d}-{value.hour:02d}"


def _encode_snapshot(snapshot: WeatherSnapshot) -> str:
    raw = json.dumps(
        snapshot.to_dict(), ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return base64.b64encode(gzip.compress(raw, compresslevel=6)).decode("ascii")


def _decode_snapshot(value: Any) -> WeatherSnapshot:
    encoded = str(value or "").strip()
    if not encoded:
        raise RuntimeError("Archivierter Wetter-Snapshot fehlt.")
    try:
        raw = gzip.decompress(base64.b64decode(encoded, validate=True))
        payload = json.loads(raw.decode("utf-8"))
    except (ValueError, OSError, json.JSONDecodeError) as exc:
        raise RuntimeError("Archivierter Wetter-Snapshot ist beschädigt.") from exc
    if not isinstance(payload, dict):
        raise RuntimeError("Archivierter Wetter-Snapshot hat ein ungültiges Format.")
    return WeatherSnapshot.from_mapping(payload)


def forecast_archive_entity(snapshot: WeatherSnapshot) -> dict[str, Any]:
    fetched = snapshot.fetched_at.astimezone(timezone.utc)
    return {
        "PartitionKey": FORECAST_ARCHIVE_PARTITION,
        "RowKey": _archive_row_key(fetched),
        "FetchedAtUtc": fetched.isoformat(),
        "Provider": snapshot.provider,
        "SnapshotGzipBase64": _encode_snapshot(snapshot),
        "SchemaVersion": 1,
    }


class WeatherStore(Protocol):
    def load_latest(self) -> WeatherSnapshot | None:
        ...

    def reserve_fetch(
        self,
        *,
        now_utc: datetime,
        minimum_interval_minutes: int,
        daily_limit: int,
        monthly_limit: int,
    ) -> tuple[bool, str, dict[str, int | str]]:
        ...

    def save_latest(self, snapshot: WeatherSnapshot) -> None:
        ...


@dataclass
class InMemoryWeatherStore:
    latest: WeatherSnapshot | None = None
    month_key: str | None = None
    month_count: int = 0
    day_key: str | None = None
    day_count: int = 0
    last_reserved_utc: datetime | None = None
    archive: dict[str, WeatherSnapshot] = field(default_factory=dict)

    def load_latest(self) -> WeatherSnapshot | None:
        return self.latest

    def reserve_fetch(
        self,
        *,
        now_utc: datetime,
        minimum_interval_minutes: int,
        daily_limit: int,
        monthly_limit: int,
    ) -> tuple[bool, str, dict[str, int | str]]:
        now = now_utc.astimezone(timezone.utc)
        month_key = now.strftime("%Y-%m")
        day_key = now.strftime("%Y-%m-%d")
        if self.month_key != month_key:
            self.month_key, self.month_count = month_key, 0
        if self.day_key != day_key:
            self.day_key, self.day_count = day_key, 0
        counters: dict[str, int | str] = {
            "month": month_key,
            "month_count": self.month_count,
            "day": day_key,
            "day_count": self.day_count,
        }
        if self.month_count >= monthly_limit:
            return False, "MONTHLY_FREE_BUDGET_EXHAUSTED", counters
        if self.day_count >= daily_limit:
            return False, "DAILY_FREE_BUDGET_EXHAUSTED", counters
        if self.last_reserved_utc is not None and now - self.last_reserved_utc < timedelta(
            minutes=minimum_interval_minutes
        ):
            return False, "MINIMUM_INTERVAL_ACTIVE", counters
        self.month_count += 1
        self.day_count += 1
        self.last_reserved_utc = now
        counters.update(month_count=self.month_count, day_count=self.day_count)
        return True, "RESERVED", counters

    def save_latest(self, snapshot: WeatherSnapshot) -> None:
        self.latest = snapshot
        self.archive[_archive_row_key(snapshot.fetched_at)] = snapshot


class AzureTableWeatherStore:
    PARTITION_KEY = "ssv53-weather"
    LATEST_ROW_KEY = "latest"

    def __init__(self, table_client: TableClient) -> None:
        self._table_client = table_client

    @classmethod
    def from_environment(
        cls,
        values: Mapping[str, str],
        *,
        credential_factory=ManagedIdentityCredential,
        table_client_factory=TableClient,
    ) -> "AzureTableWeatherStore":
        endpoint = str(values.get("SSV53_STORAGE_ACCOUNT_URL", "")).strip()
        table_name = str(values.get("SSV53_STATE_TABLE_NAME", "")).strip()
        client_id = str(
            values.get("SSV53_STATE_MANAGED_IDENTITY_CLIENT_ID")
            or values.get("AzureWebJobsStorage__clientId")
            or ""
        ).strip()
        if not endpoint or not table_name or not client_id:
            raise RuntimeError("Azure-Wettercache ist unvollständig konfiguriert.")
        credential = credential_factory(client_id=client_id)
        client = table_client_factory(
            endpoint=endpoint,
            table_name=table_name,
            credential=credential,
        )
        return cls(client)

    def load_latest(self) -> WeatherSnapshot | None:
        try:
            entity = self._table_client.get_entity(
                partition_key=self.PARTITION_KEY,
                row_key=self.LATEST_ROW_KEY,
                timeout=5,
            )
        except ResourceNotFoundError:
            return None
        payload = json.loads(str(entity.get("SnapshotJson") or "{}"))
        if not isinstance(payload, dict):
            return None
        return WeatherSnapshot.from_mapping(payload)

    def reserve_fetch(
        self,
        *,
        now_utc: datetime,
        minimum_interval_minutes: int,
        daily_limit: int,
        monthly_limit: int,
    ) -> tuple[bool, str, dict[str, int | str]]:
        now = now_utc.astimezone(timezone.utc)
        month_key = now.strftime("%Y-%m")
        day_key = now.strftime("%Y-%m-%d")
        row_key = f"budget-{month_key}"
        for _attempt in range(4):
            try:
                entity = self._table_client.get_entity(
                    partition_key=self.PARTITION_KEY,
                    row_key=row_key,
                    timeout=5,
                )
            except ResourceNotFoundError:
                created = {
                    "PartitionKey": self.PARTITION_KEY,
                    "RowKey": row_key,
                    "MonthKey": month_key,
                    "MonthCount": 1,
                    "DayKey": day_key,
                    "DayCount": 1,
                    "LastReservedUtc": now.isoformat(),
                }
                try:
                    self._table_client.create_entity(entity=created, timeout=5)
                    return True, "RESERVED", {
                        "month": month_key,
                        "month_count": 1,
                        "day": day_key,
                        "day_count": 1,
                    }
                except ResourceExistsError:
                    continue

            month_count = int(entity.get("MonthCount") or 0)
            stored_day = str(entity.get("DayKey") or "")
            day_count = int(entity.get("DayCount") or 0) if stored_day == day_key else 0
            counters: dict[str, int | str] = {
                "month": month_key,
                "month_count": month_count,
                "day": day_key,
                "day_count": day_count,
            }
            if month_count >= monthly_limit:
                return False, "MONTHLY_FREE_BUDGET_EXHAUSTED", counters
            if day_count >= daily_limit:
                return False, "DAILY_FREE_BUDGET_EXHAUSTED", counters
            last_text = str(entity.get("LastReservedUtc") or "").strip()
            if last_text:
                last = datetime.fromisoformat(last_text.replace("Z", "+00:00")).astimezone(
                    timezone.utc
                )
                if now - last < timedelta(minutes=minimum_interval_minutes):
                    return False, "MINIMUM_INTERVAL_ACTIVE", counters
            metadata = getattr(entity, "metadata", {}) or {}
            etag = str(metadata.get("etag") or "").strip()
            if not etag:
                raise RuntimeError("ETag des Wetterbudgets fehlt.")
            updated = {
                "PartitionKey": self.PARTITION_KEY,
                "RowKey": row_key,
                "MonthKey": month_key,
                "MonthCount": month_count + 1,
                "DayKey": day_key,
                "DayCount": day_count + 1,
                "LastReservedUtc": now.isoformat(),
            }
            try:
                self._table_client.update_entity(
                    entity=updated,
                    mode=UpdateMode.REPLACE,
                    etag=etag,
                    match_condition=MatchConditions.IfNotModified,
                    timeout=5,
                )
                counters.update(
                    month_count=month_count + 1,
                    day_count=day_count + 1,
                )
                return True, "RESERVED", counters
            except HttpResponseError as exc:
                if getattr(exc, "status_code", None) in {409, 412}:
                    continue
                raise
        return False, "BUDGET_RESERVATION_CONFLICT", {
            "month": month_key,
            "month_count": 0,
            "day": day_key,
            "day_count": 0,
        }

    def save_latest(self, snapshot: WeatherSnapshot) -> None:
        # Der Ringpuffer ist absichtlich auf 21 * 24 Einträge begrenzt. Ein
        # Slot wird erst drei Wochen später überschrieben; es gibt weder
        # ungebremstes Tabellenwachstum noch einen löschenden Cleanup-Job.
        self._table_client.upsert_entity(
            entity=forecast_archive_entity(snapshot),
            mode=UpdateMode.REPLACE,
            timeout=5,
        )
        self._table_client.upsert_entity(
            entity={
                "PartitionKey": self.PARTITION_KEY,
                "RowKey": self.LATEST_ROW_KEY,
                "FetchedAtUtc": snapshot.fetched_at_utc,
                "Provider": snapshot.provider,
                "SnapshotJson": json.dumps(
                    snapshot.to_dict(), ensure_ascii=False, sort_keys=True
                ),
            },
            mode=UpdateMode.REPLACE,
            timeout=5,
        )


def read_archived_weather_snapshots(
    values: Mapping[str, str],
    period_start_utc: datetime,
    period_end_utc: datetime,
    *,
    table_client: TableClient | Any | None = None,
) -> list[WeatherSnapshot]:
    """Reads the bounded forecast history and rejects stale ring-buffer slots."""

    start = period_start_utc.astimezone(timezone.utc)
    end = period_end_utc.astimezone(timezone.utc)
    if end < start:
        raise ValueError("Das Ende des Wetterzeitraums liegt vor dem Anfang.")
    if end - start > timedelta(days=FORECAST_ARCHIVE_DAYS):
        start = end - timedelta(days=FORECAST_ARCHIVE_DAYS)
    client = table_client or AzureTableWeatherStore.from_environment(values)._table_client
    snapshots: dict[datetime, WeatherSnapshot] = {}
    for entity in client.query_entities(
        query_filter="PartitionKey eq @partition",
        parameters={"partition": FORECAST_ARCHIVE_PARTITION},
    ):
        try:
            fetched = datetime.fromisoformat(
                str(entity.get("FetchedAtUtc") or "").replace("Z", "+00:00")
            )
            if fetched.tzinfo is None or fetched.utcoffset() is None:
                continue
            fetched = fetched.astimezone(timezone.utc)
            if not start <= fetched <= end:
                continue
            snapshot = _decode_snapshot(entity.get("SnapshotGzipBase64"))
        except (TypeError, ValueError, RuntimeError):
            continue
        if snapshot.fetched_at != fetched:
            continue
        snapshots[fetched] = snapshot
    return [snapshots[key] for key in sorted(snapshots)]


def forecast_rain_validation(
    snapshots: Sequence[WeatherSnapshot],
    *,
    period_start_utc: datetime,
    period_end_utc: datetime,
    minimum_forecast_lead_hours: int = 3,
) -> dict[str, float | int]:
    """Compares an earlier forecast with a later provider report.

    The later value is deliberately called *reported*, not measured: without
    an on-site rain gauge Open-Meteo cannot prove the amount on the pitch.
    """

    start = period_start_utc.astimezone(timezone.utc).replace(
        minute=0, second=0, microsecond=0
    )
    end = period_end_utc.astimezone(timezone.utc).replace(
        minute=0, second=0, microsecond=0
    )
    ordered = sorted(snapshots, key=lambda item: item.fetched_at)
    errors: list[float] = []
    forecast_total = 0.0
    reported_total = 0.0
    target = start
    while target < end:
        forecasts = [
            item
            for item in ordered
            if target - timedelta(hours=24)
            <= item.fetched_at
            <= target - timedelta(hours=minimum_forecast_lead_hours)
        ]
        reports = [
            item
            for item in ordered
            if target + timedelta(hours=1)
            <= item.fetched_at
            <= target + timedelta(hours=6)
        ]
        if forecasts and reports:
            forecast_point = next(
                (point for point in forecasts[-1].points if point.timestamp == target),
                None,
            )
            reported_point = next(
                (point for point in reports[-1].points if point.timestamp == target),
                None,
            )
            if forecast_point is not None and reported_point is not None:
                forecast_value = max(
                    0.0,
                    float(forecast_point.precipitation_mm or forecast_point.rain_mm or 0.0),
                )
                reported_value = max(
                    0.0,
                    float(reported_point.precipitation_mm or reported_point.rain_mm or 0.0),
                )
                forecast_total += forecast_value
                reported_total += reported_value
                errors.append(abs(forecast_value - reported_value))
        target += timedelta(hours=1)
    return {
        "sample_count": len(errors),
        "forecast_total_mm": round(forecast_total, 2),
        "reported_total_mm": round(reported_total, 2),
        "mean_absolute_error_mm": round(sum(errors) / len(errors), 2) if errors else 0.0,
        "maximum_error_mm": round(max(errors), 2) if errors else 0.0,
    }
