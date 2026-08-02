from __future__ import annotations

import json
import re
import unittest
from pathlib import Path

import yaml


PROJECT = Path(__file__).resolve().parents[1]
REPOSITORY = PROJECT.parent
WORKFLOW = REPOSITORY / ".github" / "workflows" / "main.yml"


class PackageTests(unittest.TestCase):
    def test_v4_interactive_search_sources_and_theme_are_packaged(self) -> None:
        config = yaml.safe_load((PROJECT / "config.yml").read_text(encoding="utf-8"))
        usernames = {
            str(item.get("username", "")).casefold()
            for item in config.get("sources", [])
            if isinstance(item, dict)
        }
        queries = "\n".join(str(item) for item in config["discovery"]["queries"])
        bot_text = (PROJECT / "src" / "bot.py").read_text(encoding="utf-8")
        self.assertIn("couphanfiles", usernames)
        self.assertTrue(config["polling"]["include_replies"])
        self.assertIn("윤정한", queries)
        self.assertIn("ジョンハン", queries)
        self.assertIn("JEONGHAN", queries)
        self.assertTrue((PROJECT / "src" / "organizer.py").is_file())
        self.assertIn('BOT_VERSION = "4.0.0"', bot_text)
        self.assertIn('"callback_data": "recent:2h"', bot_text)
        self.assertIn('"callback_data": "search:new"', bot_text)
        self.assertTrue(
            all(
                str(template).startswith("،،")
                for templates in config["themes"]["templates"].values()
                for template in templates
            )
        )

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

    def test_channel_memory_is_large_sanitized_and_persian_first(self) -> None:
        memory_path = PROJECT / "data" / "channel_memory.jsonl"
        entries = [json.loads(line) for line in memory_path.read_text(encoding="utf-8").splitlines()]
        self.assertGreaterEqual(len(entries), 9000)
        self.assertTrue(all(item.get("language") in {"persian", "mixed"} for item in entries))
        forbidden = {"from", "from_id", "actor", "actor_id", "photo", "file", "thumbnail"}
        self.assertTrue(all(not (forbidden & set(item)) for item in entries))
        recent = [item for item in entries if int(item.get("year", 0)) >= 2025]
        self.assertGreaterEqual(len(recent), 1500)

    def test_memory_profile_and_rebuild_tool_are_packaged(self) -> None:
        profile = json.loads(
            (PROJECT / "data" / "channel_voice_profile.json").read_text(encoding="utf-8")
        )
        self.assertGreaterEqual(profile["message_examples"], 8000)
        self.assertGreaterEqual(profile["reply_sequences"], 500)
        self.assertTrue((PROJECT / "tools" / "build_channel_memory.py").is_file())


if __name__ == "__main__":
    unittest.main()
