from __future__ import annotations

import html
import logging
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
MAX_ORDER_ITEMS = 20
MAX_ITEM_QTY = 50
MAX_UNIT_PRICE_CENTS = 100_000
MAX_ORDER_TOTAL_CENTS = 2_000_000

APP_BLUE = "#285EA7"
APP_DARK_BLUE = "#173B6D"
APP_GOLD = "#E0AA3F"
APP_BG = "#F2F4F6"
APP_MUTED = "#718096"
APP_BORDER = "#E3E8ED"
APP_LOGO_URL = "https://cdn.appack.de/ssv53/images/Icon_Verein.png"

PRODUCT_IMAGES = {
    ("damen", "blau"): "https://cdn.appack.de/ssv53/images/Damen_blau.jpeg",
    ("damen", "weiss"): "https://cdn.appack.de/ssv53/images/Damen_wei%C3%9F.jpeg",
    ("herren", "blau"): "https://cdn.appack.de/ssv53/images/Herren_blau.jpeg",
    ("herren", "weiss"): "https://cdn.appack.de/ssv53/images/Herren_wei%C3%9F.jpeg",
    ("kids", "blau"): "https://cdn.appack.de/ssv53/images/Kids_blau.jpeg",
    ("kids", "weiss"): "https://cdn.appack.de/ssv53/images/Kids_wei%C3%9F.jpeg",
}
ALLOWED_GROUP_KEYS = {"damen", "herren", "kids"}
ALLOWED_COLOR_KEYS = {"blau", "weiss"}


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
        credential = credential_factory(client_id=settings.managed_identity_client_id)
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
        now = datetime.now(timezone.utc).isoformat()
        self._table_client.update_entity(
            entity={
                "PartitionKey": PARTITION_KEY,
                "RowKey": order_id,
                "status": "sent",
                "recipient": recipient.lower(),
                "sentAtUtc": now,
                "updatedAtUtc": now,
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


def _safe_short_text(value: Any, *, field: str, max_length: int) -> str:
    text = re.sub(r"[\r\n\t]+", " ", str(value or "").strip())
    text = re.sub(r"\s{2,}", " ", text)
    if not text or len(text) > max_length:
        raise OrderMailError(
            "ORDER_DETAILS_INVALID",
            f"Das Feld {field} der Bestellübersicht ist ungültig.",
            status_code=400,
        )
    return text


def _strict_int(value: Any, *, field: str, minimum: int, maximum: int) -> int:
    if isinstance(value, bool):
        raise OrderMailError(
            "ORDER_DETAILS_INVALID",
            f"Das Feld {field} der Bestellübersicht ist ungültig.",
            status_code=400,
        )
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise OrderMailError(
            "ORDER_DETAILS_INVALID",
            f"Das Feld {field} der Bestellübersicht ist ungültig.",
            status_code=400,
        ) from exc
    if str(parsed) != str(value).strip() and not isinstance(value, int):
        raise OrderMailError(
            "ORDER_DETAILS_INVALID",
            f"Das Feld {field} der Bestellübersicht ist ungültig.",
            status_code=400,
        )
    if parsed < minimum or parsed > maximum:
        raise OrderMailError(
            "ORDER_DETAILS_INVALID",
            f"Das Feld {field} der Bestellübersicht ist außerhalb des erlaubten Bereichs.",
            status_code=400,
        )
    return parsed


def _normalize_product_keys(
    *,
    group_key: Any,
    color_key: Any,
    variant: str,
) -> tuple[str | None, str | None]:
    group = str(group_key or "").strip().lower()
    color = str(color_key or "").strip().lower().replace("ß", "ss")

    normalized_variant = (
        variant.lower()
        .replace("ä", "ae")
        .replace("ö", "oe")
        .replace("ü", "ue")
        .replace("ß", "ss")
    )

    if not group:
        if "frau" in normalized_variant or "damen" in normalized_variant:
            group = "damen"
        elif "maenner" in normalized_variant or "herren" in normalized_variant:
            group = "herren"
        elif "kids" in normalized_variant or "kinder" in normalized_variant:
            group = "kids"

    if not color:
        if "blau" in normalized_variant:
            color = "blau"
        elif "weiss" in normalized_variant:
            color = "weiss"

    if group and group not in ALLOWED_GROUP_KEYS:
        raise OrderMailError(
            "ORDER_DETAILS_INVALID",
            "Die Produktgruppe der Bestellübersicht ist ungültig.",
            status_code=400,
        )
    if color and color not in ALLOWED_COLOR_KEYS:
        raise OrderMailError(
            "ORDER_DETAILS_INVALID",
            "Die Produktfarbe der Bestellübersicht ist ungültig.",
            status_code=400,
        )
    return group or None, color or None


def _validate_order_details(
    payload: Mapping[str, Any],
) -> tuple[list[dict[str, Any]], int | None]:
    raw_items = payload.get("items")
    raw_total = payload.get("totalCents")

    if raw_items is None:
        if raw_total is None:
            return [], None
        total = _strict_int(
            raw_total,
            field="totalCents",
            minimum=0,
            maximum=MAX_ORDER_TOTAL_CENTS,
        )
        return [], total

    if not isinstance(raw_items, list) or not raw_items or len(raw_items) > MAX_ORDER_ITEMS:
        raise OrderMailError(
            "ORDER_DETAILS_INVALID",
            "Die Bestellübersicht enthält eine ungültige Anzahl von Positionen.",
            status_code=400,
        )

    items: list[dict[str, Any]] = []
    calculated_total = 0
    for index, raw_item in enumerate(raw_items, start=1):
        if not isinstance(raw_item, Mapping):
            raise OrderMailError(
                "ORDER_DETAILS_INVALID",
                "Eine Position der Bestellübersicht ist ungültig.",
                status_code=400,
            )

        variant = _safe_short_text(
            raw_item.get("variant"),
            field=f"items[{index}].variant",
            max_length=100,
        )
        size = _safe_short_text(
            raw_item.get("size"),
            field=f"items[{index}].size",
            max_length=24,
        )
        qty = _strict_int(
            raw_item.get("qty"),
            field=f"items[{index}].qty",
            minimum=1,
            maximum=MAX_ITEM_QTY,
        )
        unit_price_cents = _strict_int(
            raw_item.get("unitPriceCents"),
            field=f"items[{index}].unitPriceCents",
            minimum=0,
            maximum=MAX_UNIT_PRICE_CENTS,
        )
        group, color = _normalize_product_keys(
            group_key=raw_item.get("groupKey"),
            color_key=raw_item.get("colorKey"),
            variant=variant,
        )
        line_total = qty * unit_price_cents
        calculated_total += line_total

        if calculated_total > MAX_ORDER_TOTAL_CENTS:
            raise OrderMailError(
                "ORDER_DETAILS_INVALID",
                "Die Gesamtsumme der Bestellübersicht überschreitet den erlaubten Bereich.",
                status_code=400,
            )

        items.append(
            {
                "variant": variant,
                "size": size,
                "qty": qty,
                "unitPriceCents": unit_price_cents,
                "lineTotalCents": line_total,
                "groupKey": group,
                "colorKey": color,
            }
        )

    if raw_total is None:
        total = calculated_total
    else:
        total = _strict_int(
            raw_total,
            field="totalCents",
            minimum=0,
            maximum=MAX_ORDER_TOTAL_CENTS,
        )
        if total != calculated_total:
            raise OrderMailError(
                "ORDER_TOTAL_MISMATCH",
                "Die übermittelte Gesamtsumme stimmt nicht mit den Bestellpositionen überein.",
                status_code=400,
            )

    return items, total


def _validate_ready_request(
    payload: Mapping[str, Any],
) -> tuple[str, str, str, list[dict[str, Any]], int | None]:
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

    items, total_cents = _validate_order_details(payload)
    return order_id, recipient, name, items, total_cents


def _euro(cents: int) -> str:
    return f"{cents / 100:.2f}".replace(".", ",") + " €"


def _plain_order_details(items: list[dict[str, Any]], total_cents: int | None) -> str:
    if not items:
        return ""

    lines = ["", "Deine Bestellung:"]
    for item in items:
        lines.append(
            f"- {item['variant']} | Größe {item['size']} | "
            f"{item['qty']} × {_euro(item['unitPriceCents'])} = "
            f"{_euro(item['lineTotalCents'])}"
        )
    if total_cents is not None:
        lines.extend(["", f"Gesamtsumme: {_euro(total_cents)}"])
    return "\n".join(lines) + "\n"


def _product_rows_html(items: list[dict[str, Any]]) -> str:
    rows: list[str] = []
    for item in items:
        safe_variant = html.escape(item["variant"])
        safe_size = html.escape(item["size"])
        image_url = PRODUCT_IMAGES.get((item["groupKey"], item["colorKey"]))
        image_html = ""
        if image_url:
            image_html = (
                f'<img src="{html.escape(image_url, quote=True)}" '
                f'alt="{safe_variant}" width="84" '
                'style="display:block;width:84px;height:84px;object-fit:cover;'
                'border-radius:12px;border:1px solid #E3E8ED;background:#FFFFFF;">'
            )
        else:
            image_html = (
                f'<div style="width:84px;height:84px;border-radius:12px;'
                f'background:#F2F4F6;border:1px solid #E3E8ED;'
                f'text-align:center;line-height:84px;color:#718096;font-size:12px;">SSV53</div>'
            )

        rows.append(
            f"""
            <table role="presentation" width="100%" cellspacing="0" cellpadding="0"
                   style="border-collapse:separate;border-spacing:0;margin:0 0 10px 0;
                          border:1px solid {APP_BORDER};border-radius:14px;background:#FFFFFF;">
              <tr>
                <td width="104" valign="top" style="padding:12px 8px 12px 12px;">
                  {image_html}
                </td>
                <td valign="top" style="padding:14px 12px 12px 4px;">
                  <div style="font-size:16px;line-height:1.3;font-weight:700;color:#111827;">
                    {safe_variant}
                  </div>
                  <div style="margin-top:5px;font-size:13px;line-height:1.45;color:{APP_MUTED};">
                    Größe {safe_size} &nbsp;·&nbsp; {item['qty']} Stück
                  </div>
                  <div style="margin-top:8px;font-size:13px;color:{APP_MUTED};">
                    {item['qty']} × {_euro(item['unitPriceCents'])}
                  </div>
                </td>
                <td width="96" valign="middle" align="right"
                    style="padding:14px 14px 12px 6px;font-size:16px;
                           font-weight:700;color:#111827;white-space:nowrap;">
                  {_euro(item['lineTotalCents'])}
                </td>
              </tr>
            </table>
            """
        )
    return "".join(rows)


def _build_ready_message(
    *,
    settings: OrderMailSettings,
    order_id: str,
    recipient: str,
    name: str,
    items: list[dict[str, Any]],
    total_cents: int | None,
) -> EmailMessage:
    subject = "Deine SSV53-Bestellung ist abholbereit"

    details_plain = _plain_order_details(items, total_cents)
    plain = (
        f"Hallo {name},\n\n"
        f"deine Bestellung {order_id} ist ab sofort abholbereit.\n"
        f"{details_plain}\n"
        "Bezahlung: bei Abholung.\n\n"
        "Den aktuellen Status deiner Bestellung findest du in der SSV53-App "
        "unter „Sonstiges → Meine Bestellungen“.\n\n"
        "Viele Grüße\n"
        "Schönwalder SV 1953 e.V.\n"
    )

    safe_name = html.escape(name)
    safe_order_id = html.escape(order_id)
    product_rows = _product_rows_html(items)

    details_section = ""
    if items:
        total_html = (
            f"""
            <table role="presentation" width="100%" cellspacing="0" cellpadding="0"
                   style="margin-top:14px;border-collapse:separate;border-spacing:0;
                          background:{APP_BLUE};border-radius:14px;">
              <tr>
                <td style="padding:15px 16px;color:#FFFFFF;font-size:14px;font-weight:700;">
                  Gesamtsumme
                </td>
                <td align="right" style="padding:15px 16px;color:#FFFFFF;
                                         font-size:20px;font-weight:800;white-space:nowrap;">
                  {_euro(total_cents or 0)}
                </td>
              </tr>
            </table>
            """
        )
        details_section = f"""
          <div style="margin-top:26px;font-size:17px;font-weight:800;color:{APP_DARK_BLUE};">
            Deine Bestellung
          </div>
          <div style="height:2px;background:{APP_GOLD};width:86px;margin:8px 0 14px 0;"></div>
          {product_rows}
          {total_html}
        """

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
                        border-spacing:0;border-radius:18px;">
            <tr>
              <td align="center" style="padding:26px 22px 8px 22px;">
                <img src="{APP_LOGO_URL}" alt="Schönwalder SV 1953 e.V." width="72"
                     style="display:block;width:72px;height:72px;object-fit:contain;border:0;">
              </td>
            </tr>
            <tr>
              <td align="center" style="padding:4px 24px 0 24px;">
                <div style="font-size:25px;line-height:1.25;font-weight:800;color:#111111;">
                  Deine Bestellung ist abholbereit
                </div>
                <div style="margin-top:8px;font-size:14px;line-height:1.5;color:#555555;">
                  Schönwalder SV 1953 e.V.
                </div>
                <div style="height:2px;background:{APP_GOLD};width:70%;max-width:420px;
                            margin:16px auto 0 auto;"></div>
              </td>
            </tr>
            <tr>
              <td style="padding:22px 24px 28px 24px;">
                <div style="font-size:16px;line-height:1.55;color:#111827;">
                  Hallo <strong>{safe_name}</strong>,
                </div>
                <div style="margin-top:9px;font-size:15px;line-height:1.6;color:#374151;">
                  deine Bestellung ist ab sofort zur Abholung bereit.
                </div>

                <table role="presentation" width="100%" cellspacing="0" cellpadding="0"
                       style="margin-top:18px;border-collapse:separate;border-spacing:0;
                              background:#EEF4FB;border-radius:14px;">
                  <tr>
                    <td style="padding:13px 15px;">
                      <div style="font-size:12px;line-height:1.3;color:{APP_MUTED};">
                        Bestellnummer
                      </div>
                      <div style="margin-top:3px;font-size:16px;line-height:1.3;
                                  font-weight:800;color:{APP_DARK_BLUE};">
                        {safe_order_id}
                      </div>
                    </td>
                    <td align="right" valign="middle" style="padding:13px 15px;">
                      <span style="display:inline-block;padding:7px 10px;border-radius:999px;
                                   background:{APP_BLUE};color:#FFFFFF;font-size:12px;
                                   font-weight:800;">ABHOLBEREIT</span>
                    </td>
                  </tr>
                </table>

                {details_section}

                <table role="presentation" width="100%" cellspacing="0" cellpadding="0"
                       style="margin-top:18px;border-collapse:separate;border-spacing:0;
                              border:1px solid {APP_BORDER};border-radius:14px;background:#F8FAFC;">
                  <tr>
                    <td style="padding:14px 16px;font-size:14px;line-height:1.55;color:#374151;">
                      <strong style="color:{APP_DARK_BLUE};">Bezahlung</strong><br>
                      Bei Abholung
                    </td>
                  </tr>
                </table>

                <div style="margin-top:22px;font-size:14px;line-height:1.6;color:#555555;">
                  Den aktuellen Status findest du jederzeit in der SSV53-App unter
                  <strong>Sonstiges → Meine Bestellungen</strong>.
                </div>

                <div style="margin-top:28px;font-size:14px;line-height:1.6;color:#374151;">
                  Viele Grüße<br>
                  <strong style="color:{APP_DARK_BLUE};">Schönwalder SV 1953 e.V.</strong>
                </div>
              </td>
            </tr>
          </table>

          <div style="max-width:620px;margin:13px auto 0 auto;padding:0 12px;
                      text-align:center;font-size:11px;line-height:1.5;color:#8A94A3;">
            Diese E-Mail wurde automatisch zu deiner SSV53-T-Shirt-Bestellung versendet.
          </div>
        </td>
      </tr>
    </table>
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

    order_id, recipient, name, items, total_cents = _validate_ready_request(payload)
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
        items=items,
        total_cents=total_cents,
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
        "itemCount": len(items),
        "totalCents": total_cents,
    }
