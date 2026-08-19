from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
BUILDER = ROOT / "scripts" / "build_azure_full_mower_package.py"


class FullMowerPackageTests(unittest.TestCase):
    def test_package_is_start_capable_but_independently_locked(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "full-mower.zip"
            subprocess.run(
                [
                    sys.executable,
                    str(BUILDER),
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
                    "FULL_MOWER_CAPABLE_LOCKED",
                )
                self.assertTrue(manifest["automatic_start_implemented"])
                self.assertEqual(
                    manifest["automatic_restart_sources"],
                    ["match", "training"],
                )
                self.assertTrue(manifest["park_write_gate_required"])
                self.assertTrue(manifest["start_write_gate_required"])
                self.assertTrue(manifest["exact_confirmation_required"])
                self.assertFalse(
                    manifest["hydrawise_write_functions_present"]
                )
                self.assertIn("mower/full_mower.py", names)
                self.assertNotIn("mower/full_failsafe.py", names)
                self.assertIn("mower/husqvarna_start_actions.py", names)

                joined = b"\n".join(
                    archive.read(name)
                    for name in names
                    if name.endswith(".py")
                )
                self.assertIn(b"StartInWorkArea", joined)
                self.assertIn(b"ParkUntilFurtherNotice", joined)
                self.assertIn(
                    b"SSV53-TRAINING-MATCH-PARK-START",
                    joined,
                )
                for forbidden in (
                    b"manualrun.php",
                    b"stopzone.php",
                    b"ResumeSchedule",
                ):
                    with self.subTest(forbidden=forbidden):
                        self.assertNotIn(forbidden, joined)


if __name__ == "__main__":
    unittest.main()
