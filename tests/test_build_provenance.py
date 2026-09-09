import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from mower.build_provenance import inspect_installed_package


class InstalledPackageEvidenceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        names = ('function_app.py', 'host.json', 'requirements.txt', 'mower/controller.py')
        self.entries = []
        for name in names:
            path = self.root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b'test source\n')
            self.entries.append({'path': name, 'size': 12, 'sha256': hashlib.sha256(path.read_bytes()).hexdigest()})
        self.write_manifest()

    def write_manifest(self):
        (self.root / 'package-manifest.json').write_text(json.dumps({'schema_version': 1, 'files': self.entries}))

    def test_matches_package_bytes_without_claiming_dependencies_or_device_rollout(self):
        result = inspect_installed_package(self.root)
        self.assertTrue(result['manifest_files_verified'])
        self.assertEqual(result['verified_file_count'], 4)
        self.assertIsNone(result['verification_error'])
        self.assertFalse(result['all_installed_files_verified'])
        self.assertFalse(result['remote_build_dependencies_verified'])

    def test_replaced_controller_is_detected(self):
        (self.root / 'mower/controller.py').write_bytes(b'other bytes\n')
        result = inspect_installed_package(self.root)
        self.assertFalse(result['manifest_files_verified'])
        self.assertEqual(result['verification_error'], 'INSTALLED_FILE_MISMATCH')

    def test_old_extra_sender_is_not_hidden_by_matching_subset(self):
        (self.root / 'mower/old_sender.py').write_text('# old application code')
        self.assertEqual(inspect_installed_package(self.root)['verification_error'], 'UNDECLARED_APPLICATION_SOURCE')

    def test_missing_manifest_is_unknown_and_does_not_crash_timer(self):
        (self.root / 'package-manifest.json').unlink()
        self.assertEqual(inspect_installed_package(self.root)['verification_error'], 'PACKAGE_FILE_UNAVAILABLE')

    def test_fixed_entrypoint_and_manifest_links_are_rejected_before_any_content_read(self):
        for linked_name in ('function_app.py', 'package-manifest.json'):
            with self.subTest(linked_name=linked_name):
                with patch.object(Path, 'is_symlink', lambda path: path.name == linked_name), \
                        patch.object(Path, 'read_bytes', autospec=True) as content_read:
                    result = inspect_installed_package(self.root)
                content_read.assert_not_called()
                self.assertEqual(result['verification_error'], 'INVALID_MANIFEST_PATH')
                self.assertIsNone(result['entrypoint_sha256'])
                self.assertIsNone(result['package_manifest_sha256'])

    def test_invalid_or_unsafe_manifest_never_reads_outside_package(self):
        original = list(self.entries)
        for name in ('../secret.txt', '/secret.txt', 'C:/secret.txt', 'mower\\controller.py', 'mower//controller.py'):
            with self.subTest(name=name):
                self.entries = original + [{'path': name, 'size': 0, 'sha256': '0' * 64}]
                self.write_manifest()
                self.assertEqual(inspect_installed_package(self.root)['verification_error'], 'INVALID_MANIFEST_PATH')

    def test_duplicates_and_incomplete_subset_are_rejected(self):
        self.entries.append(self.entries[-1])
        self.write_manifest()
        self.assertEqual(inspect_installed_package(self.root)['verification_error'], 'INVALID_MANIFEST_PATH')
        self.entries = self.entries[:1]
        self.write_manifest()
        self.assertEqual(inspect_installed_package(self.root)['verification_error'], 'INCOMPLETE_MANIFEST')

    def test_malformed_content_is_not_echoed_in_evidence(self):
        (self.root / 'package-manifest.json').write_text('not json SECRET_TEST_VALUE')
        result = inspect_installed_package(self.root)
        self.assertEqual(result['verification_error'], 'INVALID_MANIFEST')
        self.assertNotIn('SECRET_TEST_VALUE', json.dumps(result))


if __name__ == '__main__':
    unittest.main()
