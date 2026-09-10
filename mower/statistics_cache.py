"""Shared read-only dashboard statistics cache."""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timedelta, timezone
import hashlib
import threading
from typing import Any, Callable, Mapping

_STATISTICS_CACHE: dict[str, dict[str, Any]] = {}
_STATISTICS_CACHE_LOCK = threading.Lock()
_TTL = timedelta(minutes=5)
_FAILURE_TTL = timedelta(seconds=15)
_MAX_ENTRIES = 16


def _account_key(environment: Mapping[str, str]) -> str:
    values = [
        str(environment.get(name, "")).strip()
        for name in (
            "SSV53_APP_INSIGHTS_APP_ID",
            "SSV53_STATE_MANAGED_IDENTITY_CLIENT_ID",
            "AzureWebJobsStorage__clientId",
        )
    ]
    return hashlib.sha256("\0".join(values).encode()).hexdigest()


def get_dashboard_statistics(environment: Mapping[str, str], now_utc: datetime, *,
                            loader: Callable[..., dict[str, Any]] | None = None) -> dict[str, Any]:
    if now_utc.tzinfo is None or now_utc.utcoffset() is None:
        return {"available": False, "message": "Zeitstempel ist ungültig."}
    now = now_utc.astimezone(timezone.utc)
    key = _account_key(environment)
    with _STATISTICS_CACHE_LOCK:
        entry = _STATISTICS_CACHE.get(key)
        try:
            if entry and now >= entry["at"] and now < entry["expires"] and isinstance(entry["payload"], dict):
                return deepcopy(entry["payload"])
        except (KeyError, TypeError, ValueError):
            pass  # An optional in-process cache must be replaceable after corruption.
        try:
            if loader is None:
                from daily_safety_report import dashboard_statistics
                loader = dashboard_statistics
            payload = dict(loader(now, environment))
            if payload.get("available") is not True:
                payload = {"available": False, "message": "Die 7-Tage-Auswertung ist gerade nicht erreichbar."}
                expires = now + _FAILURE_TTL
            else:
                payload["message"] = None
                expires = now + _TTL
        except Exception:
            payload = {"available": False, "message": "Die 7-Tage-Auswertung ist gerade nicht erreichbar."}
            expires = now + _FAILURE_TTL
        _STATISTICS_CACHE[key] = {"at": now, "expires": expires, "payload": deepcopy(payload)}
        while len(_STATISTICS_CACHE) > _MAX_ENTRIES:
            oldest = min(_STATISTICS_CACHE, key=lambda item: _STATISTICS_CACHE[item]["at"])
            del _STATISTICS_CACHE[oldest]
        return deepcopy(payload)


def peek_dashboard_statistics(environment: Mapping[str, str], now_utc: datetime) -> dict[str, Any] | None:
    """Historical display evidence only; never wait for a query or initiate I/O."""
    if now_utc.tzinfo is None or now_utc.utcoffset() is None:
        return None
    now = now_utc.astimezone(timezone.utc)
    if not _STATISTICS_CACHE_LOCK.acquire(blocking=False):
        return None
    key = _account_key(environment)
    try:
        entry = _STATISTICS_CACHE.get(key)
        if entry and entry["at"] <= now < entry["expires"]:
            if not isinstance(entry["payload"], dict):
                raise TypeError("Invalid optional cache payload")
            return deepcopy(entry["payload"])
        return None
    except Exception:
        # Fail only this display cache, then allow the background reader to rebuild it.
        _STATISTICS_CACHE.pop(key, None)
        return None
    finally:
        _STATISTICS_CACHE_LOCK.release()
