from __future__ import annotations

import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class ReleaseWorkflowTests(unittest.TestCase):
    def test_live_runtime_requires_one_complete_monitor_pass(self):
        workflow = (ROOT / ".github" / "workflows" / "main.yml").read_text(encoding="utf-8")
        live = workflow.split("- name: Run one complete automatic monitor pass", 1)[1].split(
            "- name: Checkpoint private review database", 1
        )[0]
        self.assertIn("python -m app", live)
        self.assertIn("ASSISTANT_RUNTIME_MODE: github_actions_polling", live)
        self.assertIn('if [ "$code" -eq 0 ]; then', live)
        self.assertIn("Automatic monitor pass completed.", live)
        self.assertNotIn("Healthy webhook runtime accepted the maintenance pass.", live)
        self.assertNotIn("successful_passes=0", live)
        self.assertNotIn('while [ "$SECONDS"', live)

    def test_scheduled_runtime_does_not_repeat_the_full_test_suite(self):
        workflow = (ROOT / ".github" / "workflows" / "main.yml").read_text(encoding="utf-8")
        validate = workflow.split("- name: Validate project", 1)[1].split("- name: Runtime smoke check", 1)[0]
        smoke = workflow.split("- name: Runtime smoke check", 1)[1].split(
            "- name: Run one complete automatic monitor pass", 1
        )[0]
        self.assertIn("github.event_name == 'push'", validate)
        self.assertIn("inputs.mode == 'check'", validate)
        self.assertNotIn("github.event_name == 'schedule'", validate)
        self.assertIn("github.event_name == 'schedule'", smoke)
        self.assertIn("python -m app --check", smoke)
        self.assertNotIn("unittest discover", smoke)

    def test_live_runtime_checks_providers_before_running(self):
        workflow = (ROOT / ".github" / "workflows" / "main.yml").read_text(encoding="utf-8")
        preflight = workflow.split("- name: Check live production providers", 1)[1].split(
            "- name: Run one complete automatic monitor pass", 1
        )[0]
        self.assertIn("python -m app.production_preflight", preflight)
        self.assertIn("TELEGRAM_BOT_TOKEN", preflight)
        self.assertIn("X_COOKIE", preflight)
        self.assertIn("GEMINI_API_KEY", preflight)

    def test_automatic_monitor_uses_five_minute_schedule_without_long_live_loop(self):
        workflow = (ROOT / ".github" / "workflows" / "main.yml").read_text(encoding="utf-8")
        self.assertIn('cron: "2,7,12,17,22,27,32,37,42,47,52,57 * * * *"', workflow)
        self.assertIn("Run one complete automatic monitor pass", workflow)
        self.assertNotIn('end=$((SECONDS + 540))', workflow)
        self.assertNotIn('end=$((SECONDS + 780))', workflow)

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

    def test_every_python_workflow_upgrades_pip_past_audited_vulnerabilities(self):
        workflows = ROOT / ".github" / "workflows"
        for name in ("main.yml", "fic-digest.yml", "translation-benchmark.yml"):
            text = (workflows / name).read_text(encoding="utf-8")
            self.assertIn('python -m pip install --upgrade "pip>=26.1.2"', text, name)


if __name__ == "__main__":
    unittest.main()
