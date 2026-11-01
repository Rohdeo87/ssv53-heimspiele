"""Server-side notifications for newly persisted Appack reservations.

No client can choose a recipient or trigger a send. The timer reads the canonical
worksheet; the public status endpoint exposes delivery metadata only.
"""
from __future__ import annotations

import json
import logging
import os
import smtplib
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage
from email.utils import formataddr
from email.headerregistry import Address
from typing import Mapping

import azure.functions as func
import requests
from azure.core.exceptions import ResourceNotFoundError

from order_mail import (
    ORDER_ID_PATTERN, EMAIL_PATTERN, OrderMailError, OrderMailSettings, OrderMailStore,
    PARTITION_KEY, _validate_ready_request, _plain_order_details, _product_rows_html,
    _open_authenticated_smtp,
)
import html

LOGGER = logging.getLogger('ssv53.reservation_notifications')
WORKSHEET = '6904811020058b7a3fc5b5d4'
SOURCE_URL = 'https://appack.de/rest-api/public/workbook/worksheet/' + WORKSHEET
ADMIN_URL = 'https://appack.de/rest-api/drender/6a8419f77b82ad5810e20858'
CHANNELS = ('customer', 'office')
notification_blueprint = func.Blueprint()


def utc(value):
    if isinstance(value, dict):
        value = value.get('$date')
    parsed = datetime.fromisoformat(str(value).replace('Z', '+00:00'))
    if parsed.tzinfo is None:
        raise ValueError('Server timestamp must include timezone')
    return parsed.astimezone(timezone.utc)


def single_email(value):
    if not EMAIL_PATTERN.fullmatch(value):
        raise ValueError("Invalid single recipient")
    try:
        if Address(addr_spec=value).addr_spec != value:
            raise ValueError("Invalid single recipient")
    except Exception:
        raise ValueError("Invalid single recipient") from None
    return value


def configuration(values):
    if str(values.get('SSV53_RESERVATION_NOTIFICATIONS_ENABLED', '')).lower() != 'true':
        return None
    cutoff = utc(values.get('SSV53_RESERVATION_NOTIFICATIONS_FROM_UTC'))
    office = str(values.get('SSV53_RESERVATION_OFFICE_EMAIL', '')).strip().lower()
    single_email(office)
    return cutoff, office


def source_rows(order_id=None, *, session=requests, offset=0):
    filters = {'resultText': 'reservierung'}
    if order_id is not None:
        if not ORDER_ID_PATTERN.fullmatch(order_id):
            raise ValueError('Invalid reservation id')
        filters['resultTitle'] = order_id
    response = session.post(SOURCE_URL, params={
        'sortBy': '_autoId', 'sortDirection': '-1', 'skip': offset,
        'limit': 2 if order_id else 100, 'export': 'false',
    }, json=filters, headers={'Accept': 'application/json'}, timeout=(5, 20), allow_redirects=False)
    if response.status_code != 200 or len(response.content) > 2_000_000:
        raise ValueError('Reservation source unavailable')
    rows = response.json()
    if not isinstance(rows, list):
        raise ValueError('Invalid reservation source')
    return rows


def reservation(row, cutoff, now):
    if row.get('resultText') != 'reservierung':
        return None
    created = utc(row.get('_created'))
    if created < cutoff or created > now + timedelta(minutes=2):
        return None
    data = json.loads(row.get('resultEntry') or '{}')
    order_id = str(data.get('orderId') or '')
    if row.get('resultTitle') != order_id or not ORDER_ID_PATTERN.fullmatch(order_id):
        raise ValueError('Reservation id mismatch')
    if data.get('status') == 'storniert':
        return None
    # Values come only from the persisted record, never from endpoint parameters.
    payload = dict(data, email=row.get('resultMail') or data.get('email'),
                   name=row.get('resultName') or data.get('name'))
    order_id, email, name, items, total = _validate_ready_request(payload)
    single_email(email)
    if not items:
        raise ValueError('Reservation has no items')
    return {'orderId': order_id, 'email': email, 'name': name,
            'items': items, 'totalCents': total, 'createdAtUtc': created.isoformat()}


def claim_id(channel, order_id):
    return 'reservation-' + channel + ':' + order_id


def build_message(settings, data, channel, office):
    message = EmailMessage()
    internal = channel == 'office'
    message['Subject'] = ('Neue T-Shirt-Vormerkung ' + data['orderId'] if internal
                          else 'Deine T-Shirt-Vormerkung beim SSV53')
    message['From'] = formataddr((settings.from_name, settings.from_address))
    message['To'] = office if internal else data['email']
    message['Reply-To'] = data['email'] if internal else settings.from_address
    details = _plain_order_details(data['items'], data['totalCents'])
    if internal:
        details = details.replace('Deine Reservierung:', 'Vorgemerkte Artikel:', 1)
        intro = ('Eine neue T-Shirt-Vormerkung wurde gespeichert.\n'
                 + 'Reservierung: ' + data['orderId'] + '\n'
                 + 'Name: ' + data['name'] + '\nE-Mail: ' + data['email'] + '\n')
        outro = '\nBitte in der Reservierungsübersicht bearbeiten:\n' + ADMIN_URL
    else:
        intro = ('Hallo ' + data['name'] + ',\n\n'
                 + 'deine unverbindliche T-Shirt-Vormerkung ' + data['orderId']
                 + ' wurde gespeichert.\n')
        outro = ('\nDas ist die Eingangsbestätigung deiner Vormerkung. '
                 'Sie ist noch keine Abholbenachrichtigung. Sobald dein Shirt zur Ausgabe '
                 'bereit ist, erhältst du eine weitere E-Mail.\n\n'
                 'Den aktuellen Stand findest du in der SSV53-App unter '
                 '„Sonstiges → Meine Reservierungen“.\n\nViele Grüße\nSchönwalder SV 1953 e.V.')
    message.set_content(intro + '\n' + details + '\n' + outro)
    total = data['totalCents']
    total_html = '' if total is None else '<p><strong>Gesamtsumme: ' + str(total // 100) + ',' + f'{total % 100:02d}' + ' €</strong></p>'
    body = ('<!doctype html><html lang="de"><meta charset="utf-8"><body style="margin:0;background:#f2f4f6;font-family:Arial,sans-serif;color:#263241">'
            '<main style="max-width:620px;margin:20px auto;background:white;padding:24px;border-radius:14px">'
            '<h1 style="font-size:22px;color:#285ea7">' + html.escape(str(message['Subject'])) + '</h1>'
            '<p style="white-space:pre-line;line-height:1.5">' + html.escape(intro) + '</p>'
            '<table style="width:100%;border-collapse:collapse">' + _product_rows_html(data['items']) + '</table>'
            + total_html + '<p style="white-space:pre-line;line-height:1.5">' + html.escape(outro) + '</p></main></body></html>')
    message.add_alternative(body, subtype='html')
    return message


def deliver(data, settings, office, store, *, smtp_open=_open_authenticated_smtp):
    result = {}
    for channel in CHANNELS:
        key = claim_id(channel, data['orderId'])
        recipient = office if channel == 'office' else data['email']
        claimed = store.claim(order_id=key, recipient=recipient)
        if claimed != 'claimed':
            result[channel] = 'sent' if claimed == 'sent' else 'sending'
            continue
        smtp = None
        submitted = False
        try:
            smtp = smtp_open(settings)
            message = build_message(settings, data, channel, office)
            submitted = True
            refused = smtp.send_message(message)
            if refused:
                raise smtplib.SMTPRecipientsRefused(refused)
            store.mark_sent(order_id=key, recipient=recipient)
            result[channel] = 'sent'
            LOGGER.info('SSV53_RESERVATION_MAIL_SENT order_id=%s channel=%s', data['orderId'], channel)
        except (smtplib.SMTPRecipientsRefused, smtplib.SMTPDataError):
            store.mark_failed(order_id=key, recipient=recipient, error_code='SMTP_REJECTED')
            result[channel] = 'failed'
        except Exception:
            # A disconnect after DATA can mean acceptance; do not blindly resend.
            if submitted:
                result[channel] = 'sending'
            else:
                store.mark_failed(order_id=key, recipient=recipient, error_code='SMTP_UNAVAILABLE')
                result[channel] = 'failed'
            LOGGER.warning('SSV53_RESERVATION_MAIL_FAILED order_id=%s channel=%s ambiguous=%s', data['orderId'], channel, submitted)
        finally:
            if smtp is not None:
                try:
                    smtp.quit()
                except Exception:
                    pass
    return result


def process(values, *, now=None, fetch=source_rows, store=None, smtp_open=_open_authenticated_smtp):
    config = configuration(values)
    if config is None:
        return {'enabled': False, 'processed': 0}
    cutoff, office = config
    now = now or datetime.now(timezone.utc)
    # A separate flag leaves the existing pickup-mail enablement untouched.
    settings = OrderMailSettings.from_mapping(dict(values, SSV53_ORDER_MAIL_ENABLED='true'))
    store = store or OrderMailStore.from_settings(settings)
    processed = 0
    # Bounded pages; old rows are never migrated or mailed.
    for offset in range(0, 2000, 100):
        rows = fetch(offset=offset)
        for row in rows:
            try:
                data = reservation(row, max(cutoff, now - timedelta(days=7)), now)
                if data is None:
                    continue
                deliver(data, settings, office, store, smtp_open=smtp_open)
                processed += 1
            except Exception:
                LOGGER.warning('SSV53_RESERVATION_RECORD_UNAVAILABLE')
        if len(rows) < 100:
            break
    return {'enabled': True, 'processed': processed}


def delivery_status(order_id, values, *, now=None, fetch=source_rows, store=None):
    if not ORDER_ID_PATTERN.fullmatch(order_id):
        raise ValueError('Invalid reservation id')
    config = configuration(values)
    if config is None:
        return {'orderId': order_id, 'enabled': False}
    cutoff, _ = config
    rows = fetch(order_id)
    if len(rows) != 1:
        raise ValueError('Reservation not found')
    data = reservation(rows[0], cutoff, now or datetime.now(timezone.utc))
    if data is None:
        return {'orderId': order_id, 'enabled': True, 'applicable': False}
    store = store or OrderMailStore.from_environment(values)
    channels = {}
    for channel in CHANNELS:
        try:
            entity = store._table_client.get_entity(partition_key=PARTITION_KEY, row_key=claim_id(channel, order_id))
            state = str(entity.get('status') or '')
            channels[channel] = {'state': state if state in {'sent','sending','failed'} else 'queued'}
            if state == 'sent':
                channels[channel]['sentAtUtc'] = entity.get('sentAtUtc')
        except ResourceNotFoundError:
            channels[channel] = {'state': 'expired' if utc(data['createdAtUtc']) < (now or datetime.now(timezone.utc)) - timedelta(days=7) else 'queued'}
    return {'orderId': order_id, 'enabled': True, 'applicable': True, 'channels': channels}


@notification_blueprint.timer_trigger(schedule='0 * * * * *', arg_name='timer', run_on_startup=False, use_monitor=True)
def reservation_notification_timer(timer: func.TimerRequest):
    try:
        result = process(os.environ)
        LOGGER.info('SSV53_RESERVATION_SCAN processed=%s enabled=%s', result['processed'], result['enabled'])
    except Exception:
        LOGGER.warning('SSV53_RESERVATION_SCAN_UNAVAILABLE')


@notification_blueprint.route(route='reservation-notifications', methods=['GET','OPTIONS'], auth_level=func.AuthLevel.ANONYMOUS)
def reservation_notification_status(req: func.HttpRequest) -> func.HttpResponse:
    headers = {'Access-Control-Allow-Origin':'*','Cache-Control':'no-store','Content-Type':'application/json; charset=utf-8'}
    if req.method == 'OPTIONS':
        return func.HttpResponse(status_code=204, headers=headers)
    try:
        result = delivery_status(str(req.params.get('orderId') or ''), os.environ)
        return func.HttpResponse(json.dumps(result), headers=headers)
    except ValueError:
        return func.HttpResponse(json.dumps({'ok':False,'code':'RESERVATION_NOT_FOUND'}), status_code=404, headers=headers)
    except Exception:
        return func.HttpResponse(json.dumps({'ok':False,'code':'NOTIFICATION_STATUS_UNAVAILABLE'}), status_code=503, headers=headers)
