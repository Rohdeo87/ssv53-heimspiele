from __future__ import annotations

import json
import tempfile
import unittest
import zipfile
from pathlib import Path

from mower.build_provenance import inspect_installed_package
from scripts import (
    build_azure_full_failsafe_package,
    build_azure_full_mower_package,
    build_azure_park_only_package,
    build_azure_source_package,
)


ROOT = Path(__file__).resolve().parents[1]
RESULT_FILES = (
    "integrations/results/results_blueprint.py",
    "integrations/results/ssv_results/__init__.py",
    "integrations/results/ssv_results/__main__.py",
    "integrations/results/ssv_results/collector.py",
    "integrations/results/ssv_results/parsers.py",
)
BUILDERS = (
    build_azure_source_package,
    build_azure_park_only_package,
    build_azure_full_mower_package,
    build_azure_full_failsafe_package,
)


class ResultsPackageIntegrationTests(unittest.TestCase):
    def test_all_complete_packages_include_exact_results_sources(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            for builder in BUILDERS:
                with self.subTest(builder=builder.__name__):
                    output = Path(directory) / f"{builder.__name__}.zip"
                    builder.build_package(ROOT, output)
                    with zipfile.ZipFile(output) as archive:
                        names = set(archive.namelist())
                        manifest = json.loads(archive.read("package-manifest.json"))
                        declared = {entry["path"] for entry in manifest["files"]}
                        self.assertTrue(set(RESULT_FILES).issubset(names & declared))
                        self.assertIn("app.register_functions(ssv_results_blueprint)",
                                      archive.read("function_app.py").decode("utf-8"))
                        for name in RESULT_FILES:
                            self.assertEqual(archive.read(name),
                                             (ROOT / name).read_bytes())
                        extracted = Path(directory) / builder.__name__
                        archive.extractall(extracted)
                    evidence = inspect_installed_package(extracted)
                    self.assertTrue(evidence["manifest_files_verified"], evidence)

    def test_registered_results_cannot_be_omitted_from_manifest(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            files = {
                "function_app.py": b"app.register_functions(ssv_results_blueprint)\n",
                "host.json": b"{}",
                "requirements.txt": b"",
                "mower/controller.py": b"",
            }
            import hashlib
            entries = []
            for name, data in files.items():
                path = root / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(data)
                entries.append({"path": name, "size": len(data),
                                "sha256": hashlib.sha256(data).hexdigest()})
            (root / "package-manifest.json").write_text(
                json.dumps({"schema_version": 1, "files": entries}), encoding="utf-8"
            )
            self.assertEqual(inspect_installed_package(root)["verification_error"],
                             "INCOMPLETE_MANIFEST")


if __name__ == "__main__":
    unittest.main()
