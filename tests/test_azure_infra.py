from __future__ import annotations

import json
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
BICEP = ROOT / "infra" / "main.bicep"
PARAMETERS = ROOT / "infra" / "main.parameters.example.json"
VALIDATION_WORKFLOW = ROOT / ".github" / "workflows" / "azure-infra-validate.yml"


class AzureInfrastructureTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.bicep = BICEP.read_text(encoding="utf-8")
        cls.parameters = json.loads(PARAMETERS.read_text(encoding="utf-8"))
        cls.workflow = VALIDATION_WORKFLOW.read_text(encoding="utf-8")

    def test_safe_defaults_are_fixed(self) -> None:
        self.assertIn("param controlMode string = 'DRY_RUN'", self.bicep)
        self.assertIn("param enableLiveReads bool = false", self.bicep)
        self.assertIn("param timerSchedule string = '0 * * * * *'", self.bicep)
        self.assertIn("param maximumInstanceCount int = 40", self.bicep)
        self.assertIn("param instanceMemoryMB int = 512", self.bicep)

    def test_storage_uses_managed_identity_without_shared_key(self) -> None:
        self.assertIn("allowSharedKeyAccess: false", self.bicep)
        self.assertIn("AzureWebJobsStorage__credential: 'managedidentity'", self.bicep)
        self.assertIn("type: 'UserAssignedIdentity'", self.bicep)
        self.assertIn("publicAccess: 'None'", self.bicep)

    def test_state_table_and_key_vault_are_declared(self) -> None:
        self.assertIn("MowerAutomationState", self.bicep)
        self.assertIn("Microsoft.KeyVault/vaults@2023-07-01", self.bicep)
        self.assertIn("Key Vault Secrets User", self.bicep)

    def test_no_secret_values_are_deployed(self) -> None:
        self.assertNotIn("Microsoft.KeyVault/vaults/secrets", self.bicep)
        self.assertEqual(
            {
                "location",
                "namePrefix",
                "environmentName",
                "timerSchedule",
                "controlMode",
                "enableLiveReads",
                "maximumInstanceCount",
                "instanceMemoryMB",
            },
            set(self.parameters["parameters"]),
        )
        serialized = json.dumps(self.parameters).casefold()
        for forbidden in (
            "client-secret",
            "api-key",
            "password",
            "token",
        ):
            self.assertNotIn(forbidden, serialized)

    def test_key_vault_references_are_versionless(self) -> None:
        for secret_name in (
            "husqvarna-client-id",
            "husqvarna-client-secret",
            "hydrawise-api-key",
            "hydrawise-controller-id",
        ):
            self.assertIn(f"secrets/{secret_name}/)", self.bicep)

    def test_validation_workflow_cannot_deploy(self) -> None:
        self.assertIn("az bicep build", self.workflow)
        self.assertNotIn("azure/login", self.workflow)
        self.assertNotIn("az deployment", self.workflow)
        self.assertNotIn("azure/functions-action", self.workflow)


if __name__ == "__main__":
    unittest.main()
