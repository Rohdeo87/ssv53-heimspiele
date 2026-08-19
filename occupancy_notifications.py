from __future__ import annotations

import hashlib
import html
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
from order_mail import (
    APP_BG,
    APP_BLUE,
    APP_BORDER,
    APP_DARK_BLUE,
    APP_GOLD,
    APP_LOGO_URL,
    APP_MUTED,
    EMAIL_PATTERN,
    OrderMailSettings,
    _open_authenticated_smtp,
)
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


def _branded_message(
    settings: OrderMailSettings,
    recipient: str,
    *,
    subject: str,
    headline: str,
    intro: str,
    badge: str,
    details: list[tuple[str, str]],
    closing: str,
    automatic_note: str,
) -> EmailMessage:
    plain_details = "\n".join(f"{label}: {value}" for label, value in details)
    plain = (
        f"{headline}\n\n{intro}\n\n{plain_details}\n\n{closing}\n\n"
        "Viele Grüße\nSchönwalder SV 1953 e.V.\n"
    )
    detail_rows = "".join(
        f"""
        <tr>
          <td style="padding:11px 14px;border-bottom:1px solid {APP_BORDER};
                     font-size:12px;line-height:1.4;color:{APP_MUTED};width:34%;">
            {html.escape(label)}
          </td>
          <td style="padding:11px 14px;border-bottom:1px solid {APP_BORDER};
                     font-size:14px;line-height:1.45;color:#1F2937;font-weight:700;">
            {html.escape(value)}
          </td>
        </tr>"""
        for label, value in details
    )
    html_body = f"""\
<!doctype html>
<html lang="de">
  <body style="margin:0;padding:0;background:{APP_BG};font-family:Arial,Helvetica,sans-serif;
               color:#111827;-webkit-text-size-adjust:100%;">
    <table role="presentation" width="100%" cellspacing="0" cellpadding="0"
           style="width:100%;background:{APP_BG};border-collapse:collapse;">
      <tr>
        <td align="center" style="padding:24px 10px;">
          <table role="presentation" width="100%" cellspacing="0" cellpadding="0"
                 style="width:100%;max-width:620px;background:#FFFFFF;border-collapse:separate;
                        border-spacing:0;border-radius:18px;overflow:hidden;">
            <tr>
              <td style="height:7px;background:{APP_BLUE};font-size:0;line-height:0;">&nbsp;</td>
            </tr>
            <tr>
              <td align="center" style="padding:25px 22px 8px 22px;">
                <img src="{APP_LOGO_URL}" alt="Schönwalder SV 1953 e.V." width="72"
                     style="display:block;width:72px;height:72px;object-fit:contain;border:0;">
              </td>
            </tr>
            <tr>
              <td align="center" style="padding:4px 24px 0 24px;">
                <span style="display:inline-block;padding:7px 11px;border-radius:999px;
                             background:#FFF4D6;color:{APP_DARK_BLUE};font-size:11px;
                             letter-spacing:.4px;font-weight:800;">
                  {html.escape(badge)}
                </span>
                <div style="margin-top:13px;font-size:25px;line-height:1.25;
                            font-weight:800;color:#111111;">
                  {html.escape(headline)}
                </div>
                <div style="margin-top:8px;font-size:14px;line-height:1.5;color:#555555;">
                  Schönwalder SV 1953 e.V.
                </div>
                <div style="height:2px;background:{APP_GOLD};width:70%;max-width:420px;
                            margin:16px auto 0 auto;"></div>
              </td>
            </tr>
            <tr>
              <td style="padding:22px 24px 29px 24px;">
                <div style="font-size:15px;line-height:1.6;color:#374151;">
                  {html.escape(intro)}
                </div>
                <table role="presentation" width="100%" cellspacing="0" cellpadding="0"
                       style="margin-top:19px;border-collapse:separate;border-spacing:0;
                              border:1px solid {APP_BORDER};border-radius:14px;
                              background:#F8FAFC;overflow:hidden;">
                  {detail_rows}
                </table>
                <div style="margin-top:21px;padding:15px 16px;border-radius:14px;
                            background:#EEF4FB;font-size:14px;line-height:1.6;color:#374151;">
                  <strong style="color:{APP_DARK_BLUE};">Nächster Schritt</strong><br>
                  {html.escape(closing)}
                </div>
                <div style="margin-top:27px;font-size:14px;line-height:1.6;color:#374151;">
                  Viele Grüße<br>
                  <strong style="color:{APP_DARK_BLUE};">Schönwalder SV 1953 e.V.</strong>
                </div>
              </td>
            </tr>
          </table>
          <div style="max-width:620px;margin:13px auto 0 auto;padding:0 12px;
                      text-align:center;font-size:11px;line-height:1.5;color:#8A94A3;">
            {html.escape(automatic_note)}
          </div>
        </td>
      </tr>
    </table>
  </body>
</html>
"""
    message = EmailMessage()
    message["From"] = formataddr((settings.from_name, settings.from_address))
    message["To"] = recipient
    message["Reply-To"] = settings.from_address
    message["Subject"] = subject
    message.set_content(plain)
    message.add_alternative(html_body, subtype="html")
    return message


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
            kickoff = _event_dt(match, "kickoff", "start")
            booking_start = _event_dt(booking, "start")
            booking_end = _event_dt(booking, "end")
            resource = str(match.get("resourceId") or "")
            place = {"rasen": "Rasenplatz", "kunstrasen": "Kunstrasenplatz"}.get(
                resource.lower(), resource or "Nicht angegeben"
            )
            message = _branded_message(
                settings,
                recipient,
                subject="SSV53: Überschneidung im Belegungsplan",
                headline="Überschneidung erkannt",
                intro=(
                    "Ein neu angesetztes Verbandsspiel überschneidet sich mit einer "
                    "bestehenden Platzbelegung."
                ),
                badge="PRÜFUNG ERFORDERLICH",
                details=[
                    ("Spiel", str(match.get("title") or "Heimspiel")),
                    ("Anstoß", kickoff.strftime("%d.%m.%Y um %H:%M Uhr")),
                    ("Bestehende Belegung", str(booking.get("title") or "Belegung")),
                    (
                        "Belegungszeit",
                        f"{booking_start.strftime('%d.%m.%Y, %H:%M')} bis "
                        f"{booking_end.strftime('%H:%M')} Uhr",
                    ),
                    ("Platz", place),
                ],
                closing="Bitte prüft und koordiniert die Platznutzung.",
                automatic_note="Automatische Benachrichtigung aus dem SSV53-Belegungsplan",
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
        message = _branded_message(
            settings,
            recipient,
            subject="[TEST] SSV53 Kollisionsbenachrichtigung",
            headline="Test erfolgreich",
            intro="Die zentrale Benachrichtigung aus dem SSV53-Belegungsplan ist einsatzbereit.",
            badge="SYSTEMTEST",
            details=[
                ("Status", "E-Mail-Versand funktioniert"),
                ("Bereich", "Platzbelegungsplan"),
                ("Empfänger", recipient),
            ],
            closing="Es ist keine weitere Aktion erforderlich.",
            automatic_note="Angeforderte Testmail aus dem SSV53-Belegungsplan",
        )
        mail_sender(settings, message)
        sent += 1
    return {"ok": True, "sent": sent}
