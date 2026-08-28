from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Mapping, Protocol

from azure.core import MatchConditions
from azure.core.exceptions import HttpResponseError, ResourceExistsError, ResourceNotFoundError
from azure.data.tables import TableClient, UpdateMode
from azure.identity import ManagedIdentityCredential

from mower.weather import WeatherSnapshot


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
