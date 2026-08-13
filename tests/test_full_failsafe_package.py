from __future__ import annotations

import json
import tempfile
import unittest
import zipfile
from pathlib import Path

from scripts.build_azure_full_failsafe_package import build_package


ROOT = Path(__file__).resolve().parents[1]


class FullFailsafePackageTests(unittest.TestCase):
    def test_package_contains_only_scoped_write_modules_and_locked_manifest(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "full-failsafe.zip"
            result = build_package(ROOT, output)
            with zipfile.ZipFile(output) as archive:
                names = set(archive.namelist())
                manifest = json.loads(archive.read("package-manifest.json"))
                joined = b"\n".join(archive.read(name) for name in names if name.endswith(".py"))
            self.assertEqual(
                manifest["safety_stage"],
                "FULL_FAILSAFE_7_ZONES_90_MIN_CAPABLE_LOCKED",
            )
            self.assertEqual(manifest["expected_hydrawise_zone_count"], 7)
            self.assertEqual(
                manifest["expected_hydrawise_relay_ids"],
                [9104894, 9104906, 9104909, 9104911, 9104913, 9104920, 9104921],
            )
            self.assertEqual(manifest["hydrawise_continuous_clear_minutes"], 90)
            self.assertIn("mower/full_failsafe.py", names)
            self.assertIn("mower/hydrawise_actions.py", names)
            self.assertIn(b"setzone.php", joined)
            self.assertNotIn(b"ResumeSchedule", joined)
            self.assertTrue(result["sha256"])


if __name__ == "__main__":
    unittest.main()
