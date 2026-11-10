"""Inspect or publish the mower-only service switch in the active Flex package.

Preserve active application settings, runtime data, dependencies and unrelated
source. Never invoke a device action or activate the service switch here.
"""
from __future__ import annotations

import argparse
import ast
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path, PurePosixPath
import subprocess
import sys
import tempfile
from urllib.parse import urlparse
import zipfile

import requests

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts.deploy_reservation_notifications import (
    APP, RG, SCM, active_package_name, arm, az, deploy_flex, existing_mail_key, patch_package,
)
from scripts.deploy_customer_mail_templates import verify_smtp

BASELINE = json.loads((ROOT / 'scripts/mower_service_release_baseline.json').read_text())
RELEASE_FILES = tuple(BASELINE['files'])
SUBSCRIPTION = 'e0ebcbdf-04e4-4096-8355-bae1164563da'


def digest(data):
    return hashlib.sha256(data).hexdigest()


def patch_entrypoint(source):
    """Forward only the new payload, retaining independent active registrations."""
    text = source.decode('utf-8')
    tree = ast.parse(text)
    actions = [node for node in tree.body if isinstance(node, ast.FunctionDef)
               and node.name == 'ssv53_platzwart_action']
    if len(actions) != 1:
        raise RuntimeError('Active action endpoint cannot be identified uniquely')
    calls = [node for node in ast.walk(actions[0]) if isinstance(node, ast.Call)
             and isinstance(node.func, ast.Name) and node.func.id == 'platzwart_request_action']
    if len(calls) != 1:
        raise RuntimeError('Active action dispatcher changed independently')
    existing = [item for item in calls[0].keywords if item.arg == 'mower_service']
    if existing:
        expected = ast.parse('body.get("mowerService")', mode='eval').body
        if len(existing) != 1 or ast.dump(existing[0].value) != ast.dump(expected):
            raise RuntimeError('Active service forwarding changed independently')
        return source
    needle = '            manual_control=body.get("manualControl"),'
    if text.count(needle) != 1:
        raise RuntimeError('Active manual-control forwarding anchor changed')
    call_source = ast.get_source_segment(text, calls[0])
    if 'manual_control=body.get("manualControl"),' not in call_source:
        raise RuntimeError('Service forwarding anchor is outside the action dispatcher')
    newline = '\r\n' if '\r\n' in text else '\n'
    updated = text.replace(needle, needle + newline + '            mower_service=body.get("mowerService"),', 1)
    ast.parse(updated)
    return updated.encode('utf-8')


def updated_manifest(archive, replacements):
    manifest = json.loads(archive.read('package-manifest.json'))
    if manifest.get('schema_version') != 1 or not isinstance(manifest.get('files'), list):
        raise RuntimeError('Active package manifest has an unknown schema')
    entries = {}
    for entry in manifest['files']:
        name = entry.get('path')
        if (not isinstance(name, str) or name in entries or PurePosixPath(name).is_absolute()
                or '..' in PurePosixPath(name).parts or '\\' in name):
            raise RuntimeError('Active package manifest contains invalid paths')
        entries[name] = dict(entry)
    # Earlier additive releases may precede manifest bookkeeping. Describe
    # actual preserved source bytes, never claim a device action or activation.
    inventory = {name for name in archive.namelist() if name.endswith('.py')
                 and ('/' not in name or name.startswith(('mower/', 'occupancy/')))
                 and '__pycache__' not in name}
    inventory.update(replacements)
    for name in sorted(set(entries) | inventory):
        try:
            content = replacements[name] if name in replacements else archive.read(name)
        except KeyError:
            raise RuntimeError('Declared production file is unavailable: ' + name) from None
        entries[name] = {**entries.get(name, {}), 'path': name,
                         'sha256': digest(content), 'size': len(content)}
    manifest['files'] = [entries[name] for name in sorted(entries)]
    if len(manifest['files']) > 250:
        raise RuntimeError('Release exceeds the installed manifest verification limit')
    return (json.dumps(manifest, indent=2, ensure_ascii=False) + '\n').encode()


def prepare_release(original, patched, root=ROOT):
    replacements = {name: (root / name).read_bytes() for name in RELEASE_FILES}
    checks = []
    with zipfile.ZipFile(original) as archive:
        for name, expected in BASELINE['files'].items():
            current = archive.read(name) if name in archive.namelist() else None
            target = replacements[name]
            known = current == target or (expected is None and current is None) or (
                current is not None and digest(current) == expected)
            checks.append({'path': name, 'active_sha256': digest(current) if current is not None else None,
                           'target_sha256': digest(target), 'baseline_matches': known})
            if not known:
                raise RuntimeError('Independent active source change requires reconciliation: ' + name)
            ast.parse(target, filename=name)
        replacements['function_app.py'] = patch_entrypoint(archive.read('function_app.py'))
        replacements['package-manifest.json'] = updated_manifest(archive, replacements)
    patch_package(original, patched, replacements)
    return checks, replacements


def inspect_sources(original, root=ROOT):
    with zipfile.ZipFile(original) as archive:
        return [{'path': name, 'active_sha256': digest(archive.read(name)) if name in archive.namelist() else None,
                 'baseline_sha256': expected, 'target_sha256': digest((root / name).read_bytes()),
                 'baseline_matches': (name not in archive.namelist() and expected is None)
                    or (name in archive.namelist() and digest(archive.read(name)) in {expected, digest((root / name).read_bytes())})}
                for name, expected in BASELINE['files'].items()]


def verify_import(package, folder):
    with zipfile.ZipFile(package) as archive:
        for name in archive.namelist():
            path = PurePosixPath(name)
            if path.is_absolute() or '..' in path.parts or '\\' in name:
                raise RuntimeError('Unsafe active package member')
        archive.extractall(folder)
    code = ('import sys,json;sys.path.insert(0,".python_packages/lib/site-packages");'
            'import function_app;print(json.dumps(sorted(f.get_function_name() for f in function_app.app.get_functions())))')
    result = subprocess.run([sys.executable, '-c', code], cwd=folder,
                            capture_output=True, text=True, timeout=90)
    if result.returncode:
        raise RuntimeError('Patched package could not register its functions; provider output withheld')
    try:
        return json.loads(result.stdout.strip().splitlines()[-1])
    except (ValueError, IndexError):
        raise RuntimeError('Function registration proof is unavailable') from None


def all_settings(session, site_url):
    values = arm(session, site_url, 'POST', '/config/appsettings/list').json().get('properties')
    if not isinstance(values, dict):
        raise RuntimeError('Application settings unavailable')
    return values


def health_without_action(session):
    host = 'https://' + APP + '.azurewebsites.net/api'
    response = session.get(host + '/occupancy', timeout=45, allow_redirects=False)
    if response.status_code != 200:
        raise RuntimeError('Public calendar health check failed HTTP ' + str(response.status_code))
    response = session.get(host + '/platzwart/status', timeout=45, allow_redirects=False)
    if response.status_code not in (401, 403):
        raise RuntimeError('Platzwart authentication boundary verification failed')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--deploy', action='store_true')
    parser.add_argument('--proof', type=Path, required=True)
    args = parser.parse_args()
    if az('account', 'show')['id'] != SUBSCRIPTION:
        raise RuntimeError('Unexpected subscription')
    token = az('account', 'get-access-token', '--resource', 'https://management.azure.com')['accessToken']
    session = requests.Session()
    session.headers.update(Authorization='Bearer ' + token)
    site_url = ('https://management.azure.com/subscriptions/' + SUBSCRIPTION +
                '/resourceGroups/' + RG + '/providers/Microsoft.Web/sites/' + APP)
    site = arm(session, site_url, 'GET', '').json()['properties']
    storage = site.get('functionAppConfig', {}).get('deployment', {}).get('storage', {})
    parsed = urlparse(storage.get('value', ''))
    if parsed.hostname != 'ssv53platzpflegeprodq7kb.blob.core.windows.net' or parsed.path.strip('/') != 'app-package-q7kbw54s':
        raise RuntimeError('Unexpected deployment storage')
    account, container = parsed.hostname.split('.')[0], parsed.path.strip('/')
    blob_args = ('--account-name', account, '--container-name', container, '--auth-mode', 'login')
    blobs = az('storage', 'blob', 'list', *blob_args)
    active = active_package_name(session, blobs)
    with tempfile.TemporaryDirectory(prefix='ssv53-mower-service-') as folder:
        folder = Path(folder)
        original, patched = folder / 'active.zip', folder / 'release.zip'
        az('storage', 'blob', 'download', *blob_args, '--name', active, '--file', str(original), '--overwrite', timeout=300)
        settings = all_settings(session, site_url)
        proof = {'deployed': False, 'inspected_at_utc': datetime.now(timezone.utc).isoformat(),
                 'active_package': active, 'sources': inspect_sources(original),
                 'control_mode': settings.get('CONTROL_MODE'),
                 'manual_sessions_enabled': settings.get('ENABLE_MANUAL_SESSIONS'),
                 'settings_written': False, 'device_actions_invoked': False, 'service_activated': False}
        args.proof.write_text(json.dumps(proof, indent=2) + '\n')
        print(json.dumps(proof), flush=True)
        if not args.deploy:
            return
        if settings.get('CONTROL_MODE') != 'FULL_FAILSAFE':
            raise RuntimeError('Active control mode requires a separately verified service integration')
        prepare_release(original, patched)
        before_functions = verify_import(original, folder / 'before')
        after_functions = verify_import(patched, folder / 'after')
        if before_functions != after_functions:
            raise RuntimeError('The release changes unrelated function registrations')
        key = existing_mail_key(session, site_url)
        health = requests.Session()
        verify_smtp(health, key, require_new_template=True)
        health_without_action(health)
        if active_package_name(session, blobs) != active or all_settings(session, site_url) != settings:
            raise RuntimeError('Production deployment or configuration changed during preparation')
        deploy_flex(session, patched)
        # Re-read the exact successful package; never use newest-blob guessing.
        blobs = az('storage', 'blob', 'list', *blob_args)
        published = active_package_name(session, blobs)
        readback = folder / 'readback.zip'
        az('storage', 'blob', 'download', *blob_args, '--name', published, '--file', str(readback), '--overwrite', timeout=300)
        with zipfile.ZipFile(patched) as expected, zipfile.ZipFile(readback) as actual:
            if set(expected.namelist()) != set(actual.namelist()) or any(
                    expected.read(name) != actual.read(name) for name in expected.namelist()):
                raise RuntimeError('Published package readback differs from the verified release')
        verify_smtp(health, key, require_new_template=True)
        health_without_action(health)
        if all_settings(session, site_url) != settings:
            raise RuntimeError('Application configuration changed concurrently')
        proof.update(deployed=True, published_package=published, published_at_utc=datetime.now(timezone.utc).isoformat(),
                     changed_files=[*RELEASE_FILES, 'function_app.py', 'package-manifest.json'],
                     functions_preserved=after_functions, unrelated_package_bytes_preserved=True,
                     active_package_readback_verified=True, all_settings_preserved=True,
                     smtp_authenticated_without_mail=True, calendar_and_authentication_verified=True)
        args.proof.write_text(json.dumps(proof, indent=2) + '\n')
        print(json.dumps(proof), flush=True)


if __name__ == '__main__':
    try:
        main()
    except Exception as error:
        if isinstance(error, RuntimeError):
            raise SystemExit(str(error)) from None
        raise SystemExit('Mower service release failed: ' + type(error).__name__) from None
