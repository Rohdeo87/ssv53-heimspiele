from pathlib import Path
import unittest


class MatchWorkflowTestEnvironmentTests(unittest.TestCase):
    def test_pytest_dependency_and_full_collection_are_explicit(self):
        content = (Path(__file__).resolve().parents[1] / ".github/workflows/update-matches.yml").read_text(encoding="utf-8")
        self.assertIn("pip install -r requirements.txt pytest", content)
        self.assertIn("python -m pytest -q", content)
        self.assertNotIn("python -m unittest discover", content)
        self.assertLess(content.index("python -m pytest -q"), content.index("python poc_scraper.py"))
        self.assertIn("cancel-in-progress: false", content)
        self.assertIn("steps.changes.outcome", content)
        self.assertIn("schedule_guard.py", content)
