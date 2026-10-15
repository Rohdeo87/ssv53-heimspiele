from __future__ import annotations

import hashlib
import json
import stat
import tempfile
import unittest
import warnings
import zipfile
from pathlib import Path

from integrations.results.prepare_verified_release import (
    read_verified_base, validate_function_delta, write_release_zip,
)


BASE_FILES = {
    "function_app.py": b"app = func.FunctionApp()\n",
    "mower/build_provenance.py": b"# provenance\n",
    "host.json": b"{}",
    "requirements.txt": b"",
    "mower/controller.py": b"# controller\n",
}


class VerifiedResultsReleaseTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "base.zip"

    def make_zip(self, files=None, entries=None, extra=None) -> str:
        files = BASE_FILES if files is None else files
        entries = entries or [
            {"path": name, "size": len(data), "sha256": hashlib.sha256(data).hexdigest()}
            for name, data in files.items()
        ]
        with zipfile.ZipFile(self.path, "w") as archive:
            for name, data in files.items():
                info = zipfile.ZipInfo(name, (2026, 1, 1, 0, 0, 0))
                info.create_system = 0
                info.external_attr = (stat.S_IFREG | 0o644) << 16
                info.compress_type = zipfile.ZIP_DEFLATED
                archive.writestr(info, data)
            info = zipfile.ZipInfo("package-manifest.json", (2026, 1, 1, 0, 0, 0))
            info.create_system = 0
            info.external_attr = (stat.S_IFREG | 0o644) << 16
            info.compress_type = zipfile.ZIP_DEFLATED
            archive.writestr(info, json.dumps({"schema_version": 1, "files": entries}))
            if extra:
                for name, data in extra:
                    if isinstance(name, str):
                        info = zipfile.ZipInfo(name, (2026, 1, 1, 0, 0, 0))
                        info.create_system = 0
                        info.external_attr = (stat.S_IFREG | 0o644) << 16
                    else:
                        info = name
                    archive.writestr(info, data)
        return hashlib.sha256(self.path.read_bytes()).hexdigest()

    def test_complete_base_is_read_and_expected_digest_is_mandatory(self) -> None:
        expected = self.make_zip()
        files, manifest = read_verified_base(self.path, expected)
        self.assertEqual(files, BASE_FILES)
        self.assertEqual(len(manifest["files"]), len(BASE_FILES))
        with self.assertRaisesRegex(ValueError, "SHA-256 mismatch"):
            read_verified_base(self.path, "0" * 64)

    def test_hash_and_inventory_mismatch_are_rejected(self) -> None:
        entries = [{"path": name, "size": len(data), "sha256": "0" * 64}
                   for name, data in BASE_FILES.items()]
        expected = self.make_zip(entries=entries)
        with self.assertRaisesRegex(ValueError, "byte mismatch"):
            read_verified_base(self.path, expected)
        expected = self.make_zip(extra=[("undeclared.py", b"x")])
        with self.assertRaisesRegex(ValueError, "inventories differ"):
            read_verified_base(self.path, expected)

    def test_unsafe_paths_duplicates_and_links_are_rejected(self) -> None:
        for extra in ([('../escape.py', b"x")], [('host.json', b"other")]):
            with self.subTest(extra=extra):
                with warnings.catch_warnings():
                    warnings.simplefilter("ignore", UserWarning)
                    expected = self.make_zip(extra=extra)
                with self.assertRaises(ValueError):
                    read_verified_base(self.path, expected)
        link = zipfile.ZipInfo("link.py")
        link.create_system = 3
        link.external_attr = (stat.S_IFLNK | 0o777) << 16
        expected = self.make_zip()
        with zipfile.ZipFile(self.path, "a") as archive:
            archive.writestr(link, "target.py")
        expected = hashlib.sha256(self.path.read_bytes()).hexdigest()
        with self.assertRaisesRegex(ValueError, "Link"):
            read_verified_base(self.path, expected)

    def test_existing_results_are_not_overwritten(self) -> None:
        files = dict(BASE_FILES)
        files["integrations/results/results_blueprint.py"] = b"existing"
        expected = self.make_zip(files=files)
        with self.assertRaisesRegex(ValueError, "already contains results"):
            read_verified_base(self.path, expected)

    def test_zip_metadata_and_readable_modes_are_preserved(self) -> None:
        expected = self.make_zip()
        base, _ = read_verified_base(self.path, expected)
        patched = dict(base)
        patched["function_app.py"] = b"patched\n"
        result_name = "integrations/results/results_blueprint.py"
        patched[result_name] = b"# results\n"
        release = self.path.with_name("release.zip")
        write_release_zip(self.path, release, base, patched, b"{}")
        with zipfile.ZipFile(self.path) as original, zipfile.ZipFile(release) as built:
            for name in (*base, "package-manifest.json"):
                with self.subTest(name=name):
                    before, after = original.getinfo(name), built.getinfo(name)
                    self.assertEqual(after.external_attr, before.external_attr)
                    self.assertEqual(after.create_system, before.create_system)
                    self.assertEqual(after.compress_type, before.compress_type)
            self.assertEqual(built.read("host.json"), base["host.json"])
            self.assertEqual(built.getinfo(result_name).external_attr >> 16,
                             stat.S_IFREG | 0o644)

    def test_unreadable_base_file_is_rejected(self) -> None:
        self.make_zip()
        bad = self.path.with_name("bad.zip")
        with zipfile.ZipFile(self.path) as original, zipfile.ZipFile(bad, "w") as archive:
            for info in original.infolist():
                data = original.read(info.filename)
                copied = zipfile.ZipInfo(info.filename, info.date_time)
                copied.create_system = info.create_system
                copied.external_attr = info.external_attr
                if info.filename == "host.json":
                    copied.external_attr = (stat.S_IFREG | 0o600) << 16
                archive.writestr(copied, data)
        with self.assertRaisesRegex(ValueError, "unreadable"):
            read_verified_base(bad, hashlib.sha256(bad.read_bytes()).hexdigest())

    def test_only_expected_result_functions_are_accepted(self) -> None:
        before = [f"existing_{index}" for index in range(16)]
        validate_function_delta(before, before + ["ssv53_results_read", "ssv53_results_update"])
        with self.assertRaisesRegex(ValueError, "registration delta"):
            validate_function_delta(before, before + ["ssv53_results_read", "unexpected_update"])


if __name__ == "__main__":
    unittest.main()
