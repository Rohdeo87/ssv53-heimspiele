from __future__ import annotations

import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
WORKFLOWS = ROOT / ".github" / "workflows"


class RepositoryHardeningTests(unittest.TestCase):
    def test_old_scheduler_heartbeat_is_removed(self) -> None:
        self.assertFalse(
            (WORKFLOWS / "scheduler-heartbeat.yml").exists()
        )

    def test_mower_workflow_is_manual_diagnostic_only(self) -> None:
        content = (
            WORKFLOWS / "mower-decision.yml"
        ).read_text(encoding="utf-8")
        head = "\n".join(content.splitlines()[:45])

        self.assertNotIn("  schedule:", head)
        self.assertNotIn("execute_park:", head)
        self.assertNotIn("execute_start:", head)
        self.assertNotIn(
            "vars.MOWER_PARKING_ENABLED",
            "\n".join(content.splitlines()[:70]),
        )
        self.assertNotIn(
            "vars.MOWER_AUTOSTART_ENABLED",
            "\n".join(content.splitlines()[:70]),
        )
        self.assertIn('EXECUTE_PARK: "false"', content)
        self.assertIn('EXECUTE_START: "false"', content)

    def test_long_lived_workflows_use_current_action_majors(self) -> None:
        forbidden = (
            "actions/checkout@v4",
            "actions/checkout@v5",
            "actions/setup-python@v5",
            "actions/upload-artifact@v4",
        )
        for workflow in WORKFLOWS.glob("*.yml"):
            if workflow.name.startswith(
                "SSV53_Repository_Phase6_1_"
            ):
                continue
            content = workflow.read_text(encoding="utf-8")
            for value in forbidden:
                with self.subTest(
                    workflow=workflow.name,
                    value=value,
                ):
                    actual_use = re.compile(
                        rf"^\s*(?:-\s*)?uses:\s*['\"]?{re.escape(value)}['\"]?\s*(?:#.*)?$",
                        re.MULTILINE,
                    )
                    self.assertIsNone(
                        actual_use.search(content),
                        msg=(
                            "Veraltete Action-Version wird tatsächlich verwendet: "
                            f"{workflow.name}: {value}"
                        ),
                    )

    def test_sensitive_local_files_are_ignored(self) -> None:
        content = (ROOT / ".gitignore").read_text(encoding="utf-8")
        for value in (
            "local.settings.json",
            ".env",
            ".python_packages/",
            ".azure/",
            "dist/",
            "*.zip",
        ):
            with self.subTest(value=value):
                self.assertIn(value, content)

    def test_runtime_dependencies_are_exactly_pinned(self) -> None:
        lines = [
            line.strip()
            for line in (
                ROOT / "requirements.txt"
            ).read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        self.assertTrue(lines)
        for line in lines:
            with self.subTest(line=line):
                self.assertIn("==", line)
                self.assertNotIn(">=", line)
                self.assertNotIn("<", line)

    def test_dependabot_covers_pip_and_actions(self) -> None:
        content = (
            ROOT / ".github" / "dependabot.yml"
        ).read_text(encoding="utf-8")
        self.assertIn('package-ecosystem: "pip"', content)
        self.assertIn(
            'package-ecosystem: "github-actions"',
            content,
        )


if __name__ == "__main__":
    unittest.main()
