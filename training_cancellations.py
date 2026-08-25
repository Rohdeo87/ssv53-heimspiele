from __future__ import annotations

import hashlib
import uuid
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from typing import Any, Mapping, Protocol

from azure.core import MatchConditions
from azure.data.tables import TableClient, UpdateMode
from azure.identity import ManagedIdentityCredential
from azure.core.exceptions import ResourceModifiedError, ResourceNotFoundError


PARTITION_KEY = "training-cancellations"
DEFAULT_CANCELLATION_RETENTION_DAYS = 90
DEFAULT_AUDIT_RETENTION_DAYS = 180


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("Zeitpunkt muss eine Zeitzone enthalten.")
    return value.astimezone(timezone.utc)


def _row_key(event_id: str) -> str:
    return "current-" + hashlib.sha256(event_id.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class TrainingCancellation:
    event_id: str
    season: str
    schedule_id: str
    day: date
    team: str
    resource_id: str
    start: datetime
    end: datetime
    cancelled_at_utc: datetime
    release_not_before_utc: datetime

    @property
    def occurrence_key(self) -> tuple[str, str]:
        return self.schedule_id, self.day.isoformat()

    def is_effective(self, now_utc: datetime) -> bool:
        return _utc(now_utc) >= self.release_not_before_utc

    def to_entity(self) -> dict[str, Any]:
        return {
            "PartitionKey": PARTITION_KEY,
            "RowKey": _row_key(self.event_id),
            "Kind": "current",
            "EventId": self.event_id,
            "Season": self.season,
            "ScheduleId": self.schedule_id,
            "Day": self.day.isoformat(),
            "Team": self.team,
            "ResourceId": self.resource_id,
            "Start": self.start.isoformat(),
            "End": self.end.isoformat(),
            "CancelledAtUtc": self.cancelled_at_utc.isoformat(),
            "ReleaseNotBeforeUtc": self.release_not_before_utc.isoformat(),
            "Active": True,
        }

    @classmethod
    def from_entity(cls, entity: Mapping[str, Any]) -> "TrainingCancellation":
        return cls(
            event_id=str(entity["EventId"]),
            season=str(entity["Season"]),
            schedule_id=str(entity["ScheduleId"]),
            day=date.fromisoformat(str(entity["Day"])),
            team=str(entity.get("Team", "Training")),
            resource_id=str(entity.get("ResourceId", "")),
            start=datetime.fromisoformat(str(entity["Start"])),
            end=datetime.fromisoformat(str(entity["End"])),
            cancelled_at_utc=datetime.fromisoformat(
                str(entity["CancelledAtUtc"])
            ).astimezone(timezone.utc),
            release_not_before_utc=datetime.fromisoformat(
                str(entity["ReleaseNotBeforeUtc"])
            ).astimezone(timezone.utc),
        )


class CancellationStore(Protocol):
    def list_active(self, start_day: date, end_day: date) -> list[TrainingCancellation]: ...

    def cancel(
        self,
        occurrence: Mapping[str, Any],
        *,
        now_utc: datetime,
        release_delay_minutes: int,
    ) -> TrainingCancellation: ...

    def restore(self, event_id: str, *, now_utc: datetime) -> bool: ...


def cancellation_from_occurrence(
    occurrence: Mapping[str, Any],
    *,
    now_utc: datetime,
    release_delay_minutes: int,
) -> TrainingCancellation:
    start = datetime.fromisoformat(str(occurrence["start"]))
    end = datetime.fromisoformat(str(occurrence["end"]))
    cancelled_at = _utc(now_utc)
    return TrainingCancellation(
        event_id=str(occurrence["id"]),
        season=str(occurrence["season"]),
        schedule_id=str(occurrence["scheduleId"]),
        day=start.date(),
        team=str(occurrence.get("team", "Training")),
        resource_id=str(occurrence["resourceId"]),
        start=start,
        end=end,
        cancelled_at_utc=cancelled_at,
        release_not_before_utc=cancelled_at
        + timedelta(minutes=max(0, release_delay_minutes)),
    )


class InMemoryCancellationStore:
    def __init__(self) -> None:
        self.items: dict[str, TrainingCancellation] = {}
        self.audit: list[dict[str, Any]] = []

    def list_active(self, start_day: date, end_day: date) -> list[TrainingCancellation]:
        return sorted(
            (
                item
                for item in self.items.values()
                if start_day <= item.day <= end_day
            ),
            key=lambda item: (item.start, item.event_id),
        )

    def cancel(
        self,
        occurrence: Mapping[str, Any],
        *,
        now_utc: datetime,
        release_delay_minutes: int,
    ) -> TrainingCancellation:
        event_id = str(occurrence["id"])
        existing = self.items.get(event_id)
        if existing is not None:
            return existing
        item = cancellation_from_occurrence(
            occurrence,
            now_utc=now_utc,
            release_delay_minutes=release_delay_minutes,
        )
        self.items[event_id] = item
        self.audit.append({"action": "cancel", "event_id": event_id, "at": _utc(now_utc)})
        return item

    def restore(self, event_id: str, *, now_utc: datetime) -> bool:
        existed = self.items.pop(event_id, None) is not None
        if existed:
            self.audit.append({"action": "restore", "event_id": event_id, "at": _utc(now_utc)})
        return existed


class AzureTableCancellationStore:
    def __init__(self, table_client: TableClient) -> None:
        self._table_client = table_client

    @classmethod
    def from_environment(
        cls,
        values: Mapping[str, str],
        *,
        credential_factory=ManagedIdentityCredential,
        table_client_factory=TableClient,
    ) -> "AzureTableCancellationStore":
        endpoint = str(values.get("SSV53_STORAGE_ACCOUNT_URL", "")).strip()
        table_name = str(values.get("SSV53_STATE_TABLE_NAME", "")).strip()
        client_id = str(
            values.get("SSV53_STATE_MANAGED_IDENTITY_CLIENT_ID")
            or values.get("AzureWebJobsStorage__clientId")
            or ""
        ).strip()
        if not endpoint or not table_name or not client_id:
            raise RuntimeError("Azure-Store für Trainingsabsagen ist unvollständig konfiguriert.")
        credential = credential_factory(client_id=client_id)
        return cls(
            table_client_factory(
                endpoint=endpoint,
                table_name=table_name,
                credential=credential,
            )
        )

    def list_active(self, start_day: date, end_day: date) -> list[TrainingCancellation]:
        query = "PartitionKey eq @partition and Kind eq @kind and Active eq true"
        entities = self._table_client.query_entities(
            query_filter=query,
            parameters={"partition": PARTITION_KEY, "kind": "current"},
        )
        result = []
        for entity in entities:
            day = date.fromisoformat(str(entity["Day"]))
            if start_day <= day <= end_day:
                result.append(TrainingCancellation.from_entity(entity))
        return sorted(result, key=lambda item: (item.start, item.event_id))

    @staticmethod
    def _audit_entity(action: str, event_id: str, now_utc: datetime) -> dict[str, Any]:
        instant = _utc(now_utc)
        return {
            "PartitionKey": PARTITION_KEY,
            "RowKey": f"audit-{instant.strftime('%Y%m%dT%H%M%S%fZ')}-{uuid.uuid4().hex}",
            "Kind": "audit",
            "Action": action,
            "EventId": event_id,
            "AtUtc": instant.isoformat(),
        }

    def cancel(
        self,
        occurrence: Mapping[str, Any],
        *,
        now_utc: datetime,
        release_delay_minutes: int,
    ) -> TrainingCancellation:
        try:
            existing = self._table_client.get_entity(
                PARTITION_KEY,
                _row_key(str(occurrence["id"])),
            )
            if bool(existing.get("Active")):
                return TrainingCancellation.from_entity(existing)
        except ResourceNotFoundError:
            pass
        item = cancellation_from_occurrence(
            occurrence,
            now_utc=now_utc,
            release_delay_minutes=release_delay_minutes,
        )
        self._table_client.submit_transaction(
            [
                ("upsert", item.to_entity(), {"mode": UpdateMode.REPLACE}),
                ("create", self._audit_entity("cancel", item.event_id, now_utc)),
            ]
        )
        return item

    def restore(self, event_id: str, *, now_utc: datetime) -> bool:
        key = _row_key(event_id)
        try:
            entity = self._table_client.get_entity(PARTITION_KEY, key)
        except ResourceNotFoundError:
            return False
        if not bool(entity.get("Active")):
            return False
        entity = dict(entity)
        entity["Active"] = False
        entity["RestoredAtUtc"] = _utc(now_utc).isoformat()
        self._table_client.submit_transaction(
            [
                ("update", entity, {"mode": UpdateMode.REPLACE}),
                ("create", self._audit_entity("restore", event_id, now_utc)),
            ]
        )
        return True

    def cleanup_retention(
        self,
        *,
        now_utc: datetime,
        cancellation_retention_days: int = DEFAULT_CANCELLATION_RETENTION_DAYS,
        audit_retention_days: int = DEFAULT_AUDIT_RETENTION_DAYS,
    ) -> dict[str, int]:
        """Bereinigt nur eindeutig abgelaufene Absagen und Auditmetadaten."""

        now = _utc(now_utc)
        cancellation_cutoff = now.date() - timedelta(
            days=max(1, cancellation_retention_days)
        )
        audit_cutoff = now - timedelta(days=max(1, audit_retention_days))
        result = {"deleted": 0, "skipped": 0}
        entities = self._table_client.query_entities(
            query_filter="PartitionKey eq @partition",
            parameters={"partition": PARTITION_KEY},
        )
        for raw_entity in entities:
            entity = dict(raw_entity)
            concurrency = _entity_concurrency(raw_entity)
            row_key = str(entity.get("RowKey") or "")
            kind = str(entity.get("Kind") or "").strip().lower()
            try:
                if kind == "current":
                    delete = date.fromisoformat(str(entity["Day"])) < cancellation_cutoff
                elif kind == "audit":
                    delete = _entity_datetime(entity, "AtUtc") < audit_cutoff
                else:
                    result["skipped"] += 1
                    continue
                if delete:
                    if concurrency is None:
                        result["skipped"] += 1
                        continue
                    self._table_client.delete_entity(
                        partition_key=PARTITION_KEY,
                        row_key=row_key,
                        **concurrency,
                    )
                    result["deleted"] += 1
            except (
                KeyError,
                TypeError,
                ValueError,
                ResourceModifiedError,
                ResourceNotFoundError,
            ):
                # Kein unsicheres Löschen bei beschädigten Legacy-Datensätzen.
                result["skipped"] += 1
        return result


def _entity_datetime(entity: Mapping[str, Any], key: str) -> datetime:
    value = entity.get(key)
    if isinstance(value, datetime):
        parsed = value
    else:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError(f"{key} benötigt eine Zeitzone")
    return parsed.astimezone(timezone.utc)


def _entity_concurrency(entity: Mapping[str, Any]) -> dict[str, Any] | None:
    metadata = getattr(entity, "metadata", {}) or {}
    etag = str(
        metadata.get("etag")
        or entity.get("etag")
        or entity.get("odata.etag")
        or ""
    ).strip()
    if not etag:
        return None
    return {"etag": etag, "match_condition": MatchConditions.IfNotModified}
