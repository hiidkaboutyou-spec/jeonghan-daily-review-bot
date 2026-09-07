import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from app import production_outcome_runtime as runtime
from app.production_outcome import OutcomeStatus, load_outcome


class ProductionOutcomeRuntimeTests(unittest.IsolatedAsyncioTestCase):
    async def test_concrete_application_override_records_provider_wide_failure(self):
        class ConcreteApplication:
            def __init__(self):
                self.settings = SimpleNamespace(
                    sources=[
                        {"handle": "source_one", "enabled": True},
                        {"handle": "source_two", "enabled": True},
                    ]
                )
                self.state = SimpleNamespace(
                    data={"last_auto_run": "2026-09-07T00:00:00+00:00"}
                )
                self.collector = SimpleNamespace(
                    # A not-due scan returns before the collector can populate
                    # errors; live preflight must still govern the outcome.
                    last_errors=[]
                )

            async def run_scheduled_scan(self):
                return None

            async def run(self):
                await self.run_scheduled_scan()

        runtime.install_application_hooks(ConcreteApplication)
        old_path = runtime._OUTCOME_PATH
        try:
            with tempfile.TemporaryDirectory() as tmp:
                runtime._OUTCOME_PATH = Path(tmp) / "outcome.json"
                runtime.start_run(run_id="concrete-app-test", trigger_event="test")
                with patch.dict("os.environ", {"X_PROVIDER_PREFLIGHT": "offline"}):
                    await ConcreteApplication().run()
                outcome = load_outcome(runtime._OUTCOME_PATH)
        finally:
            runtime._OUTCOME_PATH = old_path
            runtime._MODULE_BUILDER = None

        self.assertEqual(outcome.source_collection.active_source_count, 2)
        self.assertEqual(outcome.source_collection.failed_source_count, 2)
        self.assertFalse(outcome.source_collection.collection_complete)
        self.assertFalse(outcome.state.cursor_advanced)
        self.assertEqual(outcome.state.cursor_reason, "partial_window")
        self.assertEqual(outcome.outcome_status, OutcomeStatus.FAILED.value)


if __name__ == "__main__":
    unittest.main()
