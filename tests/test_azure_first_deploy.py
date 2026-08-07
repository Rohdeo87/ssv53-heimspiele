from __future__ import annotations

import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / '.github' / 'workflows' / 'azure-first-deploy.yml'


class AzureFirstDeployWorkflowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.workflow = WORKFLOW.read_text(encoding='utf-8')

    def test_hard_gates_exist(self) -> None:
        self.assertIn('AZURE_BOOTSTRAP_ENABLED', self.workflow)
        self.assertIn('SSV53-AZURE-ERSTDEPLOYMENT', self.workflow)
        self.assertIn('feature/azure-mower-migration', self.workflow)

    def test_safe_operating_mode_is_fixed(self) -> None:
        self.assertGreaterEqual(self.workflow.count('controlMode="DRY_RUN"'), 3)
        self.assertGreaterEqual(self.workflow.count('enableLiveReads=false'), 3)
        self.assertGreaterEqual(self.workflow.count('dynamicConfigEnabled=false'), 3)
        self.assertNotIn('enableLiveReads=true', self.workflow)
        self.assertNotIn('CONTROL_MODE=LIVE', self.workflow)

    def test_alerts_wait_until_after_code_deployment(self) -> None:
        code_pos = self.workflow.index('Python-Function auf Flex Consumption bereitstellen')
        first_disabled = self.workflow.index('alertsEnabled=false')
        enabled = self.workflow.rindex('alertsEnabled=true')
        self.assertLess(first_disabled, code_pos)
        self.assertGreater(enabled, code_pos)

    def test_remote_build_is_requested_for_python_flex(self) -> None:
        self.assertIn('az functionapp deployment source config-zip', self.workflow)
        self.assertIn('--build-remote true', self.workflow)

    def test_no_device_action_commands_are_present(self) -> None:
        for marker in ('ParkUntilFurtherNotice', 'StartInWorkArea', 'send_mower_action', '/actions'):
            self.assertNotIn(marker, self.workflow)


if __name__ == '__main__':
    unittest.main()
