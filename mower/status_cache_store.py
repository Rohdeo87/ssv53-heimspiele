"""Separate CAS storage for Hydrawise reads; never imports AutomationState."""
from __future__ import annotations

import threading
from dataclasses import asdict, dataclass, fields
from typing import Any, Mapping, Protocol

class StatusCacheConflict(RuntimeError):
    pass


@dataclass(frozen=True)
class StatusCacheEntry:
    phase: str = "EMPTY"
    payload_json: str | None = None
    fetched_at_utc: str | None = None
    source_observed_at_utc: str | None = None
    next_poll_at_utc: str | None = None
    attempt_id: str | None = None
    attempt_started_utc: str | None = None
    attempt_until_utc: str | None = None
    last_error: str | None = None
    consecutive_errors: int = 0

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "StatusCacheEntry":
        result = cls(**{field.name: value.get(field.name, field.default) for field in fields(cls)})
        if result.phase not in {"EMPTY", "PENDING", "SUCCESS", "ERROR"}:
            raise ValueError("Unknown status-cache phase")
        if not isinstance(result.consecutive_errors, int) or not 0 <= result.consecutive_errors <= 100000:
            raise ValueError("Invalid status-cache error counter")
        return result


@dataclass(frozen=True)
class StatusCacheSnapshot:
    entry: StatusCacheEntry
    token: str | None


class StatusCacheStore(Protocol):
    def load(self, key: str) -> StatusCacheSnapshot: ...
    def compare_exchange(self, key: str, token: str | None, entry: StatusCacheEntry) -> None: ...


class InMemoryStatusCacheStore:
    """Deterministic test store; shared instance models separate process clients."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._entries: dict[str, StatusCacheSnapshot] = {}
        self._version = 0

    def load(self, key: str) -> StatusCacheSnapshot:
        with self._lock:
            return self._entries.get(key, StatusCacheSnapshot(StatusCacheEntry(), None))

    def compare_exchange(self, key: str, token: str | None, entry: StatusCacheEntry) -> None:
        with self._lock:
            current = self._entries.get(key, StatusCacheSnapshot(StatusCacheEntry(), None))
            if current.token != token:
                raise StatusCacheConflict("Status cache changed concurrently")
            self._version += 1
            self._entries[key] = StatusCacheSnapshot(entry, str(self._version))


class AzureTableStatusCacheStore:
    PARTITION_KEY = "ssv53-hydrawise-status-cache-v1"

    def __init__(self, table_client: Any) -> None:
        self._table_client = table_client

    @classmethod
    def from_environment(cls, environment: Mapping[str, str], *, credential_factory=None,
                         table_client_factory=None) -> "AzureTableStatusCacheStore":
        endpoint = str(environment.get("SSV53_STORAGE_ACCOUNT_URL") or "").strip()
        table_name = str(environment.get("SSV53_STATE_TABLE_NAME") or "").strip()
        identity = str(environment.get("SSV53_STATE_MANAGED_IDENTITY_CLIENT_ID")
                       or environment.get("AzureWebJobsStorage__clientId") or "").strip()
        if not endpoint or not table_name or not identity:
            raise RuntimeError("Existing status-cache table access is not configured")
        # The standalone OFF-mode plan generator has no Azure dependency.
        # Resolve installed SDKs only when the persistent option is selected.
        if credential_factory is None:
            from azure.identity import ManagedIdentityCredential
            credential_factory = ManagedIdentityCredential
        if table_client_factory is None:
            from azure.data.tables import TableClient
            table_client_factory = TableClient
        # No table/resource creation and no AutomationState row access.
        return cls(table_client_factory(endpoint=endpoint, table_name=table_name,
                                       credential=credential_factory(client_id=identity)))

    def load(self, key: str) -> StatusCacheSnapshot:
        from azure.core.exceptions import ResourceNotFoundError

        try:
            entity = self._table_client.get_entity(partition_key=self.PARTITION_KEY, row_key=key, timeout=5)
        except ResourceNotFoundError:
            return StatusCacheSnapshot(StatusCacheEntry(), None)
        token = str((getattr(entity, "metadata", {}) or {}).get("etag") or "").strip()
        if not token:
            raise RuntimeError("Status cache ETag is missing")
        return StatusCacheSnapshot(StatusCacheEntry.from_mapping(entity), token)

    def compare_exchange(self, key: str, token: str | None, entry: StatusCacheEntry) -> None:
        from azure.core import MatchConditions
        from azure.core.exceptions import HttpResponseError, ResourceExistsError
        from azure.data.tables import UpdateMode

        entity = {"PartitionKey": self.PARTITION_KEY, "RowKey": key,
                  **{name: value for name, value in asdict(entry).items() if value is not None}}
        try:
            if token is None:
                self._table_client.create_entity(entity=entity, timeout=5)
            else:
                self._table_client.update_entity(entity=entity, mode=UpdateMode.REPLACE,
                                                etag=token, match_condition=MatchConditions.IfNotModified, timeout=5)
        except (ResourceExistsError, HttpResponseError) as exc:
            if isinstance(exc, ResourceExistsError) or getattr(exc, "status_code", None) in {409, 412}:
                raise StatusCacheConflict("Status cache changed concurrently") from exc
            raise
