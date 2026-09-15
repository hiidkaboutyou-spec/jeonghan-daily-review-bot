from __future__ import annotations

import asyncio
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from app import live_recovery_hardening as hardening


class _OutcomeBuilder:
    def __init__(self) -> None:
        self.outcome = SimpleNamespace(
            source_collection=SimpleNamespace(
                attempted_source_count=99,
                complete_source_count=99,
                partial_source_count=99,
                failed_source_count=99,
                failed_source_handles=["stale"],
                failed_source_reasons=["stale"],
                collection_complete=True,
            )
        )
        self.attempts: list[tuple[str, bool, str | None]] = []
        self.fallback_count: int | None = None
        self.recovery: dict | None = None

    def record_source_attempt(self, handle: str, *, complete: bool, error: str | None = None) -> None:
        self.attempts.append((handle, complete, error))

    def set_fallback_source_count(self, count: int) -> None:
        self.fallback_count = count

    def set_recovery(self, **kwargs) -> None:
        self.recovery = kwargs


class LiveRecoveryHardeningTests(unittest.TestCase):
    def setUp(self) -> None:
        self.original_provider_installed = hardening._PROVIDER_INSTALLED
        self.original_classification_installed = hardening._CLASSIFICATION_INSTALLED
        self.addCleanup(self._restore_flags)

    def _restore_flags(self) -> None:
        hardening._PROVIDER_INSTALLED = self.original_provider_installed
        hardening._CLASSIFICATION_INSTALLED = self.original_classification_installed

    def test_incomplete_collection_with_held_cursor_requires_recovery(self) -> None:
        def base_classify(_outcome):
            return hardening._outcome.OutcomeStatus.HEALTHY.value, []

        outcome = SimpleNamespace(
            source_collection=SimpleNamespace(
                active_source_count=4,
                attempted_source_count=2,
                partial_source_count=1,
                failed_source_count=0,
                collection_complete=False,
            ),
            state=SimpleNamespace(cursor_advanced=False),
        )

        hardening._CLASSIFICATION_INSTALLED = False
        with patch.object(hardening._outcome, "classify_outcome", new=base_classify):
            hardening._install_outcome_classification()
            status, reasons = hardening._outcome.classify_outcome(outcome)

        self.assertEqual(status, hardening._outcome.OutcomeStatus.RECOVERY_REQUIRED.value)
        self.assertIn("incomplete_collection_cursor_held", reasons)

    def test_reconcile_uses_actual_degraded_batch_instead_of_inferred_all_sources(self) -> None:
        builder = _OutcomeBuilder()
        collector = SimpleNamespace(
            _hani_degraded_attempted_sources=["alpha", "beta"],
            _hani_degraded_failed_sources={"beta": "public providers failed"},
        )
        application = SimpleNamespace(collector=collector)

        with patch.dict("os.environ", {"X_PROVIDER_PREFLIGHT": "degraded"}, clear=False), patch.object(
            hardening._outcome_runtime, "_get_builder", return_value=builder
        ):
            hardening._reconcile_degraded_outcome(application)

        sc = builder.outcome.source_collection
        self.assertEqual(sc.attempted_source_count, 0)
        self.assertEqual(sc.complete_source_count, 0)
        self.assertEqual(sc.partial_source_count, 0)
        self.assertEqual(sc.failed_source_count, 0)
        self.assertEqual(sc.failed_source_handles, [])
        self.assertEqual(sc.failed_source_reasons, [])
        self.assertFalse(sc.collection_complete)
        self.assertEqual(
            builder.attempts,
            [("alpha", False, None), ("beta", False, "public providers failed")],
        )
        self.assertEqual(builder.fallback_count, 2)
        self.assertEqual(
            builder.recovery,
            {
                "required": True,
                "reason": "x_provider_degraded_partial_collection",
                "dispatch_recommended": False,
            },
        )

    def test_public_provider_fallback_uses_fxtwitter_only_after_syndication_failure(self) -> None:
        def syndication_failure(*_args, **_kwargs):
            raise hardening._syndication.SyndicationError("offline")

        async def degraded_window(*_args, **_kwargs):
            return []

        recovered = SimpleNamespace(updates=["update"], raw_seen=3)
        hardening._PROVIDER_INSTALLED = False
        with patch.object(
            hardening._provider_recovery,
            "collect_syndication_timeline",
            new=syndication_failure,
        ), patch.object(
            hardening._provider_recovery,
            "collect_degraded_window",
            new=degraded_window,
        ), patch.object(
            hardening,
            "collect_fxtwitter_timeline",
            return_value=recovered,
        ) as fx:
            hardening._install_public_provider_fallback()
            result = hardening._provider_recovery.collect_syndication_timeline(
                "source",
                SimpleNamespace(),
                SimpleNamespace(),
                include_replies=True,
            )

        self.assertEqual(result.updates, ["update"])
        self.assertEqual(result.raw_seen, 3)
        fx.assert_called_once()
        self.assertEqual(fx.call_args.kwargs["max_pages"], 3)

    def test_install_wraps_final_scan_once_and_reconciles_after_original_scan(self) -> None:
        events: list[str] = []

        class Application:
            async def run_scheduled_scan(self):
                events.append("original")
                return "done"

        reconcile = Mock(side_effect=lambda _app: events.append("reconcile"))
        with patch.object(hardening, "_install_public_provider_fallback"), patch.object(
            hardening, "_install_outcome_classification"
        ), patch.object(hardening, "_reconcile_degraded_outcome", new=reconcile):
            hardening.install(Application)
            first_wrapper = Application.run_scheduled_scan
            hardening.install(Application)
            self.assertIs(Application.run_scheduled_scan, first_wrapper)
            result = asyncio.run(Application().run_scheduled_scan())

        self.assertEqual(result, "done")
        self.assertEqual(events, ["original", "reconcile"])
        self.assertTrue(Application._hani_live_recovery_hardening)
        reconcile.assert_called_once()


if __name__ == "__main__":
    unittest.main()
