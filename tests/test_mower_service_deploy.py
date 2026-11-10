"""Scoped package publication must preserve independent production source."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
from unittest.mock import patch
import zipfile

import pytest

from scripts import deploy_mower_service as release


ENTRYPOINT = b'''# independently installed production registration
def ssv53_platzwart_action(req):
    body = req.get_json()
    return platzwart_request_action(
            manual_control=body.get("manualControl"),
    )

def independent_live_endpoint():
    return "preserve this exact code"
'''


@pytest.fixture
def package(tmp_path):
    root = tmp_path / 'source'
    original, patched = tmp_path / 'before.zip', tmp_path / 'after.zip'
    old = b'def original():\n    return "old"\n'
    baseline = deepcopy(release.BASELINE)
    baseline['files'] = {name: hashlib.sha256(old).hexdigest() if value else None
                         for name, value in baseline['files'].items()}
    with zipfile.ZipFile(original, 'w') as archive:
        for name, value in baseline['files'].items():
            target = root / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(b'def updated():\n    return "new"\n')
            if value:
                archive.writestr(name, old)
        archive.writestr('function_app.py', ENTRYPOINT)
        archive.writestr('order_mail.py', b'def existing_mail():\n    return "same"\n')
        archive.writestr('mower/config.json', b'{"untouched": true}')
        archive.writestr('.python_packages/lib/site-packages/kept.py', b'existing dependency bytes')
        archive.writestr('package-manifest.json', json.dumps({'schema_version': 1, 'files': [
            {'path': 'function_app.py', 'sha256': 'old-manifest-proof', 'size': 0}]}))
    return root, original, patched, baseline


def test_scoped_upgrade_preserves_unrelated_application_and_dependency_bytes(package):
    root, original, patched, baseline = package
    with patch.object(release, 'BASELINE', baseline):
        release.prepare_release(original, patched, root)
    with zipfile.ZipFile(original) as before, zipfile.ZipFile(patched) as after:
        for name in ('order_mail.py', 'mower/config.json', '.python_packages/lib/site-packages/kept.py'):
            assert before.read(name) == after.read(name)
        main = after.read('function_app.py')
        assert main.replace(b'            mower_service=body.get("mowerService"),\n', b'') == ENTRYPOINT
        for name in release.RELEASE_FILES:
            assert after.read(name) == (root / name).read_bytes()
            assert after.getinfo(name).external_attr >> 16 & 0o777 == 0o644
        manifest = json.loads(after.read('package-manifest.json'))
        entries = {entry['path']: entry for entry in manifest['files']}
        assert 'mower/service_mode.py' in entries
        assert 'order_mail.py' in entries
        for name, entry in entries.items():
            assert entry['sha256'] == hashlib.sha256(after.read(name)).hexdigest()
            assert entry['size'] == len(after.read(name))
    with patch.object(release, 'BASELINE', baseline):
        release.prepare_release(patched, patched.with_name('retry.zip'), root)


def test_unknown_active_control_change_aborts_before_writing_release(package):
    root, original, patched, baseline = package
    baseline['files']['mower/full_failsafe.py'] = 'independent-version'
    with patch.object(release, 'BASELINE', baseline), pytest.raises(RuntimeError, match='Independent active source change'):
        release.prepare_release(original, patched, root)
    assert not patched.exists()


def test_entrypoint_forwarding_is_idempotent_and_rejects_ambiguous_handlers():
    updated = release.patch_entrypoint(ENTRYPOINT)
    assert release.patch_entrypoint(updated) == updated
    with pytest.raises(RuntimeError):
        release.patch_entrypoint(ENTRYPOINT + ENTRYPOINT)
    with pytest.raises(RuntimeError):
        release.patch_entrypoint(updated.replace(b'body.get("mowerService")', b'body.get("otherPayload")'))


def test_private_flex_pointer_selects_active_successful_package_not_newest_candidate():
    state = {'ActiveDeploymentId': 'active', 'LatestDeploymentId': 'failed-newer',
             'Deployments': {'active': {'Id': 'active', 'Status': 4, 'Complete': True, 'UploadedPackageName': 'active.zip'},
                             'failed-newer': {'Id': 'failed-newer', 'Status': 3, 'Complete': True}}}
    assert release.active_from_state(state, {'active.zip', 'failed-newer.zip'}) == 'active.zip'
    state['Deployments']['failed-newer']['Complete'] = False
    with pytest.raises(RuntimeError, match='in progress'):
        release.active_from_state(state, {'active.zip', 'failed-newer.zip'})
    state['LatestDeploymentId'] = 'active'
    state['Deployments']['active']['Status'] = 3
    with pytest.raises(RuntimeError, match='not a completed successful'):
        release.active_from_state(state, {'active.zip'})
