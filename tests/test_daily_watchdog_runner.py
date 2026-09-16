from __future__ import annotations

import unittest
from pathlib import Path
from unittest.mock import patch

from tools import daily_watchdog as watchdog
from tools import daily_watchdog_runner as runner

ROOT = Path(__file__).resolve().parents[1]


class DailyWatchdogRunnerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.original_fetch = watchdog.GitHubActionsClient.fetch_latest_production_outcome

    def tearDown(self) -> None:
        watchdog.GitHubActionsClient.fetch_latest_production_outcome = self.original_fetch

    def test_importing_runner_alone_is_side_effect_free(self) -> None:
        self.assertIs(
            watchdog.GitHubActionsClient.fetch_latest_production_outcome,
            self.original_fetch,
        )

    def test_runner_installs_hardening_before_delegating(self) -> None:
        from tools import daily_watchdog_hardening as hardening

        watchdog.GitHubActionsClient.fetch_latest_production_outcome = self.original_fetch
        with patch.object(watchdog, "main", return_value=17) as main:
            self.assertEqual(runner.main(), 17)
            self.assertIs(
                watchdog.GitHubActionsClient.fetch_latest_production_outcome,
                hardening._fetch_latest_production_outcome,
            )
        main.assert_called_once_with()

    def test_workflow_uses_semantic_runner_as_active_command(self) -> None:
        workflow = (ROOT / ".github" / "workflows" / "daily-watchdog.yml").read_text(
            encoding="utf-8"
        )
        active_run_lines = [
            line.strip()
            for line in workflow.splitlines()
            if line.lstrip().startswith("run:")
        ]
        self.assertIn("run: python tools/daily_watchdog_runner.py", active_run_lines)
        self.assertNotIn(
            "run: python tools/daily_watchdog_hardening.py",
            active_run_lines,
        )


if __name__ == "__main__":
    unittest.main()
