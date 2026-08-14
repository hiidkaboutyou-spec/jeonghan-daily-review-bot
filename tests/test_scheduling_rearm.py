from __future__ import annotations

import io
import unittest
from contextlib import redirect_stdout
from pathlib import Path

from tools.daily_watchdog import run_watchdog

ROOT = Path(__file__).resolve().parents[1]


class FakeClient:
    def __init__(self, runs=None, *, lookup_error=None, dispatch_error=None, live_run_ids=None):
        self.runs = list(runs or [])
        self.lookup_error = lookup_error
        self.dispatch_error = dispatch_error
        self.live_run_ids = set(live_run_ids or [])
        self.lookup_calls = 0
        self.live_check_calls = []
        self.dispatch_calls = 0

    def list_daily_runs(self):
        self.lookup_calls += 1
        if self.lookup_error:
            raise self.lookup_error
        return list(self.runs)

    def run_executed_live_monitor(self, run_id):
        self.live_check_calls.append(run_id)
        return run_id in self.live_run_ids

    def dispatch_live(self):
        self.dispatch_calls += 1
        if self.dispatch_error:
            raise self.dispatch_error


def live_run(run_number, *, status="completed", conclusion="success", event="schedule", run_id=None):
    return {
        "id": run_id or (1000 + run_number),
        "run_number": run_number,
        "head_branch": "main",
        "event": event,
        "status": status,
        "conclusion": conclusion if status == "completed" else None,
    }


def run_case(client, **overrides):
    sleeps = []
    values = {
        "source_run_id": 500,
        "source_run_number": 1107,
        "source_event": "schedule",
        "source_conclusion": "success",
        "source_actor": "repo-owner",
        "interval_minutes": 12,
        "sleep_fn": sleeps.append,
    }
    values.update(overrides)
    output = io.StringIO()
    with redirect_stdout(output):
        code = run_watchdog(client, **values)
    return code, sleeps, output.getvalue()


class SchedulingRearmDecisionTests(unittest.TestCase):
    def test_normal_daily_completion_arms_watchdog(self):
        client = FakeClient([live_run(1107, run_id=500)])
        code, sleeps, output = run_case(client)
        self.assertEqual(code, 0)
        self.assertEqual(sleeps, [720])
        self.assertEqual(client.dispatch_calls, 1)
        self.assertIn("daily_watchdog: armed", output)

    def test_no_newer_daily_dispatches_exactly_one_recovery(self):
        client = FakeClient([])
        code, _, output = run_case(client)
        self.assertEqual(code, 0)
        self.assertEqual(client.dispatch_calls, 1)
        self.assertIn("successor_dispatched", output)

    def test_successful_recovery_source_can_arm_next_watchdog_cycle(self):
        recovery_id = 501
        client = FakeClient([], live_run_ids={recovery_id})
        code, sleeps, output = run_case(
            client,
            source_run_id=recovery_id,
            source_run_number=1108,
            source_event="workflow_dispatch",
            source_conclusion="success",
            source_actor="github-actions[bot]",
        )
        self.assertEqual(code, 0)
        self.assertEqual(sleeps, [720])
        self.assertEqual(client.dispatch_calls, 1)
        self.assertIn("successor_dispatched", output)

    def test_second_watchdog_sees_normal_cron_and_noops(self):
        recovery_id = 501
        client = FakeClient([live_run(1109)], live_run_ids={recovery_id})
        code, _, output = run_case(
            client,
            source_run_id=recovery_id,
            source_run_number=1108,
            source_event="workflow_dispatch",
            source_conclusion="success",
            source_actor="github-actions[bot]",
        )
        self.assertEqual(code, 0)
        self.assertEqual(client.dispatch_calls, 0)
        self.assertIn("successor_not_needed", output)

    def test_two_missing_cycles_create_two_sequential_recoveries(self):
        first = FakeClient([])
        code1, sleeps1, _ = run_case(first)
        recovery_id = 501
        second = FakeClient([], live_run_ids={recovery_id})
        code2, sleeps2, _ = run_case(
            second,
            source_run_id=recovery_id,
            source_run_number=1108,
            source_event="workflow_dispatch",
            source_conclusion="success",
            source_actor="github-actions[bot]",
        )
        self.assertEqual((code1, code2), (0, 0))
        self.assertEqual((sleeps1, sleeps2), ([720], [720]))
        self.assertEqual(first.dispatch_calls + second.dispatch_calls, 2)

    def test_active_daily_states_block_dispatch(self):
        for status in ("queued", "in_progress", "requested", "waiting", "pending"):
            with self.subTest(status=status):
                client = FakeClient([live_run(1108, status=status)])
                code, _, output = run_case(client)
                self.assertEqual(code, 0)
                self.assertEqual(client.dispatch_calls, 0)
                self.assertIn("newer_run_exists", output)

    def test_failed_automated_recovery_does_not_chain(self):
        client = FakeClient(live_run_ids={500})
        code, sleeps, output = run_case(
            client,
            source_event="workflow_dispatch",
            source_conclusion="failure",
            source_actor="github-actions[bot]",
        )
        self.assertEqual(code, 0)
        self.assertEqual(sleeps, [])
        self.assertEqual(client.dispatch_calls, 0)
        self.assertIn("failed_automated_recovery", output)

    def test_api_lookup_failure_fails_safe(self):
        client = FakeClient(lookup_error=RuntimeError("boom"))
        code, _, output = run_case(client)
        self.assertEqual(code, 1)
        self.assertEqual(client.dispatch_calls, 0)
        self.assertIn("lookup_failed", output)

    def test_dispatch_failure_is_single_attempt_and_no_loop(self):
        client = FakeClient(dispatch_error=RuntimeError("boom"))
        code, _, output = run_case(client)
        self.assertEqual(code, 1)
        self.assertEqual(client.dispatch_calls, 1)
        self.assertIn("dispatch_failed", output)


class SchedulingRearmWorkflowTests(unittest.TestCase):
    def test_recovery_daily_rearms_watchdog_not_daily(self):
        main = (ROOT / ".github" / "workflows" / "main.yml").read_text(encoding="utf-8")
        self.assertIn("Re-arm Daily watchdog after automated recovery", main)
        self.assertIn("gh workflow run daily-watchdog.yml", main)
        self.assertIn("github.actor == 'github-actions[bot]'", main)
        self.assertNotIn("gh workflow run main.yml --ref main -f mode=live", main)

    def test_watchdog_has_no_self_recursion(self):
        watchdog = (ROOT / ".github" / "workflows" / "daily-watchdog.yml").read_text(encoding="utf-8")
        tool = (ROOT / "tools" / "daily_watchdog.py").read_text(encoding="utf-8")
        self.assertNotIn("gh workflow run daily-watchdog.yml", watchdog)
        self.assertNotIn("daily-watchdog.yml/dispatches", tool)
        self.assertIn('MAIN_WORKFLOW_FILE = "main.yml"', tool)

    def test_workflow_run_ignores_automated_recovery_to_prevent_double_watchdog(self):
        watchdog = (ROOT / ".github" / "workflows" / "daily-watchdog.yml").read_text(encoding="utf-8")
        self.assertIn("!(github.event.workflow_run.event == 'workflow_dispatch'", watchdog)
        self.assertIn("github.event.workflow_run.actor.login == 'github-actions[bot]'", watchdog)
        self.assertIn("workflow_dispatch:", watchdog)

    def test_concurrency_remains_bounded(self):
        watchdog = (ROOT / ".github" / "workflows" / "daily-watchdog.yml").read_text(encoding="utf-8")
        main = (ROOT / ".github" / "workflows" / "main.yml").read_text(encoding="utf-8")
        self.assertIn("group: jeonghan-daily-watchdog-main", watchdog)
        self.assertIn("cancel-in-progress: true", watchdog)
        self.assertIn("jeonghan-daily-review-bot-", main)
        self.assertIn("'runtime'", main)

    def test_private_review_only_boundary_preserved(self):
        watchdog = (ROOT / ".github" / "workflows" / "daily-watchdog.yml").read_text(encoding="utf-8")
        settings = (ROOT / "config" / "settings.json").read_text(encoding="utf-8")
        self.assertIn('"review_only": true', settings)
        for forbidden in ("TELEGRAM_BOT_TOKEN", "TELEGRAM_REVIEW_CHAT_ID", "python -m app"):
            self.assertNotIn(forbidden, watchdog)

    def test_fanfic_workflow_is_unchanged_by_rearm_control_plane(self):
        fic = (ROOT / ".github" / "workflows" / "fic-digest.yml").read_text(encoding="utf-8")
        self.assertNotIn("Re-arm Daily watchdog", fic)
        self.assertNotIn("daily-watchdog.yml", fic)


if __name__ == "__main__":
    unittest.main()
