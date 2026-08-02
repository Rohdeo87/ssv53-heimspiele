from __future__ import annotations

import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class AzureHardeningTests(unittest.TestCase):
    def test_what_if_is_branch_limited_and_uses_python_312(self) -> None:
        content = (
            ROOT
            / ".github"
            / "workflows"
            / "azure-infra-what-if.yml"
        ).read_text(encoding="utf-8")

        self.assertIn("actions/setup-python@v6", content)
        self.assertIn('python-version: "3.12"', content)
        self.assertIn(
            'GITHUB_REF_NAME" != "feature/azure-mower-migration',
            content,
        )
        self.assertNotIn("az deployment group create", content)

    def test_bicep_has_no_redundant_blob_contributor(self) -> None:
        content = (
            ROOT / "infra" / "main.bicep"
        ).read_text(encoding="utf-8")

        self.assertNotIn(
            "storageBlobDataContributorRoleId",
            content,
        )
        self.assertNotIn(
            "roleAssignmentBlobContributor",
            content,
        )

    def test_key_vault_references_have_no_trailing_slash(self) -> None:
        content = (
            ROOT / "infra" / "main.bicep"
        ).read_text(encoding="utf-8")

        for secret_name in (
            "husqvarna-client-id",
            "husqvarna-client-secret",
            "hydrawise-api-key",
            "hydrawise-controller-id",
        ):
            with self.subTest(secret_name=secret_name):
                self.assertIn(
                    f"secrets/{secret_name})",
                    content,
                )
                self.assertNotIn(
                    f"secrets/{secret_name}/)",
                    content,
                )

    def test_pre_deployment_decisions_are_documented(self) -> None:
        content = (
            ROOT / "infra" / "PRE_DEPLOYMENT_DECISIONS.md"
        ).read_text(encoding="utf-8")

        self.assertIn("E1", content)
        self.assertIn("E2", content)
        self.assertIn("Park-Lookahead", content)
        self.assertIn("versionierter Blob-Abruf", content)

    def test_legacy_azure_readme_is_removed(self) -> None:
        self.assertFalse(
            (ROOT / "azure_function" / "README.md").exists()
        )


if __name__ == "__main__":
    unittest.main()
