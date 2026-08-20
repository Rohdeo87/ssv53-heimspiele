from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from typing import Any, Mapping

from azure.core.exceptions import ResourceExistsError, ResourceNotFoundError
from azure.data.tables import TableClient, UpdateMode
from azure.identity import ManagedIdentityCredential

from mower.dry_run import run_read_only_cycle
from mower.runtime import RuntimeSettings
from mower.state import AutomationState
from mower.state_store import AzureTableStateStore, StateConflictError


PIN_ITERATIONS_MINIMUM = 200_000
SESSION_MINUTES = 30
REQUEST_MINUTES = 10
ALLOWED_ACTIONS = frozenset(
    {"PARK_MOWER", "START_MOWING", "START_IRRIGATION", "STOP_IRRIGATION_AFTER_ZONE"}
)


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
        "irrigationPhase": state.irrigation_phase,
        "irrigationCompletedAt": state.irrigation_completed_utc,
        "hydrawiseClearSince": state.hydrawise_clear_since_utc,
        "pendingAction": state.operator_request_action if state.operator_request_status == "PENDING" else None,
        "pendingRequestedAt": state.operator_requested_utc if state.operator_request_status == "PENDING" else None,
        "lastOperatorResult": state.operator_request_result,
    }


def live_status(environment: Mapping[str, str], now_utc: datetime) -> dict[str, Any]:
    settings = RuntimeSettings.from_mapping(environment)
    result = run_read_only_cycle(
        now_utc=now_utc,
        settings=settings,
        environment=environment,
        past_due=False,
        source="platzwart-status",
    )
    state = AzureTableStateStore.from_environment(environment).load()
    details = result.details
    mower = dict(details.get("mower") or {})
    hydrawise = dict(details.get("hydrawise") or {})
    current_plan = dict(details.get("current_plan") or {})
    mower.pop("mower_id", None)
    target = dict(mower.get("target_work_area") or {})
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
    return {
        "generatedAt": now_utc.astimezone(timezone.utc).isoformat(),
        "overall": {"code": state.last_decision_code, "message": result.message},
        "mower": {
            "activity": mower.get("activity"), "state": mower.get("state"),
            "batteryPercent": mower.get("battery_percent"), "errorCode": mower.get("error_code"),
            "workArea": target.get("name"), "workAreaProgress": target.get("progress"),
        },
        "irrigation": {
            "status": hydrawise.get("status"), "safety": safety,
            "zones": zones, "releaseConfirmation": hydrawise.get("release_confirmation"),
        },
        "occupancy": {
            "current": current_plan.get("blocked_now"), "next": current_plan.get("next_block"),
            "parking": current_plan.get("parking_block"),
        },
        "automation": _state_payload(state),
    }


def request_action(
    action: str,
    request_id: str,
    confirmation: str,
    environment: Mapping[str, str],
    now_utc: datetime,
) -> dict[str, Any]:
    normalized = action.strip().upper()
    if normalized not in ALLOWED_ACTIONS:
        raise PlatzwartError("ACTION_INVALID", "Diese Bedienaktion ist nicht erlaubt.")
    if not request_id or len(request_id) > 64:
        raise PlatzwartError("REQUEST_ID_INVALID", "Die Anfragenummer fehlt oder ist ungültig.")
    if confirmation != normalized:
        raise PlatzwartError("CONFIRMATION_INVALID", "Die Aktion wurde nicht eindeutig bestätigt.")
    settings = RuntimeSettings.from_mapping(environment)
    if not settings.full_failsafe_write_gate_enabled:
        raise PlatzwartError("AUTOMATION_LOCKED", "Die sichere Automatik ist nicht vollständig freigegeben.", 409)
    store = AzureTableStateStore.from_environment(environment)
    original = store.load()
    if original.operator_request_id == request_id:
        return {"accepted": True, "requestId": request_id, "status": original.operator_request_status}
    if original.operator_request_status == "PENDING":
        raise PlatzwartError("ACTION_PENDING", "Eine andere Bedienaktion wird bereits sicher verarbeitet.", 409)
    if normalized == "START_IRRIGATION" and original.irrigation_phase is not None:
        raise PlatzwartError(
            "IRRIGATION_ALREADY_ACTIVE",
            "Ein Beregnungsablauf oder Sicherheitsnachlauf ist bereits aktiv.",
            409,
        )
    if (
        normalized == "STOP_IRRIGATION_AFTER_ZONE"
        and original.irrigation_phase
        not in {"PLANNED", "SUSPENDING", "READY", "START_RESERVED", "RUNNING"}
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
    )
    try:
        store.save(updated, expected_revision=original.revision)
    except StateConflictError as exc:
        raise PlatzwartError("ACTION_CONFLICT", "Der Zustand hat sich geändert. Bitte neu laden.", 409) from exc
    ConsoleTableStore.from_environment(environment).audit(now_utc, normalized, "ACCEPTED", request_id)
    return {"accepted": True, "requestId": request_id, "status": "PENDING"}
