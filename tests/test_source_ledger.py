from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from app.source_ledger import (
    SourceLedgerStore,
    SourceWindowResult,
    SourceWindowStatus,
)


def _finish(store, source, start, end, status, **kwargs):
    store.finish(
        SourceWindowResult(
            source_handle=source,
            window_start=start,
            window_end=end,
            status=status,
            **kwargs,
        )
    )


class SourceLedgerRegressionTests(unittest.TestCase):
    """Phase 2 regressions run under the repository's canonical unittest command."""

    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.store = SourceLedgerStore(Path(self.temp.name) / "state.sqlite3")
        self.start = "2026-09-04T10:00:00+00:00"
        self.end = "2026-09-04T11:00:00+00:00"

    def tearDown(self) -> None:
        self.store.close()
        self.temp.cleanup()

    def test_complete_advances_only_its_source_cursor(self):
        self.store.start_attempt(
            source_handle="alpha",
            window_start=self.start,
            window_end=self.end,
            attempt_id="a1",
        )
        self.store.start_attempt(
            source_handle="beta",
            window_start=self.start,
            window_end=self.end,
            attempt_id="b1",
        )
        _finish(
            self.store,
            "alpha",
            self.start,
            self.end,
            SourceWindowStatus.COMPLETE,
            retained_count=3,
        )
        _finish(
            self.store,
            "beta",
            self.start,
            self.end,
            SourceWindowStatus.UNPROVEN,
            error_class="NetworkError",
        )

        self.assertEqual(self.store.cursor("alpha")["complete_through"], self.end)
        self.assertEqual(self.store.cursor("alpha")["last_status"], "complete")
        self.assertEqual(self.store.cursor("beta")["complete_through"], "")
        self.assertEqual(self.store.cursor("beta")["last_status"], "unproven")

    def test_partial_never_advances_existing_complete_cursor(self):
        s1 = "2026-09-04T09:00:00+00:00"
        e1 = "2026-09-04T10:00:00+00:00"
        s2 = e1
        e2 = "2026-09-04T11:00:00+00:00"
        self.store.start_attempt(
            source_handle="alpha", window_start=s1, window_end=e1
        )
        _finish(self.store, "alpha", s1, e1, SourceWindowStatus.COMPLETE)
        self.store.start_attempt(
            source_handle="alpha", window_start=s2, window_end=e2
        )
        _finish(
            self.store,
            "alpha",
            s2,
            e2,
            SourceWindowStatus.PARTIAL,
            error_class="XCompletenessError",
        )

        cursor = self.store.cursor("alpha")
        self.assertEqual(cursor["complete_through"], e1)
        self.assertEqual(cursor["last_status"], "partial")
        self.assertEqual(cursor["last_error_class"], "XCompletenessError")

    def test_retry_count_is_scoped_to_same_source_window(self):
        self.store.start_attempt(
            source_handle="alpha",
            window_start=self.start,
            window_end=self.end,
            attempt_id="a1",
        )
        _finish(
            self.store,
            "alpha",
            self.start,
            self.end,
            SourceWindowStatus.UNPROVEN,
        )
        self.store.start_attempt(
            source_handle="alpha",
            window_start=self.start,
            window_end=self.end,
            attempt_id="a2",
        )
        _finish(
            self.store,
            "alpha",
            self.start,
            self.end,
            SourceWindowStatus.COMPLETE,
        )

        row = self.store.window("alpha", self.start, self.end)
        self.assertEqual(row["attempt_count"], 2)
        self.assertEqual(row["retry_count"], 1)
        self.assertEqual(self.store.cursor("alpha")["complete_through"], self.end)

    def test_complete_cursor_is_monotonic(self):
        newer_start = "2026-09-04T10:00:00+00:00"
        newer_end = "2026-09-04T11:00:00+00:00"
        older_start = "2026-09-04T08:00:00+00:00"
        older_end = "2026-09-04T09:00:00+00:00"
        for start, end in (
            (newer_start, newer_end),
            (older_start, older_end),
        ):
            self.store.start_attempt(
                source_handle="alpha", window_start=start, window_end=end
            )
            _finish(
                self.store,
                "alpha",
                start,
                end,
                SourceWindowStatus.COMPLETE,
            )

        self.assertEqual(
            self.store.cursor("alpha")["complete_through"], newer_end
        )

    def test_complete_cursor_metadata_does_not_regress_with_older_complete(self):
        newer_start = "2026-09-04T10:00:00+00:00"
        newer_end = "2026-09-04T11:00:00+00:00"
        older_start = "2026-09-04T08:00:00+00:00"
        older_end = "2026-09-04T09:00:00+00:00"

        self.store.start_attempt(
            source_handle="alpha",
            window_start=newer_start,
            window_end=newer_end,
        )
        _finish(
            self.store,
            "alpha",
            newer_start,
            newer_end,
            SourceWindowStatus.COMPLETE,
            provider_cursor="cursor-new",
        )
        self.store.start_attempt(
            source_handle="alpha",
            window_start=older_start,
            window_end=older_end,
        )
        _finish(
            self.store,
            "alpha",
            older_start,
            older_end,
            SourceWindowStatus.COMPLETE,
            provider_cursor="cursor-old",
        )

        cursor = self.store.cursor("alpha")
        self.assertEqual(cursor["complete_through"], newer_end)
        self.assertEqual(cursor["provider_cursor"], "cursor-new")
        self.assertEqual(cursor["last_complete_window_start"], newer_start)
        self.assertEqual(cursor["last_complete_window_end"], newer_end)

    def test_counts_and_proof_are_preserved(self):
        self.store.start_attempt(
            source_handle="Alpha",
            window_start=self.start,
            window_end=self.end,
            attempt_id="x",
        )
        _finish(
            self.store,
            "Alpha",
            self.start,
            self.end,
            SourceWindowStatus.COMPLETE,
            raw_observation_count=9,
            retained_count=4,
            proof_kind="timeline_exhausted_or_lower_boundary_crossed",
        )
        row = self.store.window("alpha", self.start, self.end)
        self.assertEqual(row["raw_observation_count"], 9)
        self.assertEqual(row["retained_count"], 4)
        self.assertEqual(
            row["proof_kind"],
            "timeline_exhausted_or_lower_boundary_crossed",
        )

    def test_source_statuses_are_separate_and_sorted(self):
        for source in ("beta", "alpha"):
            self.store.start_attempt(
                source_handle=source,
                window_start=self.start,
                window_end=self.end,
            )
        self.assertEqual(
            [row["source_handle"] for row in self.store.source_statuses()],
            ["alpha", "beta"],
        )

    def test_ledger_attempt_identity_is_nonempty_and_unique(self):
        from app.source_ledger_runtime import _new_ledger_attempt_id

        first = _new_ledger_attempt_id()
        second = _new_ledger_attempt_id()
        self.assertTrue(first)
        self.assertTrue(second)
        self.assertNotEqual(first, second)
        self.assertLessEqual(len(first), 40)
        self.assertLessEqual(len(second), 40)

    def test_runtime_hook_is_installed_after_recovery_layer(self):
        from app.x_completeness import CompleteWindowXCollector

        self.assertIs(
            CompleteWindowXCollector.__dict__.get(
                "_source_ledger_installed", False
            ),
            True,
        )


if __name__ == "__main__":
    unittest.main()
