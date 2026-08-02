from __future__ import annotations

import re
import unittest
from pathlib import Path

import yaml


PROJECT = Path(__file__).resolve().parents[1]
REPOSITORY = PROJECT.parent
WORKFLOW = REPOSITORY / ".github" / "workflows" / "main.yml"


class PackageTests(unittest.TestCase):
    def test_single_root_workflow_has_check_and_live_modes(self) -> None:
        payload = yaml.load(WORKFLOW.read_text(encoding="utf-8"), Loader=yaml.BaseLoader)
        self.assertIn("on", payload)
        self.assertIn("workflow_dispatch", payload["on"])
        self.assertIn("schedule", payload["on"])
        jobs = payload["jobs"]
        self.assertEqual(list(jobs), ["validate-and-run"])
        steps = jobs["validate-and-run"]["steps"]
        commands = "\n".join(str(step.get("run", "")) for step in steps)
        self.assertIn("python -m src.bot --check", commands)
        self.assertIn("python -m unittest discover -s tests -v", commands)

    def test_third_party_actions_are_pinned_to_full_commits(self) -> None:
        text = WORKFLOW.read_text(encoding="utf-8")
        uses = re.findall(r"^\s*uses:\s*([^\s#]+)", text, flags=re.MULTILINE)
        self.assertTrue(uses)
        for value in uses:
            self.assertRegex(value, r"^[^@]+@[0-9a-f]{40}$")

    def test_vcs_dependency_is_pinned_and_build_artifacts_are_ignored(self) -> None:
        requirements = (PROJECT / "requirements.txt").read_text(encoding="utf-8")
        self.assertNotIn("twscrape.git@main", requirements)
        self.assertRegex(requirements, r"twscrape\.git@[0-9a-f]{40}")
        ignore = (REPOSITORY / ".gitignore").read_text(encoding="utf-8")
        self.assertIn("__pycache__/", ignore)
        self.assertIn("*.py[cod]", ignore)


if __name__ == "__main__":
    unittest.main()
