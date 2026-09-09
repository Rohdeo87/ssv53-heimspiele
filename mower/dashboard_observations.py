"""Load-only dashboard snapshots published by the control reader.

This module never imports or calls the Hydrawise transport.  Its store is a
separate partition in the existing state table and is deliberately independent
of the status-cache lease/budget state.
"""
from __future__ import annotations

import json
import threading
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Mapping, Protocol

from mower.hydrawise import evaluate_safety_status
from mower.status_cache import controller_cache_key
from mower.status_cache_store import StatusCacheConflict


PARTITION_KEY = "ssv53-hydrawise-dashboard-observation-v1"
MAX_PAYLOAD_UTF16_BYTES = 60 * 1024
MAX_AGE_SECONDS = 180
_TOP_LEVEL_FIELDS = {"time", "nextpoll", "relays"}
_RELAY_FIELDS = {"relay_id", "relay", "name", "time", "run"}


@dataclass(frozen=True)
class DashboardObservationEntry:
    payload_json: str
    source_observed_at_utc: str
    fetched_at_utc: str
    schema_version: str = "v1"


@dataclass(frozen=True)
class DashboardObservationSnapshot:
    entry: DashboardObservationEntry | None
    token: str | None


class DashboardObservationStore(Protocol):
    def load(self, key: str) -> DashboardObservationSnapshot: ...
    def compare_exchange(
        self, key: str, expected_token: str | None,
        entry: DashboardObservationEntry,
    ) -> None: ...


class InMemoryDashboardObservationStore:
    """Small deterministic fake; production wiring can adapt Azure Table CAS."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._entries: dict[str, DashboardObservationSnapshot] = {}
        self._version = 0

    def load(self, key: str) -> DashboardObservationSnapshot:
        with self._lock:
            return self._entries.get(key, DashboardObservationSnapshot(None, None))

    def compare_exchange(
        self, key: str, expected_token: str | None,
        entry: DashboardObservationEntry,
    ) -> None:
        with self._lock:
            current = self._entries.get(key, DashboardObservationSnapshot(None, None))
            if current.token != expected_token:
                raise StatusCacheConflict("Dashboard observation changed concurrently")
            self._version += 1
            self._entries[key] = DashboardObservationSnapshot(entry, str(self._version))


class AzureTableDashboardObservationStore:
    """CAS adapter for the existing table; it never creates infrastructure."""

    def __init__(self, table_client: Any) -> None:
        self._table_client = table_client

    @classmethod
    def from_environment(cls, environment: Mapping[str, str], *, credential_factory=None,
                         table_client_factory=None) -> "AzureTableDashboardObservationStore":
        endpoint = str(environment.get("SSV53_STORAGE_ACCOUNT_URL") or "").strip()
        table_name = str(environment.get("SSV53_STATE_TABLE_NAME") or "").strip()
        identity = str(environment.get("SSV53_STATE_MANAGED_IDENTITY_CLIENT_ID")
                       or environment.get("AzureWebJobsStorage__clientId") or "").strip()
        if not endpoint or not table_name or not identity:
            raise RuntimeError("Existing dashboard-observation table access is not configured")
        if credential_factory is None:
            from azure.identity import ManagedIdentityCredential
            credential_factory = ManagedIdentityCredential
        if table_client_factory is None:
            from azure.data.tables import TableClient
            table_client_factory = TableClient
        return cls(table_client_factory(endpoint=endpoint, table_name=table_name,
                                        credential=credential_factory(client_id=identity)))

    def load(self, key: str) -> DashboardObservationSnapshot:
        from azure.core.exceptions import ResourceNotFoundError
        try:
            entity = self._table_client.get_entity(
                partition_key=PARTITION_KEY, row_key=key, timeout=5
            )
        except ResourceNotFoundError:
            return DashboardObservationSnapshot(None, None)
        token = str((getattr(entity, "metadata", {}) or {}).get("etag") or "").strip()
        if not token:
            raise RuntimeError("Dashboard observation ETag is missing")
        try:
            entry = DashboardObservationEntry(
                payload_json=str(entity["payload_json"]),
                source_observed_at_utc=str(entity["source_observed_at_utc"]),
                fetched_at_utc=str(entity["fetched_at_utc"]),
                schema_version=str(entity.get("schema_version", "")),
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise RuntimeError("Dashboard observation metadata is malformed") from exc
        return DashboardObservationSnapshot(entry, token)

    def compare_exchange(self, key: str, expected_token: str | None,
                          entry: DashboardObservationEntry) -> None:
        from azure.core import MatchConditions
        from azure.core.exceptions import HttpResponseError, ResourceExistsError
        from azure.data.tables import UpdateMode
        entity = {"PartitionKey": PARTITION_KEY, "RowKey": key,
                  "payload_json": entry.payload_json,
                  "source_observed_at_utc": entry.source_observed_at_utc,
                  "fetched_at_utc": entry.fetched_at_utc,
                  "schema_version": entry.schema_version}
        try:
            if expected_token is None:
                self._table_client.create_entity(entity=entity, timeout=5)
            else:
                self._table_client.update_entity(entity=entity, mode=UpdateMode.REPLACE,
                                                 etag=expected_token,
                                                 match_condition=MatchConditions.IfNotModified,
                                                 timeout=5)
        except (ResourceExistsError, HttpResponseError) as exc:
            if isinstance(exc, ResourceExistsError) or getattr(exc, "status_code", None) in {409, 412}:
                raise StatusCacheConflict("Dashboard observation changed concurrently") from exc
            raise


def dashboard_observation_store_from_environment(
    environment: Mapping[str, str], *, credential_factory=None,
    table_client_factory=None,
) -> DashboardObservationStore | None:
    """Return the optional store; OFF is the safe default and does no I/O."""
    mode = str(environment.get("HYDRAWISE_DASHBOARD_OBSERVATION_MODE", "OFF")).strip().upper()
    if mode == "OFF":
        return None
    if mode != "AZURE_TABLE":
        raise ValueError("HYDRAWISE_DASHBOARD_OBSERVATION_MODE must be OFF or AZURE_TABLE")
    return AzureTableDashboardObservationStore.from_environment(
        environment, credential_factory=credential_factory,
        table_client_factory=table_client_factory,
    )


@dataclass(frozen=True)
class DashboardObservationRead:
    status: dict[str, Any] | None
    source_observed_at_utc: str | None
    fetched_at_utc: str | None
    quality: str
    age_seconds: int | None = None

    @property
    def known(self) -> bool:
        return self.status is not None


def _utc(value: str | datetime | None) -> datetime | None:
    if value is None:
        return None
    try:
        parsed = value if isinstance(value, datetime) else datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if parsed.tzinfo is None or parsed.utcoffset() is None:
            return None
        return parsed.astimezone(timezone.utc)
    except (TypeError, ValueError):
        return None


def _relay_ids(value: tuple[int, ...] | list[int]) -> tuple[int, ...] | None:
    result: list[int] = []
    try:
        for item in value:
            if isinstance(item, bool) or isinstance(item, float):
                return None
            if isinstance(item, int):
                number = item
            elif isinstance(item, str) and item.strip().isdigit():
                number = int(item.strip())
            else:
                return None
            result.append(number)
    except (TypeError, ValueError):
        return None
    result = sorted(result)
    if len(result) != 7 or len(set(result)) != 7 or any(number <= 0 for number in result):
        return None
    return tuple(result)


def _sanitize_status(value: Mapping[str, Any]) -> dict[str, Any]:
    """Keep only fields needed to render the dashboard's Hydrawise status."""
    result: dict[str, Any] = {}
    for key in _TOP_LEVEL_FIELDS:
        if key not in value:
            continue
        if key == "relays":
            relays = value[key]
            if isinstance(relays, list):
                result[key] = [
                    {field: relay[field] for field in _RELAY_FIELDS if field in relay}
                    for relay in relays if isinstance(relay, Mapping)
                ]
        else:
            result[key] = value[key]
    return result


def _safe_status(value: Mapping[str, Any]) -> dict[str, Any] | None:
    """Whitelist and validate the complete persisted shape before encoding."""
    if not isinstance(value.get("time"), int) or isinstance(value.get("time"), bool):
        return None
    if not isinstance(value.get("nextpoll"), int) or isinstance(value.get("nextpoll"), bool):
        return None
    relays = value.get("relays")
    if not isinstance(relays, list):
        return None
    sanitized = _sanitize_status(value)
    if len(sanitized.get("relays", [])) != len(relays):
        return None
    for relay in sanitized["relays"]:
        if "name" in relay and (not isinstance(relay["name"], str) or len(relay["name"]) > 160):
            return None
        if any(isinstance(item, (dict, list, bool, float)) for item in relay.values()):
            return None
    return sanitized


def _config(expected_relay_ids: tuple[int, ...]) -> dict[str, Any]:
    return {"enabled": True, "include_all_zones": True, "expected_relay_ids": list(expected_relay_ids), "before_minutes": 30}


def publish_dashboard_observation(
    store: DashboardObservationStore,
    controller_id: str | int,
    status: Mapping[str, Any],
    *,
    expected_relay_ids: tuple[int, ...] | list[int],
    clock: Callable[[], datetime] | None = None,
) -> str:
    """Validate and atomically publish one direct control-read observation."""
    expected = _relay_ids(expected_relay_ids)
    if expected is None:
        return "INVALID_CONFIGURATION"
    now = (clock or (lambda: datetime.now(timezone.utc)))()
    now_utc = _utc(now)
    if now_utc is None:
        return "INVALID_OBSERVATION"
    raw = dict(status) if isinstance(status, Mapping) else None
    safety = evaluate_safety_status(raw, _config(expected), now_utc=now_utc, max_age_seconds=MAX_AGE_SECONDS)
    observed = _utc(safety.observed_at_utc)
    if raw is None or not safety.available or not safety.fresh or not safety.relay_set_valid or observed is None or observed > now_utc:
        return "INVALID_OBSERVATION"
    sanitized = _safe_status(raw)
    if sanitized is None:
        return "INVALID_OBSERVATION"
    serialized = json.dumps(sanitized, ensure_ascii=False, allow_nan=False, separators=(",", ":"))
    if len(serialized.encode("utf-16-le")) > MAX_PAYLOAD_UTF16_BYTES:
        return "PAYLOAD_TOO_LARGE"
    key = controller_cache_key(controller_id)
    snapshot = store.load(key)
    if snapshot.entry is not None:
        previous = _utc(snapshot.entry.source_observed_at_utc)
        if previous is None or observed <= previous:
            return "STALE_OR_EQUAL"
    entry = DashboardObservationEntry(
        payload_json=serialized,
        source_observed_at_utc=observed.isoformat(),
        fetched_at_utc=now_utc.isoformat(),
    )
    store.compare_exchange(key, snapshot.token, entry)
    return "PUBLISHED"


def read_dashboard_observation(
    store: DashboardObservationStore,
    controller_id: str | int,
    *,
    expected_relay_ids: tuple[int, ...] | list[int],
    now_utc: datetime,
) -> DashboardObservationRead:
    """Read one snapshot; this function has no vendor/network fallback."""
    expected = _relay_ids(expected_relay_ids)
    if expected is None:
        return DashboardObservationRead(None, None, None, "INVALID_CONFIGURATION")
    now = _utc(now_utc)
    if now is None:
        return DashboardObservationRead(None, None, None, "INVALID_CLOCK")
    try:
        snapshot = store.load(controller_cache_key(controller_id))
        entry = snapshot.entry
        if entry is None:
            return DashboardObservationRead(None, None, None, "EMPTY")
        if entry.schema_version != "v1":
            return DashboardObservationRead(None, entry.source_observed_at_utc, entry.fetched_at_utc, "INVALID_SCHEMA")
        if len(entry.payload_json.encode("utf-16-le")) > MAX_PAYLOAD_UTF16_BYTES:
            return DashboardObservationRead(None, entry.source_observed_at_utc, entry.fetched_at_utc, "PAYLOAD_TOO_LARGE")
        observed, fetched = _utc(entry.source_observed_at_utc), _utc(entry.fetched_at_utc)
        if observed is None or fetched is None:
            return DashboardObservationRead(None, entry.source_observed_at_utc, entry.fetched_at_utc, "INVALID_TIMESTAMP")
        age = int((now - observed).total_seconds())
        if fetched < observed or fetched > now + timedelta(seconds=60):
            return DashboardObservationRead(None, entry.source_observed_at_utc, entry.fetched_at_utc, "INVALID_TIMESTAMP")
        if age < -60 or age > MAX_AGE_SECONDS:
            return DashboardObservationRead(None, entry.source_observed_at_utc, entry.fetched_at_utc, "SOURCE_STALE", age)
        payload = json.loads(entry.payload_json)
        if not isinstance(payload, dict):
            raise ValueError("snapshot is not an object")
        if payload.get("time") != int(observed.timestamp()):
            return DashboardObservationRead(None, entry.source_observed_at_utc, entry.fetched_at_utc, "SOURCE_MISMATCH", age)
        safe_payload = _safe_status(payload)
        if safe_payload is None:
            return DashboardObservationRead(None, entry.source_observed_at_utc, entry.fetched_at_utc, "SOURCE_INVALID", age)
        safety = evaluate_safety_status(safe_payload, _config(expected), now_utc=now, max_age_seconds=MAX_AGE_SECONDS)
        if not safety.available or not safety.fresh or not safety.relay_set_valid:
            return DashboardObservationRead(None, entry.source_observed_at_utc, entry.fetched_at_utc, "SOURCE_INVALID", age)
        return DashboardObservationRead(safe_payload, entry.source_observed_at_utc, entry.fetched_at_utc, "PUBLISHED", age)
    except Exception:
        return DashboardObservationRead(None, None, None, "CACHE_UNAVAILABLE")
