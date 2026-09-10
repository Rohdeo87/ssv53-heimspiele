"""Bounded, fail-closed policy state for one explicitly confirmed manual session.

This module is deliberately pure: it does not open a state store and it does
not send commands.  The console and full-failsafe runner persist its returned
documents with their existing compare-and-swap writes.  A session is an
authorization fence around the existing legacy START request, not another
command journal and not evidence of who caused an external mower state.
"""
from __future__ import annotations

import json
import hashlib
from datetime import datetime, timedelta, timezone
from typing import Any, Mapping


SESSION_VERSION = 1
MAX_SESSION_BYTES = 8_192
MAX_BLOCK_KEYS = 32
PREPARED_START_LIFETIME = timedelta(minutes=10)
SESSION_KINDS = frozenset({"START", "PARK"})
SESSION_SOURCES = frozenset({"APP", "HUSQVARNA"})
SESSION_STATUSES = frozenset({"PREPARED", "PENDING", "ACTIVE", "UNKNOWN", "ENDED"})
WATER_CHOICES = frozenset({"MOWER", "IRRIGATION"})
REQUIRED_KEYS = frozenset({
    "version", "session_id", "epoch", "mower_id", "kind", "source", "status",
    "created_at_utc", "prepared_until_utc", "departure_observed_utc", "ended_at_utc",
    "confirmed_block_keys", "confirmed_dry_until_utc", "water_conflict_id", "water_choice",
})


class ManualSessionError(ValueError):
    """A persisted or proposed manual-session document is unsafe to use."""


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ManualSessionError("MANUAL_SESSION_TIME_INVALID")
    return value.astimezone(timezone.utc)


def _parse_time(value: Any) -> datetime:
    if not isinstance(value, str) or not value.strip():
        raise ManualSessionError("MANUAL_SESSION_TIME_INVALID")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ManualSessionError("MANUAL_SESSION_TIME_INVALID") from exc
    return _utc(parsed)


def _optional_time(value: Any) -> datetime | None:
    if value is None:
        return None
    return _parse_time(value)


def _text(value: Any, field: str, maximum: int) -> str:
    if not isinstance(value, str):
        raise ManualSessionError("MANUAL_SESSION_INVALID")
    text = value.strip()
    if not text or len(text) > maximum:
        raise ManualSessionError("MANUAL_SESSION_INVALID")
    return text


def _normalize(session: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(session, Mapping) or set(session) != REQUIRED_KEYS:
        raise ManualSessionError("MANUAL_SESSION_INVALID")
    if session.get("version") != SESSION_VERSION:
        raise ManualSessionError("MANUAL_SESSION_INVALID")
    epoch = session.get("epoch")
    if type(epoch) is not int or not 1 <= epoch <= 2_147_483_647:
        raise ManualSessionError("MANUAL_SESSION_INVALID")
    kind = str(session.get("kind") or "").upper()
    source = str(session.get("source") or "").upper()
    status = str(session.get("status") or "").upper()
    if kind not in SESSION_KINDS or source not in SESSION_SOURCES or status not in SESSION_STATUSES:
        raise ManualSessionError("MANUAL_SESSION_INVALID")
    keys = session.get("confirmed_block_keys")
    if not isinstance(keys, list) or len(keys) > MAX_BLOCK_KEYS:
        raise ManualSessionError("MANUAL_SESSION_INVALID")
    normalized_keys = [_text(item, "confirmed_block_keys", 512) for item in keys]
    if len(set(normalized_keys)) != len(normalized_keys):
        raise ManualSessionError("MANUAL_SESSION_INVALID")
    choice = session.get("water_choice")
    if choice is not None:
        choice = str(choice).upper()
        if choice not in WATER_CHOICES:
            raise ManualSessionError("MANUAL_SESSION_INVALID")
    conflict_id = session.get("water_conflict_id")
    if conflict_id is not None:
        conflict_id = _text(conflict_id, "water_conflict_id", 512)
        if choice is None:
            raise ManualSessionError("MANUAL_SESSION_INVALID")
    elif choice is not None:
        raise ManualSessionError("MANUAL_SESSION_INVALID")
    created = _parse_time(session.get("created_at_utc"))
    prepared_until = _optional_time(session.get("prepared_until_utc"))
    departed = _optional_time(session.get("departure_observed_utc"))
    ended = _optional_time(session.get("ended_at_utc"))
    dry_until = _optional_time(session.get("confirmed_dry_until_utc"))
    if prepared_until is not None and not created < prepared_until <= created + PREPARED_START_LIFETIME:
        raise ManualSessionError("MANUAL_SESSION_INVALID")
    if kind == "START" and status == "PREPARED" and prepared_until is None:
        raise ManualSessionError("MANUAL_SESSION_INVALID")
    if kind == "PARK" and prepared_until is not None:
        raise ManualSessionError("MANUAL_SESSION_INVALID")
    if departed is not None and departed < created:
        raise ManualSessionError("MANUAL_SESSION_INVALID")
    if ended is not None and (ended < created or (departed is not None and ended < departed)):
        raise ManualSessionError("MANUAL_SESSION_INVALID")
    if status == "ENDED" and ended is None:
        raise ManualSessionError("MANUAL_SESSION_INVALID")
    if status != "ENDED" and ended is not None:
        raise ManualSessionError("MANUAL_SESSION_INVALID")
    if kind == "PARK" and departed is not None:
        raise ManualSessionError("MANUAL_SESSION_INVALID")
    if dry_until is not None and dry_until <= created:
        raise ManualSessionError("MANUAL_SESSION_INVALID")
    return {
        "version": SESSION_VERSION,
        "session_id": _text(session.get("session_id"), "session_id", 64),
        "epoch": epoch,
        "mower_id": _text(session.get("mower_id"), "mower_id", 128),
        "kind": kind,
        "source": source,
        "status": status,
        "created_at_utc": created.isoformat(),
        "prepared_until_utc": prepared_until.isoformat() if prepared_until else None,
        "departure_observed_utc": departed.isoformat() if departed else None,
        "ended_at_utc": ended.isoformat() if ended else None,
        "confirmed_block_keys": normalized_keys,
        "confirmed_dry_until_utc": dry_until.isoformat() if dry_until else None,
        "water_conflict_id": conflict_id,
        "water_choice": choice,
    }


def load_manual_session(value: Any) -> dict[str, Any] | None:
    """Load the one bounded session from a state object, JSON text, or None."""
    if value is None:
        return None
    raw = getattr(value, "manual_session_json", value)
    if raw in (None, ""):
        return None
    if not isinstance(raw, str) or len(raw.encode("utf-8")) > MAX_SESSION_BYTES:
        raise ManualSessionError("MANUAL_SESSION_INVALID")
    try:
        decoded = json.loads(raw)
    except (TypeError, ValueError) as exc:
        raise ManualSessionError("MANUAL_SESSION_INVALID") from exc
    return _normalize(decoded)


def dump_manual_session(session: Mapping[str, Any] | None) -> str | None:
    """Validate and serialize deterministic state-table JSON."""
    if session is None:
        return None
    payload = json.dumps(_normalize(session), ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    if len(payload.encode("utf-8")) > MAX_SESSION_BYTES:
        raise ManualSessionError("MANUAL_SESSION_INVALID")
    return payload


def new_session(
    *, session_id: str, epoch: int, mower_id: str, kind: str, source: str,
    now_utc: datetime, confirmed_block_keys: list[str] | tuple[str, ...] = (),
    confirmed_dry_until_utc: datetime | None = None, water_conflict_id: str | None = None,
    water_choice: str | None = None,
) -> dict[str, Any]:
    """Create a PREPARED session; callers CAS it with the legacy request."""
    now = _utc(now_utc)
    normalized_kind = str(kind or "").upper()
    session = {
        "version": SESSION_VERSION,
        "session_id": session_id,
        "epoch": epoch,
        "mower_id": mower_id,
        "kind": normalized_kind,
        "source": source,
        "status": "PREPARED",
        "created_at_utc": now.isoformat(),
        "prepared_until_utc": (
            (now + PREPARED_START_LIFETIME).isoformat() if normalized_kind == "START" else None
        ),
        "departure_observed_utc": None,
        "ended_at_utc": None,
        "confirmed_block_keys": list(confirmed_block_keys),
        "confirmed_dry_until_utc": (
            _utc(confirmed_dry_until_utc).isoformat() if confirmed_dry_until_utc else None
        ),
        "water_conflict_id": water_conflict_id,
        "water_choice": water_choice,
    }
    return _normalize(session)


def with_session_status(session: Mapping[str, Any], status: str, *, now_utc: datetime) -> dict[str, Any]:
    """Return a validated transition; only the full runner persists it by CAS."""
    current = _normalize(session)
    target = str(status or "").upper()
    if target not in SESSION_STATUSES:
        raise ManualSessionError("MANUAL_SESSION_INVALID")
    now = _utc(now_utc)
    if current["status"] == "ENDED" and target != "ENDED":
        raise ManualSessionError("MANUAL_SESSION_INVALID")
    if target == "ENDED":
        current["ended_at_utc"] = now.isoformat()
    elif current["ended_at_utc"] is not None:
        raise ManualSessionError("MANUAL_SESSION_INVALID")
    current["status"] = target
    return _normalize(current)


def _mower_observed_at(mower: Mapping[str, Any], now_utc: datetime) -> datetime | None:
    try:
        observed = datetime.fromtimestamp(float(mower.get("status_timestamp_ms")) / 1000, timezone.utc)
    except (TypeError, ValueError, OverflowError, OSError):
        return None
    age = (now_utc - observed).total_seconds()
    return observed if mower.get("connected") is True and -60 <= age <= 180 else None


def observe_session(
    session: Mapping[str, Any], mower: Mapping[str, Any], *, now_utc: datetime,
) -> tuple[dict[str, Any], bool]:
    """Observe start departure and its later return; never attribute external control."""
    current = _normalize(session)
    now = _utc(now_utc)
    observed = _mower_observed_at(mower, now)
    if observed is None or str(mower.get("mower_id") or "").strip() != current["mower_id"]:
        return current, False
    if current["kind"] != "START" or current["status"] in {"UNKNOWN", "ENDED"}:
        return current, False
    created = _parse_time(current["created_at_utc"])
    if observed < created:
        return current, False
    prepared = _optional_time(current["prepared_until_utc"])
    if current["status"] in {"PREPARED", "PENDING"} and (
        prepared is None or observed >= prepared
    ):
        current["status"] = "ENDED"
        current["ended_at_utc"] = observed.isoformat()
        return _normalize(current), True
    activity = str(mower.get("activity") or "").upper()
    if current["departure_observed_utc"] is None and activity in {"MOWING", "LEAVING"}:
        current["departure_observed_utc"] = observed.isoformat()
        current["status"] = "ACTIVE"
        return _normalize(current), True
    departure = _optional_time(current["departure_observed_utc"])
    if departure is not None and observed > departure and activity in {"GOING_HOME", "PARKED_IN_CS", "CHARGING"}:
        current["status"] = "ENDED"
        current["ended_at_utc"] = observed.isoformat()
        return _normalize(current), True
    return current, False


def block_key(block: Mapping[str, Any] | None) -> str | None:
    """Stable exact binding for an overrideable current occupancy block."""
    if not isinstance(block, Mapping):
        return None
    start = str(block.get("start") or "").strip()
    end = str(block.get("end") or "").strip()
    source = str(block.get("source") or "").strip().lower()
    if not start or not end or not source:
        return None
    try:
        _parse_time(start); _parse_time(end)
        canonical = json.dumps(block, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    except (ManualSessionError, TypeError, ValueError):
        return None
    if len(canonical.encode("utf-8")) > 8_192:
        return None
    return "|".join((start, end, source, hashlib.sha256(canonical.encode("utf-8")).hexdigest()))


def _safe_sources(block: Mapping[str, Any]) -> bool:
    # Reuse the established recursive source/binding-closure proof instead of
    # accepting a top-level training label that masks a nested hard block.
    from mower.safety import occupancy_override_allowed
    return occupancy_override_allowed(block)


def resolve_manual_permissions(
    session: Mapping[str, Any] | None, *, mower_id: str, now_utc: datetime,
    blocked_now: Mapping[str, Any] | None, parking_block: Mapping[str, Any] | None,
    hydrawise_safety: Mapping[str, Any] | None,
    hydrawise_release: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Resolve only current, explicitly confirmed START permissions.

    It never grants a start through active, unavailable, stale, or malformed
    irrigation data.  A future ``confirmed_dry_until_utc`` can only mark a
    dry-hold override after those water facts are proven separately.
    """
    if session is None:
        return {"allowed": False, "code": "MANUAL_SESSION_MISSING", "dry_override": False}
    try:
        current = _normalize(session)
        now = _utc(now_utc)
    except ManualSessionError:
        return {"allowed": False, "code": "MANUAL_SESSION_INVALID", "dry_override": False}
    if current["kind"] != "START" or current["status"] not in {"PREPARED", "PENDING", "ACTIVE"}:
        return {"allowed": False, "code": "MANUAL_SESSION_INACTIVE", "dry_override": False}
    if str(mower_id or "").strip() != current["mower_id"]:
        return {"allowed": False, "code": "MANUAL_SESSION_TARGET_CHANGED", "dry_override": False}
    prepared = _optional_time(current["prepared_until_utc"])
    if current["status"] in {"PREPARED", "PENDING"} and (prepared is None or now >= prepared):
        return {"allowed": False, "code": "MANUAL_SESSION_EXPIRED", "dry_override": False}
    safety = hydrawise_safety if isinstance(hydrawise_safety, Mapping) else None
    if safety is None or safety.get("available") is not True or safety.get("fresh") is not True or safety.get("relay_set_valid") is not True:
        return {"allowed": False, "code": "MANUAL_SESSION_WATER_UNKNOWN", "dry_override": False}
    active_ids = safety.get("active_relay_ids")
    imminent_ids = safety.get("imminent_relay_ids")
    try:
        active_count = int(safety.get("active_zone_count"))
        imminent_count = int(safety.get("imminent_zone_count"))
    except (TypeError, ValueError):
        return {"allowed": False, "code": "MANUAL_SESSION_WATER_UNKNOWN", "dry_override": False}
    if (
        safety.get("clear_now") is not True
        or active_count != 0 or imminent_count != 0
        or not isinstance(active_ids, (list, tuple)) or active_ids
        or not isinstance(imminent_ids, (list, tuple)) or imminent_ids
    ):
        return {"allowed": False, "code": "MANUAL_SESSION_WATER_ACTIVE", "dry_override": False}
    confirmed = set(current["confirmed_block_keys"])
    for block in (blocked_now, parking_block):
        if block in (None, {}):
            continue
        if not isinstance(block, Mapping) or not _safe_sources(block):
            return {"allowed": False, "code": "MANUAL_SESSION_BLOCK_FORBIDDEN", "dry_override": False}
        key = block_key(block)
        if key is None or key not in confirmed:
            return {"allowed": False, "code": "MANUAL_SESSION_BLOCK_CHANGED", "dry_override": False}
    dry_until = _optional_time(current["confirmed_dry_until_utc"])
    dry_override = False
    if dry_until is not None:
        release = hydrawise_release if isinstance(hydrawise_release, Mapping) else None
        try:
            release_until = _parse_time(release.get("dry_until_utc")) if release is not None else None
        except ManualSessionError:
            release_until = None
        if dry_until > now:
            if not (
                release_until is not None and release_until == dry_until and release_until > now
                and release is not None and release.get("telemetry_confirmed") is True
                and release.get("persistent_state_available") is True
                and release.get("current_water_clear") is True
            ):
                return {"allowed": False, "code": "MANUAL_SESSION_DRY_CONFIRMATION_CHANGED", "dry_override": False}
            dry_override = True
        else:
            # The old dry hold has ended. It cannot supply an override, but it
            # must not erase a separately valid occupancy approval. A newer
            # physical dry horizon requires a fresh explicit confirmation.
            if not (
                release is not None and release.get("allowed") is True
                and release.get("telemetry_confirmed") is True
                and release.get("persistent_state_available") is True
                and release.get("current_water_clear") is True
                and release_until == dry_until
            ):
                return {"allowed": False, "code": "MANUAL_SESSION_DRY_CONFIRMATION_CHANGED", "dry_override": False}
    return {
        "allowed": True, "code": "MANUAL_SESSION_ALLOWED", "dry_override": dry_override,
        "session_id": current["session_id"], "epoch": current["epoch"],
    }


def start_dispatch_permission(
    session: Mapping[str, Any] | None, *, session_id: str, epoch: int,
    mower_id: str, now_utc: datetime,
) -> dict[str, Any]:
    """Validate the session portion of the last-moment START fence.

    Occupancy and water-plan matching belong to the full-failsafe arbitration
    before reservation.  This function only proves that that same explicitly
    bound session remains current immediately before a device POST.
    """
    try:
        current = _normalize(session or {})
        now = _utc(now_utc)
    except ManualSessionError:
        return {"allowed": False, "code": "MANUAL_SESSION_INVALID", "dry_override": False}
    if current["session_id"] != str(session_id or "").strip() or current["epoch"] != epoch:
        return {"allowed": False, "code": "MANUAL_SESSION_FENCE_CHANGED", "dry_override": False}
    if current["kind"] != "START" or current["status"] not in {"PREPARED", "PENDING", "ACTIVE"}:
        return {"allowed": False, "code": "MANUAL_SESSION_INACTIVE", "dry_override": False}
    if current["mower_id"] != str(mower_id or "").strip():
        return {"allowed": False, "code": "MANUAL_SESSION_TARGET_CHANGED", "dry_override": False}
    prepared = _optional_time(current["prepared_until_utc"])
    if current["status"] in {"PREPARED", "PENDING"} and (prepared is None or now >= prepared):
        return {"allowed": False, "code": "MANUAL_SESSION_EXPIRED", "dry_override": False}
    return {
        "allowed": True, "code": "MANUAL_SESSION_ALLOWED",
        # Dispatch must still require hydrawise.safety.clear_now.  This flag
        # is exclusively for the full-failsafe's separate drying release gate.
        "dry_override": False,
    }
