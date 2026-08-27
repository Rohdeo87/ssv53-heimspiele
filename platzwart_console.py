from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
import threading
import urllib.parse
import urllib.request
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
from mower.runtime import RuntimeSettings
from mower.state import AutomationState
from mower.state_store import AzureTableStateStore, StateConflictError
from mower.irrigation_schedule import (
    IrrigationScheduleValidationError,
    SCHEDULE_ACTIONS,
    dump_object as dump_irrigation_schedule_object,
    load_history as load_irrigation_schedule_history,
    load_object as load_irrigation_schedule_object,
    validate_schedule_request,
)
from daily_safety_report import dashboard_irrigation_statistics, dashboard_statistics
from occupancy.runtime_source import resolve_occupancy_match_source


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
        *SCHEDULE_ACTIONS,
    }
)

_CLUBHOUSE_CACHE_LOCK = threading.Lock()
_CLUBHOUSE_CACHE: dict[str, Any] = {"expires": None, "events": [], "available": False}
_STATISTICS_CACHE_LOCK = threading.Lock()
_STATISTICS_CACHE: dict[str, Any] = {"expires": None, "available": False}
_IRRIGATION_STATISTICS_CACHE_LOCK = threading.Lock()
_IRRIGATION_STATISTICS_CACHE: dict[str, Any] = {"expires": None, "available": False}
_MATCH_DISPLAY_CACHE_LOCK = threading.Lock()
_MATCH_DISPLAY_CACHE: dict[str, Any] = {"path": None, "mtime_ns": None, "matches": {}}


def _dashboard_statistics(environment: Mapping[str, str], now_utc: datetime) -> dict[str, Any]:
    with _STATISTICS_CACHE_LOCK:
        expires = _STATISTICS_CACHE.get("expires")
        if isinstance(expires, datetime) and now_utc < expires:
            return {key: value for key, value in _STATISTICS_CACHE.items() if key != "expires"}
    try:
        payload = dashboard_statistics(now_utc, environment)
        payload["message"] = None
    except Exception:
        payload = {
            "available": False,
            "message": "Die 7-Tage-Auswertung ist gerade nicht erreichbar.",
        }
    with _STATISTICS_CACHE_LOCK:
        _STATISTICS_CACHE.clear()
        _STATISTICS_CACHE.update(payload)
        _STATISTICS_CACHE["expires"] = now_utc + timedelta(minutes=5)
    return payload


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
        "pendingAction": state.operator_request_action if state.operator_request_status == "PENDING" else None,
        "pendingRequestedAt": state.operator_requested_utc if state.operator_request_status == "PENDING" else None,
        "lastOperatorAction": state.operator_request_action,
        "lastOperatorStatus": state.operator_request_status,
        "lastOperatorRequestedAt": state.operator_requested_utc,
        "lastOperatorResult": state.operator_request_result,
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
    if inactive_reason == "SEARCHING_FOR_SATELLITES":
        return "SEARCHING_FOR_POSITION"
    if inactive_reason == "PLANNING":
        return "PLANNING"
    if activity != "NOT_APPLICABLE" or mower_state != "IN_OPERATION":
        return activity
    if mode in {"HOME", "MAIN_AREA", "SECONDARY_AREA", "POI"}:
        return "SEARCHING_FOR_POSITION"
    return activity


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


def live_status(environment: Mapping[str, str], now_utc: datetime) -> dict[str, Any]:
    controls_available = True
    data_quality = {
        "code": "LIVE",
        "displayOnly": False,
        "message": None,
    }
    try:
        settings = RuntimeSettings.from_mapping(environment)
        result = run_read_only_cycle(
            now_utc=now_utc,
            settings=settings,
            environment=environment,
            past_due=False,
            source="platzwart-status",
        )
    except RuntimeError as exc:
        # Die Steuerung muss bei einer abgelaufenen dynamischen Konfiguration
        # weiterhin fail-closed bleiben. Für die ausschließlich lesende Anzeige
        # dürfen wir dagegen die paketierte Konfiguration verwenden: Dieser
        # Pfad ruft nur Live-Zustände ab und kann niemals Befehle senden.
        if "Keine frische, validierte Laufzeitkonfiguration verfügbar" not in str(exc):
            raise
        display_environment = dict(environment)
        display_environment["SSV53_DYNAMIC_CONFIG_ENABLED"] = "false"
        settings = RuntimeSettings.from_mapping(display_environment)
        result = run_read_only_cycle(
            now_utc=now_utc,
            settings=settings,
            environment=display_environment,
            past_due=False,
            source="platzwart-status-display-only",
        )
        controls_available = False
        data_quality = {
            "code": "CONFIG_STALE",
            "displayOnly": True,
            "message": (
                "Der geprüfte Sicherheitsplan ist veraltet. Live-Daten werden "
                "weiter angezeigt; Mäher und Beregnung bleiben sicher gesperrt."
            ),
        }
    try:
        state = AzureTableStateStore.from_environment(environment).load()
    except Exception:
        state = AutomationState()
        controls_available = False
        data_quality = {
            "code": "STATE_UNAVAILABLE",
            "displayOnly": True,
            "message": (
                "Der Automatikzustand ist gerade nicht erreichbar. Live-Daten "
                "werden weiter angezeigt; alle Bedienaktionen bleiben gesperrt."
            ),
        }
    details = result.details
    mower = dict(details.get("mower") or {})
    hydrawise = dict(details.get("hydrawise") or {})
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
        **_dashboard_statistics(environment, now_utc),
        "currentAreaProgress": target.get("progress"),
        "bladeUsageSeconds": device_statistics.get("cutting_blade_usage_seconds"),
        "totalRunningSeconds": device_statistics.get("total_running_seconds"),
    }
    completed_cycles = statistics.get("completedAreaCycles7d")
    current_progress = statistics.get("currentAreaProgress")
    if completed_cycles is not None and current_progress is not None:
        statistics["mownAreaEquivalents7d"] = round(
            float(completed_cycles) + max(0.0, min(100.0, float(current_progress))) / 100,
            2,
        )
    else:
        statistics["mownAreaEquivalents7d"] = None
    irrigation_statistics = _dashboard_irrigation_statistics(environment, now_utc)
    zone_names = {
        int(zone["relay_id"]): str(zone.get("name") or f"Zone {zone.get('zone')}")
        for zone in hydrawise.get("zones", [])
        if isinstance(zone, dict) and zone.get("relay_id") is not None
    }
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
    return {
        "generatedAt": now_utc.astimezone(timezone.utc).isoformat(),
        "controlsAvailable": controls_available,
        "dataQuality": data_quality,
        "overall": {
            "code": state.last_decision_code if controls_available else data_quality["code"],
            "message": result.message if controls_available else data_quality["message"],
        },
        "mower": {
            "activity": mower.get("activity"), "state": mower.get("state"),
            "model": mower.get("model"),
            "displayActivity": _mower_display_activity(mower, state),
            "inactiveReason": mower.get("inactive_reason") or mower.get("inactiveReason"),
            "mode": mower.get("mode"),
            "batteryPercent": mower.get("battery_percent"), "errorCode": mower.get("error_code"),
            "connected": mower.get("connected"),
            "statusTimestamp": mower.get("status_timestamp_ms"),
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
        },
        "occupancy": {
            "current": current_plan.get("blocked_now"), "next": current_plan.get("next_block"),
            "parking": current_plan.get("parking_block"),
            "upcoming": current_plan.get("upcoming_blocks") or [],
            "safeWindows": current_plan.get("safe_mowing_windows") or [],
        },
        "automation": _state_payload(state),
        "statistics": statistics,
        "irrigationStatistics": irrigation_statistics,
        "irrigationSchedule": _irrigation_schedule_payload(
            state,
            [zone for zone in hydrawise.get("zones", []) if isinstance(zone, dict)],
        ),
        "clubhouse": _clubhouse_events(environment, now_utc),
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
        "dataQuality": {
            "code": "DISPLAY_UNAVAILABLE",
            "displayOnly": True,
            "message": message,
        },
        "overall": {"code": "DISPLAY_UNAVAILABLE", "message": message},
        "mower": {
            "activity": None,
            "state": None,
            "displayActivity": None,
            "batteryPercent": None,
            "errorCode": None,
            "connected": None,
            "workAreaProgress": None,
            "cuttingHeightMm": None,
            "cuttingHeightSupported": False,
        },
        "irrigation": {
            "status": "Daten nicht verfügbar",
            "safety": {"available": False, "fresh": False, "clear_now": False},
            "zones": [],
            "releaseConfirmation": None,
        },
        "occupancy": {
            "current": None,
            "next": None,
            "parking": None,
            "upcoming": [],
            "safeWindows": [],
        },
        "automation": {},
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
        if zone is None or not 1 <= int(zone) <= 99:
            raise PlatzwartError("ZONE_INVALID", "Bitte eine gültige Beregnungszone wählen.")
        if run_seconds is None or not 60 <= int(run_seconds) <= 7200:
            raise PlatzwartError("DURATION_INVALID", "Die Laufzeit muss zwischen 1 und 120 Minuten liegen.")
        cutting_height_mm = None
    elif normalized == "SET_CUTTING_HEIGHT":
        if cutting_height_mm is None:
            raise PlatzwartError("CUTTING_HEIGHT_INVALID", "Bitte eine Schnitthöhe wählen.")
        try:
            cutting_height_mm = int(cutting_height_mm)
        except (TypeError, ValueError) as exc:
            raise PlatzwartError("CUTTING_HEIGHT_INVALID", "Bitte eine gültige Schnitthöhe wählen.") from exc
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
    if not settings.full_failsafe_write_gate_enabled:
        raise PlatzwartError("AUTOMATION_LOCKED", "Die sichere Automatik ist nicht vollständig freigegeben.", 409)
    store = AzureTableStateStore.from_environment(environment)
    original = store.load()
    if original.operator_request_id == request_id:
        return {"accepted": True, "requestId": request_id, "status": original.operator_request_status}
    if original.operator_request_status == "PENDING":
        raise PlatzwartError("ACTION_PENDING", "Eine andere Bedienaktion wird bereits sicher verarbeitet.", 409)
    if normalized in {"START_IRRIGATION", "START_IRRIGATION_ZONE"} and original.irrigation_phase is not None:
        raise PlatzwartError(
            "IRRIGATION_ALREADY_ACTIVE",
            "Ein Beregnungsablauf oder Sicherheitsnachlauf ist bereits aktiv.",
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
    )
    try:
        store.save(updated, expected_revision=original.revision)
    except StateConflictError as exc:
        raise PlatzwartError("ACTION_CONFLICT", "Der Zustand hat sich geändert. Bitte neu laden.", 409) from exc
    ConsoleTableStore.from_environment(environment).audit(now_utc, normalized, "ACCEPTED", request_id)
    return {"accepted": True, "requestId": request_id, "status": "PENDING"}
