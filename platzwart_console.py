from __future__ import annotations

import base64
import hashlib
import hmac
import json
import importlib
import secrets
import threading
import urllib.parse
import urllib.request
from copy import deepcopy
from pathlib import Path
from zoneinfo import ZoneInfo
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from typing import Any, Mapping

from azure.core import MatchConditions
from azure.core.exceptions import (
    ResourceExistsError,
    ResourceModifiedError,
    ResourceNotFoundError,
)
from azure.data.tables import TableClient, UpdateMode
from azure.identity import ManagedIdentityCredential

from mower.dry_run import run_read_only_cycle
from mower.cutting_height import (
    LOW_HEIGHT_WARNING_BELOW_MM,
    MAXIMUM_MM,
    MINIMUM_MM,
    RECOMMENDED_MINIMUM_MM,
    cutting_height_percent_to_mm,
    supports_metric_cutting_height,
)
from mower.runtime import ControlMode, CycleResult, RuntimeSettings
from mower.state import AutomationState
from mower.state_store import AzureTableStateStore, StateConflictError
from mower.safety import occupancy_override_allowed
from mower.irrigation_schedule import (
    IrrigationOperatingWindowError,
    IrrigationScheduleValidationError,
    SCHEDULE_ACTIONS,
    START_BLOCKING_SCHEDULE_STATUSES,
    dump_object as dump_irrigation_schedule_object,
    load_history as load_irrigation_schedule_history,
    load_object as load_irrigation_schedule_object,
    validate_schedule_request,
)
from daily_safety_report import dashboard_irrigation_statistics, dashboard_statistics, estimate_charging_end, estimate_charging_display_end
from mower.charging_forecast_store import display as validated_charging_display
from mower.statistics_cache import (
    peek_dashboard_statistics,
    get_dashboard_statistics,
    _STATISTICS_CACHE,
    _STATISTICS_CACHE_LOCK,
)
from occupancy.runtime_source import resolve_occupancy_match_source
from occupancy.training_control import (
    TrainingControlChanged,
    TrainingControlSnapshot,
    control_enabled as training_control_enabled,
    next_local_midnight,
    resolve_training_control,
    schedule_winter_training,
    snapshot_from_state as training_control_from_state,
)


PIN_ITERATIONS_MINIMUM = 200_000
SESSION_MINUTES = 30
REQUEST_MINUTES = 10
DEFAULT_LOGIN_RETENTION_DAYS = 7
DEFAULT_CONSOLE_AUDIT_RETENTION_DAYS = 180
ALLOWED_ACTIONS = frozenset(
    {
        "PARK_MOWER", "START_MOWING", "START_IRRIGATION",
        "START_IRRIGATION_ZONE", "STOP_IRRIGATION_AFTER_ZONE",
        "STOP_IRRIGATION_NOW",
        "SET_CUTTING_HEIGHT",
        "RESET_BLADE_USAGE",
        "MANUAL_CONTROL",
        "SET_WINTER_TRAINING",
        *SCHEDULE_ACTIONS,
    }
)

_CLUBHOUSE_CACHE_LOCK = threading.Lock()
_CLUBHOUSE_CACHE: dict[str, Any] = {"expires": None, "events": [], "available": False}
_IRRIGATION_STATISTICS_CACHE_LOCK = threading.Lock()
_IRRIGATION_STATISTICS_CACHE: dict[str, Any] = {"expires": None, "available": False}
_MATCH_DISPLAY_CACHE_LOCK = threading.Lock()
_MATCH_DISPLAY_CACHE: dict[str, Any] = {"path": None, "mtime_ns": None, "matches": {}}
_CONTROLLER_IRRIGATION_PHASES = frozenset(
    {"PLANNED", "SUSPENDING", "READY", "START_RESERVED", "RUNNING", "STOPPING"}
)


def _irrigation_intent_payload(
    state: AutomationState,
    environment: Mapping[str, str],
    *,
    state_available: bool,
) -> dict[str, Any]:
    """Project verified controller provenance for display, never authorization."""

    phase = str(state.irrigation_phase or "").strip().upper()
    active = phase in _CONTROLLER_IRRIGATION_PHASES
    unknown = {
        "source": "UNKNOWN" if active or not state_available else None,
        "verified": False,
        "controllerManaged": active and state_available,
        "automaticWindowApplies": None,
    }
    if not state_available or not active:
        return unknown
    try:
        plan = json.loads(state.irrigation_plan_json or "")
        expected_count = int(
            str(environment.get("HYDRAWISE_EXPECTED_ZONE_COUNT", "7")).strip()
        )
        expected_relays = {
            int(value.strip())
            for value in str(environment.get("HYDRAWISE_EXPECTED_RELAY_IDS", "")).split(",")
            if value.strip()
        }
        relay_ids = [
            zone.get("relay_id")
            for zone in plan
            if isinstance(zone, dict)
        ]
        canonical = json.dumps(
            plan, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        )
        plan_verified = (
            isinstance(plan, list)
            and len(plan) == expected_count
            and len(relay_ids) == expected_count
            and all(type(relay_id) is int and relay_id > 0 for relay_id in relay_ids)
            and len(set(relay_ids)) == expected_count
            and set(relay_ids) == expected_relays
            and bool(state.irrigation_plan_id)
            and hashlib.sha256(canonical.encode("utf-8")).hexdigest()
            == state.irrigation_plan_id
        )
    except (AttributeError, TypeError, ValueError, json.JSONDecodeError):
        return unknown
    if not plan_verified:
        return unknown
    manual_flags = [zone.get("operator_manual") is True for zone in plan]
    if any(manual_flags) and not all(manual_flags):
        return unknown
    manual = all(manual_flags)
    return {
        "source": "MANUAL_OPERATOR" if manual else "AUTOMATIC",
        "verified": True,
        "controllerManaged": True,
        "automaticWindowApplies": not manual,
    }


def _dashboard_statistics(environment: Mapping[str, str], now_utc: datetime) -> dict[str, Any]:
    return get_dashboard_statistics(environment, now_utc, loader=dashboard_statistics)


def _peek_display_cache(cache: dict, lock: Any, now_utc: datetime) -> dict[str, Any]:
    """No I/O and no waiting behind an in-flight optional display query."""
    pending = {"available": False, "loading": True}
    if not lock.acquire(blocking=False):
        return pending
    try:
        expires = cache.get("expires")
        if isinstance(expires, datetime) and expires - timedelta(minutes=5) <= now_utc < expires:
            return deepcopy({key: value for key, value in cache.items() if key != "expires"})
        return pending
    except Exception:
        cache.clear()
        return pending
    finally:
        lock.release()


def _drop_display_cache(cache: dict, lock: Any) -> None:
    if lock.acquire(blocking=False):
        try:
            cache.clear()
        finally:
            lock.release()


def _dashboard_irrigation_statistics(
    environment: Mapping[str, str],
    now_utc: datetime,
) -> dict[str, Any]:
    with _IRRIGATION_STATISTICS_CACHE_LOCK:
        expires = _IRRIGATION_STATISTICS_CACHE.get("expires")
        if isinstance(expires, datetime) and now_utc < expires:
            return {
                key: value
                for key, value in _IRRIGATION_STATISTICS_CACHE.items()
                if key != "expires"
            }
    try:
        payload = dashboard_irrigation_statistics(now_utc, environment)
        payload["message"] = None
    except Exception:
        payload = {
            "available": False,
            "message": "Die Beregnungsstatistik ist gerade nicht erreichbar.",
        }
    with _IRRIGATION_STATISTICS_CACHE_LOCK:
        _IRRIGATION_STATISTICS_CACHE.clear()
        _IRRIGATION_STATISTICS_CACHE.update(payload)
        _IRRIGATION_STATISTICS_CACHE["expires"] = now_utc + timedelta(minutes=5)
    return payload


def _display_match_index(
    environment: Mapping[str, str],
    now_utc: datetime,
) -> dict[str, dict[str, Any]]:
    try:
        path = Path(
            resolve_occupancy_match_source(
                environment,
                now_utc=now_utc,
            ).matches_path
        )
        mtime_ns = path.stat().st_mtime_ns
    except (OSError, RuntimeError, ValueError):
        return {}
    with _MATCH_DISPLAY_CACHE_LOCK:
        if (
            _MATCH_DISPLAY_CACHE.get("path") == str(path)
            and _MATCH_DISPLAY_CACHE.get("mtime_ns") == mtime_ns
        ):
            return dict(_MATCH_DISPLAY_CACHE.get("matches") or {})
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        matches = payload.get("matches") if isinstance(payload, dict) else None
        if not isinstance(matches, list) or len(matches) > 5000:
            return {}
        index = {
            str(item.get("id") or ""): item
            for item in matches
            if isinstance(item, dict) and str(item.get("id") or "")
        }
    except (OSError, UnicodeError, json.JSONDecodeError):
        return {}
    with _MATCH_DISPLAY_CACHE_LOCK:
        _MATCH_DISPLAY_CACHE.update(
            {"path": str(path), "mtime_ns": mtime_ns, "matches": index}
        )
    return dict(index)


def _match_id_from_uid(value: Any) -> str:
    uid = str(value or "").strip().split("@", 1)[0]
    if uid.startswith("dfb-"):
        return "dfb:" + uid[4:]
    return uid


def _enrich_display_block(
    value: Any,
    matches: Mapping[str, Mapping[str, Any]],
) -> dict[str, Any] | None:
    if not isinstance(value, dict):
        return None
    block = dict(value)
    details = dict(block.get("details") or {})
    items = details.get("items")
    if isinstance(items, list):
        details["items"] = [
            enriched
            for item in items
            if (enriched := _enrich_display_block(item, matches)) is not None
        ]
    if str(block.get("source") or "").lower() == "match":
        match = matches.get(_match_id_from_uid(details.get("uid")))
        if isinstance(match, Mapping):
            details.update(
                {
                    "kickoff": match.get("kickoff") or match.get("start"),
                    "match_end": match.get("end"),
                    "team": match.get("team"),
                    "teamCategory": match.get("teamCategory"),
                    "matchType": match.get("matchType"),
                }
            )
    block["details"] = details
    return block


def _display_current_plan(
    current_plan: Mapping[str, Any],
    environment: Mapping[str, str],
    now_utc: datetime,
) -> dict[str, Any]:
    display = dict(current_plan)
    matches = _display_match_index(environment, now_utc)
    for name in ("blocked_now", "next_block", "parking_block"):
        display[name] = _enrich_display_block(current_plan.get(name), matches)
    display["upcoming_blocks"] = [
        enriched
        for block in current_plan.get("upcoming_blocks") or []
        if (enriched := _enrich_display_block(block, matches)) is not None
    ]
    return display


class PlatzwartError(RuntimeError):
    def __init__(self, code: str, message: str, status_code: int = 400) -> None:
        super().__init__(message)
        self.code = code
        self.status_code = status_code


def create_pin_hash(pin: str, *, salt: bytes | None = None, iterations: int = 310_000) -> str:
    if len(pin) != 4 or not pin.isdigit():
        raise ValueError("Die Platzwart-PIN muss genau vier Ziffern enthalten.")
    if iterations < PIN_ITERATIONS_MINIMUM:
        raise ValueError("Die PBKDF2-Iterationszahl ist zu niedrig.")
    actual_salt = salt or secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", pin.encode("ascii"), actual_salt, iterations)
    return "pbkdf2_sha256${}${}${}".format(
        iterations,
        base64.urlsafe_b64encode(actual_salt).decode("ascii").rstrip("="),
        base64.urlsafe_b64encode(digest).decode("ascii").rstrip("="),
    )


def _b64decode(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


def verify_pin(pin: str, encoded: str) -> bool:
    try:
        algorithm, raw_iterations, raw_salt, raw_digest = encoded.split("$", 3)
        iterations = int(raw_iterations)
        if algorithm != "pbkdf2_sha256" or iterations < PIN_ITERATIONS_MINIMUM:
            return False
        if len(pin) != 4 or not pin.isdigit():
            return False
        actual = hashlib.pbkdf2_hmac(
            "sha256", pin.encode("ascii"), _b64decode(raw_salt), iterations
        )
        return hmac.compare_digest(actual, _b64decode(raw_digest))
    except (TypeError, ValueError):
        return False


def create_activation_hash(code: str) -> str:
    normalized = code.strip()
    if len(normalized) < 20:
        raise ValueError("Der Aktivierungscode muss mindestens 20 Zeichen lang sein.")
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def _secret(environment: Mapping[str, str]) -> bytes:
    value = str(environment.get("SSV53_PLATZWART_SESSION_SECRET", "")).strip()
    if len(value) < 32:
        raise PlatzwartError("CONSOLE_NOT_CONFIGURED", "Platzwart-Zugang ist nicht konfiguriert.", 503)
    return value.encode("utf-8")


def _b64encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode("ascii").rstrip("=")


def issue_session(
    environment: Mapping[str, str],
    now_utc: datetime,
    device_id: str,
) -> tuple[str, str]:
    now = now_utc.astimezone(timezone.utc)
    expires = now + timedelta(minutes=SESSION_MINUTES)
    payload = {
        "aud": "ssv53-platzwart",
        "iat": int(now.timestamp()),
        "exp": int(expires.timestamp()),
        "jti": secrets.token_urlsafe(12),
        "did": device_id,
    }
    body = _b64encode(json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8"))
    signature = _b64encode(hmac.new(_secret(environment), body.encode("ascii"), hashlib.sha256).digest())
    return f"{body}.{signature}", expires.isoformat()


def require_session(token: str, environment: Mapping[str, str], now_utc: datetime) -> dict[str, Any]:
    try:
        body, signature = token.split(".", 1)
        expected = _b64encode(hmac.new(_secret(environment), body.encode("ascii"), hashlib.sha256).digest())
        if not hmac.compare_digest(signature, expected):
            raise ValueError
        payload = json.loads(_b64decode(body))
        now = int(now_utc.astimezone(timezone.utc).timestamp())
        if payload.get("aud") != "ssv53-platzwart" or int(payload["iat"]) > now + 30:
            raise ValueError
        if now >= int(payload["exp"]):
            raise PlatzwartError("SESSION_EXPIRED", "Die Anmeldung ist abgelaufen.", 401)
        return payload
    except PlatzwartError:
        raise
    except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise PlatzwartError("SESSION_INVALID", "Bitte erneut mit der Platzwart-PIN anmelden.", 401) from exc


class ConsoleTableStore:
    PARTITION = "ssv53-platzwart"

    def __init__(self, client: TableClient) -> None:
        self.client = client

    @classmethod
    def from_environment(cls, environment: Mapping[str, str]) -> "ConsoleTableStore":
        endpoint = str(environment.get("SSV53_STORAGE_ACCOUNT_URL", "")).strip()
        table_name = str(environment.get("SSV53_STATE_TABLE_NAME", "")).strip()
        client_id = str(
            environment.get("SSV53_STATE_MANAGED_IDENTITY_CLIENT_ID")
            or environment.get("AzureWebJobsStorage__clientId")
            or ""
        ).strip()
        if not endpoint or not table_name or not client_id:
            raise PlatzwartError("CONSOLE_NOT_CONFIGURED", "Platzwart-Zugang ist nicht konfiguriert.", 503)
        return cls(
            TableClient(
                endpoint=endpoint,
                table_name=table_name,
                credential=ManagedIdentityCredential(client_id=client_id),
            )
        )

    def check_login(self, client_key: str, now_utc: datetime) -> None:
        try:
            entity = self.client.get_entity(
                partition_key=self.PARTITION,
                row_key=f"login-{client_key}",
            )
        except ResourceNotFoundError:
            return
        locked_until = str(entity.get("locked_until_utc") or "")
        if locked_until and datetime.fromisoformat(locked_until).astimezone(timezone.utc) > now_utc:
            raise PlatzwartError("LOGIN_LOCKED", "Zu viele Fehlversuche. Bitte 30 Minuten warten.", 429)

    def record_login(self, client_key: str, now_utc: datetime, *, success: bool) -> None:
        if success:
            return
        self.client.create_entity(
            {
                "PartitionKey": self.PARTITION,
                "RowKey": f"loginfail-{client_key}-{now_utc.strftime('%Y%m%d%H%M%S%f')}-{secrets.token_hex(3)}",
                "client_key": client_key,
                "failure_utc": now_utc,
            }
        )
        recent = list(
            self.client.query_entities(
                query_filter=(
                    "PartitionKey eq @partition and client_key eq @client "
                    "and failure_utc ge @since"
                ),
                parameters={
                    "partition": self.PARTITION,
                    "client": client_key,
                    "since": now_utc - timedelta(minutes=15),
                },
                select=["RowKey"],
            )
        )
        if len(recent) >= 5:
            self.client.upsert_entity(
                {
                    "PartitionKey": self.PARTITION,
                    "RowKey": f"login-{client_key}",
                    "locked_until_utc": (now_utc + timedelta(minutes=30)).isoformat(),
                    "updated_utc": now_utc.isoformat(),
                },
                mode=UpdateMode.REPLACE,
            )

    def audit(self, now_utc: datetime, action: str, result: str, request_id: str = "") -> None:
        nonce = secrets.token_hex(6)
        self.client.create_entity(
            {
                "PartitionKey": self.PARTITION,
                "RowKey": f"audit-{now_utc.strftime('%Y%m%d%H%M%S%f')}-{nonce}",
                "timestamp_utc": now_utc.isoformat(),
                "action": action[:48],
                "result": result[:96],
                "request_id": request_id[:64],
            }
        )

    def enroll_device(
        self,
        now_utc: datetime,
        activation_fingerprint: str,
        signing_secret: bytes,
    ) -> dict[str, str]:
        device_id = secrets.token_hex(12)
        device_token = secrets.token_urlsafe(32)
        token_hash = hmac.new(
            signing_secret,
            b"device:" + device_token.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()
        # Der Claim wird zuerst atomar angelegt. Damit ist selbst bei zwei
        # gleichzeitigen Requests nur genau eine Verwendung möglich.
        self.client.create_entity(
            {
                "PartitionKey": self.PARTITION,
                "RowKey": f"activation-{activation_fingerprint}",
                "used_utc": now_utc.isoformat(),
                "device_id": device_id,
            }
        )
        self.client.create_entity(
            {
                "PartitionKey": self.PARTITION,
                "RowKey": f"device-{device_id}",
                "token_hash": token_hash,
                "created_utc": now_utc.isoformat(),
                "revoked": False,
            }
        )
        return {"deviceId": device_id, "deviceToken": device_token}

    def verify_device(
        self,
        device_id: str,
        device_token: str,
        signing_secret: bytes,
    ) -> bool:
        if not device_id or not device_token:
            return False
        try:
            entity = self.client.get_entity(
                partition_key=self.PARTITION,
                row_key=f"device-{device_id}",
            )
        except ResourceNotFoundError:
            return False
        expected = str(entity.get("token_hash") or "")
        actual = hmac.new(
            signing_secret,
            b"device:" + device_token.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()
        return not bool(entity.get("revoked")) and hmac.compare_digest(expected, actual)

    def cleanup_retention(
        self,
        *,
        now_utc: datetime,
        login_retention_days: int = DEFAULT_LOGIN_RETENTION_DAYS,
        audit_retention_days: int = DEFAULT_CONSOLE_AUDIT_RETENTION_DAYS,
    ) -> dict[str, int]:
        """Löscht alte Login- und Auditmetadaten, niemals Gerätefreigaben."""

        now = now_utc.astimezone(timezone.utc)
        login_cutoff = now - timedelta(days=max(1, login_retention_days))
        audit_cutoff = now - timedelta(days=max(1, audit_retention_days))
        result = {"deleted": 0, "skipped": 0}
        entities = self.client.query_entities(
            query_filter="PartitionKey eq @partition",
            parameters={"partition": self.PARTITION},
        )
        for raw_entity in entities:
            entity = dict(raw_entity)
            concurrency = _retention_concurrency(raw_entity)
            row_key = str(entity.get("RowKey") or "")
            try:
                if row_key.startswith("loginfail-"):
                    delete = _retention_datetime(entity.get("failure_utc")) < login_cutoff
                elif row_key.startswith("login-"):
                    # Sperren bleiben bis zum Ablauf plus Aufbewahrungsfrist
                    # bestehen; ein Cleanup kann daher nie eine aktive Sperre
                    # vorzeitig aufheben.
                    delete = _retention_datetime(
                        entity.get("locked_until_utc") or entity.get("updated_utc")
                    ) < login_cutoff
                elif row_key.startswith("audit-"):
                    delete = _retention_datetime(entity.get("timestamp_utc")) < audit_cutoff
                else:
                    # activation-* und device-* sind für die einmalige
                    # Gerätefreischaltung dauerhaft erforderlich.
                    continue
                if delete:
                    if concurrency is None:
                        result["skipped"] += 1
                        continue
                    self.client.delete_entity(
                        partition_key=self.PARTITION,
                        row_key=row_key,
                        **concurrency,
                    )
                    result["deleted"] += 1
            except (
                TypeError,
                ValueError,
                ResourceModifiedError,
                ResourceNotFoundError,
            ):
                result["skipped"] += 1
        return result


def _retention_datetime(value: Any) -> datetime:
    if isinstance(value, datetime):
        parsed = value
    else:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("Aufbewahrungszeitpunkt benötigt eine Zeitzone")
    return parsed.astimezone(timezone.utc)


def _retention_concurrency(entity: Mapping[str, Any]) -> dict[str, Any] | None:
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


def client_key(remote_ip: str, environment: Mapping[str, str]) -> str:
    normalized = (remote_ip or "unknown").strip().split(",", 1)[0]
    return hmac.new(_secret(environment), normalized.encode("utf-8"), hashlib.sha256).hexdigest()[:32]


def enroll(
    activation_code: str,
    environment: Mapping[str, str],
    now_utc: datetime,
) -> dict[str, str]:
    signing_secret = _secret(environment)
    actual = create_activation_hash(activation_code)
    expected = str(environment.get("SSV53_PLATZWART_ACTIVATION_HASH", "")).strip().lower()
    if len(expected) != 64 or not hmac.compare_digest(actual, expected):
        raise PlatzwartError("ACTIVATION_INVALID", "Der Aktivierungscode ist nicht gültig.", 401)
    store = ConsoleTableStore.from_environment(environment)
    try:
        result = store.enroll_device(now_utc, actual[:32], signing_secret)
    except ResourceExistsError as exc:
        raise PlatzwartError(
            "ACTIVATION_USED",
            "Dieser Aktivierungscode wurde bereits verwendet.",
            409,
        ) from exc
    store.audit(now_utc, "DEVICE_ENROLLMENT", "SUCCESS")
    return result


def login(
    pin: str,
    device_id: str,
    device_token: str,
    remote_ip: str,
    environment: Mapping[str, str],
    now_utc: datetime,
) -> dict[str, Any]:
    signing_secret = _secret(environment)
    store = ConsoleTableStore.from_environment(environment)
    ip_key = client_key(remote_ip, environment)
    device_key = hashlib.sha256(device_id.encode("utf-8")).hexdigest()[:32]
    store.check_login(ip_key, now_utc)
    store.check_login(device_key, now_utc)
    valid_device = store.verify_device(device_id, device_token, signing_secret)
    valid = valid_device and verify_pin(
        pin,
        str(environment.get("SSV53_PLATZWART_PIN_HASH", "")),
    )
    store.record_login(ip_key, now_utc, success=valid)
    store.record_login(device_key, now_utc, success=valid)
    store.audit(now_utc, "LOGIN", "SUCCESS" if valid else "REJECTED")
    if not valid:
        raise PlatzwartError("PIN_INVALID", "Die PIN ist nicht richtig.", 401)
    token, expires = issue_session(environment, now_utc, device_id)
    return {"token": token, "expiresAt": expires, "sessionMinutes": SESSION_MINUTES}


def _state_payload(state: AutomationState) -> dict[str, Any]:
    return {
        "revision": state.revision,
        "lastCycleAt": state.last_cycle_started_utc,
        "lastSuccessAt": state.last_success_utc,
        "decisionCode": state.last_decision_code,
        "mowerActivity": state.last_mower_activity,
        "mowerState": state.last_mower_state,
        "mowerErrorCode": state.last_error_code,
        "parkedByAutomation": state.parked_by_automation,
        "parkReason": state.automation_park_source,
        "parkConfirmedAt": state.park_confirmed_utc,
        "continuousMowingOwned": state.continuous_mowing_owned,
        "irrigationPhase": state.irrigation_phase,
        "irrigationCompletedAt": state.irrigation_completed_utc,
        "hydrawiseClearSince": state.hydrawise_clear_since_utc,
        "hydrawiseClearOrigin": state.hydrawise_clear_origin,
        "hydrawiseDryingSince": state.hydrawise_drying_since_utc,
        "mowerStartOutcomeUnconfirmed": state.mower_start_pending_since_utc is not None,
        "pendingAction": state.operator_request_action if state.operator_request_status == "PENDING" else None,
        "pendingRequestedAt": state.operator_requested_utc if state.operator_request_status == "PENDING" else None,
        "lastOperatorAction": state.operator_request_action,
        "lastOperatorStatus": state.operator_request_status,
        "lastOperatorRequestedAt": state.operator_requested_utc,
        "lastOperatorResult": state.operator_request_result,
    }


def _mower_status_age_seconds(
    mower: Mapping[str, Any],
    now_utc: datetime,
) -> int | None:
    """Return the accepted Husqvarna status age used by all console gates."""

    try:
        observed_at = datetime.fromtimestamp(
            float(mower.get("status_timestamp_ms")) / 1000,
            timezone.utc,
        )
        age_seconds = (
            now_utc.astimezone(timezone.utc) - observed_at
        ).total_seconds()
    except (ValueError, TypeError, OverflowError, OSError):
        return None
    return round(age_seconds) if age_seconds >= -30 else None


def _mower_telemetry_fresh(
    mower: Mapping[str, Any],
    environment: Mapping[str, str],
    now_utc: datetime,
) -> tuple[bool, int | None]:
    """Keep the visible mower freshness exactly aligned with MOWER_TELEMETRY."""

    age_seconds = _mower_status_age_seconds(mower, now_utc)
    try:
        maximum_age = int(environment.get("MOWER_STATUS_MAX_AGE_SECONDS", "180"))
    except (TypeError, ValueError):
        # A malformed runtime value can never turn telemetry into a permit.
        return False, age_seconds
    return (
        mower.get("connected") is True
        and age_seconds is not None
        and age_seconds <= maximum_age,
        age_seconds,
    )


def _runtime_device_controls_enabled(settings: RuntimeSettings) -> bool:
    """The console may queue a device action only in the fully armed runtime."""

    return (
        settings.control_mode is ControlMode.FULL_FAILSAFE
        and settings.enable_live_reads
        and settings.full_failsafe_write_gate_enabled
    )


def _protection_payload(settings: RuntimeSettings) -> dict[str, bool]:
    """Expose the two independent safety outcomes used by the dashboard."""
    automatic_start = (
        settings.control_mode is ControlMode.FULL_FAILSAFE
        and settings.enable_live_reads
        and settings.full_failsafe_write_gate_enabled
    ) or (
        settings.control_mode is ControlMode.FULL_MOWER
        and settings.enable_live_reads
        and settings.full_mower_write_gate_enabled
    )
    if settings.control_mode is ControlMode.OPERATOR_ONLY:
        protective_parking = (
            settings.enable_operator_safety_guard
            and settings.operator_control_gate_enabled
            and settings.enable_park_commands
        )
    elif settings.control_mode in {
        ControlMode.PARK_ONLY, ControlMode.FULL_MOWER, ControlMode.FULL_FAILSAFE
    }:
        protective_parking = (
            settings.enable_live_reads
            and settings.enable_park_commands
            and (
                settings.control_mode is ControlMode.PARK_ONLY
                or settings.full_mower_write_gate_enabled
            )
        )
    else:
        protective_parking = False
    return {
        "automaticStartEnabled": bool(automatic_start),
        "protectiveParkingEnabled": bool(protective_parking),
    }


def _coordination_payload(details, state, current_plan, environment, now_utc, data_quality, *, charging_end_estimate=None, charging_display_estimate=None):
    """Read-only explanation of simultaneous conditions; never a start permit."""
    now = now_utc.astimezone(timezone.utc)
    mower = dict(details.get("mower") or {})
    water = dict(details.get("hydrawise") or {})
    safety = dict(water.get("safety") or {})
    release = dict(water.get("release_confirmation") or {})
    inputs = dict(details.get("input_files") or {})
    projected_automation = dict(details.get("automation_state") or {})
    drying_reason = projected_automation.get("clear_origin") or state.hydrawise_clear_origin
    blockers = []

    def age(value, *, milliseconds=False):
        try:
            stamp = datetime.fromtimestamp(float(value) / 1000, timezone.utc) if milliseconds else datetime.fromisoformat(str(value).replace("Z", "+00:00"))
            if stamp.tzinfo is None:
                return None
            elapsed = (now - stamp.astimezone(timezone.utc)).total_seconds()
            return round(elapsed) if elapsed >= -30 else None
        except (ValueError, TypeError, OverflowError, OSError):
            return None

    def add(code, label, condition, until=None):
        blockers.append({"code": code, "label": label, "resolution": condition, "until": until})

    if state.maintenance_mode or state.last_decision_code == "OPERATOR_PARK_HOLD" or (state.automation_park_source == "operator" and not state.automation_restart_allowed):
        add("MANUAL_STOP", "Manuelle Stoppsperre", "Ausdrückliche Freigabe durch den Platzwart und erneute Sicherheitsprüfung.")
    if state.mower_start_pending_since_utc is not None:
        add("START_UNCONFIRMED", "Wirkung eines Mäherstarts ungeklärt", "Gerätewarteschlange, nativen Zeitplan und sicheren Parkzustand vor manueller Freigabe abgleichen. Eine Parkmeldung allein hebt die Sperre nicht auf.")
    if _mower_error_active(mower):
        add("MOWER_ERROR", _mower_error_message(mower) or "Mäherstörung", "Fehler beheben; frische fehlerfreie Gerätemeldung erforderlich.")
    mower_fresh, mower_age = _mower_telemetry_fresh(mower, environment, now)
    if not mower_fresh:
        add("MOWER_TELEMETRY", "Mäherzustand nicht aktuell bestätigt", "Neue, verbundene Gerätemeldung innerhalb der Altersgrenze.")
    for block in (current_plan.get("blocked_now"), current_plan.get("parking_block")):
        if block and not any(item["code"] == "OCCUPANCY" for item in blockers):
            add("OCCUPANCY", str(block.get("title") or "Platzsperre einschließlich Vorlauf"), "Verbindliche Belegung und Schutzpuffer müssen beendet sein.", block.get("end"))
    if data_quality.get("displayOnly"):
        add("DATA_QUALITY", "Sicherheitsdaten nicht vollständig verfügbar", "Frischer validierter Plan und erreichbarer Automatikzustand.")
    if safety.get("available") is not True or safety.get("fresh") is not True:
        add("IRRIGATION_TELEMETRY", "Bewässerungszustand unbekannt", "Vollständige aktuelle Meldung aller freigegebenen Zonen.")
    elif safety.get("clear_now") is not True:
        add("IRRIGATION_ACTIVE_OR_DUE", "Bewässerung läuft oder steht bevor", "Bestätigtes Ende aller Zonen und Ablauf der Schutzzeiten.")
    if state.irrigation_phase and state.irrigation_phase != "COMPLETE_HOLD":
        add("IRRIGATION_SEQUENCE", "Bewässerungsablauf: " + state.irrigation_phase, "Sicherer Stationszustand, bestätigter Ablaufabschluss; bei Fehler manuelle Klärung.")
    if release and not release.get("allowed"):
        add("DRYING_OR_CONFIRMATION", "Trocknung oder erneute Datenbestätigung", "Physische Trocknungsfrist und aktuelle Datenbestätigung müssen beide erfüllt sein.", release.get("release_at_utc"))
    if mower.get("activity") == "CHARGING":
        add("CHARGING", "Akku wird geladen", "Geräteeigene Ladefreigabe und alle Sicherheitsbedingungen; Ladeende ist unbekannt.")
    controller_age = age(state.last_cycle_started_utc)
    if controller_age is None or controller_age > 180:
        add("CONTROLLER_STALE", "Letzte Steuerungsentscheidung nicht aktuell", "Erfolgreicher neuer Steuerungszyklus; Geräte können ihren eigenen Zustand beibehalten.")
    action_status = str(state.operator_request_status or "NONE")
    action_labels = {"PENDING": "Angefordert", "ACCEPTED": "Vom Dienst angenommen", "COMPLETE": "Dienstverarbeitung abgeschlossen", "COMPLETED": "Dienstverarbeitung abgeschlossen", "SUCCEEDED": "Dienstverarbeitung abgeschlossen", "SENT_UNCONFIRMED": "Gesendet, Wirkung ungeklärt", "FAILED": "Fehlgeschlagen", "EXPIRED": "Abgelaufen", "REJECTED": "Abgewiesen"}
    return {
        "explanationOnly": True, "primaryBlocker": blockers[0] if blockers else None,
        "blockers": blockers, "dryingMinutes": int(environment.get("POST_IRRIGATION_DRYING_MINUTES", "150")),
        "dryUntil": release.get("dry_until_utc"), "releaseNotBefore": release.get("release_at_utc"),
        "dryingReason": drying_reason,
        "telemetryConfirmed": release.get("telemetry_confirmed"),
        "chargingEndEstimate": charging_end_estimate,
        "chargingDisplayEstimate": charging_display_estimate,
        "dataAgeSeconds": {"mower": mower_age, "irrigation": age(safety.get("observed_at_utc")),
                           "controller": controller_age, "safetyBundle": age(inputs.get("published_at_utc"))},
        "importObservedAt": None,
        "lastAction": {"action": state.operator_request_action, "status": action_status,
                       "label": action_labels.get(action_status, "Kein bestätigter Abschluss"),
                       "requestedAt": state.operator_requested_utc,
                       "deviceExecutionConfirmed": False},
        "note": "Zeitangaben sind früheste Prüfzeitpunkte. Eine Befehlsannahme bestätigt keine physische Ausführung.",
    }


def _irrigation_schedule_payload(
    state: AutomationState,
    hydrawise_zones: list[dict[str, Any]],
) -> dict[str, Any]:
    try:
        override = load_irrigation_schedule_object(
            state.irrigation_schedule_override_json,
            "Beregnungsplan-Anpassung",
        )
    except RuntimeError as exc:
        return {
            "available": False,
            "message": str(exc),
            "override": None,
            "nextRun": None,
            "history": [],
        }
    try:
        history = load_irrigation_schedule_history(
            state.irrigation_schedule_history_json
        )
    except RuntimeError:
        history = []

    def public_zone(zone: Mapping[str, Any]) -> dict[str, Any]:
        return {
            "zone": int(zone.get("zone") or 0),
            "name": str(zone.get("name") or f"Zone {zone.get('zone') or '?'}"),
            "runSeconds": int(
                zone.get("run_seconds")
                or zone.get("runSeconds")
                or 0
            ),
            "selected": zone.get("selected") is not False,
            "start": zone.get("scheduled_start_utc"),
            "end": zone.get("scheduled_end_utc"),
        }

    source = [dict(zone) for zone in hydrawise_zones if isinstance(zone, dict)]
    if override and isinstance(override.get("zones"), list):
        source = [dict(zone) for zone in override["zones"] if isinstance(zone, dict)]
    elif override and isinstance(override.get("source_zones"), list):
        source = [
            dict(zone) for zone in override["source_zones"] if isinstance(zone, dict)
        ]
    public_zones = sorted(
        (public_zone(zone) for zone in source), key=lambda item: item["zone"]
    )
    starts = [str(zone.get("start") or "") for zone in public_zones if zone.get("start")]
    ends = [str(zone.get("end") or "") for zone in public_zones if zone.get("end")]
    next_run = (
        {
            "start": min(starts),
            "end": max(ends),
            "zones": public_zones,
            "selectedZoneCount": sum(1 for zone in public_zones if zone["selected"]),
        }
        if starts and ends and public_zones
        else None
    )
    public_override = None
    if override:
        public_override = {
            "kind": override.get("kind"),
            "status": override.get("status"),
            "createdAt": override.get("created_utc"),
            "suspendUntil": override.get("suspend_until_utc"),
            "sourceStart": override.get("source_start_utc"),
            "sourceEnd": override.get("source_end_utc"),
            "desiredStart": override.get("desired_start_utc"),
            "desiredEnd": override.get("desired_end_utc"),
            "confirmedAt": override.get("confirmed_utc"),
            "processedZones": len(override.get("commanded_relay_ids") or []),
            "totalZones": 7,
            "error": override.get("error"),
            "externalChangeConfirming": bool(
                override.get("external_resume_candidate_since_utc")
            ),
            "zones": public_zones,
        }
    return {
        "available": True,
        "message": None,
        "override": public_override,
        "nextRun": next_run,
        "history": history[:6],
    }


def _mower_display_activity(
    mower: Mapping[str, Any],
    _state: AutomationState,
) -> str:
    """Verdichtet gerätespezifische Husqvarna-Kombinationen für die UI.

    Der 580 EPOS meldet beim Suchen nach dem Satellitensignal zeitweise
    ``NOT_APPLICABLE`` + ``IN_OPERATION`` statt einer eigenen
    SEARCHING-Aktivität. ``mode`` beschreibt dabei den Zielbereich und darf
    nicht als Suche nach der Ladestation interpretiert werden.
    """

    activity = str(mower.get("activity") or "UNKNOWN").upper()
    inactive_reason = str(
        mower.get("inactive_reason") or mower.get("inactiveReason") or "NONE"
    ).upper()
    mower_state = str(mower.get("state") or "UNKNOWN").upper()
    mode = str(mower.get("mode") or "UNKNOWN").upper()
    if mower_state in {"ERROR", "FATAL_ERROR", "ERROR_AT_POWER_UP"}:
        return "ERROR"
    if mower_state == "PAUSED":
        return "PAUSED"
    if mower_state == "STOPPED" or activity == "STOPPED_IN_GARDEN":
        return "STOPPED"
    if mower_state in {"OFF", "WAIT_UPDATING", "WAIT_POWER_UP"}:
        return mower_state
    if inactive_reason == "SEARCHING_FOR_SATELLITES":
        return "SEARCHING_FOR_POSITION"
    if inactive_reason == "PLANNING":
        return "PLANNING"
    # A parked or charging station report is more informative than the broad
    # RESTRICTED state. ERROR, PAUSED and STOPPED above intentionally retain
    # their stronger operator-facing priority.
    if activity in {"PARKED_IN_CS", "CHARGING"}:
        return activity
    if mower_state == "RESTRICTED":
        return "RESTRICTED"
    if activity != "NOT_APPLICABLE" or mower_state != "IN_OPERATION":
        return activity
    if mode in {"HOME", "MAIN_AREA", "SECONDARY_AREA", "POI"}:
        return "SEARCHING_FOR_POSITION"
    return activity


_MOWER_ERROR_MESSAGES = {
    # Die Codes entsprechen der Status-/Fehlercodetabelle der Husqvarna
    # Automower Connect API. Unbekannte Codes bleiben sichtbar, werden aber
    # nicht als unverständlicher Rohstatus ausgegeben.
    78: "Mäher ist gerutscht",
    93: "Keine genaue Satellitenposition",
}


def _mower_error_active(mower: Mapping[str, Any]) -> bool:
    """Trennt einen aktiven Fehlerzustand von einem verzögert gelieferten Code.

    Die Sicherheitslogik verwendet weiterhin unverändert jeden Fehlercode als
    Startsperre. Für die Anzeige gilt ein Fehler aber nur dann als *aktuell*,
    wenn Husqvarna zugleich einen ERROR-Zustand meldet. So kann ein sauberer
    Folgezustand wie PAUSED nicht mehr als fortbestehender Fehler erscheinen.
    """

    try:
        error_code = int(mower.get("error_code") or mower.get("errorCode") or 0)
    except (TypeError, ValueError):
        error_code = 0
    mower_state = str(mower.get("state") or "UNKNOWN").upper()
    return error_code > 0 and mower_state in {
        "ERROR",
        "FATAL_ERROR",
        "ERROR_AT_POWER_UP",
    }


def _mower_error_message(mower: Mapping[str, Any]) -> str | None:
    try:
        error_code = int(mower.get("error_code") or mower.get("errorCode") or 0)
    except (TypeError, ValueError):
        return None
    if error_code <= 0:
        return None
    return _MOWER_ERROR_MESSAGES.get(error_code, f"Gerätefehler (Code {error_code})")


def _mower_display_label(
    mower: Mapping[str, Any],
    state: AutomationState,
) -> str:
    display_activity = _mower_display_activity(mower, state)
    if display_activity == "ERROR":
        return _mower_error_message(mower) or "Mäherfehler"
    if display_activity == "RESTRICTED":
        restricted_reason = str(
            mower.get("restricted_reason") or mower.get("restrictedReason") or "NONE"
        ).upper()
        return {
            "WEEK_SCHEDULE": "Wartet auf Zeitplan",
            "PARK_OVERRIDE": "Geparkt bis zur Freigabe",
            "SENSOR": "Pause durch Sensor",
            "DAILY_LIMIT": "Tagesziel erreicht",
            "FOTA": "Softwareupdate läuft",
            "FROST": "Frostschutz aktiv",
            "ALL_WORK_AREAS_COMPLETED": "Rasenfläche vollständig gemäht",
            "EXTERNAL": "Extern angehalten",
            "WORK_AREA_ABANDONED": "Arbeitsbereich abgebrochen",
        }.get(restricted_reason, "Zurzeit nicht im Mähbetrieb")
    return {
        "SEARCHING": "Sucht Satellitensignal",
        "SEARCHING_FOR_POSITION": "Sucht Satellitensignal",
        "SEARCHING_FOR_CHARGING_STATION": "Sucht Ladestation",
        "PLANNING": "Plant die Route",
        "MOWING": "Mäht",
        "LEAVING": "Fährt auf den Platz",
        "GOING_HOME": "Fährt zur Station",
        "PARKED_IN_CS": "In der Station",
        "CHARGING": "Lädt",
        "PAUSED": "Pausiert",
        "STOPPED": "Manuell gestoppt",
        "OFF": "Ausgeschaltet",
        "WAIT_UPDATING": "Softwareupdate läuft",
        "WAIT_POWER_UP": "Startet das System",
    }.get(display_activity, "Status wird geprüft")


def _appack_graphql(token: str, query: str, variables: Mapping[str, Any]) -> dict[str, Any]:
    body = json.dumps({"query": query, "variables": dict(variables)}).encode("utf-8")
    request = urllib.request.Request(
        "https://api.appack.de/graphql",
        data=body,
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "User-Agent": "SSV53-Platzwart/1.0",
        },
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=8) as response:
        payload = json.loads(response.read(512_000).decode("utf-8"))
    if not isinstance(payload, dict) or payload.get("errors"):
        raise RuntimeError("Appack-Reservierungsdaten konnten nicht gelesen werden.")
    return dict(payload.get("data") or {})


def _iso_datetime(value: Any) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("Zeitangabe enthält keine Zeitzone.")
    return parsed.astimezone(timezone.utc)


def _restart_battery_percent(environment: Mapping[str, str]) -> int:
    try:
        configured = int(environment.get("MOWER_RESTART_BATTERY_PERCENT", "90"))
    except (TypeError, ValueError):
        configured = 90
    return max(60, min(100, configured))


def _clubhouse_events(environment: Mapping[str, str], now_utc: datetime) -> dict[str, Any]:
    url = str(environment.get("SSV53_CLUBHOUSE_RESERVATION_URL") or "").strip()
    if not url:
        return {"available": False, "events": [], "message": "Vereinsheim-Reservierungen sind nicht eingerichtet."}
    now = now_utc.astimezone(timezone.utc)
    with _CLUBHOUSE_CACHE_LOCK:
        expires = _CLUBHOUSE_CACHE.get("expires")
        if isinstance(expires, datetime) and now < expires:
            return {
                "available": bool(_CLUBHOUSE_CACHE.get("available")),
                "events": list(_CLUBHOUSE_CACHE.get("events") or []),
                "message": _CLUBHOUSE_CACHE.get("message"),
            }
    try:
        redirect_request = urllib.request.Request(
            url, headers={"User-Agent": "SSV53-Platzwart/1.0"}
        )
        with urllib.request.urlopen(redirect_request, timeout=8) as response:
            resolved_url = response.geturl()
        parsed = urllib.parse.urlparse(resolved_url)
        token = urllib.parse.parse_qs(parsed.query).get("jwt", [""])[0]
        component = urllib.parse.parse_qs(parsed.query).get("component", [""])[0]
        if len(token) < 40 or not component:
            raise RuntimeError("Appack-Einbettungslink ist ungültig.")
        resources_data = _appack_graphql(
            token,
            "query FindResources($componentId:String!){findBookingResources(componentId:$componentId){id name}}",
            {"componentId": component},
        )
        resources = resources_data.get("findBookingResources") or []
        clubhouse = next(
            (
                item for item in resources
                if isinstance(item, dict)
                and str(item.get("name") or "").strip().casefold() == "vereinsheim"
            ),
            None,
        )
        if not clubhouse or not clubhouse.get("id"):
            raise RuntimeError("Ressource Vereinsheim wurde nicht gefunden.")
        berlin = ZoneInfo("Europe/Berlin")
        today = now.astimezone(berlin).date()
        calendar_end = today + timedelta(days=90)
        calendar_data = _appack_graphql(
            token,
            "query FindCalendar($resourceId:ID!,$start:Date!,$end:Date!){findBookingCalendar(resourceId:$resourceId,start:$start,end:$end){start end items}}",
            {
                "resourceId": str(clubhouse["id"]),
                "start": f"{today.isoformat()}T00:00:00.000Z",
                "end": f"{calendar_end.isoformat()}T23:59:59.000Z",
            },
        )
        calendar = dict(calendar_data.get("findBookingCalendar") or {})
        statuses = calendar.get("items") or []
        booked_days = [
            today + timedelta(days=index)
            for index, status in enumerate(statuses)
            if str(status or "").strip().upper() not in {"", "AVAILABLE"}
        ]
        slots_by_key: dict[tuple[str, str, str], dict[str, Any]] = {}
        for batch_start in range(0, len(booked_days), 31):
            day_batch = booked_days[batch_start : batch_start + 31]
            definitions = ["$resourceId:ID!", "$includeBooked:Boolean"] + [
                f"$day{index}:String" for index in range(len(day_batch))
            ]
            selections = [
                f"day{index}:findResourcePlans(resourceId:$resourceId,day:$day{index},includeBooked:$includeBooked)"
                "{slots{start end available blocked bookingId booking{profileName comment}}}"
                for index in range(len(day_batch))
            ]
            variables: dict[str, Any] = {
                "resourceId": str(clubhouse["id"]),
                "includeBooked": True,
            }
            variables.update(
                {f"day{index}": day.isoformat() for index, day in enumerate(day_batch)}
            )
            plans_data = _appack_graphql(
                token,
                "query FindPlans(" + ",".join(definitions) + "){"
                + "".join(selections)
                + "}",
                variables,
            )
            for index, day in enumerate(day_batch):
                for plan in plans_data.get(f"day{index}") or []:
                    for slot in (plan.get("slots") or []) if isinstance(plan, dict) else []:
                        if not isinstance(slot, dict) or slot.get("available") is not False:
                            continue
                        if not slot.get("bookingId") or not slot.get("start") or not slot.get("end"):
                            continue
                        start = _iso_datetime(slot["start"])
                        end = _iso_datetime(slot["end"])
                        if start is None or end is None or end <= now or start.astimezone(berlin).date() != day:
                            continue
                        booking_id = str(slot["bookingId"])
                        key = (booking_id, start.isoformat(), end.isoformat())
                        booking = slot.get("booking") if isinstance(slot.get("booking"), dict) else {}
                        booked_by = " ".join(str(booking.get("profileName") or "").split())[:100]
                        content = " ".join(str(booking.get("comment") or "").split())[:200]
                        slots_by_key[key] = {
                            "bookingId": booking_id,
                            "title": content or "Vereinsheim belegt",
                            "bookedBy": booked_by or "Nicht angegeben",
                            "start": start,
                            "end": end,
                        }
        merged_by_booking: list[dict[str, Any]] = []
        for item in sorted(
            slots_by_key.values(),
            key=lambda value: (value["bookingId"], value["start"], value["end"]),
        ):
            previous = merged_by_booking[-1] if merged_by_booking else None
            if (
                previous is not None
                and previous["bookingId"] == item["bookingId"]
                and previous["title"] == item["title"]
                and previous["bookedBy"] == item["bookedBy"]
                and item["start"] <= previous["end"]
            ):
                previous["end"] = max(previous["end"], item["end"])
            else:
                merged_by_booking.append(dict(item))
        events = [
            {
                "title": item["title"],
                "bookedBy": item["bookedBy"],
                "start": item["start"].isoformat(),
                "end": item["end"].isoformat(),
            }
            for item in sorted(merged_by_booking, key=lambda value: value["start"])
        ][:5]
        payload = {"available": True, "events": events, "message": None}
    except Exception:
        payload = {"available": False, "events": [], "message": "Vereinsheim-Daten sind gerade nicht erreichbar."}
    with _CLUBHOUSE_CACHE_LOCK:
        _CLUBHOUSE_CACHE.update(payload)
        _CLUBHOUSE_CACHE["expires"] = now + timedelta(minutes=5)
    return payload


def _operator_module():
    # The read-only distribution intentionally omits all command executors.
    try:
        return importlib.import_module("mower.operator_controls")
    except ModuleNotFoundError as exc:
        if exc.name != "mower.operator_controls":
            raise
        return None


def _manual_module():
    try:
        return importlib.import_module("mower.manual_control_api")
    except ModuleNotFoundError as exc:
        if exc.name not in {"mower.manual_control_api", "mower.manual_session", "mower.manual_water_conflict"}:
            raise
        return None


def _onsite_module():
    # Read-only and mower-only distributions deliberately omit water dispatch.
    try:
        return importlib.import_module("mower.onsite_dock_proof")
    except ModuleNotFoundError as exc:
        if exc.name != "mower.onsite_dock_proof":
            raise
        return None


def _manual_status(state, details, environment, now_utc, *, state_available=True, sources_available=True):
    disabled = {"enabled": False, "canStart": False, "canPark": False, "canResume": False}
    if not state_available or not RuntimeSettings.from_mapping(environment).enable_manual_sessions:
        return disabled
    module = _manual_module()
    if module is None:
        return disabled
    try:
        return module.manual_context(state, {**details, "manual_sources_available": sources_available}, environment, now_utc)[0]
    except (ValueError, TypeError, KeyError):
        return {**disabled, "title": "Manuelle Bedienung nicht verfügbar",
                "message": "Bitte den Mäher vor Ort prüfen und den Platzwart informieren."}


def _request_manual_action(environment, now_utc, request_id, payload, state_store_factory):
    module = _manual_module()
    if module is None:
        raise PlatzwartError("MANUAL_CONTROL_LOCKED", "Die manuelle Vorrangregel ist noch nicht eingeschaltet.", 409)
    settings = RuntimeSettings.from_mapping(environment)
    if not settings.enable_manual_sessions or settings.control_mode is not ControlMode.FULL_FAILSAFE or not settings.full_failsafe_write_gate_enabled:
        raise PlatzwartError("MANUAL_CONTROL_LOCKED", "Die manuelle Vorrangregel ist noch nicht eingeschaltet.", 409)
    store = state_store_factory(environment)
    state = store.load()
    training = (training_control_from_state(state, now_utc=now_utc)
                if training_control_enabled(environment) else resolve_training_control(environment, now_utc=now_utc))
    try:
        read = run_read_only_cycle(now_utc=now_utc, settings=settings, environment=environment,
                                  past_due=False, source="platzwart-manual-admission", persist_observations=False,
                                  training_control_snapshot=training)
        details = read.details
    except Exception as exc:
        if not isinstance(payload, Mapping) or payload.get("operation") != "PARK":
            raise PlatzwartError("MANUAL_INPUTS_UNAVAILABLE", "Start noch nicht möglich. Bitte aktualisieren und den Platzwart informieren.", 409) from exc
        operator = _operator_module()
        if operator is None:
            raise PlatzwartError("MANUAL_PARK_UNAVAILABLE", "Der Mäher ist nicht erreichbar. Bitte am Mäher parken.", 409) from exc
        details = {"mower": operator.read_operator_mower(environment), "manual_sources_available": False}
    try:
        _, response = module.request_manual_control(store=store, state=state, details=details,
            environment=environment, now_utc=now_utc, request_id=request_id, payload=payload)
    except module.ManualControlError as exc:
        raise PlatzwartError(exc.code, str(exc), 409) from exc
    except StateConflictError as exc:
        raise PlatzwartError("MANUAL_CONTEXT_CHANGED", "Der Auftrag wurde zwischenzeitlich geändert. Bitte erneut prüfen.", 409) from exc
    ConsoleTableStore.from_environment(environment).audit(now_utc, "MANUAL_CONTROL", "ACCEPTED", request_id)
    return response


def _action_status(settings, state, mower, *, state_available, controls_available,
                   telemetry_fresh, environment):
    module = _operator_module()
    capabilities = {
        action: {"available": bool(controls_available and _runtime_device_controls_enabled(settings)),
                 "reason": "AVAILABLE" if controls_available and _runtime_device_controls_enabled(settings) else "AUTOMATION_LOCKED"}
        for action in ALLOWED_ACTIONS if action != "SET_WINTER_TRAINING"
    }
    commands = module.operator_commands_payload(state) if module else {}
    if settings.control_mode is not ControlMode.OPERATOR_ONLY:
        return capabilities, commands
    capabilities = module.action_capabilities(settings) if module else {
        action: {"available": False, "reason": "OPERATOR_CONTROL_LOCKED"}
        for action in capabilities
    }
    journal_valid = not any(item.get("messageCode") == "JOURNAL_INVALID" for item in commands.values())
    configured_id = str(environment.get("HUSQVARNA_MOWER_ID") or "").strip()
    target_known = bool(configured_id and str(mower.get("mower_id") or "") == configured_id)
    target = mower.get("target_work_area") or {}
    for action, capability in capabilities.items():
        if not state_available or not journal_valid:
            capability.update(available=False, reason="STATE_UNAVAILABLE")
        elif not target_known:
            capability.update(available=False, reason="MOWER_TARGET_UNAVAILABLE")
        elif action == "SET_CUTTING_HEIGHT" and any(
            item.get("status") == "UNKNOWN" for item in commands.values()
        ):
            capability.update(available=False, reason="OPERATOR_ACTION_UNCONFIRMED")
        elif action == "SET_CUTTING_HEIGHT" and not (
            telemetry_fresh and supports_metric_cutting_height(mower.get("model"))
            and not _mower_error_active(mower)
            and target.get("use_global_cutting_height") is False
            and target.get("enabled") is True
            and str(target.get("name") or "").casefold() == "rasenfläche"
            and type(target.get("id")) is int and target["id"] > 0
        ):
            capability.update(available=False, reason="MOWER_STATE_UNAVAILABLE")
    return capabilities, commands


def _operation_mode(settings, state, mower, telemetry_fresh):
    if not telemetry_fresh:
        return "UNKNOWN"
    if settings.control_mode in {ControlMode.FULL_MOWER, ControlMode.FULL_FAILSAFE} and (
        settings.full_mower_write_gate_enabled and state.continuous_mowing_owned
        and state.last_decision_code != "EXTERNAL_OVERRIDE"
    ):
        return "AUTOMATIC"
    return "MANUAL"


def _operator_display_fallback(settings, environment, now_utc):
    if settings.control_mode is not ControlMode.OPERATOR_ONLY:
        raise RuntimeError("Operator display fallback requires OPERATOR_ONLY")
    module = _operator_module()
    if module is None:
        raise RuntimeError("Operator display unavailable")
    mower = module.read_operator_mower(environment)
    return CycleResult(2, now_utc.isoformat(), "platzwart-status-mower-only",
                       settings.control_mode.value, False, "PLAN_UNAVAILABLE", False,
                       "Der Belegungsplan oder die Bewässerungsdaten fehlen. Bitte aktualisieren.",
                       {"mower": mower, "current_plan": {}, "hydrawise": {}})


def live_status(environment: Mapping[str, str], now_utc: datetime, *,
                include_details: bool = True) -> dict[str, Any]:
    controls_available = True
    dashboard_snapshot_only = str(environment.get("HYDRAWISE_DASHBOARD_OBSERVATION_MODE") or "OFF").strip().upper() != "OFF"
    data_quality = {
        "code": "LIVE",
        "displayOnly": False,
        "message": None,
    }
    state_available = True
    try:
        state = AzureTableStateStore.from_environment(environment).load()
    except Exception:
        state = AutomationState()
        state_available = False
        controls_available = False
        data_quality = {
            "code": "STATE_UNAVAILABLE",
            "displayOnly": True,
            "message": (
                "Der Automatikzustand ist gerade nicht erreichbar. Live-Daten "
                "werden weiter angezeigt; alle Bedienaktionen bleiben gesperrt."
            ),
        }
    if not training_control_enabled(environment):
        training_control = resolve_training_control(
            environment, now_utc=now_utc
        )
    elif state_available and str(
        environment.get("SHARED_TRAINING_MODE", "OFF")
    ).strip().upper() == "ACTIVE":
        training_control = training_control_from_state(
            state, now_utc=now_utc
        )
    else:
        training_control = TrainingControlSnapshot(
            False, None, None, None, None, "automation_state",
            (
                "TRAINING_CONTROL_UNAVAILABLE"
                if not state_available
                else "TRAINING_CONTROL_REQUIRES_ACTIVE_RUNTIME"
            ),
            None,
            next_local_midnight(now_utc).isoformat(),
        )
    settings = RuntimeSettings.from_mapping(environment)
    try:
        result = run_read_only_cycle(
            now_utc=now_utc, settings=settings, environment=environment,
            past_due=False, source="platzwart-status", persist_observations=False,
            dashboard_snapshot_only=dashboard_snapshot_only,
            training_control_snapshot=training_control,
        )
    except Exception as exc:
        stale_config = isinstance(exc, RuntimeError) and (
            "Keine frische, validierte Laufzeitkonfiguration verfügbar" in str(exc)
        )
        if stale_config:
            # Packaged fallback only explains the display; it never authorizes a start.
            display_environment = dict(environment)
            display_environment["SSV53_DYNAMIC_CONFIG_ENABLED"] = "false"
            result = run_read_only_cycle(
                now_utc=now_utc, settings=settings, environment=display_environment,
                past_due=False, source="platzwart-status-display-only",
                persist_observations=False, dashboard_snapshot_only=dashboard_snapshot_only,
                training_control_snapshot=training_control,
            )
            if state_available:
                data_quality = {"code": "CONFIG_STALE", "displayOnly": True,
                                "message": "Der Belegungsplan ist nicht aktuell. Bitte aktualisieren und den Platzwart informieren. Keine Geräte starten."}
        elif settings.control_mode is ControlMode.OPERATOR_ONLY:
            # Parking remains available when the independent mower GET works.
            result = _operator_display_fallback(settings, environment, now_utc)
            data_quality = {"code": "PLAN_UNAVAILABLE", "displayOnly": True,
                            "message": result.message}
        else:
            raise
        controls_available = False
    details = result.details
    mower = dict(details.get("mower") or {})
    hydrawise = dict(details.get("hydrawise") or {})
    telemetry_fresh, status_age_seconds = _mower_telemetry_fresh(
        mower, environment, now_utc
    )
    if dashboard_snapshot_only and not (
        (hydrawise.get("safety") or {}).get("available") is True
        and (hydrawise.get("safety") or {}).get("fresh") is True
        and (hydrawise.get("safety") or {}).get("relay_set_valid") is True
    ):
        controls_available = False
        if data_quality["code"] == "LIVE":
            data_quality = {"code": "IRRIGATION_STATUS_UNAVAILABLE", "displayOnly": True,
                            "message": "Bewässerungsstand fehlt. Bitte aktualisieren und die Anlage prüfen. Keine Geräte starten."}
    device_controls_available = bool(
        controls_available
        and _runtime_device_controls_enabled(settings)
    )
    action_capabilities, operator_commands = _action_status(
        settings, state, mower, state_available=state_available,
        controls_available=controls_available, telemetry_fresh=telemetry_fresh,
        environment=environment,
    )
    manual_control = _manual_status(state, details, environment, now_utc,
                                    state_available=state_available, sources_available=controls_available)
    onsite_dock_proof = _onsite_module()
    irrigation_dock_confirmation = (
        onsite_dock_proof.context(state, mower, environment, now_utc)
        if onsite_dock_proof is not None and state_available and controls_available
        else {
            "enabled": onsite_dock_proof is not None and onsite_dock_proof.enabled(environment),
            "required": False,
            "canConfirm": False,
            "contextToken": None,
            "expiresInSeconds": onsite_dock_proof.ADMISSION_SECONDS if onsite_dock_proof else None,
            "reason": "INPUTS_UNAVAILABLE" if onsite_dock_proof else "COMPONENT_NOT_INSTALLED",
        }
    )
    action_capabilities["MANUAL_CONTROL"] = {
        "available": manual_control.get("enabled") is True and any(manual_control.get(key) for key in ("canStart", "canPark", "canResume")),
        "reason": "AVAILABLE" if manual_control.get("enabled") else "MANUAL_CONTROL_LOCKED",
    }
    current_plan = _display_current_plan(
        dict(details.get("current_plan") or {}),
        environment,
        now_utc,
    )
    mower.pop("mower_id", None)
    target = dict(mower.get("target_work_area") or {})
    cutting_height_percent = (
        mower.get("global_cutting_height_percent")
        if target.get("use_global_cutting_height") is True
        else target.get("cutting_height_percent")
    )
    cutting_height_mm = None
    cutting_height_supported = supports_metric_cutting_height(mower.get("model"))
    if cutting_height_supported and cutting_height_percent is not None:
        try:
            cutting_height_mm = cutting_height_percent_to_mm(
                int(cutting_height_percent)
            )
        except (TypeError, ValueError):
            cutting_height_mm = None
    raw_safety = dict(hydrawise.get("safety") or {})
    safety = {
        key: raw_safety.get(key)
        for key in (
            "available",
            "fresh",
            "clear_now",
            "reason",
            "active_zone_count",
            "imminent_zone_count",
            "selected_zone_count",
            "observed_at_utc",
        )
    }
    zones = [
        {
            key: zone.get(key)
            for key in (
                "zone",
                "name",
                "running",
                "run_seconds",
                "scheduled_start_utc",
                "scheduled_end_utc",
            )
        }
        for zone in hydrawise.get("zones", [])
        if isinstance(zone, dict)
    ]
    device_statistics = dict(mower.get("statistics") or {})
    statistics = {
        **(_dashboard_statistics(environment, now_utc) if include_details else
           peek_dashboard_statistics(environment, now_utc) or {"available": False, "loading": True}),
        "currentAreaProgress": target.get("progress"),
        "bladeUsageSeconds": device_statistics.get("cutting_blade_usage_seconds"),
        "totalRunningSeconds": device_statistics.get("total_running_seconds"),
    }
    try:
        # Recalculate against this request's fresh live battery. The five-minute
        # cache contains historical evidence only, never a cached charging permit.
        charging_evidence = statistics.pop("_chargingEvidence", None)
        charging_mower = dict(details.get("mower") or {})
        charging_end_estimate = estimate_charging_end(charging_evidence, charging_mower, now_utc)
        completed_cycles = statistics.get("estimatedAreaCycles7d", statistics.get("completedAreaCycles7d"))
        statistics["mownAreaEquivalentsEstimated"] = True
        current_progress = statistics.get("currentAreaProgress")
        if completed_cycles is not None and current_progress is not None:
            statistics["mownAreaEquivalents7d"] = round(
                float(completed_cycles) + max(0.0, min(100.0, float(current_progress))) / 100,
                2,
            )
        else:
            statistics["mownAreaEquivalents7d"] = None
    except Exception:
        # Malformed optional history must not replace a valid live safety read.
        _drop_display_cache(_STATISTICS_CACHE, _STATISTICS_CACHE_LOCK)
        statistics = {"available": False, "loading": True,
                      "currentAreaProgress": target.get("progress"),
                      "bladeUsageSeconds": device_statistics.get("cutting_blade_usage_seconds"),
                      "totalRunningSeconds": device_statistics.get("total_running_seconds")}
        charging_end_estimate = None
    # This single display source is independent of optional statistics/cache
    # failures. Neither manufacturer nor calibrated clocks grant a start.
    charging_mower = dict(details.get("mower") or {})
    charging_display_estimate = (
        validated_charging_display(charging_mower, now_utc, environment)
        or estimate_charging_display_end(None, charging_mower, now_utc)
    )
    irrigation_statistics = (_dashboard_irrigation_statistics(environment, now_utc)
                             if include_details else _peek_display_cache(
                                 _IRRIGATION_STATISTICS_CACHE, _IRRIGATION_STATISTICS_CACHE_LOCK, now_utc))
    zone_names = {
        int(zone["relay_id"]): str(zone.get("name") or f"Zone {zone.get('zone')}")
        for zone in hydrawise.get("zones", [])
        if isinstance(zone, dict) and zone.get("relay_id") is not None
    }
    try:
        measured_zone_minutes = {
            int(item.get("relayId") or 0): int(item.get("minutes") or 0)
            for item in irrigation_statistics.get("zoneMinutes7d") or []
            if isinstance(item, dict) and item.get("relayId") is not None
        }
        attention = irrigation_statistics.get("attention")
        if isinstance(attention, dict):
            for affected in attention.get("affectedRuns") or []:
                if not isinstance(affected, dict):
                    continue
                affected["confirmedZoneNames"] = [
                    zone_names[relay_id]
                    for relay_id in affected.get("confirmedRelayIds") or []
                    if relay_id in zone_names
                ]
        irrigation_statistics["zoneMinutes7d"] = [
            {
                "relayId": relay_id,
                "name": name,
                "minutes": measured_zone_minutes.get(relay_id, 0),
            }
            for relay_id, name in zone_names.items()
        ]
    except Exception:
        _drop_display_cache(_IRRIGATION_STATISTICS_CACHE, _IRRIGATION_STATISTICS_CACHE_LOCK)
        irrigation_statistics = {"available": False, "loading": True}
    clubhouse = (_clubhouse_events(environment, now_utc) if include_details else
                 _peek_display_cache(_CLUBHOUSE_CACHE, _CLUBHOUSE_CACHE_LOCK, now_utc)
                 if str(environment.get("SSV53_CLUBHOUSE_RESERVATION_URL") or "").strip()
                 else {"available": False, "events": []})
    return {
        "generatedAt": now_utc.astimezone(timezone.utc).isoformat(),
        "detailsDeferred": any(item.get("loading") for item in (statistics, irrigation_statistics, clubhouse)),
        "controlsAvailable": controls_available,
        "deviceControlsAvailable": device_controls_available,
        "protection": _protection_payload(settings),
        "actionCapabilities": action_capabilities,
        "operatorCommands": operator_commands,
        "manualControl": manual_control,
        "irrigationDockConfirmation": irrigation_dock_confirmation,
        "dataQuality": data_quality,
        "overall": {
            "code": state.last_decision_code if controls_available else data_quality["code"],
            "message": result.message if controls_available else data_quality["message"],
        },
        "mower": {
            "activity": mower.get("activity"), "state": mower.get("state"),
            "model": mower.get("model"),
            "displayActivity": _mower_display_activity(mower, state),
            "displayLabel": _mower_display_label(mower, state),
            "inactiveReason": mower.get("inactive_reason") or mower.get("inactiveReason"),
            "mode": mower.get("mode"),
            "operationMode": _operation_mode(settings, state, mower, telemetry_fresh),
            "batteryPercent": mower.get("battery_percent"), "errorCode": mower.get("error_code"),
            "errorActive": _mower_error_active(mower),
            "errorMessage": _mower_error_message(mower),
            "connected": mower.get("connected"),
            "statusTimestamp": mower.get("status_timestamp_ms"),
            "telemetryFresh": telemetry_fresh,
            "statusAgeSeconds": status_age_seconds,
            "restartBatteryPercent": _restart_battery_percent(environment),
            "restrictedReason": mower.get("restricted_reason"),
            "workArea": target.get("name"), "workAreaProgress": target.get("progress"),
            "cuttingHeightMm": cutting_height_mm,
            "cuttingHeightSupported": cutting_height_supported,
            "cuttingHeightMinimumMm": MINIMUM_MM,
            "cuttingHeightMaximumMm": MAXIMUM_MM,
            "cuttingHeightRecommendedMinimumMm": RECOMMENDED_MINIMUM_MM,
            "cuttingHeightWarningBelowMm": LOW_HEIGHT_WARNING_BELOW_MM,
        },
        "irrigation": {
            "status": hydrawise.get("status"), "safety": safety,
            "zones": zones, "releaseConfirmation": hydrawise.get("release_confirmation"),
            "intent": _irrigation_intent_payload(
                state, environment, state_available=state_available,
            ),
        },
        "occupancy": {
            "available": data_quality["code"] not in {"CONFIG_STALE", "PLAN_UNAVAILABLE"},
            "overrideAllowed": occupancy_override_allowed(current_plan.get("blocked_now")),
            "current": current_plan.get("blocked_now"), "next": current_plan.get("next_block"),
            "parking": current_plan.get("parking_block"),
            "upcoming": current_plan.get("upcoming_blocks") or [],
            "safeWindows": current_plan.get("safe_mowing_windows") or [],
        },
        "automation": _state_payload(state),
        "trainingControl": training_control.public_payload(),
        "coordination": _coordination_payload(details, state, current_plan, environment, now_utc, data_quality,
                                              charging_end_estimate=charging_end_estimate,
                                              charging_display_estimate=charging_display_estimate),
        "statistics": statistics,
        "irrigationStatistics": irrigation_statistics,
        "irrigationSchedule": _irrigation_schedule_payload(
            state,
            [zone for zone in hydrawise.get("zones", []) if isinstance(zone, dict)],
        ),
        "clubhouse": clubhouse,
    }


def unavailable_live_status(now_utc: datetime) -> dict[str, Any]:
    """Immer darstellbare, strikt bedienungslose Antwort bei unerwarteten Lesefehlern."""

    message = (
        "Die Live-Daten konnten gerade nicht vollständig geladen werden. "
        "Alle Bedienaktionen bleiben sicher gesperrt; die Anzeige versucht es automatisch erneut."
    )
    return {
        "generatedAt": now_utc.astimezone(timezone.utc).isoformat(),
        "controlsAvailable": False,
        "deviceControlsAvailable": False,
        "protection": {
            "automaticStartEnabled": False,
            "protectiveParkingEnabled": False,
        },
        "actionCapabilities": {action: {"available": False, "reason": "STATE_UNAVAILABLE"} for action in ALLOWED_ACTIONS},
        "operatorCommands": {},
        "dataQuality": {
            "code": "DISPLAY_UNAVAILABLE",
            "displayOnly": True,
            "message": message,
        },
        "overall": {"code": "DISPLAY_UNAVAILABLE", "message": message},
        "mower": {
            "activity": None,
            "operationMode": "UNKNOWN",
            "state": None,
            "displayActivity": None,
            "displayLabel": None,
            "batteryPercent": None,
            "errorCode": None,
            "errorActive": False,
            "errorMessage": None,
            "connected": None,
            "telemetryFresh": False,
            "statusAgeSeconds": None,
            "workAreaProgress": None,
            "cuttingHeightMm": None,
            "cuttingHeightSupported": False,
        },
        "irrigation": {
            "status": "Daten nicht verfügbar",
            "safety": {"available": False, "fresh": False, "clear_now": False},
            "zones": [],
            "releaseConfirmation": None,
            "intent": {
                "source": "UNKNOWN",
                "verified": False,
                "controllerManaged": False,
                "automaticWindowApplies": None,
            },
        },
        "occupancy": {
            "available": False,
            "current": None,
            "next": None,
            "parking": None,
            "upcoming": [],
            "safeWindows": [],
        },
        "automation": {},
        "trainingControl": {
            "available": False,
            "active": None,
            "pending": None,
            "effectiveAt": None,
            "nextEffectiveAt": next_local_midnight(now_utc).isoformat(),
            "stateRevision": None,
            "trainingRevision": None,
        },
        "statistics": {"available": False, "message": "Statistiken sind gerade nicht erreichbar."},
        "irrigationStatistics": {"available": False, "message": "Beregnungsstatistiken sind gerade nicht erreichbar."},
        "irrigationSchedule": {"available": False, "override": None, "nextRun": None, "history": []},
        "clubhouse": {"available": False, "events": [], "message": "Vereinsheim-Daten sind gerade nicht erreichbar."},
    }


def request_action(
    action: str,
    request_id: str,
    confirmation: str,
    environment: Mapping[str, str],
    now_utc: datetime,
    *,
    zone: int | None = None,
    run_seconds: int | None = None,
    cutting_height_mm: int | None = None,
    occupancy_override_key: str | None = None,
    irrigation_schedule: Mapping[str, Any] | None = None,
    winter_training_enabled: bool | None = None,
    training_revision: str | None = None,
    client_contract_version: int | None = None,
    manual_control: Mapping[str, Any] | None = None,
    state_store_factory=AzureTableStateStore.from_environment,
) -> dict[str, Any]:
    normalized = action.strip().upper()
    if normalized not in ALLOWED_ACTIONS:
        raise PlatzwartError("ACTION_INVALID", "Diese Bedienaktion ist nicht erlaubt.")
    if not request_id or len(request_id) > 64:
        raise PlatzwartError("REQUEST_ID_INVALID", "Die Anfragenummer fehlt oder ist ungültig.")
    normalized_override_key = str(occupancy_override_key or "").strip()
    occupancy_override_requested = normalized == "START_MOWING" and bool(
        normalized_override_key
    )
    expected_confirmation = (
        "START_MOWING_OCCUPANCY_OVERRIDE"
        if occupancy_override_requested
        else normalized
    )
    if confirmation != expected_confirmation:
        raise PlatzwartError("CONFIRMATION_INVALID", "Die Aktion wurde nicht eindeutig bestätigt.")
    if normalized_override_key and not occupancy_override_requested:
        raise PlatzwartError(
            "OCCUPANCY_OVERRIDE_INVALID",
            "Eine Belegungsausnahme ist nur für einen ausdrücklich bestätigten Mäherstart erlaubt.",
        )
    if len(normalized_override_key) > 512:
        raise PlatzwartError(
            "OCCUPANCY_OVERRIDE_INVALID",
            "Die bestätigte Belegung ist ungültig.",
        )
    if occupancy_override_requested:
        # The actual block is revalidated by the controller immediately before
        # action. Reject binding/unknown source types at the request boundary too.
        parts = normalized_override_key.rsplit("|", 1)
        if len(parts) != 2 or not occupancy_override_allowed({"source": parts[-1]}):
            raise PlatzwartError("OCCUPANCY_OVERRIDE_FORBIDDEN", "Verbindliche oder unbekannte Platzsperren können nicht übersteuert werden.", 409)
    manual_settings = RuntimeSettings.from_mapping(environment)
    if manual_settings.enable_manual_sessions and manual_settings.control_mode is ControlMode.FULL_FAILSAFE:
        if normalized == "PARK_MOWER":
            # Older cached templates must retain a working protective PARK.
            return _request_manual_action(environment, now_utc, request_id,
                {"operation": "PARK", "source": "APP"}, state_store_factory)
        if normalized == "START_MOWING":
            raise PlatzwartError("CLIENT_UPDATE_REQUIRED", "Bitte die Platzpflegeseite neu öffnen und den manuellen Start dort bestätigen.", 409)
    if normalized == "MANUAL_CONTROL":
        if type(client_contract_version) is not int or client_contract_version != 3:
            raise PlatzwartError("CLIENT_UPDATE_REQUIRED", "Bitte die Platzpflegeseite neu öffnen.", 409)
        if any(value is not None for value in (zone, run_seconds, cutting_height_mm, irrigation_schedule, winter_training_enabled, training_revision)):
            raise PlatzwartError("MANUAL_CONFIRMATION_INVALID", "Bitte die Bedienaktion neu öffnen.")
        return _request_manual_action(environment, now_utc, request_id, manual_control, state_store_factory)
    onsite_dock_proof = _onsite_module()
    if (
        normalized in {"START_IRRIGATION", "START_IRRIGATION_ZONE"}
        and str(environment.get("IRRIGATION_ONSITE_DOCK_CONFIRMATION_ENABLED", "false")).strip().lower() == "true"
        and onsite_dock_proof is None
    ):
        raise PlatzwartError(
            "ONSITE_DOCK_COMPONENT_UNAVAILABLE",
            "Die Stationsbestätigung ist noch nicht verfügbar. Bitte den Platzwart informieren.",
            409,
        )
    onsite_payload_allowed = (
        normalized in {"START_IRRIGATION", "START_IRRIGATION_ZONE"}
        and onsite_dock_proof is not None
        and onsite_dock_proof.enabled(environment)
    )
    if manual_control is not None and not onsite_payload_allowed:
        raise PlatzwartError("MANUAL_CONFIRMATION_INVALID", "Die manuelle Bestätigung gehört zu einer anderen Bedienaktion.")
    if normalized == "SET_WINTER_TRAINING":
        if type(winter_training_enabled) is not bool:
            raise PlatzwartError(
                "WINTER_TRAINING_VALUE_INVALID",
                "Der Wintertrainingsschalter benötigt einen eindeutigen Wert.",
            )
        try:
            snapshot = schedule_winter_training(
                environment,
                enabled=winter_training_enabled,
                request_id=request_id,
                expected_training_revision=str(training_revision or ""),
                now_utc=now_utc,
                state_store_factory=state_store_factory,
            )
        except TrainingControlChanged as exc:
            raise PlatzwartError(
                "TRAINING_CONTROL_CHANGED",
                "Der Trainingsschalter wurde zwischenzeitlich geändert. Bitte neu laden.",
                409,
            ) from exc
        except StateConflictError as exc:
            raise PlatzwartError(
                "TRAINING_CONTROL_CHANGED",
                "Der Zustand wurde zwischenzeitlich geändert. Bitte neu laden.",
                409,
            ) from exc
        except ValueError as exc:
            raise PlatzwartError(
                "TRAINING_REVISION_INVALID",
                "Der Trainingsschalter-Stand ist ungültig. Bitte neu laden.",
                400,
            ) from exc
        except RuntimeError as exc:
            if str(exc) == "TRAINING_CONTROL_REQUIRES_ACTIVE_RUNTIME":
                raise PlatzwartError(
                    "TRAINING_CONTROL_UNAVAILABLE",
                    "Der Trainingsschalter ist erst mit dem aktiven gemeinsamen Trainingskalender verfügbar.",
                    409,
                ) from exc
            raise PlatzwartError(
                "TRAINING_CONTROL_LOCKED",
                "Der Wintertrainingsschalter ist serverseitig noch nicht freigegeben.",
                409,
            ) from exc
        ConsoleTableStore.from_environment(environment).audit(
            now_utc, normalized, "ACCEPTED", request_id
        )
        return {
            "accepted": True,
            "requestId": request_id,
            "status": "SCHEDULED",
            "trainingControl": snapshot.public_payload(),
        }
    if winter_training_enabled is not None or training_revision is not None:
        raise PlatzwartError(
            "WINTER_TRAINING_VALUE_INVALID",
            "Trainingsschalter-Angaben sind nur für SET_WINTER_TRAINING erlaubt.",
        )
    schedule_json: str | None = None
    if normalized in SCHEDULE_ACTIONS:
        try:
            expected_zones = int(
                str(environment.get("HYDRAWISE_EXPECTED_ZONE_COUNT", "7")).strip()
            )
            schedule_payload = validate_schedule_request(
                normalized,
                irrigation_schedule,
                now_utc=now_utc,
                expected_zone_count=expected_zones,
            )
        except IrrigationOperatingWindowError as exc:
            raise PlatzwartError(exc.code, str(exc), 409) from exc
        except (TypeError, ValueError, IrrigationScheduleValidationError) as exc:
            raise PlatzwartError("IRRIGATION_SCHEDULE_INVALID", str(exc)) from exc
        schedule_json = dump_irrigation_schedule_object(schedule_payload)
        zone = None
        run_seconds = None
        cutting_height_mm = None
    elif irrigation_schedule:
        raise PlatzwartError(
            "IRRIGATION_SCHEDULE_INVALID",
            "Beregnungsplan-Angaben sind bei dieser Aktion nicht erlaubt.",
        )
    elif normalized == "START_IRRIGATION_ZONE":
        if type(zone) is not int or not 1 <= zone <= 99:
            raise PlatzwartError("ZONE_INVALID", "Bitte eine gültige Beregnungszone wählen.")
        if type(run_seconds) is not int or not 60 <= run_seconds <= 7200:
            raise PlatzwartError("DURATION_INVALID", "Die Laufzeit muss zwischen 1 und 120 Minuten liegen.")
        cutting_height_mm = None
    elif normalized == "SET_CUTTING_HEIGHT":
        if cutting_height_mm is None:
            raise PlatzwartError("CUTTING_HEIGHT_INVALID", "Bitte eine Schnitthöhe wählen.")
        if type(cutting_height_mm) is not int:
            raise PlatzwartError("CUTTING_HEIGHT_INVALID", "Bitte eine ganze Schnitthöhe in Millimetern wählen.")
        if not MINIMUM_MM <= cutting_height_mm <= MAXIMUM_MM:
            raise PlatzwartError(
                "CUTTING_HEIGHT_INVALID",
                f"Die Schnitthöhe muss zwischen {MINIMUM_MM} und {MAXIMUM_MM} mm liegen.",
            )
        zone = None
        run_seconds = None
    else:
        zone = None
        run_seconds = None
        cutting_height_mm = None
    settings = RuntimeSettings.from_mapping(environment)
    if settings.control_mode is ControlMode.OPERATOR_ONLY:
        if type(client_contract_version) is not int or client_contract_version != 2:
            raise PlatzwartError("APP_UPDATE_REQUIRED", "Bitte die Platzpflegeseite schließen und neu öffnen.", 409)
        module = _operator_module()
        if module is None or normalized not in {"PARK_MOWER", "SET_CUTTING_HEIGHT"}:
            raise PlatzwartError("OPERATOR_CONTROL_LOCKED", "Diese Bedienaktion ist noch nicht freigegeben.", 409)
        mower_id = str(environment.get("HUSQVARNA_MOWER_ID") or "").strip()
        if not mower_id:
            raise PlatzwartError("OPERATOR_CONTROL_LOCKED", "Der Mäher ist noch nicht für die Bedienung eingerichtet.", 409)
        try:
            store = state_store_factory(environment)
            command = module.queue_operator_action(store, settings, normalized, request_id,
                                                   now_utc, mower_id=mower_id,
                                                   cutting_height_mm=cutting_height_mm)
        except StateConflictError as exc:
            raise PlatzwartError("STATE_CHANGED", "Der Stand hat sich geändert. Bitte aktualisieren.", 409) from exc
        except module.OperatorControlError as exc:
            code = exc.code if exc.code in {"ACTION_PENDING", "REQUEST_ID_REUSED", "OPERATOR_ACTION_UNCONFIRMED"} else "OPERATOR_CONTROL_LOCKED"
            message = {
                "ACTION_PENDING": "Diese Bedienaktion wird bereits bearbeitet. Bitte warten.",
                "REQUEST_ID_REUSED": "Die Anfrage passt nicht zum bisherigen Auftrag. Bitte aktualisieren.",
                "OPERATOR_ACTION_UNCONFIRMED": "Die vorherige Änderung ist noch nicht bestätigt. Bitte den aktuellen Wert prüfen.",
            }.get(code, "Diese Änderung ist gerade nicht möglich. Bitte aktualisieren.")
            raise PlatzwartError(code, message, 409) from exc
        ConsoleTableStore.from_environment(environment).audit(now_utc, normalized, "ACCEPTED", request_id)
        return {"accepted": True, "requestId": request_id, "status": command["status"], "operatorCommand": command}
    if not _runtime_device_controls_enabled(settings):
        raise PlatzwartError("AUTOMATION_LOCKED", "Die sichere Automatik ist nicht vollständig freigegeben.", 409)
    store = AzureTableStateStore.from_environment(environment)
    original = store.load()
    if original.operator_request_id == request_id:
        return {"accepted": True, "requestId": request_id, "status": original.operator_request_status}
    if original.operator_request_status == "PENDING":
        raise PlatzwartError("ACTION_PENDING", "Eine andere Bedienaktion wird bereits sicher verarbeitet.", 409)
    if normalized == "START_MOWING":
        try:
            schedule_override = load_irrigation_schedule_object(
                original.irrigation_schedule_override_json,
                "Beregnungsplan-Anpassung",
            )
        except RuntimeError as exc:
            raise PlatzwartError(
                "IRRIGATION_SCHEDULE_STATE_INVALID",
                "Der Beregnungsplan-Zustand ist nicht eindeutig. Der Mäher bleibt sicher geparkt.",
                409,
            ) from exc
        schedule_status = str(
            (schedule_override or {}).get("status") or ""
        ).strip().upper()
        if schedule_status in START_BLOCKING_SCHEDULE_STATUSES:
            raise PlatzwartError(
                "IRRIGATION_SCHEDULE_CHANGE_PENDING",
                (
                    "Die Beregnungsänderung wird noch für alle sieben Zonen "
                    "bestätigt. Bitte warten Sie bis zum Abschluss und starten "
                    "Sie den Mäher danach erneut."
                ),
                409,
            )
    if normalized in {"START_IRRIGATION", "START_IRRIGATION_ZONE"} and original.irrigation_phase is not None:
        raise PlatzwartError(
            "IRRIGATION_ALREADY_ACTIVE",
            "Ein Beregnungsablauf oder Sicherheitsnachlauf ist bereits aktiv.",
            409,
        )
    onsite_proof_json = None
    if normalized in {"START_IRRIGATION", "START_IRRIGATION_ZONE"} and onsite_dock_proof is not None and onsite_dock_proof.enabled(environment):
        try:
            read = run_read_only_cycle(
                now_utc=now_utc,
                settings=settings,
                environment=environment,
                past_due=False,
                source="platzwart-onsite-dock-admission",
                persist_observations=False,
            )
            mower = dict(read.details.get("mower") or {})
            dock_context = onsite_dock_proof.context(original, mower, environment, now_utc)
        except Exception as exc:
            raise PlatzwartError(
                "ONSITE_DOCK_INPUTS_UNAVAILABLE",
                "Die aktuelle Mähermeldung konnte nicht sicher geprüft werden. Bitte aktualisieren.",
                409,
            ) from exc
        if dock_context["required"]:
            if type(client_contract_version) is not int or client_contract_version != 4:
                raise PlatzwartError(
                    "CLIENT_UPDATE_REQUIRED",
                    "Bitte die Platzpflegeseite neu öffnen und die Station dort bestätigen.",
                    409,
                )
            try:
                onsite_proof_json = onsite_dock_proof.admit(
                    original,
                    mower,
                    environment,
                    now_utc,
                    request_id=request_id,
                    action=normalized,
                    zone=zone,
                    run_seconds=run_seconds,
                    payload=manual_control,
                )
            except onsite_dock_proof.OnsiteDockProofError as exc:
                raise PlatzwartError(exc.code, str(exc), 409) from exc
        elif manual_control is not None:
            raise PlatzwartError(
                "ONSITE_DOCK_CONFIRMATION_NOT_APPLICABLE",
                "Die Mähermeldung passt nicht mehr zur Stationsbestätigung. Bitte aktualisieren.",
                409,
            )
    if normalized in SCHEDULE_ACTIONS and original.irrigation_phase is not None:
        raise PlatzwartError(
            "IRRIGATION_SEQUENCE_ACTIVE",
            "Während eines laufenden Beregnungsablaufs kann der nächste Plan nicht geändert werden. Bitte den laufenden Ablauf zuerst beenden.",
            409,
        )
    if (
        normalized in {"STOP_IRRIGATION_AFTER_ZONE", "STOP_IRRIGATION_NOW"}
        and original.irrigation_phase
        not in {"PLANNED", "SUSPENDING", "READY", "START_RESERVED", "RUNNING", "STOPPING"}
    ):
        raise PlatzwartError(
            "IRRIGATION_NOT_ACTIVE",
            "Es läuft kein Beregnungsablauf, der beendet werden kann.",
            409,
        )
    updated = replace(
        original,
        revision=original.revision + 1,
        operator_request_id=request_id,
        operator_request_action=normalized,
        operator_requested_utc=now_utc.astimezone(timezone.utc).isoformat(),
        operator_request_expires_utc=(now_utc.astimezone(timezone.utc) + timedelta(minutes=REQUEST_MINUTES)).isoformat(),
        operator_request_status="PENDING",
        operator_request_result=None,
        operator_request_zone=zone,
        operator_request_run_seconds=run_seconds,
        operator_request_cutting_height_mm=cutting_height_mm,
        operator_request_occupancy_override_key=(
            normalized_override_key or None
        ),
        operator_request_irrigation_schedule_json=schedule_json,
        # A new operator request revokes consent in the next controller cycle,
        # but must not erase the exact running zone's protective-stop binding.
        irrigation_onsite_dock_proof_json=(
            onsite_proof_json
            if onsite_proof_json is not None
            else original.irrigation_onsite_dock_proof_json
            if original.irrigation_phase in {"START_RESERVED", "RUNNING", "STOPPING"}
            else None
        ),
    )
    try:
        store.save(updated, expected_revision=original.revision)
    except StateConflictError as exc:
        raise PlatzwartError("ACTION_CONFLICT", "Der Zustand hat sich geändert. Bitte neu laden.", 409) from exc
    ConsoleTableStore.from_environment(environment).audit(now_utc, normalized, "ACCEPTED", request_id)
    return {"accepted": True, "requestId": request_id, "status": "PENDING"}
