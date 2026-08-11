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
        self.assertIn("timeout-minutes: 15", live)
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
        self.assertIn("if: env.LIVE_RUN == 'true'", smoke)
        self.assertIn("github.event_name == 'schedule'", workflow)
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

    def test_live_workflows_pin_the_verified_stable_gemini_model(self):
        workflows = ROOT / ".github" / "workflows"
        for name in ("main.yml", "fic-digest.yml"):
            text = (workflows / name).read_text(encoding="utf-8")
            self.assertIn("GEMINI_MODEL: gemini-3.1-flash-lite", text, name)
            self.assertNotIn("gemini-3.1-flash-lite-preview", text, name)

    def test_direct_main_push_runs_one_live_pass_without_exposing_prs(self):
        workflow = (ROOT / ".github" / "workflows" / "main.yml").read_text(encoding="utf-8")
        self.assertIn(
            "(github.event_name == 'push' && github.ref == 'refs/heads/main')",
            workflow,
        )
        self.assertIn("LIVE_RUN: ${{", workflow)
        self.assertIn("if: env.LIVE_RUN == 'true'", workflow)
        self.assertIn("github.ref != 'refs/heads/main'", workflow)

    def test_automatic_monitor_uses_five_minute_schedule_without_long_live_loop(self):
        workflow = (ROOT / ".github" / "workflows" / "main.yml").read_text(encoding="utf-8")
        self.assertIn('cron: "2,7,12,17,22,27,32,37,42,47,52,57 0-17,19-23 * * *"', workflow)
        self.assertIn('cron: "2,7,12,17,22 18 * * *"', workflow)
        self.assertNotIn('27,32,37,42,47,52,57 18 * * *', workflow)
        self.assertIn("Run one complete automatic monitor pass", workflow)
        self.assertNotIn('end=$((SECONDS + 540))', workflow)
        self.assertNotIn('end=$((SECONDS + 780))', workflow)

    def test_live_runtime_self_dispatches_without_duplicate_chains(self):
        workflow = (ROOT / ".github" / "workflows" / "main.yml").read_text(encoding="utf-8")
        queue = workflow.split("- name: Queue next live assistant pass", 1)[1]
        self.assertIn("actions: write", workflow)
        self.assertIn("success()", queue)
        self.assertIn("github.ref == 'refs/heads/main'", queue)
        self.assertIn("for status in pending queued in_progress", queue)
        self.assertIn('select(.id != $current)', queue)
        self.assertIn("gh_workflow_retry main.yml --ref main -f mode=live", queue)
        self.assertIn("gh_api_retry", queue)
        self.assertIn("gh_workflow_retry", queue)
        self.assertIn("for attempt in 1 2 3", queue)
        self.assertIn("Another runtime pass is already queued or running", queue)
        self.assertIn("Queued the next live assistant pass.", queue)
        self.assertIn("actions/workflows/fic-digest.yml/runs", queue)
        self.assertIn("yielding the shared runtime slot", queue)

    def test_fic_runtime_resumes_main_after_serialized_delivery(self):
        workflow = (ROOT / ".github" / "workflows" / "fic-digest.yml").read_text(encoding="utf-8")
        resume = workflow.split("- name: Resume live assistant after fanfic digest", 1)[1]
        self.assertIn("actions: write", workflow)
        self.assertIn("actions/workflows/main.yml/runs", resume)
        self.assertIn("gh_workflow_retry main.yml --ref main -f mode=live", resume)
        self.assertIn("gh_api_retry", resume)
        self.assertIn("gh_workflow_retry", resume)

    def test_live_chain_dispatches_one_missing_nightly_fic_after_due_time(self):
        workflow = (ROOT / ".github" / "workflows" / "main.yml").read_text(encoding="utf-8")
        due = workflow.split("- name: Queue due nightly fanfic digest", 1)[1].split(
            "- name: Queue next live assistant pass", 1
        )[0]
        self.assertIn("10#$now_hm", due)
        self.assertIn("1830", due)
        self.assertIn('actor.login == "github-actions[bot]"', due)
        self.assertIn('.head_sha == $sha', due)
        self.assertIn('--arg sha "$GITHUB_SHA"', due)
        self.assertIn("status != \"completed\" or .conclusion == \"success\"", due)
        self.assertIn("gh_workflow_retry fic-digest.yml --ref main", due)
        self.assertIn("gh_api_retry", due)
        self.assertIn("gh_workflow_retry", due)
        self.assertIn("for attempt in 1 2 3", due)

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
