from __future__ import annotations

import io
import unittest
from contextlib import redirect_stdout
from datetime import datetime, timezone
from pathlib import Path

from tools.daily_watchdog import load_due_interval_minutes, run_periodic_watchdog, run_watchdog

ROOT = Path(__file__).resolve().parents[1]
NOW = datetime(2026, 8, 14, 18, 0, tzinfo=timezone.utc)


class FakeClient:
    def __init__(self, runs=None, *, lookup_error=None, dispatch_error=None, live_run_ids=None, live_check_error=None):
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


def live_run(run_number, *, status="completed", conclusion="success", event="schedule", run_id=None, actor="hiidkaboutyou-spec", updated_at="2026-08-14T17:40:00Z"):
    return {
        "id": run_id or (1000 + run_number),
        "run_number": run_number,
        "head_branch": "main",
        "event": event,
        "display_title": "Jeonghan Daily Review Bot",
        "status": status,
        "conclusion": conclusion if status == "completed" else None,
        "actor": {"login": actor},
        "updated_at": updated_at,
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
        self.assertEqual((code, sleeps, client.dispatch_calls), (0, [720], 1))
        self.assertIn("successor_dispatched", output)

    def test_newer_queued_runtime_is_noop(self):
        client = FakeClient([live_run(1108, status="queued")])
        code, _, output = self.run_case(client)
        self.assertEqual((code, client.dispatch_calls), (0, 0))
        self.assertIn("successor_not_needed", output)

    def test_newer_running_runtime_is_noop(self):
        client = FakeClient([live_run(1108, status="in_progress")])
        code, _, output = self.run_case(client)
        self.assertEqual((code, client.dispatch_calls), (0, 0))
        self.assertIn("status=in_progress", output)

    def test_normally_arriving_cron_is_noop(self):
        client = FakeClient([live_run(1108)])
        code, _, output = self.run_case(client)
        self.assertEqual((code, client.dispatch_calls), (0, 0))
        self.assertIn("successor_not_needed", output)

    def test_completed_check_mode_dispatch_does_not_cover_or_arm_watchdog(self):
        check_id = 2000
        client = FakeClient([live_run(1108, event="workflow_dispatch", run_id=check_id)], live_run_ids=set())
        code, _, _ = self.run_case(client)
        self.assertEqual((code, client.dispatch_calls), (0, 1))
        guarded = FakeClient(live_run_ids=set())
        code, sleeps, output = self.run_case(guarded, source_event="workflow_dispatch")
        self.assertEqual((code, sleeps, guarded.dispatch_calls), (0, [], 0))
        self.assertIn("reason=source_not_live", output)

    def test_completed_live_dispatch_covers_watchdog(self):
        live_id = 2001
        client = FakeClient([live_run(1108, event="workflow_dispatch", run_id=live_id)], live_run_ids={live_id})
        code, _, output = self.run_case(client)
        self.assertEqual((code, client.dispatch_calls), (0, 0))
        self.assertIn("newer_run_exists", output)

    def test_lookup_failure_fails_safe_without_dispatch(self):
        client = FakeClient(lookup_error=RuntimeError("boom"))
        code, _, output = self.run_case(client)
        self.assertEqual((code, client.dispatch_calls), (1, 0))
        self.assertIn("lookup_failed", output)

    def test_dispatch_failure_is_one_attempt_and_visible(self):
        client = FakeClient(dispatch_error=RuntimeError("boom"))
        code, _, output = self.run_case(client)
        self.assertEqual((code, client.dispatch_calls), (1, 1))
        self.assertIn("dispatch_failed", output)

    def test_failed_automated_recovery_does_not_chain_forever(self):
        client = FakeClient(live_run_ids={500})
        code, sleeps, output = self.run_case(client, source_event="workflow_dispatch", source_conclusion="failure", source_actor="github-actions[bot]")
        self.assertEqual((code, sleeps, client.dispatch_calls), (0, [], 0))
        self.assertIn("failed_automated_recovery", output)

    def test_interval_is_read_from_production_settings(self):
        self.assertEqual(load_due_interval_minutes(ROOT / "config" / "settings.json"), 12)


class PeriodicWatchdogTests(unittest.TestCase):
    def run_periodic(self, client):
        output = io.StringIO()
        with redirect_stdout(output):
            code = run_periodic_watchdog(client, interval_minutes=12, now=NOW)
        return code, output.getvalue()

    def test_recovery_success_is_covered_then_next_cycle_can_recover_again(self):
        recovery = live_run(1108, event="workflow_dispatch", actor="github-actions[bot]", run_id=2108, updated_at="2026-08-14T17:55:00Z")
        client = FakeClient([recovery], live_run_ids={2108})
        code, output = self.run_periodic(client)
        self.assertEqual((code, client.dispatch_calls), (0, 0))
        self.assertIn("recent_daily", output)
        recovery["updated_at"] = "2026-08-14T17:40:00Z"
        code, output = self.run_periodic(client)
        self.assertEqual((code, client.dispatch_calls), (0, 1))
        self.assertIn("successor_dispatched", output)

    def test_cron_absent_two_cycles_yields_two_sequential_recoveries(self):
        first = FakeClient([live_run(1107, updated_at="2026-08-14T17:40:00Z")])
        self.run_periodic(first)
        self.assertEqual(first.dispatch_calls, 1)
        recovered = live_run(1108, event="workflow_dispatch", actor="github-actions[bot]", run_id=2108, updated_at="2026-08-14T17:40:00Z")
        second = FakeClient([recovered], live_run_ids={2108})
        self.run_periodic(second)
        self.assertEqual(second.dispatch_calls, 1)

    def test_second_cycle_sees_normal_cron_and_noops(self):
        client = FakeClient([live_run(1109, updated_at="2026-08-14T17:56:00Z")])
        code, output = self.run_periodic(client)
        self.assertEqual((code, client.dispatch_calls), (0, 0))
        self.assertIn("recent_daily", output)

    def test_active_daily_blocks_periodic_dispatch(self):
        for status in ("queued", "in_progress", "requested", "waiting", "pending"):
            with self.subTest(status=status):
                client = FakeClient([live_run(1109, status=status)])
                code, _ = self.run_periodic(client)
                self.assertEqual((code, client.dispatch_calls), (0, 0))

    def test_failed_automated_recovery_halts_periodic_chain(self):
        failed = live_run(1108, event="workflow_dispatch", actor="github-actions[bot]", conclusion="failure", updated_at="2026-08-14T17:40:00Z")
        client = FakeClient([failed])
        code, output = self.run_periodic(client)
        self.assertEqual((code, client.dispatch_calls), (0, 0))
        self.assertIn("failed_automated_recovery", output)

    def test_periodic_lookup_failure_fails_safe(self):
        client = FakeClient(lookup_error=RuntimeError("boom"))
        code, output = self.run_periodic(client)
        self.assertEqual((code, client.dispatch_calls), (1, 0))
        self.assertIn("periodic_run_check", output)

    def test_periodic_dispatch_failure_does_not_loop(self):
        client = FakeClient([live_run(1107)], dispatch_error=RuntimeError("boom"))
        code, output = self.run_periodic(client)
        self.assertEqual((code, client.dispatch_calls), (1, 1))
        self.assertIn("dispatch_failed", output)


class DailyWatchdogWorkflowTests(unittest.TestCase):
    def test_watchdog_has_workflow_run_and_independent_periodic_heartbeat(self):
        watchdog = (ROOT / ".github" / "workflows" / "daily-watchdog.yml").read_text(encoding="utf-8")
        main = (ROOT / ".github" / "workflows" / "main.yml").read_text(encoding="utf-8")
        self.assertIn("workflow_run:", watchdog)
        self.assertIn("schedule:", watchdog)
        self.assertIn('cron: "4,16,28,40,52 * * * *"', watchdog)
        self.assertIn("group: jeonghan-daily-watchdog-main", watchdog)
        self.assertIn("cancel-in-progress: true", watchdog)
        self.assertIn("timeout-minutes: 18", watchdog)
        self.assertNotIn("jeonghan-daily-review-bot-runtime", watchdog)
        self.assertIn("jeonghan-daily-review-bot-", main)
        self.assertIn("'runtime'", main)

    def test_no_direct_daily_or_watchdog_self_recursion(self):
        watchdog = (ROOT / ".github" / "workflows" / "daily-watchdog.yml").read_text(encoding="utf-8")
        main = (ROOT / ".github" / "workflows" / "main.yml").read_text(encoding="utf-8")
        self.assertNotIn("gh workflow run daily-watchdog.yml", watchdog)
        self.assertNotIn("gh workflow run main.yml", main)
        self.assertNotIn("Queue next live assistant pass", main)

    def test_watchdog_has_no_telegram_public_delivery_or_state_surface(self):
        watchdog = (ROOT / ".github" / "workflows" / "daily-watchdog.yml").read_text(encoding="utf-8")
        for forbidden in ("TELEGRAM_BOT_TOKEN", "TELEGRAM_REVIEW_CHAT_ID", "TELEGRAM_ADMIN_USER_ID", "python -m app", ".state/state.json", "private-review.sqlite3", "config/sources.json"):
            self.assertNotIn(forbidden, watchdog)
        settings = (ROOT / "config" / "settings.json").read_text(encoding="utf-8")
        self.assertIn('"review_only": true', settings)

    def test_dispatch_remains_live_only_and_fanfic_unchanged(self):
        tool = (ROOT / "tools" / "daily_watchdog.py").read_text(encoding="utf-8")
        main = (ROOT / ".github" / "workflows" / "main.yml").read_text(encoding="utf-8")
        fic = (ROOT / ".github" / "workflows" / "fic-digest.yml").read_text(encoding="utf-8")
        self.assertIn('{"ref": MAIN_BRANCH, "inputs": {"mode": "live"}}', tool)
        self.assertIn('LIVE_MONITOR_STEP = "Run one complete automatic monitor pass"', tool)
        self.assertIn("Queue due nightly fanfic digest", main)
        self.assertIn("Nightly", fic)


if __name__ == "__main__":
    unittest.main()
