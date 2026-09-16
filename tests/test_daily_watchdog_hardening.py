from __future__ import annotations

import importlib
import unittest
from pathlib import Path

from tools import daily_watchdog as watchdog
from tools import daily_watchdog_transport as transport

ROOT = Path(__file__).resolve().parents[1]


class DailyWatchdogHardeningCompatibilityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls._original_fetch = watchdog.GitHubActionsClient.fetch_latest_production_outcome
        cls.hardening = importlib.import_module("tools.daily_watchdog_hardening")

    @classmethod
    def tearDownClass(cls) -> None:
        watchdog.GitHubActionsClient.fetch_latest_production_outcome = cls._original_fetch

    def test_import_installs_canonical_transport_fetcher(self) -> None:
        self.assertIs(
            watchdog.GitHubActionsClient.fetch_latest_production_outcome,
            transport._fetch_latest_production_outcome,
        )

    def test_historical_symbols_alias_canonical_transport(self) -> None:
        self.assertIs(self.hardening._watchdog, watchdog)
        self.assertIs(self.hardening._transport, transport)
        self.assertIs(self.hardening._NoRedirect, transport._NoRedirect)
        self.assertIs(
            self.hardening._download_zip_without_cross_origin_auth,
            transport._download_zip_without_cross_origin_auth,
        )
        self.assertIs(
            self.hardening._fetch_latest_production_outcome,
            transport._fetch_latest_production_outcome,
        )
        self.assertIs(self.hardening.request, transport.request)
        self.assertIs(self.hardening.time, transport.time)

    def test_compatibility_import_reinstalls_transport_idempotently(self) -> None:
        watchdog.GitHubActionsClient.fetch_latest_production_outcome = self._original_fetch
        importlib.reload(self.hardening)
        self.assertIs(
            watchdog.GitHubActionsClient.fetch_latest_production_outcome,
            transport._fetch_latest_production_outcome,
        )

    def test_production_workflow_uses_semantic_entrypoint(self) -> None:
        workflow = (ROOT / ".github" / "workflows" / "daily-watchdog.yml").read_text(
            encoding="utf-8"
        )
        active_run_lines = [
            line.strip()
            for line in workflow.splitlines()
            if line.lstrip().startswith("run:")
        ]
        self.assertIn("run: python tools/daily_watchdog_runner.py", active_run_lines)
        self.assertNotIn("run: python tools/daily_watchdog_hardening.py", active_run_lines)


if __name__ == "__main__":
    unittest.main()
