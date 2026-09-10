import json
from pathlib import Path
import shutil
import subprocess
import textwrap
import unittest


ROOT = Path(__file__).resolve().parents[1]
NODE = shutil.which("node")

NODE_HARNESS = r"""
const fs = require("fs");
const payload = JSON.parse(fs.readFileSync(0, "utf8"));
const outputs = {};
const dispatches = [];
const github = {
  rest: {
    actions: {
      listJobsForWorkflowRun: async () => ({ data: payload.jobsResponse }),
      createWorkflowDispatch: async (request) => { dispatches.push(request); },
    },
  },
};
const context = {
  repo: { owner: "Rohdeo87", repo: "ssv53-heimspiele" },
  payload: { workflow_run: { id: 12345 } },
};
const core = {
  setOutput: (name, value) => { outputs[name] = String(value); },
  info: () => {},
};
process.env.IMPORT_WORKFLOW_RUN = "true";
process.env.SOURCE_SHA = "";
(async () => {
  try {
    const run = new Function(
      "github", "context", "core", "process",
      `return (async () => {\n${payload.script}\n})()`
    );
    await run(github, context, core, process);
    process.stdout.write(JSON.stringify({ outputs, dispatches }));
  } catch (error) {
    process.stderr.write(error.stack || String(error));
    process.exit(1);
  }
})();
"""


class RuntimeConfigAutomationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.dispatcher = (
            ROOT
            / ".github"
            / "workflows"
            / "SSV53_Runtime_Config_Auto_Dispatch.yml"
        ).read_text(encoding="utf-8")
        cls.dispatch_script = cls._script_for("id: runtime_dispatch")

    @classmethod
    def _script_for(cls, marker: str) -> str:
        lines = cls.dispatcher.splitlines()
        start = next(index for index, line in enumerate(lines) if marker in line)
        script_line = next(
            index for index in range(start, len(lines))
            if lines[index].strip() == "script: |"
        )
        body: list[str] = []
        for line in lines[script_line + 1:]:
            if line and not line.startswith("            "):
                break
            body.append(line)
        return textwrap.dedent("\n".join(body)).strip()

    def _run_workflow_dispatch(self, jobs_response: object) -> dict[str, object]:
        self.assertIsNotNone(NODE, "Node.js ist für den github-script-Vertrag erforderlich.")
        completed = subprocess.run(
            [str(NODE), "-e", NODE_HARNESS],
            input=json.dumps({"script": self.dispatch_script, "jobsResponse": jobs_response}),
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        return json.loads(completed.stdout)

    @staticmethod
    def _jobs(*, scrape: str, persist: str, conclusion: str = "success") -> dict[str, object]:
        return {
            "jobs": [{
                "name": "update",
                "conclusion": conclusion,
                "steps": [
                    {"name": "Heimspiele zurückhaltend abrufen", "conclusion": scrape},
                    {"name": "Daten und Schutzstatus konfliktfrei speichern", "conclusion": persist},
                ],
            }],
        }

    def test_successful_own_main_import_is_the_only_workflow_run_trigger(self) -> None:
        self.assertIn("workflow_run:", self.dispatcher)
        self.assertIn("- SSV53 Heimspiele aktualisieren", self.dispatcher)
        self.assertIn("- completed", self.dispatcher)
        self.assertIn("github.event.workflow_run.conclusion == 'success'", self.dispatcher)
        for event_name in ("schedule", "workflow_dispatch", "push"):
            self.assertIn(f"github.event.workflow_run.event == '{event_name}'", self.dispatcher)
        self.assertIn(
            "github.event.workflow_run.head_branch == github.event.repository.default_branch",
            self.dispatcher,
        )
        self.assertIn(
            "github.event.workflow_run.head_repository.full_name == github.repository",
            self.dispatcher,
        )
        self.assertIn("github.event_name == 'push' && github.sha || ''", self.dispatcher)
        self.assertNotIn("source_sha: context.payload.workflow_run.head_sha", self.dispatcher)
        self.assertIn("source_sha: process.env.SOURCE_SHA", self.dispatcher)

    def test_dispatch_script_requires_proven_import_and_persist_steps(self) -> None:
        cases = {
            "success": self._jobs(scrape="success", persist="success"),
            "scrape_skipped": self._jobs(scrape="skipped", persist="skipped"),
            "persist_failed": self._jobs(scrape="success", persist="failure", conclusion="failure"),
            "unknown_jobs": {"jobs": None},
        }
        for name, jobs_response in cases.items():
            with self.subTest(name=name):
                result = self._run_workflow_dispatch(jobs_response)
                should_dispatch = name == "success"
                self.assertEqual(result["outputs"]["dispatched"], str(should_dispatch).lower())
                self.assertEqual(len(result["dispatches"]), int(should_dispatch))
                if should_dispatch:
                    request = result["dispatches"][0]
                    self.assertEqual(request["workflow_id"], "azure-runtime-config-rollout.yml")
                    self.assertEqual(request["inputs"]["source_sha"], "")

    def test_existing_manual_push_and_scheduled_dispatches_remain_available(self) -> None:
        self.assertIn("workflow_dispatch:", self.dispatcher)
        self.assertIn("push:", self.dispatcher)
        self.assertIn('cron: "47 1,7,13,19 * * *"', self.dispatcher)
        self.assertIn('ref: "feature/azure-mower-migration"', self.dispatcher)


if __name__ == "__main__":
    unittest.main()
