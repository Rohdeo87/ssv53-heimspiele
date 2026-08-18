from __future__ import annotations

import html
import logging
import os
import re
import smtplib
import ssl
from dataclasses import dataclass
from datetime import datetime, timezone
from email.message import EmailMessage
from email.utils import formataddr
from typing import Any, Mapping

from azure.core import MatchConditions
from azure.core.exceptions import ResourceExistsError, ResourceNotFoundError
from azure.data.tables import TableClient, UpdateMode
from azure.identity import ManagedIdentityCredential


LOGGER = logging.getLogger("ssv53.azure.order_mail")

ORDER_ID_PATTERN = re.compile(r"^SSV53-\d{6}-\d{6}-[A-Z0-9]{4}$")
EMAIL_PATTERN = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
PARTITION_KEY = "ssv53-order-ready-mail-v1"


class OrderMailError(RuntimeError):
    def __init__(self, code: str, message: str, *, status_code: int = 500) -> None:
        super().__init__(message)
        self.code = code
        self.status_code = status_code


@dataclass(frozen=True)
class OrderMailSettings:
    enabled: bool
    smtp_host: str
    smtp_port: int
    smtp_username: str
    smtp_password: str
    from_address: str
    from_name: str
    storage_account_url: str
    state_table_name: str
    managed_identity_client_id: str

    @classmethod
    def from_mapping(cls, values: Mapping[str, str]) -> "OrderMailSettings":
        enabled = str(values.get("SSV53_ORDER_MAIL_ENABLED", "")).strip().lower() == "true"
        smtp_host = str(values.get("SSV53_ORDER_MAIL_SMTP_HOST", "")).strip()
        smtp_username = str(values.get("SSV53_ORDER_MAIL_SMTP_USERNAME", "")).strip()
        smtp_password = str(values.get("SSV53_ORDER_MAIL_SMTP_PASSWORD", "")).strip()
        from_address = str(values.get("SSV53_ORDER_MAIL_FROM_ADDRESS", "")).strip()
        from_name = str(values.get("SSV53_ORDER_MAIL_FROM_NAME", "")).strip()
        storage_account_url = str(values.get("SSV53_STORAGE_ACCOUNT_URL", "")).strip()
        state_table_name = str(values.get("SSV53_STATE_TABLE_NAME", "")).strip()
        client_id = str(
            values.get("SSV53_STATE_MANAGED_IDENTITY_CLIENT_ID")
            or values.get("AzureWebJobsStorage__clientId")
            or ""
        ).strip()

        try:
            smtp_port = int(str(values.get("SSV53_ORDER_MAIL_SMTP_PORT", "587")).strip())
        except (TypeError, ValueError) as exc:
            raise OrderMailError(
                "SMTP_PORT_INVALID",
                "Der konfigurierte SMTP-Port ist ungültig.",
                status_code=503,
            ) from exc

        required = {
            "SSV53_ORDER_MAIL_SMTP_HOST": smtp_host,
            "SSV53_ORDER_MAIL_SMTP_USERNAME": smtp_username,
            "SSV53_ORDER_MAIL_SMTP_PASSWORD": smtp_password,
            "SSV53_ORDER_MAIL_FROM_ADDRESS": from_address,
        }
        missing = [name for name, value in required.items() if not value]
        if missing:
            raise OrderMailError(
                "SMTP_CONFIG_INCOMPLETE",
                "Die SMTP-Konfiguration ist unvollständig.",
                status_code=503,
            )
        if smtp_password.startswith("@Microsoft.KeyVault("):
            raise OrderMailError(
                "SMTP_SECRET_NOT_RESOLVED",
                "Das SMTP-Passwort konnte aus Azure Key Vault nicht aufgelöst werden.",
                status_code=503,
            )
        if smtp_port not in {465, 587}:
            raise OrderMailError(
                "SMTP_PORT_NOT_ALLOWED",
                "Für den Mailversand sind ausschließlich die sicheren SMTP-Ports 465 oder 587 freigegeben.",
                status_code=503,
            )
        if not EMAIL_PATTERN.fullmatch(smtp_username):
            raise OrderMailError(
                "SMTP_USERNAME_INVALID",
                "Der SMTP-Benutzername ist keine gültige E-Mail-Adresse.",
                status_code=503,
            )
        if not EMAIL_PATTERN.fullmatch(from_address):
            raise OrderMailError(
                "SMTP_FROM_INVALID",
                "Die Absenderadresse ist ungültig.",
                status_code=503,
            )

        return cls(
            enabled=enabled,
            smtp_host=smtp_host,
            smtp_port=smtp_port,
            smtp_username=smtp_username,
            smtp_password=smtp_password,
            from_address=from_address,
            from_name=from_name or "Schönwalder SV 1953 e.V.",
            storage_account_url=storage_account_url,
            state_table_name=state_table_name,
            managed_identity_client_id=client_id,
        )


class OrderMailStore:
    def __init__(self, table_client: TableClient) -> None:
        self._table_client = table_client

    @classmethod
    def from_settings(
        cls,
        settings: OrderMailSettings,
        *,
        credential_factory=ManagedIdentityCredential,
        table_client_factory=TableClient,
    ) -> "OrderMailStore":
        if (
            not settings.storage_account_url
            or not settings.state_table_name
            or not settings.managed_identity_client_id
        ):
            raise OrderMailError(
                "MAIL_STORE_CONFIG_INCOMPLETE",
                "Der Azure-Statusspeicher für den Mailversand ist unvollständig konfiguriert.",
                status_code=503,
            )
        credential = credential_factory(
            client_id=settings.managed_identity_client_id
        )
        client = table_client_factory(
            endpoint=settings.storage_account_url,
            table_name=settings.state_table_name,
            credential=credential,
        )
        return cls(client)

    def claim(self, *, order_id: str, recipient: str) -> str:
        now = datetime.now(timezone.utc).isoformat()
        entity = {
            "PartitionKey": PARTITION_KEY,
            "RowKey": order_id,
            "status": "sending",
            "recipient": recipient.lower(),
            "updatedAtUtc": now,
        }
        try:
            self._table_client.create_entity(entity=entity)
            return "claimed"
        except ResourceExistsError:
            pass

        try:
            current = self._table_client.get_entity(
                partition_key=PARTITION_KEY,
                row_key=order_id,
            )
        except ResourceNotFoundError:
            try:
                self._table_client.create_entity(entity=entity)
                return "claimed"
            except ResourceExistsError:
                return "busy"

        status = str(current.get("status") or "").strip().lower()
        saved_recipient = str(current.get("recipient") or "").strip().lower()
        if saved_recipient and saved_recipient != recipient.lower():
            raise OrderMailError(
                "ORDER_RECIPIENT_CONFLICT",
                "Für diese Bestellnummer wurde bereits eine andere Empfängeradresse registriert.",
                status_code=409,
            )
        if status == "sent":
            return "sent"
        if status == "sending":
            return "busy"

        metadata = getattr(current, "metadata", {}) or {}
        etag = str(metadata.get("etag") or "").strip()
        if not etag:
            return "busy"

        retry_entity = {
            "PartitionKey": PARTITION_KEY,
            "RowKey": order_id,
            "status": "sending",
            "recipient": recipient.lower(),
            "updatedAtUtc": now,
        }
        try:
            self._table_client.update_entity(
                entity=retry_entity,
                mode=UpdateMode.MERGE,
                etag=etag,
                match_condition=MatchConditions.IfNotModified,
            )
        except Exception:
            return "busy"
        return "claimed"

    def mark_sent(self, *, order_id: str, recipient: str) -> None:
        self._table_client.update_entity(
            entity={
                "PartitionKey": PARTITION_KEY,
                "RowKey": order_id,
                "status": "sent",
                "recipient": recipient.lower(),
                "sentAtUtc": datetime.now(timezone.utc).isoformat(),
                "updatedAtUtc": datetime.now(timezone.utc).isoformat(),
            },
            mode=UpdateMode.MERGE,
        )

    def mark_failed(self, *, order_id: str, recipient: str, error_code: str) -> None:
        self._table_client.update_entity(
            entity={
                "PartitionKey": PARTITION_KEY,
                "RowKey": order_id,
                "status": "failed",
                "recipient": recipient.lower(),
                "lastErrorCode": error_code[:64],
                "updatedAtUtc": datetime.now(timezone.utc).isoformat(),
            },
            mode=UpdateMode.MERGE,
        )


def _open_authenticated_smtp(
    settings: OrderMailSettings,
    *,
    smtp_factory=smtplib.SMTP,
    smtp_ssl_factory=smtplib.SMTP_SSL,
):
    context = ssl.create_default_context()

    if settings.smtp_port == 465:
        smtp = smtp_ssl_factory(
            settings.smtp_host,
            settings.smtp_port,
            timeout=20,
            context=context,
        )
        smtp.ehlo()
        smtp.login(settings.smtp_username, settings.smtp_password)
        return smtp

    smtp = smtp_factory(
        settings.smtp_host,
        settings.smtp_port,
        timeout=20,
    )
    smtp.ehlo()
    smtp.starttls(context=context)
    smtp.ehlo()
    smtp.login(settings.smtp_username, settings.smtp_password)
    return smtp


def check_smtp_connection(
    values: Mapping[str, str],
    *,
    smtp_factory=smtplib.SMTP,
    smtp_ssl_factory=smtplib.SMTP_SSL,
) -> dict[str, Any]:
    settings = OrderMailSettings.from_mapping(values)
    try:
        smtp = _open_authenticated_smtp(
            settings,
            smtp_factory=smtp_factory,
            smtp_ssl_factory=smtp_ssl_factory,
        )
        try:
            smtp.noop()
        finally:
            smtp.quit()
    except smtplib.SMTPAuthenticationError as exc:
        raise OrderMailError(
            "SMTP_AUTH_FAILED",
            "Die Anmeldung am OVH-Mailserver wurde abgelehnt.",
            status_code=503,
        ) from exc
    except (smtplib.SMTPException, OSError) as exc:
        raise OrderMailError(
            "SMTP_CONNECTION_FAILED",
            "Die sichere Verbindung zum OVH-Mailserver ist fehlgeschlagen.",
            status_code=503,
        ) from exc

    return {
        "ok": True,
        "smtpAuthenticated": True,
        "mailEnabled": settings.enabled,
        "smtpHost": settings.smtp_host,
        "smtpPort": settings.smtp_port,
    }


def _validate_ready_request(payload: Mapping[str, Any]) -> tuple[str, str, str]:
    order_id = str(payload.get("orderId") or "").strip().upper()
    recipient = str(payload.get("email") or "").strip().lower()
    name = re.sub(r"[\r\n]+", " ", str(payload.get("name") or "").strip())[:120]

    if not ORDER_ID_PATTERN.fullmatch(order_id):
        raise OrderMailError(
            "ORDER_ID_INVALID",
            "Die Bestellnummer ist ungültig.",
            status_code=400,
        )
    if not EMAIL_PATTERN.fullmatch(recipient):
        raise OrderMailError(
            "RECIPIENT_INVALID",
            "Die Empfängeradresse ist ungültig.",
            status_code=400,
        )
    if not name:
        name = "SSV53-Mitglied"

    return order_id, recipient, name


def _build_ready_message(
    *,
    settings: OrderMailSettings,
    order_id: str,
    recipient: str,
    name: str,
) -> EmailMessage:
    subject = "Deine SSV53-Bestellung ist abholbereit"
    plain = (
        f"Hallo {name},\n\n"
        f"deine Bestellung {order_id} ist ab sofort abholbereit.\n\n"
        "Den aktuellen Status deiner Bestellung findest du in der SSV53-App "
        "unter „Sonstiges → Meine Bestellungen“.\n\n"
        "Die Bezahlung erfolgt bei Abholung.\n\n"
        "Viele Grüße\n"
        "Schönwalder SV 1953 e.V.\n"
    )
    safe_name = html.escape(name)
    safe_order_id = html.escape(order_id)
    html_body = f"""\
<!doctype html>
<html lang="de">
  <body style="font-family:Arial,sans-serif;color:#172033;line-height:1.55">
    <div style="max-width:600px;margin:0 auto;padding:24px">
      <div style="font-size:22px;font-weight:700;color:#285EA7;margin-bottom:18px">
        Deine SSV53-Bestellung ist abholbereit
      </div>
      <p>Hallo {safe_name},</p>
      <p>
        deine Bestellung <strong>{safe_order_id}</strong> ist ab sofort
        <strong>abholbereit</strong>.
      </p>
      <p>
        Den aktuellen Status findest du jederzeit in der SSV53-App unter
        <strong>Sonstiges → Meine Bestellungen</strong>.
      </p>
      <p>Die Bezahlung erfolgt bei Abholung.</p>
      <p style="margin-top:28px">
        Viele Grüße<br>
        <strong>Schönwalder SV 1953 e.V.</strong>
      </p>
    </div>
  </body>
</html>
"""

    message = EmailMessage()
    message["Subject"] = subject
    message["From"] = formataddr((settings.from_name, settings.from_address))
    message["To"] = recipient
    message["Reply-To"] = settings.from_address
    message.set_content(plain)
    message.add_alternative(html_body, subtype="html")
    return message


def send_order_ready_mail(
    payload: Mapping[str, Any],
    values: Mapping[str, str],
    *,
    smtp_factory=smtplib.SMTP,
    smtp_ssl_factory=smtplib.SMTP_SSL,
    store: OrderMailStore | None = None,
) -> dict[str, Any]:
    settings = OrderMailSettings.from_mapping(values)
    if not settings.enabled:
        raise OrderMailError(
            "ORDER_MAIL_DISABLED",
            "Der produktive Bestell-Mailversand ist noch deaktiviert.",
            status_code=503,
        )

    if str(payload.get("confirmation") or "") != "SSV53-ORDER-ABHOLBEREIT":
        raise OrderMailError(
            "CONFIRMATION_INVALID",
            "Die Versandbestätigung ist ungültig.",
            status_code=400,
        )

    order_id, recipient, name = _validate_ready_request(payload)
    store = store or OrderMailStore.from_settings(settings)

    claim = store.claim(order_id=order_id, recipient=recipient)
    if claim == "sent":
        return {
            "ok": True,
            "sent": False,
            "alreadySent": True,
            "orderId": order_id,
        }
    if claim != "claimed":
        raise OrderMailError(
            "ORDER_MAIL_BUSY",
            "Für diese Bestellung wird bereits eine Mail verarbeitet.",
            status_code=409,
        )

    message = _build_ready_message(
        settings=settings,
        order_id=order_id,
        recipient=recipient,
        name=name,
    )

    try:
        smtp = _open_authenticated_smtp(
            settings,
            smtp_factory=smtp_factory,
            smtp_ssl_factory=smtp_ssl_factory,
        )
        try:
            smtp.send_message(message)
        finally:
            smtp.quit()
    except smtplib.SMTPAuthenticationError as exc:
        store.mark_failed(
            order_id=order_id,
            recipient=recipient,
            error_code="SMTP_AUTH_FAILED",
        )
        raise OrderMailError(
            "SMTP_AUTH_FAILED",
            "Die Anmeldung am OVH-Mailserver wurde abgelehnt.",
            status_code=503,
        ) from exc
    except (smtplib.SMTPException, OSError) as exc:
        store.mark_failed(
            order_id=order_id,
            recipient=recipient,
            error_code="SMTP_SEND_FAILED",
        )
        raise OrderMailError(
            "SMTP_SEND_FAILED",
            "Die Abhol-Mail konnte nicht sicher versendet werden.",
            status_code=502,
        ) from exc

    store.mark_sent(order_id=order_id, recipient=recipient)
    LOGGER.info("SSV53_ORDER_READY_MAIL_SENT order_id=%s", order_id)

    return {
        "ok": True,
        "sent": True,
        "alreadySent": False,
        "orderId": order_id,
    }
