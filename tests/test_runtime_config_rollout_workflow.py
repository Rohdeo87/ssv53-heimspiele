from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "azure-runtime-config-rollout.yml"


class RuntimeConfigRolloutWorkflowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.content = WORKFLOW.read_text(encoding="utf-8")

    def test_runtime_code_is_migration_branch_but_source_is_atomic_main_commit(self):
        self.assertIn("ref: feature/azure-mower-migration", self.content)
        self.assertIn(
            "SOURCE_BRANCH: ${{ github.event.repository.default_branch }}",
            self.content,
        )
        self.assertIn(
            'SOURCE_SHA="$(git rev-parse "refs/remotes/origin/$SOURCE_BRANCH")"',
            self.content,
        )
        self.assertIn('git show "$SOURCE_SHA:public/$name"', self.content)
        self.assertNotIn('cp "public/$name" "$SOURCE_DIR/$name"', self.content)

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
