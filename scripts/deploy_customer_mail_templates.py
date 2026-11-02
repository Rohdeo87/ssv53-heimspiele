"""Upgrade only customer-mail rendering in the active Flex package.

The original notification cutoff, switches, delivery claims and every unrelated
source file stay intact. No test reservation or real email is created.
"""
import ast
import hashlib
import json
from pathlib import Path
import sys
import time
from urllib.parse import urlparse
import zipfile

import requests

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts.deploy_reservation_notifications import (
    APP, RG, SCM, az, arm, active_package_name, patch_package, deploy_flex, existing_mail_key,
)
from order_mail import MAIL_TEMPLATE_VERSION

BASELINE_SHA256 = {
    'order_mail.py': '2cbcea6f772098af828c5267aefb1a62e650f9cf1dba68d7b3d0a242a7f5d0a7',
    'reservation_notifications.py': '8bfc9fcda97de69b735f28c49315b2f9c9c10ebab72a29c3b7bbb39ece507848',
}
RELEASE_FILES = tuple(BASELINE_SHA256) + tuple(
    f'mail_assets/contact-{icon}.png' for icon in ('mail', 'whatsapp', 'phone'))
PRESERVED_SETTINGS = ('SSV53_RESERVATION_NOTIFICATIONS_ENABLED',
    'SSV53_RESERVATION_NOTIFICATIONS_FROM_UTC', 'SSV53_RESERVATION_OFFICE_EMAIL',
    'SSV53_ORDER_MAIL_ENABLED')


def source_hash(data):
    return hashlib.sha256(data).hexdigest()


def prepare_release(original, patched, root=ROOT):
    replacements = {name: (root / name).read_bytes() for name in RELEASE_FILES}
    with zipfile.ZipFile(original) as archive:
        for name, expected in BASELINE_SHA256.items():
            current = archive.read(name)
            if source_hash(current) not in {expected, source_hash(replacements[name])}:
                raise RuntimeError('Active mail source changed independently: ' + name)
            ast.parse(replacements[name])
        for name in RELEASE_FILES[2:]:
            if name in archive.namelist() and archive.read(name) != replacements[name]:
                raise RuntimeError('Active icon changed independently: ' + name)
        main_hash = source_hash(archive.read('function_app.py'))
    patch_package(original, patched, replacements)
    return main_hash


def settings_snapshot(session, site_url):
    values = arm(session, site_url, 'POST', '/config/appsettings/list').json().get('properties', {})
    if not values.get('SSV53_RESERVATION_NOTIFICATIONS_FROM_UTC'):
        raise RuntimeError('Original activation cutoff unavailable; no settings will be changed')
    return {key: values.get(key) for key in PRESERVED_SETTINGS}


def verify_smtp(session, key, *, require_new_template):
    for _ in range(24):
        try:
            response = session.post('https://' + APP + '.azurewebsites.net/api/order-mail',
                headers={'x-functions-key': key}, json={'action': 'check'},
                timeout=30, allow_redirects=False)
            if response.status_code == 200:
                result = response.json()
                healthy = result.get('ok') is True and result.get('smtpAuthenticated') is True
                if require_new_template:
                    healthy = healthy and result.get('mailTemplateVersion') == MAIL_TEMPLATE_VERSION \
                        and result.get('mailTemplateAssetsReady') is True
                if healthy:
                    return
        except (requests.RequestException, ValueError):
            pass
        time.sleep(5)
    raise RuntimeError('SMTP/template readiness check failed without sending mail')


def main():
    token = az('account', 'get-access-token', '--resource', 'https://management.azure.com')['accessToken']
    subscription = az('account', 'show')['id']
    site_url = ('https://management.azure.com/subscriptions/' + subscription +
                '/resourceGroups/' + RG + '/providers/Microsoft.Web/sites/' + APP)
    session = requests.Session()
    session.headers.update(Authorization='Bearer ' + token)
    site = arm(session, site_url, 'GET', '').json()['properties']
    storage = site.get('functionAppConfig', {}).get('deployment', {}).get('storage', {})
    parsed = urlparse(storage.get('value', ''))
    if parsed.hostname != 'ssv53platzpflegeprodq7kb.blob.core.windows.net':
        raise RuntimeError('Unexpected deployment storage')
    account = parsed.hostname.split('.')[0]
    container = parsed.path.strip('/')
    blobs = az('storage', 'blob', 'list', '--account-name', account,
               '--container-name', container, '--auth-mode', 'login')
    active_name = active_package_name(session, blobs)
    original = Path('/tmp/ssv53-mail-layout-before.zip')
    patched = Path('/tmp/ssv53-mail-layout-after.zip')
    az('storage', 'blob', 'download', '--account-name', account, '--container-name', container,
       '--name', active_name, '--auth-mode', 'login', '--file', str(original), '--overwrite', timeout=300)
    original_main_hash = prepare_release(original, patched)
    original_settings = settings_snapshot(session, site_url)
    key = existing_mail_key(session, site_url)
    # Use an unauthenticated HTTP session for the function call: it receives only
    # its existing function key, never the management bearer token.
    health = requests.Session()
    verify_smtp(health, key, require_new_template=False)
    if active_package_name(session, blobs) != active_name:
        raise RuntimeError('Active deployment changed during preparation')
    published = False
    published_id = None
    try:
        deploy_flex(session, patched)
        published = True
        publication = session.get(SCM + '/api/deployments/latest', timeout=30, allow_redirects=False)
        if publication.status_code != 200:
            raise RuntimeError('Published deployment identity unavailable')
        published_id = publication.json().get('id')
        if not published_id:
            raise RuntimeError('Published deployment identity unavailable')
        verify_smtp(health, key, require_new_template=True)
        if settings_snapshot(session, site_url) != original_settings:
            raise RuntimeError('Notification configuration changed concurrently')
    except Exception as error:
        # HTTP 409 means our publication was rejected in favour of another one.
        # Do not overwrite it. Once published, restore only if still our package.
        if 'HTTP 409' not in str(error):
            if published:
                current = session.get(SCM + '/api/deployments/latest', timeout=30, allow_redirects=False)
                if current.status_code != 200 or not published_id or current.json().get('id') != published_id:
                    raise RuntimeError('Concurrent deployment detected; automatic rollback skipped') from None
            deploy_flex(session, original)
            verify_smtp(health, key, require_new_template=False)
        raise
    print(json.dumps({'deployed': published, 'template_version': MAIL_TEMPLATE_VERSION,
        'smtp_check_without_mail': True, 'template_icons_readable_in_runtime': True,
        'activation_cutoff_preserved': True, 'mail_switches_preserved': True,
        'delivery_claims_untouched': True, 'function_app_sha256_preserved': original_main_hash,
        'changed_files': list(RELEASE_FILES)}), flush=True)


if __name__ == '__main__':
    try:
        main()
    except Exception as error:
        # All explicit errors above are safe; do not expose SDK/HTTP exception text.
        if isinstance(error, RuntimeError):
            raise SystemExit(str(error))
        raise SystemExit('Customer mail template deployment failed: ' + type(error).__name__)
