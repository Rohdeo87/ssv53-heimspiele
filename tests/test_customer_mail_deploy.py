"""Check scoped template replacement and preservation of the original cutoff."""
import hashlib
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import Mock, patch
import zipfile

from scripts import deploy_customer_mail_templates as release


class CustomerMailDeployTests(TestCase):
    def fixture(self, folder):
        root = Path(folder) / 'source'
        root.mkdir()
        original, patched = Path(folder) / 'before.zip', Path(folder) / 'after.zip'
        old = b'def old_renderer():\n    return "old"\n'
        for name in release.RELEASE_FILES:
            path = root / name
            path.parent.mkdir(exist_ok=True, parents=True)
            path.write_bytes(b'def new_renderer():\n    return "new"\n' if name.endswith('.py') else b'\x89PNG\r\n\x1a\n')
        with zipfile.ZipFile(original, 'w') as archive:
            for name in release.BASELINE_SHA256:
                archive.writestr(name, old)
            archive.writestr('function_app.py', b'original main and registrations')
            archive.writestr('mower/config.json', b'original device configuration')
        baseline = {name: hashlib.sha256(old).hexdigest() for name in release.BASELINE_SHA256}
        return root, original, patched, baseline

    def test_upgrade_preserves_all_unrelated_live_files(self):
        with TemporaryDirectory() as folder:
            root, original, patched, baseline = self.fixture(folder)
            with patch.object(release, 'BASELINE_SHA256', baseline):
                main_hash = release.prepare_release(original, patched, root)
            self.assertEqual(main_hash, hashlib.sha256(b'original main and registrations').hexdigest())
            with zipfile.ZipFile(original) as before, zipfile.ZipFile(patched) as after:
                for name in ('function_app.py', 'mower/config.json'):
                    self.assertEqual(before.read(name), after.read(name))
                for name in release.RELEASE_FILES:
                    self.assertEqual(after.read(name), (root / name).read_bytes())
                    self.assertEqual(after.getinfo(name).external_attr >> 16 & 0o777, 0o644)
                # Rerunning the same release accepts the already-installed sources.
            with patch.object(release, 'BASELINE_SHA256', baseline):
                release.prepare_release(patched, Path(folder) / 'repeat.zip', root)

    def test_unknown_independent_source_change_aborts_before_build_or_deployment(self):
        with TemporaryDirectory() as folder:
            root, original, patched, baseline = self.fixture(folder)
            baseline['order_mail.py'] = 'unknown-independent-version'
            with patch.object(release, 'BASELINE_SHA256', baseline), self.assertRaises(RuntimeError):
                release.prepare_release(original, patched, root)
            self.assertFalse(patched.exists())

    def test_cutoff_snapshot_selects_only_nonsecret_feature_settings(self):
        original = dict.fromkeys(release.PRESERVED_SETTINGS, 'synthetic-value')
        original['SSV53_RESERVATION_NOTIFICATIONS_FROM_UTC'] = '2026-11-02T14:19:14+00:00'
        response = Mock()
        response.json.return_value = {'properties': dict(original, SMTP_PASSWORD='synthetic-secret')}
        with patch.object(release, 'arm', return_value=response) as arm:
            self.assertEqual(release.settings_snapshot(Mock(), 'https://management.azure.com/site'), original)
        self.assertEqual(arm.call_args.args[2:], ('POST', '/config/appsettings/list'))
        # No setting update is part of this release.
        self.assertEqual(arm.call_count, 1)

    def test_health_check_requires_running_new_renderer_and_readable_icons_without_sending(self):
        session = Mock()
        old_response, new_response = Mock(), Mock()
        old_response.status_code = new_response.status_code = 200
        old_response.json.return_value = {'ok': True, 'smtpAuthenticated': True}
        new_response.json.return_value = {'ok': True, 'smtpAuthenticated': True,
            'mailTemplateVersion': release.MAIL_TEMPLATE_VERSION, 'mailTemplateAssetsReady': True}
        session.post.side_effect = [old_response, new_response]
        with patch.object(release.time, 'sleep'):
            release.verify_smtp(session, 'synthetic-key', require_new_template=True)
        self.assertEqual(session.post.call_count, 2)
        for call in session.post.call_args_list:
            self.assertEqual(call.kwargs['json'], {'action': 'check'})
            self.assertFalse(call.kwargs['allow_redirects'])
