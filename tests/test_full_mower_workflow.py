from __future__ import annotations

import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = (
    ROOT
    / ".github"
    / "workflows"
    / "SSV53_FULL_MOWER_Locked_Deploy.yml"
)


class FullMowerWorkflowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.content = WORKFLOW.read_text(encoding="utf-8")

    def test_workflow_is_manual_and_branch_gated(self) -> None:
        self.assertIn("workflow_dispatch:", self.content)
        self.assertNotIn("schedule:", self.content)
        self.assertNotIn("push:", self.content)
        self.assertIn("feature/azure-mower-migration", self.content)
        self.assertIn("SSV53-FULL-MOWER-LOCKED-DEPLOY", self.content)

    def test_deployment_forces_all_write_gates_closed(self) -> None:
        for required in (
            "CONTROL_MODE=DRY_RUN",
            "ENABLE_LIVE_READS=true",
            "ENABLE_PARK_COMMANDS=false",
            "ENABLE_START_COMMANDS=false",
            "FULL_MOWER_CONFIRMATION=LOCKED",
        ):
            with self.subTest(required=required):
                self.assertIn(required, self.content)

    def test_irrigation_remains_read_only(self) -> None:
        self.assertIn(
            "hydrawise_continuous_clear_confirmation_required",
            self.content,
        )
        for forbidden in (
            "manualrun.php",
            "stopzone.php",
            "CONTROL_MODE=FULL_FAILSAFE",
        ):
            with self.subTest(forbidden=forbidden):
                # Die beiden API-Namen erscheinen ausschließlich in der
                # Negativprüfung des Pakets, nie als aufgerufener Endpunkt.
                if forbidden.endswith(".php"):
                    self.assertEqual(self.content.count(forbidden), 1)
                else:
                    self.assertNotIn(forbidden, self.content)


if __name__ == "__main__":
    unittest.main()
