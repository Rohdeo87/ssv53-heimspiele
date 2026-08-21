from __future__ import annotations

import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = (
    ROOT
    / ".github"
    / "workflows"
    / "SSV53_Occupancy_Automation.yml"
)


class OccupancyDeployWorkflowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.workflow = WORKFLOW.read_text(encoding="utf-8")

    def test_live_check_retries_host_http_and_schema_together(self) -> None:
        self.assertIn("for attempt in $(seq 1 18)", self.workflow)
        self.assertIn('if [ -n "$HOST" ] && curl', self.workflow)
        self.assertIn("if python - <<'PY'", self.workflow)
        self.assertIn('payload.get("schema_version") == 1', self.workflow)
        self.assertIn('payload.get("data_source") == "azure"', self.workflow)
        self.assertIn('isinstance(payload.get("events"), list)', self.workflow)

    def test_transient_hostname_lookup_cannot_abort_the_first_attempt(self) -> None:
        self.assertIn("2>/dev/null \\", self.workflow)
        self.assertIn("|| true", self.workflow)
        self.assertIn('if [ "$success" != "true" ]; then', self.workflow)
        self.assertNotIn('\n          [ -n "$HOST" ]\n', self.workflow)

    def test_deploy_still_preserves_settings_and_full_failsafe_package(self) -> None:
        self.assertIn("Azure-Appsettings nach Deployment vergleichen", self.workflow)
        self.assertIn("FULL_FAILSAFE_7_ZONES_120_MIN_CAPABLE_LOCKED", self.workflow)
        self.assertIn("park_write_gate_required", self.workflow)
        self.assertIn("start_write_gate_required", self.workflow)
        self.assertIn("irrigation_write_gate_required", self.workflow)


if __name__ == "__main__":
    unittest.main()
