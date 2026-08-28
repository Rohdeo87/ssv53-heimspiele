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
        self.assertIn("param alertsEnabled bool = true", self.bicep)
        self.assertIn("param dynamicConfigEnabled bool = false", self.bicep)
        self.assertIn("param timerSchedule string = '0 * * * * *'", self.bicep)
        self.assertIn("param maximumInstanceCount int = 40", self.bicep)
        self.assertIn("param instanceMemoryMB int = 512", self.bicep)
        self.assertIn("ENABLE_IRRIGATION_COMMANDS: 'false'", self.bicep)
        self.assertIn("FULL_FAILSAFE_CONFIRMATION: 'LOCKED'", self.bicep)
        self.assertIn("HYDRAWISE_EXPECTED_ZONE_COUNT: '7'", self.bicep)
        self.assertIn(
            "HYDRAWISE_EXPECTED_RELAY_IDS: '9104894,9104906,9104909,9104911,9104913,9104920,9104921'",
            self.bicep,
        )
        self.assertIn("POST_IRRIGATION_DRYING_MINUTES: '150'", self.bicep)
        self.assertIn("HYDRAWISE_CLEAR_CONFIRMATION_MINUTES: '150'", self.bicep)
        self.assertIn(
            "FULL_MOWER_HYDRAWISE_CLEAR_CONFIRMATION_MINUTES: '10'",
            self.bicep,
        )
        self.assertIn("MOWER_PARK_LEAD_MINUTES: '4'", self.bicep)
        self.assertIn("MOWER_PARK_CONFIRMATION_CYCLES: '2'", self.bicep)
        self.assertIn("param weatherEnabled bool = false", self.bicep)
        self.assertIn("WEATHER_ENABLED: string(weatherEnabled)", self.bicep)
        self.assertIn("WEATHER_PROVIDER: 'OPEN_METEO'", self.bicep)
        self.assertIn("WEATHER_MONTHLY_CALL_LIMIT: '900'", self.bicep)
        self.assertIn("ADAPTIVE_EXECUTION_ENABLED: 'false'", self.bicep)
        self.assertIn("IRRIGATION_FINISH_AFTER_SUNRISE_MINUTES: '60'", self.bicep)

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
                "alertEmail",
                "runtimeConfigMaxAgeMinutes",
                "alertsEnabled",
                "dynamicConfigEnabled",
                "weatherEnabled",
                "weatherLatitude",
                "weatherLongitude",
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
            self.assertIn(f"secrets/{secret_name})", self.bicep)
            self.assertNotIn(f"secrets/{secret_name}/)", self.bicep)

    def test_validation_workflow_cannot_deploy(self) -> None:
        self.assertIn("az bicep build", self.workflow)
        self.assertNotIn("azure/login", self.workflow)
        self.assertNotIn("az deployment", self.workflow)
        self.assertNotIn("azure/functions-action", self.workflow)


    def test_runtime_config_blob_and_alerting_are_declared(self) -> None:
        self.assertIn("runtimeConfigContainerName = 'runtime-config'", self.bicep)
        self.assertIn("SSV53_DYNAMIC_CONFIG_ENABLED: string(dynamicConfigEnabled)", self.bicep)
        self.assertIn("enabled: alertsEnabled", self.bicep)
        self.assertIn("SSV53_CONFIG_MAX_AGE_MINUTES", self.bicep)
        self.assertIn("Microsoft.Insights/actionGroups@2023-01-01", self.bicep)
        self.assertIn("Microsoft.Insights/scheduledQueryRules@2023-12-01", self.bicep)
        self.assertIn("SSV53_CONTROL_CYCLE", self.bicep)
        self.assertIn("query: 'exceptions | where not(", self.bicep)
        self.assertIn('outerMessage == "python exited with code 143 (0x8F)"', self.bicep)
        self.assertIn(
            'tostring(customDimensions["Category"]) startswith '
            '"Worker.rpcWorkerProcess.python."',
            self.bicep,
        )
        self.assertIn("query: 'traces | where message startswith \"SSV53_CONTROL_CYCLE\"'", self.bicep)
        self.assertNotIn("AppTraces", self.bicep)
        self.assertNotIn("AppExceptions", self.bicep)
        self.assertIn("emailAddress: alertEmail", self.bicep)
        self.assertIn("SSV53 Platzpflege – Sicherheitszustand", self.bicep)
        self.assertIn("IRRIGATION_PLAN_CHANGED", self.bicep)
        self.assertIn("IRRIGATION_FAILED_HOLD", self.bicep)
        self.assertIn("persisted == false", self.bicep)
        self.assertIn("allowlist == false", self.bicep)
        self.assertIn("IRRIGATION_PLAN_LEASE_MINUTES: '3'", self.bicep)
        self.assertIn(
            "IRRIGATION_PLAN_CHANGE_CONFIRMATION_MINUTES: '2'",
            self.bicep,
        )
        self.assertIn(
            "IRRIGATION_EARLY_STOP_TOLERANCE_SECONDS: '120'",
            self.bicep,
        )
        self.assertIn("IRRIGATION_DURATION_CHANGE_UNSAFE", self.bicep)
        self.assertIn("IRRIGATION_RUN_CANCELLED_EARLY", self.bicep)

    def test_alert_email_is_parameterized_not_hardcoded(self) -> None:
        self.assertIn("param alertEmail string", self.bicep)
        self.assertNotIn("@ssv53.de", self.bicep.casefold())

    def test_flex_consumption_avoids_legacy_worker_runtime_setting(self) -> None:
        self.assertNotIn("FUNCTIONS_WORKER_RUNTIME", self.bicep)
        self.assertIn("functionAppConfig:", self.bicep)
        self.assertIn("runtime:", self.bicep)
        self.assertIn("name: 'python'", self.bicep)
        self.assertIn("version: '3.12'", self.bicep)


if __name__ == "__main__":
    unittest.main()
