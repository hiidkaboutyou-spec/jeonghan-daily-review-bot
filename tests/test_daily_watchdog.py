from __future__ import annotations

import io
import unittest
from contextlib import redirect_stdout
from pathlib import Path

from tools.daily_watchdog import load_due_interval_minutes, run_watchdog

ROOT = Path(__file__).resolve().parents[1]


class FakeClient:
    def __init__(
        self,
        runs=None,
        *,
        lookup_error=None,
        dispatch_error=None,
        live_run_ids=None,
        live_check_error=None,
    ):
        self.runs = list(runs or [])
        self.lookup_error = lookup_error
        self.dispatch_error = dispatch_error
        self.live_run_ids = set(live_run_ids or [])
        self.live_check_error = live_check_error
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
        if self.live_check_error:
            raise self.live_check_error
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
        "display_title": "Jeonghan Daily Review Bot",
        "status": status,
        "conclusion": conclusion if status == "completed" else None,
    }


class DailyWatchdogDecisionTests(unittest.TestCase):
    def run_case(self, client, **overrides):
        sleep_calls = []
        values = {
            "source_run_id": 500,
            "source_run_number": 1107,
            "source_event": "schedule",
            "source_conclusion": "success",
            "source_actor": "hiidkaboutyou-spec",
            "interval_minutes": 12,
            "sleep_fn": sleep_calls.append,
        }
        values.update(overrides)
        output = io.StringIO()
        with redirect_stdout(output):
            code = run_watchdog(client, **values)
        return code, sleep_calls, output.getvalue()

    def test_no_newer_runtime_dispatches_exactly_one_recovery(self):
        client = FakeClient([live_run(1107, run_id=500)])
        code, sleeps, output = self.run_case(client)
        self.assertEqual(code, 0)
        self.assertEqual(sleeps, [720])
        self.assertEqual(client.lookup_calls, 1)
        self.assertEqual(client.dispatch_calls, 1)
        self.assertIn("daily_watchdog: successor_dispatched", output)

    def test_newer_queued_runtime_is_noop(self):
        client = FakeClient([live_run(1108, status="queued")])
        code, _, output = self.run_case(client)
        self.assertEqual(code, 0)
        self.assertEqual(client.dispatch_calls, 0)
        self.assertIn("daily_watchdog: newer_run_exists", output)
        self.assertIn("daily_watchdog: successor_not_needed", output)

    def test_newer_running_runtime_is_noop(self):
        client = FakeClient([live_run(1108, status="in_progress")])
        code, _, output = self.run_case(client)
        self.assertEqual(code, 0)
        self.assertEqual(client.dispatch_calls, 0)
        self.assertIn("status=in_progress", output)

    def test_normally_arriving_cron_is_noop(self):
        client = FakeClient([live_run(1108, status="completed", conclusion="success")])
        code, _, output = self.run_case(client)
        self.assertEqual(code, 0)
        self.assertEqual(client.dispatch_calls, 0)
        self.assertIn("successor_not_needed", output)

    def test_completed_check_mode_dispatch_does_not_cover_or_arm_watchdog(self):
        check_id = 2000
        check_run = live_run(
            1108,
            event="workflow_dispatch",
            status="completed",
            conclusion="success",
            run_id=check_id,
        )
        client = FakeClient([check_run], live_run_ids=set())
        code, _, _ = self.run_case(client)
        self.assertEqual(code, 0)
        self.assertEqual(client.dispatch_calls, 1)
        self.assertEqual(client.live_check_calls, [check_id])

        guarded = FakeClient(live_run_ids=set())
        code, sleeps, output = self.run_case(
            guarded,
            source_event="workflow_dispatch",
        )
        self.assertEqual(code, 0)
        self.assertEqual(sleeps, [])
        self.assertEqual(guarded.lookup_calls, 0)
        self.assertEqual(guarded.dispatch_calls, 0)
        self.assertEqual(guarded.live_check_calls, [500])
        self.assertIn("reason=source_not_live", output)

    def test_completed_live_dispatch_covers_watchdog(self):
        live_id = 2001
        client = FakeClient(
            [live_run(1108, event="workflow_dispatch", run_id=live_id)],
            live_run_ids={live_id},
        )
        code, _, output = self.run_case(client)
        self.assertEqual(code, 0)
        self.assertEqual(client.dispatch_calls, 0)
        self.assertEqual(client.live_check_calls, [live_id])
        self.assertIn("newer_run_exists", output)

    def test_lookup_failure_fails_safe_without_dispatch(self):
        client = FakeClient(lookup_error=RuntimeError("boom"))
        code, _, output = self.run_case(client)
        self.assertEqual(code, 1)
        self.assertEqual(client.dispatch_calls, 0)
        self.assertIn("daily_watchdog: lookup_failed", output)

    def test_live_mode_lookup_failure_fails_safe_without_dispatch(self):
        client = FakeClient(live_check_error=RuntimeError("boom"))
        code, sleeps, output = self.run_case(client, source_event="workflow_dispatch")
        self.assertEqual(code, 1)
        self.assertEqual(sleeps, [])
        self.assertEqual(client.dispatch_calls, 0)
        self.assertIn("phase=source_live_check", output)

    def test_dispatch_failure_is_one_attempt_and_visible(self):
        client = FakeClient(dispatch_error=RuntimeError("boom"))
        code, _, output = self.run_case(client)
        self.assertEqual(code, 1)
        self.assertEqual(client.dispatch_calls, 1)
        self.assertIn("daily_watchdog: dispatch_failed", output)

    def test_failed_automated_recovery_does_not_chain_forever(self):
        client = FakeClient(live_run_ids={500})
        code, sleeps, output = self.run_case(
            client,
            source_event="workflow_dispatch",
            source_conclusion="failure",
            source_actor="github-actions[bot]",
        )
        self.assertEqual(code, 0)
        self.assertEqual(sleeps, [])
        self.assertEqual(client.lookup_calls, 0)
        self.assertEqual(client.dispatch_calls, 0)
        self.assertIn("reason=failed_automated_recovery", output)

    def test_interval_is_read_from_production_settings(self):
        self.assertEqual(load_due_interval_minutes(ROOT / "config" / "settings.json"), 12)


class DailyWatchdogWorkflowTests(unittest.TestCase):
    def test_workflow_run_watchdog_is_bounded_and_separate_from_runtime_concurrency(self):
        watchdog = (ROOT / ".github" / "workflows" / "daily-watchdog.yml").read_text(encoding="utf-8")
        main = (ROOT / ".github" / "workflows" / "main.yml").read_text(encoding="utf-8")
        self.assertIn("workflow_run:", watchdog)
        self.assertIn("Jeonghan Daily Review Bot", watchdog)
        self.assertIn("types:\n      - completed", watchdog)
        self.assertIn("group: jeonghan-daily-watchdog-main", watchdog)
        self.assertIn("cancel-in-progress: true", watchdog)
        self.assertIn("timeout-minutes: 18", watchdog)
        self.assertNotIn("jeonghan-daily-review-bot-runtime", watchdog)
        self.assertNotIn("while ", watchdog)
        self.assertIn("jeonghan-daily-review-bot-", main)
        self.assertIn("'runtime'", main)
        self.assertIn("cancel-in-progress: ${{", main)

    def test_watchdog_has_no_telegram_public_delivery_or_state_surface(self):
        watchdog = (ROOT / ".github" / "workflows" / "daily-watchdog.yml").read_text(encoding="utf-8")
        for forbidden in (
            "TELEGRAM_BOT_TOKEN",
            "TELEGRAM_REVIEW_CHAT_ID",
            "TELEGRAM_ADMIN_USER_ID",
            "python -m app",
            ".state/state.json",
            "private-review.sqlite3",
            "config/sources.json",
        ):
            self.assertNotIn(forbidden, watchdog)
        settings = (ROOT / "config" / "settings.json").read_text(encoding="utf-8")
        self.assertIn('"review_only": true', settings)

    def test_dispatch_is_live_only_and_main_keeps_direct_self_dispatch_disabled(self):
        tool = (ROOT / "tools" / "daily_watchdog.py").read_text(encoding="utf-8")
        main = (ROOT / ".github" / "workflows" / "main.yml").read_text(encoding="utf-8")
        self.assertIn('{"ref": MAIN_BRANCH, "inputs": {"mode": "live"}}', tool)
        self.assertIn('LIVE_MONITOR_STEP = "Run one complete automatic monitor pass"', tool)
        self.assertNotIn("gh_workflow_retry main.yml --ref main -f mode=live", main)
        self.assertNotIn("- name: Queue next live assistant pass", main)

    def test_bootstrap_is_covered_by_existing_main_push_paths(self):
        main = (ROOT / ".github" / "workflows" / "main.yml").read_text(encoding="utf-8")
        self.assertIn('- "tests/**"', main)
        self.assertIn('- "tools/**"', main)
        self.assertIn("(github.event_name == 'push' && github.ref == 'refs/heads/main')", main)


if __name__ == "__main__":
    unittest.main()
