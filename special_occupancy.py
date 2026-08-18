from __future__ import annotations

import base64
import hashlib
import json
import re
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Mapping, Protocol
from zoneinfo import ZoneInfo

from azure.core.exceptions import ResourceExistsError, ResourceNotFoundError
from azure.data.tables import TableClient, UpdateMode
from azure.identity import ManagedIdentityCredential


PARTITION_KEY = "special-occupancy"
COMMAND_MARKER = "SSV53_OCCUPANCY_V1:"
SUBJECT_PREFIX = "[SSV53-BELEGUNG]"
VALID_RESOURCES = frozenset({"rasen", "kunstrasen"})
VALID_AREAS = frozenset({"vorne", "hinten", "vorne & hinten"})
EVENT_ID_PATTERN = re.compile(r"^[a-z0-9][a-z0-9._-]{2,100}$")
COMMAND_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{5,140}$")
TZ = ZoneInfo("Europe/Berlin")


class SpecialOccupancyError(RuntimeError):
    def __init__(
        self,
        code: str,
        message: str,
        *,
        status_code: int = 400,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.status_code = status_code


def enabled(values: Mapping[str, str]) -> bool:
    return str(
        values.get("SSV53_SPECIAL_OCCUPANCY_ENABLED", "false")
    ).strip().casefold() in {"1", "true", "yes", "on"}


def _aware_local(value: Any, field: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(str(value).strip().replace("Z", "+00:00"))
    except ValueError as exc:
        raise SpecialOccupancyError(
            "DATETIME_INVALID",
            f"{field} ist kein gültiger ISO-Zeitpunkt.",
        ) from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise SpecialOccupancyError(
            "DATETIME_TIMEZONE_REQUIRED",
            f"{field} muss eine Zeitzone enthalten.",
        )
    return parsed.astimezone(TZ)


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("Zeitpunkt muss eine Zeitzone enthalten.")
    return value.astimezone(timezone.utc)


def _bounded_text(value: Any, *, field: str, maximum: int, required: bool = False) -> str:
    text = str(value or "").strip()
    if required and not text:
        raise SpecialOccupancyError("FIELD_REQUIRED", f"{field} darf nicht leer sein.")
    if len(text) > maximum:
        raise SpecialOccupancyError(
            "FIELD_TOO_LONG",
            f"{field} darf höchstens {maximum} Zeichen enthalten.",
        )
    return text


def _event_row_key(event_id: str) -> str:
    return "event-" + hashlib.sha256(event_id.encode("utf-8")).hexdigest()


def _command_row_key(command_id: str) -> str:
    return "command-" + hashlib.sha256(command_id.encode("utf-8")).hexdigest()


def _fingerprint(command: Mapping[str, Any]) -> str:
    canonical = json.dumps(
        command,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _buffer(value: Any, field: str, default: int = 30) -> int:
    if value in (None, ""):
        return default
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise SpecialOccupancyError(
            "BUFFER_INVALID",
            f"{field} muss eine ganze Zahl sein.",
        ) from exc
    if not 0 <= parsed <= 180:
        raise SpecialOccupancyError(
            "BUFFER_RANGE_INVALID",
            f"{field} muss zwischen 0 und 180 Minuten liegen.",
        )
    return parsed


@dataclass(frozen=True)
class SpecialOccupancyEvent:
    event_id: str
    title: str
    start: datetime
    end: datetime
    resource_id: str
    area: str
    description: str
    creator_id: str
    creator_name: str
    creator_phone: str
    creator_mobile: str
    creator_email: str
    creator_chat_id: str
    creator_image: str
    suppress_training: bool
    mower_buffer_before_minutes: int
    mower_buffer_after_minutes: int
    created_at_utc: datetime
    updated_at_utc: datetime

    @classmethod
    def from_command(
        cls,
        raw: Mapping[str, Any],
        *,
        now_utc: datetime,
        existing: "SpecialOccupancyEvent | None" = None,
    ) -> "SpecialOccupancyEvent":
        event_id = str(raw.get("id") or "").strip().lower()
        if not EVENT_ID_PATTERN.fullmatch(event_id):
            raise SpecialOccupancyError(
                "EVENT_ID_INVALID",
                "Die Event-ID enthält unzulässige Zeichen oder hat eine ungültige Länge.",
            )
        title = _bounded_text(
            raw.get("title"),
            field="title",
            maximum=120,
            required=True,
        )
        start = _aware_local(raw.get("start"), "start")
        end = _aware_local(raw.get("end"), "end")
        if end <= start:
            raise SpecialOccupancyError(
                "RANGE_INVALID",
                "end muss nach start liegen.",
            )
        if end - start > timedelta(days=14):
            raise SpecialOccupancyError(
                "RANGE_TOO_LONG",
                "Eine Sonderbelegung darf höchstens 14 Tage dauern.",
            )
        resource_id = str(raw.get("resourceId") or raw.get("resource_id") or "").strip().lower()
        if resource_id not in VALID_RESOURCES:
            raise SpecialOccupancyError(
                "RESOURCE_INVALID",
                "resourceId muss rasen oder kunstrasen sein.",
            )
        area = str(raw.get("area") or "vorne & hinten").strip().lower()
        if area not in VALID_AREAS:
            raise SpecialOccupancyError(
                "AREA_INVALID",
                "area muss vorne, hinten oder vorne & hinten sein.",
            )
        description = _bounded_text(
            raw.get("description"),
            field="description",
            maximum=500,
        )
        creator = raw.get("creator") if isinstance(raw.get("creator"), Mapping) else {}
        suppress_training = bool(raw.get("suppressTraining", True))
        now = _utc(now_utc)
        return cls(
            event_id=event_id,
            title=title,
            start=start,
            end=end,
            resource_id=resource_id,
            area=area,
            description=description,
            creator_id=_bounded_text(creator.get("id"), field="creator.id", maximum=180),
            creator_name=_bounded_text(creator.get("name"), field="creator.name", maximum=120),
            creator_phone=_bounded_text(creator.get("phone"), field="creator.phone", maximum=80),
            creator_mobile=_bounded_text(creator.get("mobile"), field="creator.mobile", maximum=80),
            creator_email=_bounded_text(creator.get("email"), field="creator.email", maximum=180),
            creator_chat_id=_bounded_text(creator.get("chatId"), field="creator.chatId", maximum=180),
            creator_image=_bounded_text(creator.get("image"), field="creator.image", maximum=500),
            suppress_training=suppress_training,
            mower_buffer_before_minutes=_buffer(
                raw.get("mowerBufferBeforeMinutes"),
                "mowerBufferBeforeMinutes",
            ),
            mower_buffer_after_minutes=_buffer(
                raw.get("mowerBufferAfterMinutes"),
                "mowerBufferAfterMinutes",
            ),
            created_at_utc=(existing.created_at_utc if existing else now),
            updated_at_utc=now,
        )

    @classmethod
    def from_entity(cls, entity: Mapping[str, Any]) -> "SpecialOccupancyEvent":
        return cls(
            event_id=str(entity["EventId"]),
            title=str(entity["Title"]),
            start=_aware_local(entity["Start"], "Start"),
            end=_aware_local(entity["End"], "End"),
            resource_id=str(entity["ResourceId"]),
            area=str(entity.get("Area", "vorne & hinten")),
            description=str(entity.get("Description", "")),
            creator_id=str(entity.get("CreatorId", "")),
            creator_name=str(entity.get("CreatorName", "")),
            creator_phone=str(entity.get("CreatorPhone", "")),
            creator_mobile=str(entity.get("CreatorMobile", "")),
            creator_email=str(entity.get("CreatorEmail", "")),
            creator_chat_id=str(entity.get("CreatorChatId", "")),
            creator_image=str(entity.get("CreatorImage", "")),
            suppress_training=bool(entity.get("SuppressTraining", True)),
            mower_buffer_before_minutes=int(entity.get("MowerBufferBeforeMinutes", 30)),
            mower_buffer_after_minutes=int(entity.get("MowerBufferAfterMinutes", 30)),
            created_at_utc=datetime.fromisoformat(
                str(entity["CreatedAtUtc"])
            ).astimezone(timezone.utc),
            updated_at_utc=datetime.fromisoformat(
                str(entity["UpdatedAtUtc"])
            ).astimezone(timezone.utc),
        )

    def to_entity(self) -> dict[str, Any]:
        return {
            "PartitionKey": PARTITION_KEY,
            "RowKey": _event_row_key(self.event_id),
            "Kind": "event",
            "EventId": self.event_id,
            "Title": self.title,
            "Start": self.start.isoformat(),
            "End": self.end.isoformat(),
            "ResourceId": self.resource_id,
            "Area": self.area,
            "Description": self.description,
            "CreatorId": self.creator_id,
            "CreatorName": self.creator_name,
            "CreatorPhone": self.creator_phone,
            "CreatorMobile": self.creator_mobile,
            "CreatorEmail": self.creator_email,
            "CreatorChatId": self.creator_chat_id,
            "CreatorImage": self.creator_image,
            "SuppressTraining": self.suppress_training,
            "MowerBufferBeforeMinutes": self.mower_buffer_before_minutes,
            "MowerBufferAfterMinutes": self.mower_buffer_after_minutes,
            "CreatedAtUtc": self.created_at_utc.isoformat(),
            "UpdatedAtUtc": self.updated_at_utc.isoformat(),
            "Active": True,
        }

    def to_public_event(self) -> dict[str, Any]:
        return {
            "id": "one-off:" + self.event_id,
            "title": self.title,
            "start": self.start.isoformat(),
            "end": self.end.isoformat(),
            "resourceId": self.resource_id,
            "source": "special",
            "season": None,
            "team": "",
            "area": self.area,
            "description": self.description,
            "creator": {
                "id": self.creator_id,
                "name": self.creator_name,
                "phone": self.creator_phone,
                "mobile": self.creator_mobile,
                "email": self.creator_email,
                "chatId": self.creator_chat_id,
                "image": self.creator_image,
            },
        }


class SpecialOccupancyStore(Protocol):
    def get_active(self, event_id: str) -> SpecialOccupancyEvent | None: ...

    def list_active(
        self,
        range_start: datetime,
        range_end: datetime,
    ) -> list[SpecialOccupancyEvent]: ...

    def apply(
        self,
        command: Mapping[str, Any],
        *,
        now_utc: datetime,
    ) -> dict[str, Any]: ...


class InMemorySpecialOccupancyStore:
    def __init__(self) -> None:
        self.events: dict[str, SpecialOccupancyEvent] = {}
        self.commands: dict[str, str] = {}

    def get_active(self, event_id: str) -> SpecialOccupancyEvent | None:
        return self.events.get(str(event_id).strip().lower())

    def list_active(
        self,
        range_start: datetime,
        range_end: datetime,
    ) -> list[SpecialOccupancyEvent]:
        start = _aware_local(range_start, "range_start")
        end = _aware_local(range_end, "range_end")
        return sorted(
            (
                event
                for event in self.events.values()
                if event.end > start and event.start < end
            ),
            key=lambda event: (event.start, event.resource_id, event.title),
        )

    def apply(
        self,
        command: Mapping[str, Any],
        *,
        now_utc: datetime,
    ) -> dict[str, Any]:
        command_id, action = _validate_command_header(command)
        fingerprint = _fingerprint(command)
        existing_fingerprint = self.commands.get(command_id)
        if existing_fingerprint is not None:
            if existing_fingerprint != fingerprint:
                raise SpecialOccupancyError(
                    "COMMAND_ID_CONFLICT",
                    "commandId wurde bereits mit anderem Inhalt verwendet.",
                    status_code=409,
                )
            event_id = _command_event_id(command)
            event = self.events.get(event_id)
            return _result(
                command_id,
                action,
                event,
                duplicate=True,
            )

        event_id = _command_event_id(command)
        if action == "upsert":
            existing = self.events.get(event_id)
            event = SpecialOccupancyEvent.from_command(
                _command_event(command),
                now_utc=now_utc,
                existing=existing,
            )
            self.events[event.event_id] = event
        else:
            event = self.events.pop(event_id, None)
        self.commands[command_id] = fingerprint
        return _result(command_id, action, event, duplicate=False)


class AzureTableSpecialOccupancyStore:
    def __init__(self, table_client: TableClient) -> None:
        self._table_client = table_client

    @classmethod
    def from_environment(
        cls,
        values: Mapping[str, str],
        *,
        credential_factory=ManagedIdentityCredential,
        table_client_factory=TableClient,
    ) -> "AzureTableSpecialOccupancyStore":
        endpoint = str(values.get("SSV53_STORAGE_ACCOUNT_URL", "")).strip()
        table_name = str(values.get("SSV53_STATE_TABLE_NAME", "")).strip()
        client_id = str(
            values.get("SSV53_STATE_MANAGED_IDENTITY_CLIENT_ID")
            or values.get("AzureWebJobsStorage__clientId")
            or ""
        ).strip()
        if not endpoint or not table_name or not client_id:
            raise RuntimeError(
                "Azure-Store für Sonderbelegungen ist unvollständig konfiguriert."
            )
        credential = credential_factory(client_id=client_id)
        return cls(
            table_client_factory(
                endpoint=endpoint,
                table_name=table_name,
                credential=credential,
            )
        )

    def _get(self, event_id: str) -> SpecialOccupancyEvent | None:
        try:
            entity = self._table_client.get_entity(
                PARTITION_KEY,
                _event_row_key(event_id),
            )
        except ResourceNotFoundError:
            return None
        if not bool(entity.get("Active")):
            return None
        return SpecialOccupancyEvent.from_entity(entity)

    def get_active(self, event_id: str) -> SpecialOccupancyEvent | None:
        return self._get(str(event_id).strip().lower())

    def list_active(
        self,
        range_start: datetime,
        range_end: datetime,
    ) -> list[SpecialOccupancyEvent]:
        start = _aware_local(range_start, "range_start")
        end = _aware_local(range_end, "range_end")
        entities = self._table_client.query_entities(
            query_filter=(
                "PartitionKey eq @partition and Kind eq @kind and Active eq true"
            ),
            parameters={"partition": PARTITION_KEY, "kind": "event"},
        )
        events: list[SpecialOccupancyEvent] = []
        for entity in entities:
            event = SpecialOccupancyEvent.from_entity(entity)
            if event.end > start and event.start < end:
                events.append(event)
        return sorted(
            events,
            key=lambda event: (event.start, event.resource_id, event.title),
        )

    @staticmethod
    def _audit_entity(
        *,
        action: str,
        command_id: str,
        event_id: str,
        now_utc: datetime,
    ) -> dict[str, Any]:
        instant = _utc(now_utc)
        return {
            "PartitionKey": PARTITION_KEY,
            "RowKey": (
                f"audit-{instant.strftime('%Y%m%dT%H%M%S%fZ')}-{uuid.uuid4().hex}"
            ),
            "Kind": "audit",
            "Action": action,
            "CommandId": command_id,
            "EventId": event_id,
            "AtUtc": instant.isoformat(),
        }

    def _command_entity(
        self,
        *,
        command_id: str,
        event_id: str,
        action: str,
        fingerprint: str,
        now_utc: datetime,
    ) -> dict[str, Any]:
        return {
            "PartitionKey": PARTITION_KEY,
            "RowKey": _command_row_key(command_id),
            "Kind": "command",
            "CommandId": command_id,
            "EventId": event_id,
            "Action": action,
            "CommandHash": fingerprint,
            "AppliedAtUtc": _utc(now_utc).isoformat(),
        }

    def _duplicate_result(
        self,
        command: Mapping[str, Any],
        *,
        fingerprint: str,
    ) -> dict[str, Any] | None:
        command_id, action = _validate_command_header(command)
        try:
            entity = self._table_client.get_entity(
                PARTITION_KEY,
                _command_row_key(command_id),
            )
        except ResourceNotFoundError:
            return None
        if str(entity.get("CommandHash", "")) != fingerprint:
            raise SpecialOccupancyError(
                "COMMAND_ID_CONFLICT",
                "commandId wurde bereits mit anderem Inhalt verwendet.",
                status_code=409,
            )
        event_id = str(entity.get("EventId") or _command_event_id(command))
        return _result(
            command_id,
            action,
            self._get(event_id),
            duplicate=True,
        )

    def apply(
        self,
        command: Mapping[str, Any],
        *,
        now_utc: datetime,
    ) -> dict[str, Any]:
        command_id, action = _validate_command_header(command)
        event_id = _command_event_id(command)
        fingerprint = _fingerprint(command)
        duplicate = self._duplicate_result(command, fingerprint=fingerprint)
        if duplicate is not None:
            return duplicate

        operations: list[tuple] = []
        if action == "upsert":
            existing = self._get(event_id)
            event = SpecialOccupancyEvent.from_command(
                _command_event(command),
                now_utc=now_utc,
                existing=existing,
            )
            operations.append(
                ("upsert", event.to_entity(), {"mode": UpdateMode.REPLACE})
            )
        else:
            event = self._get(event_id)
            if event is not None:
                entity = event.to_entity()
                entity["Active"] = False
                entity["DeletedAtUtc"] = _utc(now_utc).isoformat()
                operations.append(
                    ("update", entity, {"mode": UpdateMode.REPLACE})
                )

        operations.extend(
            [
                (
                    "create",
                    self._command_entity(
                        command_id=command_id,
                        event_id=event_id,
                        action=action,
                        fingerprint=fingerprint,
                        now_utc=now_utc,
                    ),
                ),
                (
                    "create",
                    self._audit_entity(
                        action=action,
                        command_id=command_id,
                        event_id=event_id,
                        now_utc=now_utc,
                    ),
                ),
            ]
        )
        try:
            self._table_client.submit_transaction(operations)
        except ResourceExistsError:
            duplicate = self._duplicate_result(command, fingerprint=fingerprint)
            if duplicate is not None:
                return duplicate
            raise
        return _result(command_id, action, event, duplicate=False)


def _validate_command_header(command: Mapping[str, Any]) -> tuple[str, str]:
    if not isinstance(command, Mapping):
        raise SpecialOccupancyError(
            "COMMAND_INVALID",
            "Der Belegungsbefehl muss ein JSON-Objekt sein.",
        )
    command_id = str(command.get("commandId") or "").strip()
    if not COMMAND_ID_PATTERN.fullmatch(command_id):
        raise SpecialOccupancyError(
            "COMMAND_ID_INVALID",
            "commandId fehlt oder ist ungültig.",
        )
    action = str(command.get("action") or "").strip().lower()
    if action not in {"upsert", "delete"}:
        raise SpecialOccupancyError(
            "ACTION_INVALID",
            "action muss upsert oder delete sein.",
        )
    return command_id, action


def _command_event(command: Mapping[str, Any]) -> Mapping[str, Any]:
    event = command.get("event")
    if not isinstance(event, Mapping):
        raise SpecialOccupancyError(
            "EVENT_INVALID",
            "Für upsert wird ein event-Objekt benötigt.",
        )
    return event


def _command_event_id(command: Mapping[str, Any]) -> str:
    action = str(command.get("action") or "").strip().lower()
    raw = (
        _command_event(command).get("id")
        if action == "upsert"
        else command.get("eventId")
    )
    event_id = str(raw or "").strip().lower()
    if not EVENT_ID_PATTERN.fullmatch(event_id):
        raise SpecialOccupancyError(
            "EVENT_ID_INVALID",
            "Die Event-ID fehlt oder ist ungültig.",
        )
    return event_id


def _result(
    command_id: str,
    action: str,
    event: SpecialOccupancyEvent | None,
    *,
    duplicate: bool,
) -> dict[str, Any]:
    return {
        "ok": True,
        "commandId": command_id,
        "action": action,
        "duplicate": duplicate,
        "event": event.to_public_event() if event else None,
    }


def encode_command(command: Mapping[str, Any]) -> str:
    raw = json.dumps(
        command,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def decode_command(value: str) -> dict[str, Any]:
    token = str(value or "").strip()
    if not re.fullmatch(r"[A-Za-z0-9_-]+", token):
        raise SpecialOccupancyError(
            "COMMAND_ENCODING_INVALID",
            "Der codierte Belegungsbefehl ist ungültig.",
        )
    padding = "=" * ((4 - len(token) % 4) % 4)
    try:
        decoded = base64.urlsafe_b64decode((token + padding).encode("ascii"))
        command = json.loads(decoded.decode("utf-8"))
    except (ValueError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise SpecialOccupancyError(
            "COMMAND_ENCODING_INVALID",
            "Der codierte Belegungsbefehl konnte nicht gelesen werden.",
        ) from exc
    if not isinstance(command, dict):
        raise SpecialOccupancyError(
            "COMMAND_INVALID",
            "Der Belegungsbefehl muss ein JSON-Objekt sein.",
        )
    _validate_command_header(command)
    _command_event_id(command)
    return command


def _sender_address(value: Any) -> str:
    if isinstance(value, Mapping):
        value = (
            value.get("address")
            or value.get("emailAddress", {}).get("address")
            if isinstance(value.get("emailAddress"), Mapping)
            else value.get("address")
        )
    text = str(value or "").strip().lower()
    match = re.search(r"<([^<>@\s]+@[^<>@\s]+)>", text)
    return (match.group(1) if match else text).strip().lower()


def parse_admin_request(
    body: Mapping[str, Any],
    *,
    allowed_sender: str = "",
) -> dict[str, Any]:
    if not isinstance(body, Mapping):
        raise SpecialOccupancyError(
            "REQUEST_INVALID",
            "Der Request muss ein JSON-Objekt enthalten.",
        )

    if body.get("commandId") and body.get("action"):
        command = dict(body)
        _validate_command_header(command)
        _command_event_id(command)
        return command

    if body.get("encodedCommand"):
        return decode_command(str(body["encodedCommand"]))

    mail_body = str(body.get("mailBody") or body.get("body") or "")
    mail_subject = str(body.get("mailSubject") or body.get("subject") or "")
    sender = _sender_address(
        body.get("mailFrom")
        or body.get("mailFromAddress")
        or body.get("from")
    )

    if not mail_subject.startswith(SUBJECT_PREFIX):
        raise SpecialOccupancyError(
            "MAIL_SUBJECT_INVALID",
            "Die Befehls-Mail besitzt nicht den erwarteten Betreff.",
            status_code=403,
        )
    expected_sender = str(allowed_sender or "").strip().lower()
    if expected_sender and sender != expected_sender:
        raise SpecialOccupancyError(
            "MAIL_SENDER_INVALID",
            "Der Absender der Befehls-Mail ist nicht freigegeben.",
            status_code=403,
        )
    match = re.search(
        re.escape(COMMAND_MARKER) + r"([A-Za-z0-9_-]+)",
        mail_body,
    )
    if not match:
        raise SpecialOccupancyError(
            "MAIL_COMMAND_MISSING",
            "Die Befehls-Mail enthält keinen gültigen Belegungsbefehl.",
        )
    return decode_command(match.group(1))


def _event_dt(item: Mapping[str, Any], name: str) -> datetime:
    return _aware_local(item.get(name), name)


def _area_parts(value: Any) -> frozenset[str]:
    normalized = str(value or "").strip().lower()
    if normalized == "vorne":
        return frozenset({"vorne"})
    if normalized == "hinten":
        return frozenset({"hinten"})
    return frozenset({"vorne", "hinten"})


def _areas_overlap(left: Any, right: Any) -> bool:
    return bool(_area_parts(left) & _area_parts(right))


def merge_public_special_events(
    payload: Mapping[str, Any],
    specials: list[SpecialOccupancyEvent],
) -> dict[str, Any]:
    result = dict(payload)
    dynamic_ids = {"one-off:" + event.event_id for event in specials}
    events = [
        dict(item)
        for item in payload.get("events", [])
        if isinstance(item, Mapping)
        and str(item.get("id") or "") not in dynamic_ids
    ]

    for special in specials:
        if special.suppress_training:
            events = [
                event
                for event in events
                if not (
                    str(event.get("source") or "").lower() == "training"
                    and str(event.get("resourceId") or "").lower()
                    == special.resource_id
                    and _areas_overlap(event.get("area"), special.area)
                    and _event_dt(event, "end") > special.start
                    and _event_dt(event, "start") < special.end
                )
            ]
        events.append(special.to_public_event())

    events.sort(
        key=lambda item: (
            str(item.get("start", "")),
            str(item.get("resourceId", "")),
            str(item.get("title", "")),
        )
    )
    result["events"] = events
    return result


def fail_closed_public_events(
    range_start: datetime,
    range_end: datetime,
) -> list[SpecialOccupancyEvent]:
    start = _aware_local(range_start, "range_start")
    end = _aware_local(range_end, "range_end")
    now = datetime.now(timezone.utc)
    return [
        SpecialOccupancyEvent(
            event_id=f"storage-unavailable-{resource_id}",
            title="Platzstatus unklar – bitte nicht nutzen",
            start=start,
            end=end,
            resource_id=resource_id,
            area="vorne & hinten",
            description=(
                "Sonderbelegungen konnten vorübergehend nicht aus Azure geladen werden."
            ),
            suppress_training=True,
            mower_buffer_before_minutes=0,
            mower_buffer_after_minutes=0,
            created_at_utc=now,
            updated_at_utc=now,
        )
        for resource_id in ("rasen", "kunstrasen")
    ]


def event_to_mower_block(
    event: SpecialOccupancyEvent,
    block_factory,
):
    if event.resource_id != "rasen":
        return None
    return block_factory(
        start=event.start - timedelta(minutes=event.mower_buffer_before_minutes),
        end=event.end + timedelta(minutes=event.mower_buffer_after_minutes),
        source="special",
        title=event.title,
        details={
            "event_id": event.event_id,
            "area": event.area,
            "dynamic_special_occupancy": True,
        },
    )
