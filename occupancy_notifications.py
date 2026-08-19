from __future__ import annotations

import hashlib
import re
from datetime import datetime, timedelta
from email.message import EmailMessage
from email.utils import formataddr
from typing import Any, Callable, Mapping
from zoneinfo import ZoneInfo

from azure.core.exceptions import ResourceExistsError, ResourceNotFoundError
from azure.data.tables import TableClient, UpdateMode
from azure.identity import ManagedIdentityCredential

from occupancy.service import build_occupancy_payload
from order_mail import EMAIL_PATTERN, OrderMailSettings, _open_authenticated_smtp
from special_occupancy import AzureTableSpecialOccupancyStore, merge_public_special_events
from training_cancellations import AzureTableCancellationStore


# Neue Partition, damit frühere trainerbezogene Zustellungen keine zentralen
# Benachrichtigungen unterdrücken können.
DELIVERY_PARTITION = "ssv53-occupancy-collision-central-v1"
MAX_RECIPIENTS = 5


def enabled(values: Mapping[str, str]) -> bool:
    return str(
        values.get("SSV53_OCCUPANCY_COLLISION_NOTIFICATIONS_ENABLED", "")
    ).strip().lower() == "true"


def collision_recipients(values: Mapping[str, str]) -> tuple[str, ...]:
    raw = str(values.get("SSV53_OCCUPANCY_COLLISION_RECIPIENTS", ""))
    recipients: list[str] = []
    seen: set[str] = set()
    for item in re.split(r"[,;\n]", raw):
        address = item.strip().lower()
        if not address:
            continue
        if not EMAIL_PATTERN.fullmatch(address):
            raise RuntimeError("Die zentralen Kollisions-Empfänger sind ungültig konfiguriert.")
        if address not in seen:
            recipients.append(address)
            seen.add(address)
    if not recipients:
        raise RuntimeError("Es sind keine zentralen Kollisions-Empfänger konfiguriert.")
    if len(recipients) > MAX_RECIPIENTS:
        raise RuntimeError("Es sind zu viele zentrale Kollisions-Empfänger konfiguriert.")
    return tuple(recipients)


def _hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


class AzureOccupancyNotificationStore:
    def __init__(self, table_client: TableClient) -> None:
        self._table = table_client

    @classmethod
    def from_environment(
        cls,
        values: Mapping[str, str],
        *,
        credential_factory=ManagedIdentityCredential,
        table_client_factory=TableClient,
    ) -> "AzureOccupancyNotificationStore":
        endpoint = str(values.get("SSV53_STORAGE_ACCOUNT_URL", "")).strip()
        table_name = str(values.get("SSV53_STATE_TABLE_NAME", "")).strip()
        client_id = str(
            values.get("SSV53_STATE_MANAGED_IDENTITY_CLIENT_ID")
            or values.get("AzureWebJobsStorage__clientId")
            or ""
        ).strip()
        if not endpoint or not table_name or not client_id:
            raise RuntimeError("Der Azure-Zustandsspeicher für Kollisionsmails ist unvollständig.")
        return cls(
            table_client_factory(
                endpoint=endpoint,
                table_name=table_name,
                credential=credential_factory(client_id=client_id),
            )
        )

    def claim_delivery(self, fingerprint: str, now_utc: datetime) -> bool:
        row_key = _hash(fingerprint)
        try:
            self._table.create_entity(
                {
                    "PartitionKey": DELIVERY_PARTITION,
                    "RowKey": row_key,
                    "Status": "sending",
                    "CreatedAtUtc": now_utc.isoformat(),
                }
            )
            return True
        except ResourceExistsError:
            try:
                current = self._table.get_entity(DELIVERY_PARTITION, row_key)
            except ResourceNotFoundError:
                return False
            if str(current.get("Status") or "").lower() != "failed":
                return False
            try:
                updated = datetime.fromisoformat(
                    str(current.get("UpdatedAtUtc") or current.get("CreatedAtUtc") or "")
                )
            except ValueError:
                return False
            if updated > now_utc - timedelta(minutes=15):
                return False
            self._table.update_entity(
                {
                    "PartitionKey": DELIVERY_PARTITION,
                    "RowKey": row_key,
                    "Status": "sending",
                    "UpdatedAtUtc": now_utc.isoformat(),
                },
                mode=UpdateMode.MERGE,
            )
            return True

    def mark_delivery(self, fingerprint: str, status: str, now_utc: datetime) -> None:
        self._table.update_entity(
            {
                "PartitionKey": DELIVERY_PARTITION,
                "RowKey": _hash(fingerprint),
                "Status": status,
                "UpdatedAtUtc": now_utc.isoformat(),
            },
            mode=UpdateMode.MERGE,
        )


MailSender = Callable[[OrderMailSettings, EmailMessage], None]


def _send_message(settings: OrderMailSettings, message: EmailMessage) -> None:
    with _open_authenticated_smtp(settings) as smtp:
        smtp.send_message(message)


def _event_dt(event: Mapping[str, Any], *fields: str) -> datetime:
    for field in fields:
        value = event.get(field)
        if value:
            parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
            if parsed.tzinfo is not None:
                return parsed
    raise ValueError("Termin enthält keine gültige Zeit.")


def find_collisions(events: list[Mapping[str, Any]]) -> list[tuple[Mapping[str, Any], Mapping[str, Any]]]:
    matches = [item for item in events if str(item.get("source") or "").lower() == "match"]
    bookings = [
        item for item in events
        if str(item.get("source") or "").lower() in {"training", "special"}
        and item.get("cancelled") is not True
    ]
    result = []
    for match in matches:
        match_start = _event_dt(match, "occupancyStart", "start")
        match_end = _event_dt(match, "occupancyEnd", "end")
        for booking in bookings:
            if str(match.get("resourceId") or "") != str(booking.get("resourceId") or ""):
                continue
            if match_end > _event_dt(booking, "start") and match_start < _event_dt(booking, "end"):
                result.append((match, booking))
    return result


def _current_payload(now_utc: datetime, values: Mapping[str, str]) -> dict[str, Any]:
    local = now_utc.astimezone(ZoneInfo("Europe/Berlin"))
    end_day = local.date() + timedelta(days=63)
    season = "Winter" if local.month in {11, 12, 1, 2} else "Sommer"
    kwargs = {
        "config_path": str(values.get("OCCUPANCY_CONFIG_PATH") or "occupancy/config.json"),
        "matches_path": str(values.get("OCCUPANCY_MATCHES_PATH") or "public/matches.json"),
        "start": local.date().isoformat(),
        "end": end_day.isoformat(),
        "season": season,
        "generated_at": now_utc,
    }
    payload = build_occupancy_payload(**kwargs)
    cancellations = AzureTableCancellationStore.from_environment(values).list_active(local.date(), end_day)
    if cancellations:
        payload = build_occupancy_payload(
            **kwargs,
            cancelled_occurrences={item.occurrence_key for item in cancellations},
        )
    specials = AzureTableSpecialOccupancyStore.from_environment(values).list_active(
        datetime.fromisoformat(payload["range"]["start"]),
        datetime.fromisoformat(payload["range"]["end"]),
    )
    return merge_public_special_events(payload, specials)


def process_collision_notifications(
    now_utc: datetime,
    values: Mapping[str, str],
    *,
    payload: Mapping[str, Any] | None = None,
    store: AzureOccupancyNotificationStore | None = None,
    mail_sender: MailSender = _send_message,
) -> dict[str, int]:
    if not enabled(values):
        return {"collisions": 0, "sent": 0}
    recipients = collision_recipients(values)
    settings = OrderMailSettings.from_mapping(values)
    directory = store or AzureOccupancyNotificationStore.from_environment(values)
    occupancy = dict(payload) if payload is not None else _current_payload(now_utc, values)
    collisions = find_collisions(list(occupancy.get("events", [])))
    sent = 0
    for match, booking in collisions:
        for recipient in recipients:
            fingerprint = "|".join(
                (
                    str(match.get("id") or ""),
                    str(match.get("occupancyStart") or match.get("start") or ""),
                    str(match.get("occupancyEnd") or match.get("end") or ""),
                    str(booking.get("id") or ""),
                    str(booking.get("start") or ""),
                    str(booking.get("end") or ""),
                    str(booking.get("resourceId") or ""),
                    _hash(recipient),
                )
            )
            if not directory.claim_delivery(fingerprint, now_utc):
                continue
            message = EmailMessage()
            message["From"] = formataddr((settings.from_name, settings.from_address))
            message["To"] = recipient
            message["Subject"] = "SSV53: Spiel kollidiert mit Platzbelegung"
            kickoff = _event_dt(match, "kickoff", "start")
            booking_start = _event_dt(booking, "start")
            booking_end = _event_dt(booking, "end")
            message.set_content(
                "Hallo,\n\n"
                "ein Verbandsspiel überschneidet sich mit einer bestehenden Platzbelegung:\n\n"
                f"Spiel: {match.get('title') or 'Heimspiel'}\n"
                f"Anstoß: {kickoff.strftime('%d.%m.%Y %H:%M')} Uhr\n"
                f"Bestehende Belegung: {booking.get('title') or 'Belegung'}\n"
                f"Belegungszeit: {booking_start.strftime('%d.%m.%Y %H:%M')} bis {booking_end.strftime('%H:%M')} Uhr\n"
                f"Platz: {match.get('resourceId') or ''}\n\n"
                "Bitte prüft und koordiniert die Platznutzung. Diese Nachricht enthält keine "
                "Kontaktdaten von Trainerinnen, Trainern oder Erstellern.\n\n"
                "Schönwalder SV 1953 e.V."
            )
            try:
                mail_sender(settings, message)
                directory.mark_delivery(fingerprint, "sent", now_utc)
                sent += 1
            except Exception:
                directory.mark_delivery(fingerprint, "failed", now_utc)
                raise
    return {"collisions": len(collisions), "sent": sent}


def send_collision_test_mail(
    values: Mapping[str, str],
    *,
    mail_sender: MailSender = _send_message,
) -> dict[str, int | bool]:
    """Sendet eine neutrale Testmail nur an die serverseitig festgelegten Empfänger."""
    if not enabled(values):
        raise RuntimeError("Kollisionsbenachrichtigungen sind nicht aktiviert.")
    recipients = collision_recipients(values)
    settings = OrderMailSettings.from_mapping(values)
    sent = 0
    for recipient in recipients:
        message = EmailMessage()
        message["From"] = formataddr((settings.from_name, settings.from_address))
        message["To"] = recipient
        message["Subject"] = "[TEST] SSV53 Kollisionsbenachrichtigung"
        message.set_content(
            "Dies ist eine angeforderte Testmail der zentralen "
            "Platzbelegungs-Kollisionsbenachrichtigung.\n\n"
            "Es liegt keine echte neue Kollision zugrunde. Trainerinnen und Trainer "
            "erhalten keine Kopie dieser Nachricht.\n\n"
            "Schönwalder SV 1953 e.V."
        )
        mail_sender(settings, message)
        sent += 1
    return {"ok": True, "sent": sent}
