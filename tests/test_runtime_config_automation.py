from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]


class RuntimeConfigAutomationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.rollout = (
            ROOT / ".github" / "workflows" / "azure-runtime-config-rollout.yml"
        ).read_text(encoding="utf-8")
        cls.dispatcher = (
            ROOT
            / ".github"
            / "workflows"
            / "SSV53_Runtime_Config_Auto_Dispatch.yml"
        ).read_text(encoding="utf-8")

    def test_dispatcher_runs_after_match_updates_and_on_fallback_schedule(self) -> None:
        self.assertIn("workflow_run:", self.dispatcher)
        self.assertIn("SSV53 Heimspiele aktualisieren", self.dispatcher)
        self.assertIn('cron: "47 1,7,13,19 * * *"', self.dispatcher)
        self.assertIn("createWorkflowDispatch", self.dispatcher)
        self.assertIn('ref: "feature/azure-mower-migration"', self.dispatcher)

    def test_dispatcher_only_requests_runtime_publish(self) -> None:
        self.assertIn('operation: "publish"', self.dispatcher)
        self.assertIn(
            'publish_confirmation: "SSV53-RUNTIME-CONFIG-PUBLISH"',
            self.dispatcher,
        )
        for forbidden in (
            "CONTROL_MODE=PARK_ONLY",
            "ENABLE_PARK_COMMANDS=true",
            "StartInWorkArea",
            "ResumeSchedule",
            "HYDRAWISE_API_KEY",
        ):
            self.assertNotIn(forbidden, self.dispatcher)

    def test_rollout_uses_fresh_main_data_and_current_timing_engine(self) -> None:
        self.assertIn("included_matches.json summary.json quality_report.json", self.rollout)
        self.assertIn(
            "SOURCE_BRANCH: ${{ github.event.repository.default_branch }}",
            self.rollout,
        )
        self.assertIn('git show "$SOURCE_SHA:public/$name"', self.rollout)
        self.assertIn("scripts/build_runtime_config_bundle.py", self.rollout)
        self.assertIn("--timing-config config.json", self.rollout)
        self.assertIn('MAX_SOURCE_AGE_MINUTES: "720"', self.rollout)
        self.assertNotIn("2026-08-12", self.rollout)

    def test_rollout_keeps_atomic_fallback_and_failure_notification(self) -> None:
        self.assertIn("previous/manifest.json", self.rollout)
        self.assertIn("current/manifest.json", self.rollout)
        self.assertIn("trap rollback ERR", self.rollout)
        self.assertIn("Azure-Laufzeitdaten nicht aktualisiert", self.rollout)
        self.assertIn("issues: write", self.rollout)

    def test_rollout_does_not_enable_device_commands(self) -> None:
        for forbidden in (
            "CONTROL_MODE=PARK_ONLY",
            "ENABLE_PARK_COMMANDS=true",
            "FULL_MOWER",
            "FULL_FAILSAFE",
            "StartInWorkArea",
            "ResumeSchedule",
        ):
            self.assertNotIn(forbidden, self.rollout)


if __name__ == "__main__":
    unittest.main()
