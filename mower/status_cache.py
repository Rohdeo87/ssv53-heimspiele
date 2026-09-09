"""Opt-in, controller-wide Hydrawise read coordination with true source age.

Only the existing table's separate cache partition is written. No commands,
AutomationState writes, implicit table creation or direct-fetch fallback on
cache failure exist here. Lost reads recover after a bounded shared backoff;
every replacement reservation fences out the previous reader's late reply.
"""
from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import asdict, dataclass, replace
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from functools import lru_cache
from typing import Any, Callable, Mapping

from mower.hydrawise import HydrawiseError, evaluate_safety_status, fetch_status
from mower.status_cache_store import (
    AzureTableStatusCacheStore, StatusCacheConflict, StatusCacheEntry, StatusCacheStore,
)


MINIMUM_POLL_SECONDS = 60
ERROR_BACKOFF_SECONDS = 300
FETCH_TIMEOUT_SECONDS = 20
FETCH_RESERVATION_SECONDS = 60
ESCALATION_FAILURES = 3
MAX_ERROR_BACKOFF_SECONDS = 3600
MAX_PAYLOAD_UTF16_BYTES = 60 * 1024


def _utc(value: str | datetime | None) -> datetime | None:
    if value is None:
        return None
    result = value if isinstance(value, datetime) else datetime.fromisoformat(value.replace("Z", "+00:00"))
    if result.tzinfo is None or result.utcoffset() is None:
        raise ValueError("Cache times must include a timezone")
    return result.astimezone(timezone.utc)


def _seconds(value: Any) -> int:
    if isinstance(value, bool) or value in (None, ""):
        raise ValueError("Missing polling budget")
    number = float(value)
    if not number.is_integer() or not 0 <= number <= 2147483647:
        raise ValueError("Invalid polling budget")
    return int(number)


def controller_cache_key(controller_id: str | int | None) -> str:
    identity = str(controller_id or "").strip()
    if not identity or not identity.isdecimal() or int(identity) <= 0:
        raise ValueError("An explicit numeric Hydrawise controller ID is required")
    # Different API keys for one controller MUST coordinate on the same row.
    # Neither API key nor raw controller identity is stored in that row key.
    return hashlib.sha256(("statusschedule:" + str(int(identity))).encode()).hexdigest()


def cache_mode(environment: Mapping[str, str]) -> str:
    mode = str(environment.get("HYDRAWISE_STATUS_CACHE_MODE") or "OFF").strip().upper()
    if mode not in {"OFF", "AZURE_TABLE"}:
        raise ValueError("HYDRAWISE_STATUS_CACHE_MODE must be OFF or AZURE_TABLE")
    return mode


@dataclass(frozen=True)
class CachedStatusRead:
    status: dict[str, Any] | None
    last_known_status: dict[str, Any] | None
    quality: str
    source_observed_at_utc: str | None = None
    fetched_at_utc: str | None = None
    next_poll_at_utc: str | None = None
    new_observation: bool = False
    attempt_until_utc: str | None = None
    consecutive_errors: int = 0
    escalation_required: bool = False

    @property
    def confirmation_observed_until_utc(self) -> str | None:
        return self.fetched_at_utc if self.status is not None else None

    def metadata(self) -> dict[str, Any]:
        # Status bodies stay out of diagnostic metadata and error messages.
        return {key: value for key, value in asdict(self).items()
                if key not in {"status", "last_known_status"}} | {
                    "confirmation_observed_until_utc": self.confirmation_observed_until_utc,
                    "coordination_scope": "CONTROLLER_TABLE",
                }


def _backoff_seconds(failures: int) -> int:
    # Repeated failures require operator attention, but a read-only service can
    # still recover automatically at a bounded hourly rate after an outage.
    return (MAX_ERROR_BACKOFF_SECONDS if failures >= ESCALATION_FAILURES else
            ERROR_BACKOFF_SECONDS * 2 ** max(failures - 1, 0))


def _retry_at(error: Exception, now: datetime, failures: int) -> datetime:
    deadline = now + timedelta(seconds=_backoff_seconds(failures))
    value = getattr(error, "retry_after", None)
    if value is None:
        return deadline
    try:
        parsed = now + timedelta(seconds=_seconds(value))
    except (TypeError, ValueError, OverflowError):
        try:
            parsed = _utc(parsedate_to_datetime(str(value)))
        except (TypeError, ValueError, OverflowError):
            return deadline
    return max(deadline, parsed) if parsed is not None else deadline


class _FetchLeaseExpired(TimeoutError):
    pass


class _SourceNotAdvanced(ValueError):
    pass


class _SourceStale(ValueError):
    pass


class CoordinatedHydrawiseStatusCache:
    def __init__(self, store: StatusCacheStore, *, clock: Callable[[], datetime] | None = None,
                 fetcher: Callable[..., dict[str, Any]] = fetch_status) -> None:
        self.store = store
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        self.fetcher = fetcher

    def _view(self, entry: StatusCacheEntry, *, now: datetime, config: dict[str, Any], max_age_seconds: int,
              quality: str | None = None, new_observation: bool = False) -> CachedStatusRead:
        payload = None
        if entry.payload_json:
            payload = json.loads(entry.payload_json)
            if not isinstance(payload, dict):
                raise ValueError("Invalid cache payload")
        code = quality
        usable = False
        failures = entry.consecutive_errors
        if code is None:
            if entry.phase == "PENDING":
                deadline = _utc(entry.attempt_until_utc)
                if deadline is None:
                    code = "CACHE_TIMESTAMP_INVALID"
                elif now < deadline:
                    code = "FETCH_IN_PROGRESS"
                else:
                    code = "FETCH_RECOVERY_BACKOFF"
                    failures = min(100000, failures + 1)
            elif entry.phase == "ERROR":
                code = entry.last_error or "LAST_FETCH_FAILED"
            elif entry.phase == "SUCCESS" and payload is not None:
                fetched = _utc(entry.fetched_at_utc)
                safety = evaluate_safety_status(payload, config, now_utc=now, max_age_seconds=max_age_seconds)
                if (safety.observed_at_utc != entry.source_observed_at_utc or fetched is None
                        or fetched > now + timedelta(seconds=30)):
                    code = "CACHE_TIMESTAMP_INVALID"
                else:
                    usable = bool(safety.available and safety.fresh and safety.relay_set_valid)
                    code = "FETCHED" if usable and new_observation else "CACHED" if usable else "SOURCE_STALE_OR_INVALID"
            else:
                code = "CACHE_EMPTY"
        return CachedStatusRead(
            status=payload if usable else None, last_known_status=payload, quality=code,
            source_observed_at_utc=entry.source_observed_at_utc, fetched_at_utc=entry.fetched_at_utc,
            next_poll_at_utc=entry.next_poll_at_utc, new_observation=bool(usable and new_observation),
            attempt_until_utc=entry.attempt_until_utc,
            consecutive_errors=failures, escalation_required=failures >= ESCALATION_FAILURES,
        )

    def read(self, api_key: str, controller_id: str | int | None, *, hydrawise_config: Mapping[str, Any],
             max_age_seconds: int = 180) -> CachedStatusRead:
        now = _utc(self.clock())
        config = dict(hydrawise_config)
        try:
            key = controller_cache_key(controller_id)
            snapshot = self.store.load(key)
            entry = snapshot.entry
            budget = _utc(entry.next_poll_at_utc)
            failures = entry.consecutive_errors
            if entry.phase == "PENDING":
                started, until = _utc(entry.attempt_started_utc), _utc(entry.attempt_until_utc)
                if (not entry.attempt_id or started is None or until is None or budget is None
                        or until < started + timedelta(seconds=FETCH_RESERVATION_SECONDS) or budget < until):
                    return self._view(entry, now=now, config=config, max_age_seconds=max_age_seconds,
                                      quality="CACHE_TIMESTAMP_INVALID")
                if now < max(until, budget):
                    return self._view(entry, now=now, config=config, max_age_seconds=max_age_seconds)
                failures = min(100000, failures + 1)
            elif budget is not None and now < budget:
                return self._view(entry, now=now, config=config, max_age_seconds=max_age_seconds)
            token = uuid.uuid4().hex
            # Reserve the recovery budget BEFORE the GET. It survives a crash
            # without creating a permanent latch. A later CAS winner may start
            # a new read, but the old token can never publish into its row.
            reserved = replace(entry, phase="PENDING", attempt_id=token, attempt_started_utc=now.isoformat(),
                               attempt_until_utc=(now + timedelta(seconds=FETCH_RESERVATION_SECONDS)).isoformat(),
                               next_poll_at_utc=(now + timedelta(seconds=FETCH_RESERVATION_SECONDS
                                                               + _backoff_seconds(failures + 1))).isoformat(),
                               consecutive_errors=failures,
                               last_error="FETCH_OUTCOME_UNKNOWN" if entry.phase == "PENDING" else entry.last_error)
            self.store.compare_exchange(key, snapshot.token, reserved)
        except StatusCacheConflict:
            try:
                return self._view(self.store.load(key).entry, now=now, config=config, max_age_seconds=max_age_seconds)
            except Exception:
                return CachedStatusRead(None, None, "CACHE_UNAVAILABLE")
        except Exception:
            return CachedStatusRead(None, None, "CACHE_UNAVAILABLE")

        response_budget = now + timedelta(seconds=MINIMUM_POLL_SECONDS)
        try:
            response = self.fetcher(api_key, controller_id, timeout=FETCH_TIMEOUT_SECONDS)
            received = _utc(self.clock())
            if received < now:
                raise ValueError("Clock moved backwards during fetch")
            if not isinstance(response, dict):
                raise ValueError("Hydrawise response is not an object")
            nextpoll = _seconds(response.get("nextpoll"))
            response_budget = received + timedelta(seconds=max(MINIMUM_POLL_SECONDS, nextpoll))
            if received > _utc(reserved.attempt_until_utc):
                raise _FetchLeaseExpired("Status-cache fetch exceeded its observation lease")
            safety = evaluate_safety_status(response, config, now_utc=received, max_age_seconds=max_age_seconds)
            if not safety.available or not safety.relay_set_valid:
                raise ValueError("Malformed Hydrawise status")
            if not safety.fresh:
                raise _SourceStale("Hydrawise returned a stale source observation")
            previous_source = _utc(entry.source_observed_at_utc)
            if previous_source is not None and _utc(safety.observed_at_utc) <= previous_source:
                raise _SourceNotAdvanced("Hydrawise source observation did not advance")
            # No API key is returned by this documented response. Discard any
            # unexpected credential-like fields and account message explicitly.
            payload = {key: value for key, value in response.items()
                       if key.lower() not in {"api_key", "apikey", "access_token", "client_secret", "message"}}
            serialized = json.dumps(payload, ensure_ascii=False, allow_nan=False, separators=(",", ":"))
            if len(serialized.encode("utf-16-le")) > MAX_PAYLOAD_UTF16_BYTES:
                raise ValueError("Hydrawise cache payload exceeds table property limit")
            completed = replace(reserved, phase="SUCCESS", payload_json=serialized,
                                fetched_at_utc=received.isoformat(), source_observed_at_utc=safety.observed_at_utc,
                                next_poll_at_utc=response_budget.isoformat(),
                                attempt_id=None, attempt_until_utc=None, last_error=None, consecutive_errors=0)
        except Exception as error:
            received = max(now, _utc(self.clock()))
            failures = min(100000, reserved.consecutive_errors + 1)
            code = "HTTP_" + str(error.http_status) if isinstance(error, HydrawiseError) and error.http_status else (
                "FETCH_LEASE_EXPIRED" if isinstance(error, _FetchLeaseExpired) else
                "SOURCE_NOT_ADVANCED" if isinstance(error, _SourceNotAdvanced) else
                "SOURCE_STALE" if isinstance(error, _SourceStale) else
                "STATUS_INVALID" if isinstance(error, (ValueError, TypeError)) else "FETCH_FAILED"
            )
            completed = replace(reserved, phase="ERROR", attempt_id=None, attempt_until_utc=None,
                                next_poll_at_utc=max(response_budget,
                                                    _retry_at(error, received, failures)).isoformat(),
                                last_error=code, consecutive_errors=failures)

        try:
            latest = self.store.load(key)
            if latest.entry.phase != "PENDING" or latest.entry.attempt_id != token:
                return self._view(latest.entry, now=received, config=config, max_age_seconds=max_age_seconds,
                                  quality="FETCH_RESERVATION_CHANGED")
            self.store.compare_exchange(key, latest.token, completed)
        except Exception:
            # Even a successful HTTP reply may not be published without a
            # confirmed fenced cache write; a peer can reconcile committed data.
            return self._view(entry, now=received, config=config, max_age_seconds=max_age_seconds,
                              quality="CACHE_COMMIT_UNCONFIRMED")
        return self._view(completed, now=received, config=config, max_age_seconds=max_age_seconds,
                          new_observation=completed.phase == "SUCCESS")


@lru_cache(maxsize=4)
def _azure_store(endpoint: str, table: str, identity: str) -> AzureTableStatusCacheStore:
    return AzureTableStatusCacheStore.from_environment({
        "SSV53_STORAGE_ACCOUNT_URL": endpoint, "SSV53_STATE_TABLE_NAME": table,
        "SSV53_STATE_MANAGED_IDENTITY_CLIENT_ID": identity,
    })


def read_status_cached(api_key: str, controller_id: str | int | None, *, environment: Mapping[str, str],
                       hydrawise_config: Mapping[str, Any], now_utc: datetime | None = None,
                       fetcher: Callable[..., dict[str, Any]] = fetch_status,
                       store: StatusCacheStore | None = None,
                       clock: Callable[[], datetime] | None = None) -> CachedStatusRead:
    """OFF performs neither a fetch nor a storage call; caller keeps legacy path.

    ``now_utc`` is accepted for callsite compatibility; cache budgets/receipt
    times use the actual clock, not the earlier controller-cycle timestamp.
    Tests supply a deterministic clock. Cache errors never fall back directly.
    """
    try:
        if cache_mode(environment) == "OFF":
            return CachedStatusRead(None, None, "CACHE_DISABLED")
        selected = store or _azure_store(
            str(environment.get("SSV53_STORAGE_ACCOUNT_URL") or ""),
            str(environment.get("SSV53_STATE_TABLE_NAME") or ""),
            str(environment.get("SSV53_STATE_MANAGED_IDENTITY_CLIENT_ID") or environment.get("AzureWebJobsStorage__clientId") or ""),
        )
        return CoordinatedHydrawiseStatusCache(selected, clock=clock, fetcher=fetcher).read(
            api_key, controller_id, hydrawise_config=hydrawise_config,
            max_age_seconds=int(environment.get("HYDRAWISE_STATUS_MAX_AGE_SECONDS", "180")),
        )
    except Exception:
        return CachedStatusRead(None, None, "CACHE_UNAVAILABLE")
