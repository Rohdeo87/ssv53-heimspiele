from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PARK_BUILDER = ROOT / "scripts" / "build_azure_park_only_package.py"
READ_ONLY_BUILDER = ROOT / "scripts" / "build_azure_source_package.py"


class ParkOnlyPackageTests(unittest.TestCase):
    def test_read_only_builder_remains_read_only(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "readonly.zip"
            subprocess.run(
                [
                    sys.executable,
                    str(READ_ONLY_BUILDER),
                    "--repository-root",
                    str(ROOT),
                    "--output",
                    str(output),
                ],
                check=True,
                cwd=ROOT,
                capture_output=True,
                text=True,
            )
            with zipfile.ZipFile(output) as archive:
                names = set(archive.namelist())
                manifest = json.loads(
                    archive.read("package-manifest.json").decode("utf-8")
                )
                self.assertEqual(
                    manifest["safety_stage"],
                    "DRY_RUN_READ_ONLY",
                )
                self.assertNotIn("mower/husqvarna_actions.py", names)
                self.assertNotIn("mower/park_only.py", names)

    def test_park_only_builder_is_write_limited(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "park-only.zip"
            subprocess.run(
                [
                    sys.executable,
                    str(PARK_BUILDER),
                    "--repository-root",
                    str(ROOT),
                    "--output",
                    str(output),
                ],
                check=True,
                cwd=ROOT,
                capture_output=True,
                text=True,
            )
            with zipfile.ZipFile(output) as archive:
                names = set(archive.namelist())
                manifest = json.loads(
                    archive.read("package-manifest.json").decode("utf-8")
                )
                self.assertEqual(
                    manifest["safety_stage"],
                    "PARK_ONLY_CAPABLE_LOCKED",
                )
                self.assertFalse(manifest["automatic_start_implemented"])
                self.assertTrue(manifest["park_write_gate_required"])
                self.assertIn("mower/husqvarna_actions.py", names)
                self.assertIn("mower/park_only.py", names)

                joined = b"\n".join(
                    archive.read(name)
                    for name in names
                    if name.endswith(".py")
                )
                self.assertIn(b"ParkUntilFurtherNotice", joined)
                self.assertIn(b"/actions", joined)
                for forbidden in (
                    b'"type": "Start"',
                    b"ResumeSchedule",
                    b"StartInWorkArea",
                    b"def start_",
                ):
                    with self.subTest(forbidden=forbidden):
                        self.assertNotIn(forbidden, joined)


if __name__ == "__main__":
    unittest.main()
