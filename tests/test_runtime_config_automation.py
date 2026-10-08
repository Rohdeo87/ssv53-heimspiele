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
const RealDate = Date;
const now = RealDate.parse(payload.now || "2026-09-30T10:00:00Z");
global.Date = class extends RealDate { constructor(...args){super(...(args.length ? args : [now]));} static now(){return now;} };
const outputs = {};
const dispatches = [];
let serial = 100;
const runs = [];
const polls = {};
const waited = [];
let stamp = new Date(Date.now() - (payload.age || 60)*60000).toISOString();
if (payload.activeImport) runs.push({id:50,workflow_id:"update-matches.yml",event:payload.activeEvent || "schedule",status:"in_progress",created_at:new Date(now-600000).toISOString(),html_url:"active-import"});
const github = {
  rest: {
    repos: {getContent: async () => ({data:{content:Buffer.from(JSON.stringify({generated_at:stamp})).toString("base64")}})},
    actions: {
      listWorkflowRuns: async (request) => {
        if (payload.finishedDuringLookup && request.workflow_id === "update-matches.yml" && !request.event) stamp=new Date().toISOString();
        const selected=runs.filter(r=>r.workflow_id === request.workflow_id && (!request.event || r.event === request.event));
        // Model an older run appearing late in the list API, with a higher ID
        // than those visible in the pre-dispatch lookup.
        if(payload.lateOldRun && request.per_page === 10) selected.push({id:99,status:"completed",conclusion:"success",created_at:new Date(now-600000).toISOString(),html_url:"old-run"});
        return {data:{workflow_runs:selected}};
      },
      getWorkflowRun: async ({run_id}) => {
        waited.push(run_id); polls[run_id]=(polls[run_id] || 0)+1;
        const run=runs.find(r=>r.id === run_id);
        if (!run) throw new Error("Wrong run selected: "+run_id);
        const status=payload.neverCompletes || polls[run_id] < (payload.completeAfter || 3) ? "in_progress" : "completed";
        const conclusion=payload.fail ? "failure" : "success";
        if(status === "completed" && conclusion === "success" && run.workflow_id === "update-matches.yml") stamp=new Date().toISOString();
        return {data:{...run,status,conclusion}};
      },
      listJobsForWorkflowRun: async () => ({ data: payload.jobsResponse }),
      createWorkflowDispatch: async (request) => { dispatches.push(request); runs.push({id:++serial,workflow_id:request.workflow_id,event:"workflow_dispatch",status:"queued",created_at:new Date().toISOString(),html_url:"test-run-"+serial}); },
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
      "github", "context", "core", "process", "fetch", "setTimeout",
      `return (async () => {\n${payload.script}\n})()`
    );
    await run(github, context, core, process, async () => ({ok:!payload.neverHealthy && (payload.healthy || dispatches.some(r=>r.workflow_id === "azure-runtime-config-rollout.yml")),json:async()=>({match_source_fresh:true,training_calendar:{fail_closed:false},match_source_generated_at_utc:stamp})}), cb=>cb());
    process.stdout.write(JSON.stringify({ outputs, dispatches, polls, waited }));
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

    def _run_workflow_dispatch(self, jobs_response: object, **scenario) -> dict[str, object]:
        self.assertIsNotNone(NODE, "Node.js ist für den github-script-Vertrag erforderlich.")
        completed = subprocess.run(
            [str(NODE), "-e", NODE_HARNESS],
            input=json.dumps({"script": self.dispatch_script, "jobsResponse": jobs_response, **scenario}),
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
        self.assertIn('source_sha:""', self.dispatcher)

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

    def test_healthy_calendar_does_not_republish(self):
        result = self._run_workflow_dispatch(self._jobs(scrape="success", persist="success"), healthy=True)
        self.assertEqual(result["dispatches"], [])
        self.assertEqual(result["outputs"]["dispatched"], "false")

    def test_failed_rollout_is_not_reported_as_success(self):
        with self.assertRaisesRegex(AssertionError, "fehlgeschlagen"):
            self._run_workflow_dispatch(self._jobs(scrape="success", persist="success"), fail=True)

    def test_old_source_is_refreshed_before_publication(self):
        result = self._run_workflow_dispatch(self._jobs(scrape="success", persist="success"), age=400)
        self.assertEqual([r["workflow_id"] for r in result["dispatches"]], ["update-matches.yml", "azure-runtime-config-rollout.yml"])
        self.assertEqual(result["dispatches"][0]["inputs"]["allow_destructive_change"], "false")

    def test_nighttime_recovery_prevents_the_october_8_morning_gap(self):
        cases = [
            # Last genuine source refresh: Oct 7 16:40 UTC.
            ("2026-10-07T22:03:00Z", 323, []),
            ("2026-10-08T02:02:00Z", 562, ["update-matches.yml"]),
            # With the source refreshed at 02:02 UTC, morning stays healthy.
            ("2026-10-08T06:47:00Z", 285, []),
        ]
        for now, age, expected in cases:
            with self.subTest(now=now):
                result = self._run_workflow_dispatch(
                    self._jobs(scrape="success", persist="success"),
                    now=now, age=age, healthy=True,
                )
                self.assertEqual([r["workflow_id"] for r in result["dispatches"]], expected)

    def test_nighttime_six_hour_threshold_and_existing_import_reuse(self):
        for age, expected in ((359, []), (360, ["update-matches.yml"]), (721, ["update-matches.yml"])):
            with self.subTest(age=age):
                result = self._run_workflow_dispatch(
                    self._jobs(scrape="success", persist="success"),
                    now="2026-10-08T01:00:00Z", age=age, healthy=True,
                )
                self.assertEqual([r["workflow_id"] for r in result["dispatches"]], expected)
        result = self._run_workflow_dispatch(
            self._jobs(scrape="success", persist="success"),
            now="2026-10-08T02:02:00Z", age=562, healthy=True, activeImport=True,
        )
        self.assertEqual(result["dispatches"], [])
        self.assertEqual(result["waited"], [50, 50, 50])

    def test_unhealthy_public_calendar_cannot_report_success(self):
        with self.assertRaisesRegex(AssertionError, "weiterhin nicht fehlerfrei"):
            self._run_workflow_dispatch(self._jobs(scrape="success", persist="success"), neverHealthy=True)

    def test_existing_import_is_awaited_without_a_second_scrape(self):
        for event in ("schedule", "workflow_dispatch", "push"):
            with self.subTest(event=event):
                result = self._run_workflow_dispatch(self._jobs(scrape="success", persist="success"), age=700,
                                                     activeImport=True, activeEvent=event, completeAfter=40)
                self.assertEqual([r["workflow_id"] for r in result["dispatches"]], ["azure-runtime-config-rollout.yml"])
                self.assertEqual(result["polls"]["50"], 40)
                self.assertEqual(result["waited"][:40], [50] * 40)

    def test_import_completed_during_lookup_is_not_repeated(self):
        result = self._run_workflow_dispatch(self._jobs(scrape="success", persist="success"),
                                            age=400, finishedDuringLookup=True)
        self.assertEqual([r["workflow_id"] for r in result["dispatches"]], ["azure-runtime-config-rollout.yml"])

    def test_completed_old_run_cannot_confirm_new_publication(self):
        result = self._run_workflow_dispatch(self._jobs(scrape="success", persist="success"), lateOldRun=True)
        self.assertEqual(result["waited"], [101, 101, 101])

    def test_failed_existing_import_remains_a_failure(self):
        with self.assertRaisesRegex(AssertionError, "active-import"):
            self._run_workflow_dispatch(self._jobs(scrape="success", persist="success"), age=400, activeImport=True, fail=True)

    def test_import_wait_is_bounded_and_reports_the_run_link(self):
        with self.assertRaisesRegex(AssertionError, "Abschluss nicht best.*active-import"):
            self._run_workflow_dispatch(self._jobs(scrape="success", persist="success"), age=400, activeImport=True, neverCompletes=True)

    def test_all_main_import_triggers_reload_after_queue_without_changing_test_refs(self):
        workflow = (ROOT / ".github/workflows/update-matches.yml").read_text(encoding="utf-8")
        refresh = workflow.split("- name: Nach der Warteschlange den neuesten main-Stand verwenden", 1)[1].split("- name:", 1)[0]
        self.assertIn("github.ref == format('refs/heads/{0}', github.event.repository.default_branch)", refresh)
        self.assertIn('git fetch origin "${{ github.event.repository.default_branch }}"', refresh)
        self.assertIn('git reset --hard "origin/${{ github.event.repository.default_branch }}"', refresh)
        self.assertIn("group: ssv53-match-update", workflow)
        self.assertIn("cancel-in-progress: false", workflow)
        self.assertIn("Der veröffentlichte Bestand wurde parallel geändert", workflow)

    def test_existing_manual_push_and_scheduled_dispatches_remain_available(self) -> None:
        self.assertIn("workflow_dispatch:", self.dispatcher)
        self.assertIn("push:", self.dispatcher)
        self.assertIn('cron: "17 * * * *"', self.dispatcher)
        self.assertIn('"azure-runtime-config-rollout.yml","feature/azure-mower-migration"', self.dispatcher)


if __name__ == "__main__":
    unittest.main()
