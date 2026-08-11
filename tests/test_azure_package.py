from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
BUILDER = ROOT / "scripts" / "build_azure_source_package.py"
WORKFLOW = ROOT / ".github" / "workflows" / "azure-package-build.yml"


class AzurePackageTests(unittest.TestCase):
    def test_source_package_has_safe_root_layout(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "source.zip"
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
                self.assertIn("host.json", names)
                self.assertIn("function_app.py", names)
                self.assertIn("requirements.txt", names)
                self.assertIn("package-manifest.json", names)
                self.assertIn("mower/controller.py", names)
                self.assertIn("public/rasen.ics", names)
                self.assertIn("public/kunstrasen.ics", names)
                self.assertIn("public/matches.json", names)

                self.assertFalse(
                    any(
                        name.startswith(
                            (".git/", ".github/", "tests/", "infra/", "generated/")
                        )
                        for name in names
                    )
                )

                manifest = json.loads(
                    archive.read("package-manifest.json").decode("utf-8")
                )
                self.assertEqual(
                    manifest["safety_stage"],
                    "DRY_RUN_READ_ONLY",
                )
                self.assertTrue(manifest["remote_build_required"])

                joined = b"\n".join(
                    archive.read(name)
                    for name in names
                    if name.endswith((".py", ".json", ".txt", ".ics"))
                )
                for marker in (
                    b"/actions",
                    b"ParkUntilFurtherNotice",
                    b"StartInWorkArea",
                    b"send_mower_action",
                ):
                    with self.subTest(marker=marker):
                        self.assertNotIn(marker, joined)

    def test_build_workflow_is_artifact_only(self) -> None:
        content = WORKFLOW.read_text(encoding="utf-8")
        for required in (
            "actions/checkout@v6",
            "actions/setup-python@v6",
            "actions/upload-artifact@v7",
            "scripts/build_azure_source_package.py",
            "DRY_RUN_READ_ONLY",
        ):
            with self.subTest(required=required):
                self.assertIn(required, content)

        for forbidden in (
            "azure/login",
            "az deployment group create",
            "func azure functionapp publish",
            "azure/functions-action",
            "client-secret:",
        ):
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(forbidden, content)


if __name__ == "__main__":
    unittest.main()
