"""Verify Appack's current user, never roles or IDs asserted by the calendar.

The caller's short-lived JWT is sent only to Appack's fixed HTTPS GraphQL
endpoint. ``whoami`` resolves the authenticated profile and its *current* roles.
No service token, decoded JWT claims, role-name matching or stale permission
cache can grant access. This authority is deliberately limited to occupancy.
"""
from __future__ import annotations

from datetime import datetime, timezone
from collections import deque
from contextlib import contextmanager
import re
import threading
import time

import requests

from platzwart_console import PlatzwartError


APPACK_GRAPHQL_URL = "https://api.appack.de/graphql"
APP_ID = "ssv53"
WHOAMI_QUERY = """query SSV53OccupancyIdentity {
  whoami {
    expiresAt appScope
    userProfile {
      id appId name firstname email locked verificationPending
      roles { enumKey }
    }
  }
}"""
_JWT = re.compile(r"[A-Za-z0-9_-]+=*\.[A-Za-z0-9_-]+=*\.[A-Za-z0-9_-]+=*\Z")
_PROFILE_ID = re.compile(r"[A-Za-z0-9_-]{1,120}\Z")
_ADMISSION_LOCK = threading.Lock()
_PROVIDER_SLOTS = threading.BoundedSemaphore(3)
_PROVIDER_CALLS = deque()


@contextmanager
def _provider_admission():
    # Bound this worker's fan-out before attempting online authentication.
    # No credential or permission cache; scaling workers has independent caps.
    # Refuse immediately rather than queueing callers beside device control.
    if not _PROVIDER_SLOTS.acquire(blocking=False):
        raise PlatzwartError("APPACK_BUSY", "Bitte kurz warten und erneut versuchen.", 429)
    try:
        with _ADMISSION_LOCK:
            now = time.monotonic()
            while _PROVIDER_CALLS and _PROVIDER_CALLS[0] <= now - 60:
                _PROVIDER_CALLS.popleft()
            if len(_PROVIDER_CALLS) >= 30:
                raise PlatzwartError("APPACK_BUSY", "Bitte kurz warten und erneut versuchen.", 429)
            _PROVIDER_CALLS.append(now)
        yield
    finally:
        _PROVIDER_SLOTS.release()


def _unavailable() -> PlatzwartError:
    return PlatzwartError(
        "APPACK_UNAVAILABLE",
        "Die Anmeldung konnte gerade nicht geprüft werden. Bitte gleich erneut versuchen.",
        503,
    )


def _invalid() -> PlatzwartError:
    return PlatzwartError(
        "APPACK_SESSION_INVALID",
        "Bitte die Platzbelegung in der angemeldeten SSV53-App erneut öffnen.",
        401,
    )


def _whoami(token: str) -> dict:
    # No redirects: never forward a personal credential to another destination.
    try:
        with requests.post(
            APPACK_GRAPHQL_URL,
            json={"query": WHOAMI_QUERY},
            headers={"Authorization": "Bearer " + token, "Accept": "application/json"},
            timeout=(3, 8), allow_redirects=False, stream=True,
        ) as response:
            if response.status_code in (401, 403):
                raise _invalid()
            if response.status_code != 200:
                raise _unavailable()
            raw = bytearray()
            for chunk in response.iter_content(chunk_size=4096):
                raw.extend(chunk)
                if len(raw) > 32768:
                    raise _unavailable()
            import json
            payload = json.loads(raw)
    except (requests.RequestException, ValueError):
        # Never include provider error bodies, tokens or headers in errors/logs.
        raise _unavailable() from None
    if not isinstance(payload, dict) or payload.get("errors"):
        raise _invalid()
    data = payload.get("data")
    identity = data.get("whoami") if isinstance(data, dict) else None
    if not isinstance(identity, dict):
        raise _invalid()
    return identity


def _expiry(value: object) -> datetime:
    try:
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            # Appack Date scalars may use epoch milliseconds or ISO 8601.
            return datetime.fromtimestamp(value / 1000, timezone.utc)
        result = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if result.tzinfo is None or result.utcoffset() is None:
            raise ValueError
        return result.astimezone(timezone.utc)
    except (ValueError, OverflowError, OSError):
        raise _invalid() from None


def require_appack_occupancy_identity(token: str, now_utc: datetime) -> dict:
    if len(token) > 16384 or not _JWT.fullmatch(token):
        raise _invalid()
    with _provider_admission():
        identity = _whoami(token)
    profile = identity.get("userProfile")
    if (
        identity.get("appScope") != APP_ID
        or _expiry(identity.get("expiresAt")) <= now_utc
        or not isinstance(profile, dict)
        or profile.get("appId") != APP_ID
        or not _PROFILE_ID.fullmatch(str(profile.get("id") or ""))
        or profile.get("locked") is not False
        or profile.get("verificationPending") is not False
    ):
        raise _invalid()
    roles = profile.get("roles")
    if not isinstance(roles, list):
        raise _invalid()
    keys = {str(role.get("enumKey") or "").strip().upper() for role in roles
            if isinstance(role, dict) and isinstance(role.get("enumKey"), str)}
    # Verified against the SSV53 CMS role configuration on 2026-09-10.
    # Requested roles and translated role labels do not grant permissions.
    if not keys.intersection({"TR", "AA"}):
        raise PlatzwartError(
            "TRAINER_ROLE_REQUIRED",
            "Nur freigeschaltete Trainer und App-Administratoren können Trainings ändern.",
            403,
        )
    name = " ".join(str(profile.get(field) or "").strip()
                    for field in ("firstname", "name")).strip()[:160]
    return {
        "requesterId": profile["id"],
        "isAppAdministrator": "AA" in keys,
        "creator": {
            "id": profile["id"], "name": name,
            "email": str(profile.get("email") or "")[:254],
            "role": "App-Administrator" if "AA" in keys else "Trainer",
        },
    }
