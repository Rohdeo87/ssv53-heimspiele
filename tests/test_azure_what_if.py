from __future__ import annotations

import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
WHAT_IF = ROOT / ".github" / "workflows" / "azure-infra-what-if.yml"
VALIDATE = ROOT / ".github" / "workflows" / "azure-infra-validate.yml"


class AzureWhatIfWorkflowTests(unittest.TestCase):
    def test_what_if_workflow_is_oidc_only_and_non_deploying(self) -> None:
        content = WHAT_IF.read_text(encoding="utf-8")

        required = (
            "actions/checkout@v6",
            "azure/login@v3",
            "id-token: write",
            "az deployment group validate",
            "az deployment group what-if",
            "--validation-level ProviderNoRbac",
            'controlMode="DRY_RUN"',
            "enableLiveReads=false",
            "vars.AZURE_CLIENT_ID",
            "vars.AZURE_TENANT_ID",
            "vars.AZURE_SUBSCRIPTION_ID",
        )
        for value in required:
            with self.subTest(value=value):
                self.assertIn(value, content)

        forbidden = (
            "az deployment group create",
            "azure/functions-action",
            "HUSQVARNA_CLIENT_SECRET: ${{ secrets.",
            "HYDRAWISE_API_KEY: ${{ secrets.",
            "client-secret:",
        )
        for value in forbidden:
            with self.subTest(value=value):
                self.assertNotIn(value, content)

    def test_validation_workflow_uses_node24_checkout(self) -> None:
        content = VALIDATE.read_text(encoding="utf-8")
        self.assertIn("actions/checkout@v6", content)
        self.assertNotIn("actions/checkout@v4", content)


if __name__ == "__main__":
    unittest.main()
