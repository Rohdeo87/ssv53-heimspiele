from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "azure-runtime-config-rollout.yml"


class RuntimeConfigRolloutWorkflowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.content = WORKFLOW.read_text(encoding="utf-8")

    def test_runtime_code_is_pinned_to_dispatched_migration_sha_and_source_is_atomic_main_commit(self):
        # The dispatch ref is branch-gated below; checkout itself must be the
        # immutable workflow commit, rather than a later branch tip.
        self.assertIn("MIGRATION_BRANCH: feature/azure-mower-migration", self.content)
        self.assertIn("ref: ${{ github.sha }}", self.content)
        self.assertIn('if [ "$GITHUB_REF_NAME" != "$MIGRATION_BRANCH" ]; then', self.content)
        self.assertIn(
            "SOURCE_BRANCH: ${{ github.event.repository.default_branch }}",
            self.content,
        )
        self.assertIn(
            'LATEST_SOURCE_SHA="$(git rev-parse "refs/remotes/origin/$SOURCE_BRANCH")"',
            self.content,
        )
        self.assertIn("REQUESTED_SOURCE_SHA: ${{ inputs.source_sha }}", self.content)
        self.assertIn(
            'git merge-base --is-ancestor "$REQUESTED_SOURCE_SHA" "$LATEST_SOURCE_SHA"',
            self.content,
        )
        self.assertIn('SOURCE_SHA="$REQUESTED_SOURCE_SHA"', self.content)
        self.assertIn('git show "$SOURCE_SHA:public/$name"', self.content)
        self.assertNotIn('cp "public/$name" "$SOURCE_DIR/$name"', self.content)

    def test_persisted_calendar_binding_is_used_by_dispatches_without_inputs(self):
        # The six-hour dispatcher supplies no calendar inputs.  An approved
        # binding therefore has to come from the persistent configuration values,
        # and a true manual-control flag must construct both envelope copies.
        self.assertIn(
            "inputs.manual_training_control || vars.SSV53_MANUAL_TRAINING_CONTROL_ENABLED == 'true'",
            self.content,
        )
        for value in (
            "vars.SSV53_TRAINING_CONTENT_SHA256",
            "vars.SSV53_TRAINING_APPROVAL_REFERENCE",
            "vars.SSV53_TRAINING_APPROVED_AT",
        ):
            self.assertIn(value, self.content)
        self.assertIn("prepare_training_calendar_approval.py", self.content)
        self.assertIn(
            'TRAINING_ARGS=(--shared-training-calendar "$APPROVED_CALENDAR" --occupancy-config occupancy/config.json --manual-training-control)',
            self.content,
        )

    def test_publish_safety_gates_and_atomic_rollback_remain_required(self):
        self.assertIn('[ "$GITHUB_REF_NAME" = "$MIGRATION_BRANCH" ]', self.content)
        self.assertIn(
            '[ "$PUBLISH_CONFIRMATION" = "SSV53-RUNTIME-CONFIG-PUBLISH" ]',
            self.content,
        )
        self.assertIn('trap rollback ERR', self.content)
        self.assertIn('previous/manifest.json', self.content)
        self.assertIn('assert remote == source', self.content)
        self.assertIn('source["config_sha256"]', self.content)
        self.assertIn('source["matches_sha256"]', self.content)
        self.assertIn('source["occupancy_matches_sha256"]', self.content)
        self.assertIn('OCCUPANCY_MATCHES_BLOB="versions/$VERSION/public/matches.json"', self.content)
        self.assertIn("resolve_occupancy_match_source", self.content)


if __name__ == "__main__":
    unittest.main()
