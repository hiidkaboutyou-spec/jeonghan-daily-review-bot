from __future__ import annotations

import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class ReleaseWorkflowTests(unittest.TestCase):
    def test_live_window_requires_at_least_one_successful_bot_pass(self):
        workflow = (ROOT / ".github" / "workflows" / "main.yml").read_text(encoding="utf-8")
        self.assertIn("successful_passes=0", workflow)
        self.assertIn('if [ "$successful_passes" -eq 0 ]; then', workflow)
        self.assertIn("No bot pass completed successfully during the live window.", workflow)

    def test_runtime_runs_are_serialized_and_only_safe_state_is_cached(self):
        workflow = (ROOT / ".github" / "workflows" / "main.yml").read_text(encoding="utf-8")
        self.assertIn("jeonghan-daily-review-bot-", workflow)
        self.assertIn("'runtime'", workflow)
        self.assertIn("path: .state/state.json", workflow)
        self.assertIn("jeonghan-state-v2-", workflow)
        self.assertNotIn("path: .state\n", workflow)

    def test_ffmpeg_setup_is_bounded_and_retried(self):
        workflow = (ROOT / ".github" / "workflows" / "main.yml").read_text(encoding="utf-8")
        self.assertIn("- name: FFmpeg\n        timeout-minutes: 5", workflow)
        self.assertIn("command -v ffmpeg", workflow)
        self.assertIn("timeout 120s sudo apt-get", workflow)
        self.assertIn("Acquire::https::Timeout=20", workflow)
        self.assertIn("Installing FFmpeg (attempt $attempt/2).", workflow)
        self.assertIn("FFmpeg installation failed after bounded retries.", workflow)

    def test_temporary_release_workflows_are_not_part_of_release_tree(self):
        workflows = ROOT / ".github" / "workflows"
        self.assertFalse((workflows / "release-finalization-smoke.yml").exists())
        self.assertFalse((workflows / "release-cache-cleanup.yml").exists())


if __name__ == "__main__":
    unittest.main()
