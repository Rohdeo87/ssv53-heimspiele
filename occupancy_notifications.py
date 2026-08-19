from __future__ import annotations

import hashlib
import json
import re
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
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


CONTACT_PARTITION = "ssv53-occupancy-contact-v2"
DELIVERY_PARTITION = "ssv53-occupancy-collision-v2"
MAX_CONTACTS = 100
VERIFICATION_HOURS = 48
VERIFICATION_RESEND_HOURS = 24


@dataclass(frozen=True)
class VerifiedContact:
    name: str
    email: str
    team_keys: frozenset[str]


@dataclass(frozen=True)
class PendingVerification:
    name: str
    email: str
    team_keys: frozenset[str]
    token: str
    token_hash: str


def enabled(values: Mapping[str, str]) -> bool:
    return str(
        values.get("SSV53_OCCUPANCY_COLLISION_NOTIFICATIONS_ENABLED", "")
    ).strip().lower() == "true"


def _hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _clean_text(value: Any, maximum: int) -> str:
    return re.sub(r"\s+", " ", str(value or "").strip())[:maximum]


def _parse_keys(value: Any) -> frozenset[str]:
    if not isinstance(value, list):
        return frozenset()
    return frozenset(
        _clean_text(item, 80).lower()
        for item in value
        if _clean_text(item, 80).lower().startswith(("team:", "year:"))
    )


def _public_base_url(values: Mapping[str, str]) -> str:
    configured = str(values.get("SSV53_PUBLIC_FUNCTION_BASE_URL", "")).strip().rstrip("/")
    if configured:
        return configured
    hostname = str(values.get("WEBSITE_HOSTNAME", "")).strip()
    if not hostname:
        raise RuntimeError("Die öffentliche Function-URL ist nicht konfiguriert.")
    return "https://" + hostname


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
            raise RuntimeError("Der geschützte Azure-Kontaktspeicher ist unvollständig.")
        return cls(
            table_client_factory(
                endpoint=endpoint,
                table_name=table_name,
                credential=credential_factory(client_id=client_id),
            )
        )

    def _get_contact(self, email: str) -> Mapping[str, Any] | None:
        try:
            return self._table.get_entity(CONTACT_PARTITION, _hash(email))
        except ResourceNotFoundError:
            return None

    def register_candidate(
        self,
        *,
        name: str,
        email: str,
        team_keys: frozenset[str],
        token: str,
        token_hash: str,
        now_utc: datetime,
    ) -> bool:
        current = self._get_contact(email)
        keys_json = json.dumps(sorted(team_keys), ensure_ascii=True)
        if current is not None:
            if (
                bool(current.get("Verified"))
                and str(current.get("Name") or "") == name
                and str(current.get("TeamKeysJson") or "") == keys_json
            ):
                return False
            sent_at = str(current.get("VerificationSentAtUtc") or "")
            if sent_at:
                try:
                    if datetime.fromisoformat(sent_at) > now_utc - timedelta(hours=VERIFICATION_RESEND_HOURS):
                        return False
                except ValueError:
                    pass
            if (
                str(current.get("PendingName") or "") == name
                and str(current.get("PendingTeamKeysJson") or "") == keys_json
            ):
                if not sent_at:
                    return False
        entity = {
            "PartitionKey": CONTACT_PARTITION,
            "RowKey": _hash(email),
            "Email": email,
            "PendingName": name,
            "PendingTeamKeysJson": keys_json,
            "PendingToken": token,
            "PendingTokenHash": token_hash,
            "PendingExpiresAtUtc": (now_utc + timedelta(hours=VERIFICATION_HOURS)).isoformat(),
            "PendingAtUtc": now_utc.isoformat(),
            "VerificationSentAtUtc": "",
            "Verified": bool(current.get("Verified")) if current else False,
        }
        if current and current.get("Verified"):
            entity.update(
                {
                    "Name": str(current.get("Name") or ""),
                    "TeamKeysJson": str(current.get("TeamKeysJson") or "[]"),
                    "VerifiedAtUtc": str(current.get("VerifiedAtUtc") or ""),
                }
            )
        self._table.upsert_entity(entity, mode=UpdateMode.REPLACE)
        return True

    def mark_verification_sent(self, email: str, token_hash: str, now_utc: datetime) -> None:
        current = self._get_contact(email)
        if current is None or str(current.get("PendingTokenHash") or "") != token_hash:
            return
        self._table.update_entity(
            {
                "PartitionKey": CONTACT_PARTITION,
                "RowKey": _hash(email),
                "VerificationSentAtUtc": now_utc.isoformat(),
                "PendingToken": "",
            },
            mode=UpdateMode.MERGE,
        )

    def pending_verifications(
        self,
        now_utc: datetime,
        *,
        limit: int = 5,
    ) -> list[PendingVerification]:
        rows = self._table.query_entities(
            "PartitionKey eq @partition",
            parameters={"partition": CONTACT_PARTITION},
        )
        pending: list[PendingVerification] = []
        for item in rows:
            token = str(item.get("PendingToken") or "")
            token_hash = str(item.get("PendingTokenHash") or "")
            if not token or not token_hash or str(item.get("VerificationSentAtUtc") or ""):
                continue
            try:
                expires = datetime.fromisoformat(str(item.get("PendingExpiresAtUtc") or ""))
                keys = frozenset(json.loads(str(item.get("PendingTeamKeysJson") or "[]")))
            except (TypeError, ValueError, json.JSONDecodeError):
                continue
            email = str(item.get("Email") or "").strip().lower()
            name = str(item.get("PendingName") or "").strip()
            if expires > now_utc and EMAIL_PATTERN.fullmatch(email) and name and keys:
                pending.append(PendingVerification(name, email, keys, token, token_hash))
            if len(pending) >= limit:
                break
        return pending

    def verify(self, token_hash: str, now_utc: datetime) -> bool:
        rows = self._table.query_entities(
            "PartitionKey eq @partition and PendingTokenHash eq @token",
            parameters={"partition": CONTACT_PARTITION, "token": token_hash},
        )
        current = next(iter(rows), None)
        if current is None:
            return False
        try:
            expires = datetime.fromisoformat(str(current.get("PendingExpiresAtUtc") or ""))
        except ValueError:
            return False
        if expires <= now_utc:
            return False
        self._table.update_entity(
            {
                "PartitionKey": CONTACT_PARTITION,
                "RowKey": str(current["RowKey"]),
                "Name": str(current.get("PendingName") or ""),
                "TeamKeysJson": str(current.get("PendingTeamKeysJson") or "[]"),
                "Verified": True,
                "VerifiedAtUtc": now_utc.isoformat(),
                "PendingName": "",
                "PendingTeamKeysJson": "",
                "PendingTokenHash": "",
                "PendingToken": "",
                "PendingExpiresAtUtc": "",
            },
            mode=UpdateMode.MERGE,
        )
        return True

    def verified_contacts(self) -> list[VerifiedContact]:
        rows = self._table.query_entities(
            "PartitionKey eq @partition and Verified eq true",
            parameters={"partition": CONTACT_PARTITION},
        )
        contacts: list[VerifiedContact] = []
        for item in rows:
            email = str(item.get("Email") or "").strip().lower()
            try:
                keys = frozenset(json.loads(str(item.get("TeamKeysJson") or "[]")))
            except (TypeError, ValueError, json.JSONDecodeError):
                continue
            if EMAIL_PATTERN.fullmatch(email) and keys:
                contacts.append(
                    VerifiedContact(
                        name=str(item.get("Name") or "Trainer*in"),
                        email=email,
                        team_keys=keys,
                    )
                )
        return contacts

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


def _team_labels(keys: frozenset[str]) -> str:
    return ", ".join(sorted(key.removeprefix("team:").upper() for key in keys if key.startswith("team:")))


def register_contacts(
    raw_contacts: list[Mapping[str, Any]],
    values: Mapping[str, str],
    *,
    now_utc: datetime,
    store: AzureOccupancyNotificationStore | None = None,
) -> dict[str, int]:
    if not enabled(values):
        raise RuntimeError("Trainerbenachrichtigungen sind nicht aktiviert.")
    if len(raw_contacts) > MAX_CONTACTS:
        raise ValueError("Zu viele Kontakte in einem Request.")
    directory = store or AzureOccupancyNotificationStore.from_environment(values)
    accepted = pending = 0
    for raw in raw_contacts:
        name = _clean_text(raw.get("name"), 120)
        email = _clean_text(raw.get("email"), 180).lower()
        keys = _parse_keys(raw.get("teamKeys"))
        if not name or not EMAIL_PATTERN.fullmatch(email) or not keys:
            continue
        accepted += 1
        token = secrets.token_urlsafe(32)
        token_hash = _hash(token)
        if not directory.register_candidate(
            name=name,
            email=email,
            team_keys=keys,
            token=token,
            token_hash=token_hash,
            now_utc=now_utc,
        ):
            continue
        pending += 1
    return {"accepted": accepted, "pending": pending}


def process_contact_verifications(
    now_utc: datetime,
    values: Mapping[str, str],
    *,
    store: AzureOccupancyNotificationStore | None = None,
    mail_sender: MailSender = _send_message,
) -> int:
    if not enabled(values):
        return 0
    settings = OrderMailSettings.from_mapping(values)
    directory = store or AzureOccupancyNotificationStore.from_environment(values)
    sent = 0
    for candidate in directory.pending_verifications(now_utc, limit=5):
        link = (
            _public_base_url(values)
            + "/api/occupancy-contact-verify?token="
            + candidate.token
        )
        message = EmailMessage()
        message["From"] = formataddr((settings.from_name, settings.from_address))
        message["To"] = candidate.email
        message["Subject"] = "SSV53: Trainerbenachrichtigungen bestätigen"
        message.set_content(
            f"Hallo {candidate.name},\n\n"
            "bitte bestätige, dass du automatische Hinweise zu Platzüberschneidungen "
            f"für folgende Mannschaft(en) erhalten möchtest: {_team_labels(candidate.team_keys)}.\n\n"
            f"{link}\n\n"
            "Der Link ist 48 Stunden gültig. Falls die Zuordnung nicht stimmt, klicke ihn bitte nicht.\n\n"
            "Schönwalder SV 1953 e.V."
        )
        mail_sender(settings, message)
        directory.mark_verification_sent(candidate.email, candidate.token_hash, now_utc)
        sent += 1
    return sent


def verify_contact(
    token: str,
    values: Mapping[str, str],
    *,
    now_utc: datetime,
    store: AzureOccupancyNotificationStore | None = None,
) -> bool:
    if not re.fullmatch(r"[A-Za-z0-9_-]{32,80}", str(token or "")):
        return False
    directory = store or AzureOccupancyNotificationStore.from_environment(values)
    return directory.verify(_hash(token), now_utc)


def _event_dt(event: Mapping[str, Any], *fields: str) -> datetime:
    for field in fields:
        value = event.get(field)
        if value:
            parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
            if parsed.tzinfo is not None:
                return parsed
    raise ValueError("Termin enthält keine gültige Zeit.")


def event_team_keys(event: Mapping[str, Any]) -> frozenset[str]:
    value = str(event.get("team") or event.get("teamCategory") or event.get("title") or "")
    normalized = value.lower().replace("ü", "ue").replace("ä", "ae").replace("ö", "oe")
    keys: set[str] = set()
    youth = re.search(r"(?:^|\W)([a-g])\s*([1-9]\d?)?(?:\W|$)", normalized)
    if youth:
        keys.add("team:" + youth.group(1) + (youth.group(2) or ""))
    if "minis" in normalized or "bambini" in normalized:
        keys.add("team:minis")
    senior = re.search(r"(?:ue|u|ueber)?\s*(40|50|60)", normalized)
    if senior:
        keys.add("team:ue" + senior.group(1))
    if "herren" in normalized or "maenner" in normalized:
        keys.add("team:herren")
    if "frauen" in normalized or "damen" in normalized:
        keys.add("team:frauen")
    return frozenset(keys)


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


def _collision_recipients(
    booking: Mapping[str, Any],
    contacts: list[VerifiedContact],
    settings: OrderMailSettings,
) -> list[VerifiedContact]:
    if str(booking.get("source") or "").lower() == "special":
        if booking.get("replacesTrainingEventId"):
            keys = event_team_keys(booking)
            matched = [contact for contact in contacts if keys.intersection(contact.team_keys)]
            if matched:
                return matched
        creator = booking.get("creator") if isinstance(booking.get("creator"), Mapping) else {}
        email = _clean_text(creator.get("email"), 180).lower()
        if EMAIL_PATTERN.fullmatch(email):
            verified = next(
                (contact for contact in contacts if contact.email == email),
                None,
            )
            if verified is not None:
                return [verified]
    keys = event_team_keys(booking)
    matched = [contact for contact in contacts if keys.intersection(contact.team_keys)]
    if matched:
        return matched
    return [VerifiedContact("Platzbelegung", settings.from_address, frozenset())]


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
    settings = OrderMailSettings.from_mapping(values)
    directory = store or AzureOccupancyNotificationStore.from_environment(values)
    contacts = directory.verified_contacts()
    occupancy = dict(payload) if payload is not None else _current_payload(now_utc, values)
    collisions = find_collisions(list(occupancy.get("events", [])))
    sent = 0
    for match, booking in collisions:
        for recipient in _collision_recipients(booking, contacts, settings):
            recipient_hash = _hash(recipient.email)
            fingerprint = "|".join(
                (
                    str(match.get("id") or ""),
                    str(match.get("occupancyStart") or match.get("start") or ""),
                    str(match.get("occupancyEnd") or match.get("end") or ""),
                    str(booking.get("id") or ""),
                    str(booking.get("start") or ""),
                    str(booking.get("end") or ""),
                    str(booking.get("resourceId") or ""),
                    recipient_hash,
                )
            )
            if not directory.claim_delivery(fingerprint, now_utc):
                continue
            message = EmailMessage()
            message["From"] = formataddr((settings.from_name, settings.from_address))
            message["To"] = recipient.email
            message["Subject"] = "SSV53: Spiel kollidiert mit Platzbelegung"
            kickoff = _event_dt(match, "start")
            booking_start = _event_dt(booking, "start")
            booking_end = _event_dt(booking, "end")
            message.set_content(
                f"Hallo {recipient.name},\n\n"
                "ein Verbandsspiel überschneidet sich mit einer bestehenden Platzbelegung:\n\n"
                f"Spiel: {match.get('title') or 'Heimspiel'}\n"
                f"Anstoß: {kickoff.strftime('%d.%m.%Y %H:%M')} Uhr\n"
                f"Bestehende Belegung: {booking.get('title') or 'Belegung'}\n"
                f"Belegungszeit: {booking_start.strftime('%d.%m.%Y %H:%M')} bis {booking_end.strftime('%H:%M')} Uhr\n"
                f"Platz: {match.get('resourceId') or ''}\n\n"
                "Bitte stimmt die Platznutzung miteinander ab. Es werden keine Kontaktdaten anderer Personen übermittelt.\n\n"
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
