from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from app.completeness_engine import CompletenessEngine, utc
from app.completeness_evidence import TraversalEvidence, active_evidence
from app.completeness_provider_proof import _record_structural_proof
from app.source_ledger import SourceLedgerStore


def _parse(value: str) -> datetime:
    return datetime.fromisoformat(str(value).replace("Z", "+00:00")).astimezone(
        timezone.utc
    )


def _fake_core(op: str, **fields):
    """Deterministic stand-in for Python state tests.

    The real Rust JSONL binary remains covered by test_completeness_engine.py and
    cargo tests. These acceptance tests exercise Python persistence/isolation
    without making a second implementation authoritative in production.
    """
    if op == "evaluate_completeness":
        proof = fields["proof"]
        complete = bool(
            not proof["failed"]
            and proof["pages"] > 0
            and proof["valid_response"]
            and proof["exhausted"]
            and not proof["resumed"]
            and not proof["lower_boundary"]
        )
        if complete:
            return "complete"
        return "partial" if proof["raw_count"] > 0 else "unproven"

    if op == "advance_cursor":
        state = fields["state"]
        candidate = fields["candidate"]
        if state["completeness"] != "complete":
            raise ValueError("only COMPLETE can advance")
        start = _parse(state["window_start"])
        end = _parse(state["window_end"])
        chosen = _parse(candidate)
        if not start <= chosen <= end:
            raise ValueError("candidate outside complete window")
        current = state.get("complete_through")
        if current and chosen < _parse(current):
            raise ValueError("cursor regression")
        return candidate

    raise ValueError(f"unsupported fake op: {op}")


def _tweet(identifier: str, when: datetime, source: str = "alpha"):
    return SimpleNamespace(
        id=int(identifier),
        id_str=identifier,
        user=SimpleNamespace(username=source),
        date=when,
    )


class Phase4AcceptanceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.ledger = SourceLedgerStore(Path(self.temp.name) / "state.sqlite3")
        self.engine = CompletenessEngine(self.ledger)
        self.start = "2026-09-05T00:00:00Z"
        self.end = "2026-09-05T01:00:00Z"
        self.core = patch("app.completeness_engine.core_request", side_effect=_fake_core)
        self.core.start()

    def tearDown(self) -> None:
        self.core.stop()
        self.ledger.close()
        self.temp.cleanup()

    def _run(self, source: str, evidence: TraversalEvidence, error: str = "", retained: int = 0):
        run_id = self.engine.plan([{"handle": source}], self.start, self.end)
        attempt = self.engine.start(run_id, source)
        for post_id in sorted(evidence.observation_ids):
            self.engine.link_observation(attempt, post_id)
        self.engine.finish(attempt, evidence, retained, error)
        return run_id, attempt, self.engine.report(run_id)["sources"][0]

    def test_verdict_matrix_proven_partial_and_unproven(self):
        proven = [
            (
                "pagination_exhausted",
                TraversalEvidence(pages=1, valid_response=True, exhausted=True),
                "validated_provider_exhaustion",
            ),
            (
                "lower_boundary_crossed",
                TraversalEvidence(
                    pages=2,
                    raw_count=2,
                    valid_response=True,
                    lower_boundary=True,
                    lower_boundary_proven=True,
                    expected_window_ids={"1", "2"},
                    observation_ids={"1", "2"},
                ),
                "validated_ordered_lower_boundary",
            ),
            (
                "proven_empty",
                TraversalEvidence(
                    pages=1,
                    raw_count=0,
                    valid_response=True,
                    exhausted=True,
                ),
                "validated_provider_exhaustion",
            ),
            (
                "multiple_pages",
                TraversalEvidence(
                    pages=4,
                    raw_count=8,
                    valid_response=True,
                    exhausted=True,
                ),
                "validated_provider_exhaustion",
            ),
        ]
        for name, evidence, proof_kind in proven:
            with self.subTest(name=name):
                _, _, row = self._run(name, evidence)
                self.assertEqual(row["status"], "complete")
                self.assertEqual(row["evidence"]["proof_kind"], proof_kind)

        partial_errors = (
            "NetworkError",
            "RateLimitError",
            "DuplicateCursor",
            "ConversionError",
        )
        for index, error in enumerate(partial_errors):
            with self.subTest(error=error):
                evidence = TraversalEvidence(
                    pages=2,
                    raw_count=3,
                    valid_response=True,
                    observation_ids={f"partial-{index}"},
                    provider_cursor="next",
                )
                _, _, row = self._run(f"partial-{index}", evidence, error)
                self.assertEqual(row["status"], "partial")
                self.assertEqual(
                    row["evidence"]["proof_kind"], "partial_provider_failure"
                )
                self.assertFalse(row["cursor_advanced"])

        unproven = [
            ("auth", TraversalEvidence(), "AuthenticationError"),
            ("provider_down", TraversalEvidence(), "ProviderUnavailable"),
            (
                "ambiguous",
                TraversalEvidence(pages=1, valid_response=True),
                "",
            ),
        ]
        for name, evidence, error in unproven:
            with self.subTest(name=name):
                _, _, row = self._run(name, evidence, error)
                self.assertEqual(row["status"], "unproven")
                self.assertFalse(row["cursor_advanced"])

    def test_critical_multi_source_partial_then_retry_is_isolated_and_idempotent(self):
        sources = [{"handle": value} for value in ("a", "b", "c")]
        run_id = self.engine.plan(sources, self.start, self.end)

        attempt_a = self.engine.start(run_id, "a")
        self.engine.finish(
            attempt_a,
            TraversalEvidence(pages=1, valid_response=True, exhausted=True),
            1,
        )

        attempt_b = self.engine.start(run_id, "b")
        for post_id in ("b1", "b2", "b2"):
            self.engine.link_observation(attempt_b, post_id)
        self.engine.finish(
            attempt_b,
            TraversalEvidence(
                pages=2,
                raw_count=2,
                valid_response=True,
                observation_ids={"b1", "b2"},
                provider_cursor="cursor-b",
            ),
            2,
            "NetworkError",
        )

        attempt_c = self.engine.start(run_id, "c")
        self.engine.finish(
            attempt_c,
            TraversalEvidence(pages=1, valid_response=True, exhausted=True),
            1,
        )

        first = self.engine.report(run_id)
        self.assertEqual(
            [row["status"] for row in first["sources"]],
            ["complete", "partial", "complete"],
        )
        cursors_before_retry = {
            row["source"]: dict(row)
            for row in self.ledger.conn.execute(
                "SELECT * FROM completeness_shadow_cursors ORDER BY source"
            )
        }
        self.assertEqual(set(cursors_before_retry), {"a", "c"})
        self.assertEqual(
            self.ledger.conn.execute(
                "SELECT count(*) FROM completeness_observations WHERE attempt_id=?",
                (attempt_b,),
            ).fetchone()[0],
            2,
        )

        retry_run = self.engine.plan([{"handle": "b"}], self.start, self.end)
        retry_attempt = self.engine.start(retry_run, "b")
        for post_id in ("b1", "b2", "b2", "b3"):
            self.engine.link_observation(retry_attempt, post_id)
        self.engine.finish(
            retry_attempt,
            TraversalEvidence(
                pages=3,
                raw_count=4,
                valid_response=True,
                exhausted=True,
                expected_window_ids={"b1", "b2", "b3"},
                observation_ids={"b1", "b2", "b3"},
            ),
            3,
        )

        retry_row = self.engine.report(retry_run)["sources"][0]
        self.assertEqual(retry_row["status"], "complete")
        self.assertEqual(retry_row["retry_count"], 1)
        self.assertEqual(retry_row["attempt_number"], 2)
        self.assertTrue(retry_row["cursor_advanced"])

        cursors_after_retry = {
            row["source"]: dict(row)
            for row in self.ledger.conn.execute(
                "SELECT * FROM completeness_shadow_cursors ORDER BY source"
            )
        }
        self.assertEqual(cursors_after_retry["a"], cursors_before_retry["a"])
        self.assertEqual(cursors_after_retry["c"], cursors_before_retry["c"])
        self.assertIn("b", cursors_after_retry)
        self.assertEqual(
            self.ledger.conn.execute(
                "SELECT count(*) FROM completeness_observations WHERE attempt_id=?",
                (attempt_b,),
            ).fetchone()[0],
            2,
        )
        self.assertEqual(
            self.ledger.conn.execute(
                "SELECT count(*) FROM completeness_observations WHERE attempt_id=?",
                (retry_attempt,),
            ).fetchone()[0],
            3,
        )

    def test_cursor_safety_monotonicity_equal_stale_and_adjacent_windows(self):
        partial_run, partial_attempt, _ = self._run(
            "partial",
            TraversalEvidence(pages=1, raw_count=1, valid_response=True),
            "NetworkError",
        )
        self.assertIsNone(
            self.ledger.conn.execute(
                "SELECT * FROM completeness_shadow_cursors WHERE source='partial'"
            ).fetchone()
        )

        self._run("unproven", TraversalEvidence(), "AuthenticationError")
        self.assertIsNone(
            self.ledger.conn.execute(
                "SELECT * FROM completeness_shadow_cursors WHERE source='unproven'"
            ).fetchone()
        )

        attempting_run = self.engine.plan(
            [{"handle": "attempting"}], self.start, self.end
        )
        self.engine.start(attempting_run, "attempting")
        self.assertIsNone(
            self.ledger.conn.execute(
                "SELECT * FROM completeness_shadow_cursors WHERE source='attempting'"
            ).fetchone()
        )

        first_run, first_attempt, first_row = self._run(
            "alpha",
            TraversalEvidence(pages=1, valid_response=True, exhausted=True),
        )
        self.assertTrue(first_row["cursor_advanced"])
        first_cursor = dict(
            self.ledger.conn.execute(
                "SELECT * FROM completeness_shadow_cursors WHERE source='alpha'"
            ).fetchone()
        )

        equal_run, equal_attempt, equal_row = self._run(
            "alpha",
            TraversalEvidence(pages=1, valid_response=True, exhausted=True),
        )
        self.assertEqual(equal_row["status"], "complete")
        self.assertFalse(equal_row["cursor_advanced"])
        self.assertEqual(
            dict(
                self.ledger.conn.execute(
                    "SELECT * FROM completeness_shadow_cursors WHERE source='alpha'"
                ).fetchone()
            ),
            first_cursor,
        )

        older_run = self.engine.plan(
            [{"handle": "alpha"}],
            "2026-09-04T22:00:00Z",
            "2026-09-04T23:00:00Z",
        )
        older_attempt = self.engine.start(older_run, "alpha")
        self.engine.finish(
            older_attempt,
            TraversalEvidence(pages=1, valid_response=True, exhausted=True),
            0,
        )
        self.assertEqual(
            dict(
                self.ledger.conn.execute(
                    "SELECT * FROM completeness_shadow_cursors WHERE source='alpha'"
                ).fetchone()
            ),
            first_cursor,
        )

        adjacent_run = self.engine.plan(
            [{"handle": "alpha"}],
            "2026-09-05T01:00:00Z",
            "2026-09-05T02:00:00Z",
        )
        adjacent_attempt = self.engine.start(adjacent_run, "alpha")
        self.engine.finish(
            adjacent_attempt,
            TraversalEvidence(pages=1, valid_response=True, exhausted=True),
            0,
        )
        adjacent_row = self.engine.report(adjacent_run)["sources"][0]
        self.assertTrue(adjacent_row["cursor_advanced"])
        self.assertEqual(
            adjacent_row["cursor_after"],
            "2026-09-05T02:00:00.000000+00:00",
        )

        self.assertEqual(
            self.engine.report(partial_run)["sources"][0]["attempt_id"],
            partial_attempt,
        )
        self.assertNotEqual(first_attempt, equal_attempt)

    def test_crash_and_filtering_independence_preserve_raw_evidence(self):
        categories = {
            "keyword-hidden",
            "media-only",
            "reply",
            "quote",
            "retweet",
            "not-relevant",
        }
        run_id = self.engine.plan([{"handle": "alpha"}], self.start, self.end)
        attempt = self.engine.start(run_id, "alpha")
        for post_id in categories:
            self.engine.link_observation(attempt, post_id)
        self.engine.finish(
            attempt,
            TraversalEvidence(
                pages=1,
                raw_count=len(categories),
                valid_response=True,
                exhausted=True,
                expected_window_ids=set(categories),
                observation_ids=set(categories),
            ),
            retained=0,
        )
        row = self.engine.report(run_id)["sources"][0]
        self.assertEqual(row["status"], "complete")
        self.assertEqual(row["retained_count"], 0)
        self.assertEqual(
            set(row["evidence"]["observation_ids"]),
            categories,
        )

        crash_run = self.engine.plan([{"handle": "beta"}], self.start, self.end)
        crash_attempt = self.engine.start(crash_run, "beta")
        self.engine.link_observation(crash_attempt, "persisted-before-crash")
        self.engine.checkpoint(
            crash_attempt,
            TraversalEvidence(
                pages=1,
                raw_count=1,
                valid_response=True,
                provider_cursor="next",
                observation_ids={"persisted-before-crash"},
            ),
        )
        self.engine.close_run(crash_run)
        crash_row = self.engine.report(crash_run)["sources"][0]
        self.assertEqual(crash_row["status"], "unproven")
        self.assertEqual(crash_row["evidence"]["proof_kind"], "unproven_interrupted")
        self.assertFalse(crash_row["cursor_advanced"])
        self.assertEqual(
            crash_row["evidence"]["observation_ids"],
            ["persisted-before-crash"],
        )

    def test_half_open_window_timezone_and_cross_page_order(self):
        start = datetime(2026, 9, 5, 0, 0, tzinfo=timezone.utc)
        end = start + timedelta(hours=1)
        evidence = TraversalEvidence(
            source_handle="alpha",
            window_start=utc(start),
            window_end=utc(end),
        )
        token = active_evidence.set(evidence)
        try:
            valid = _record_structural_proof(
                tweets=[
                    _tweet("4", end),
                    _tweet("3", start + timedelta(minutes=30)),
                    _tweet("2", start),
                    _tweet("1", start - timedelta(seconds=1)),
                ],
                ordered_ids=["4", "3", "2", "1"],
                pinned_ids=set(),
            )
        finally:
            active_evidence.reset(token)
        self.assertTrue(valid)
        self.assertEqual(evidence.expected_window_ids, {"2", "3"})
        self.assertTrue(evidence.lower_boundary_proven)
        self.assertEqual(
            utc("2026-09-05T04:30:00+04:30"),
            "2026-09-05T00:00:00.000000+00:00",
        )

        cross = TraversalEvidence(
            source_handle="alpha",
            window_start=utc(start),
            window_end=utc(end),
        )
        token = active_evidence.set(cross)
        try:
            first_valid = _record_structural_proof(
                tweets=[
                    _tweet("40", start + timedelta(minutes=50)),
                    _tweet("30", start + timedelta(minutes=40)),
                ],
                ordered_ids=["40", "30"],
                pinned_ids=set(),
            )
            second_valid = _record_structural_proof(
                tweets=[
                    _tweet("20", start + timedelta(minutes=45)),
                    _tweet("10", start + timedelta(minutes=30)),
                ],
                ordered_ids=["20", "10"],
                pinned_ids=set(),
            )
        finally:
            active_evidence.reset(token)
        self.assertTrue(first_valid)
        self.assertFalse(second_valid)
        self.assertFalse(cross.timeline_order_valid)

    def test_proof_record_explains_attempt_and_cursor_decision(self):
        run_id, attempt, row = self._run(
            "alpha",
            TraversalEvidence(
                pages=5,
                raw_count=17,
                valid_response=True,
                exhausted=True,
                newest_top_level_at="2026-09-05T00:58:00.000000+00:00",
                oldest_top_level_at="2026-09-05T00:01:00.000000+00:00",
            ),
            retained=9,
        )
        self.assertEqual(row["attempt_id"], attempt)
        self.assertEqual(row["attempt_number"], 1)
        self.assertEqual(row["evidence_version"], 1)
        self.assertTrue(row["started_at"])
        self.assertTrue(row["finished_at"])
        self.assertEqual(row["cursor_before"], "")
        self.assertEqual(
            row["cursor_candidate"], "2026-09-05T01:00:00.000000+00:00"
        )
        self.assertEqual(row["cursor_after"], row["cursor_candidate"])
        self.assertTrue(row["cursor_advanced"])
        self.assertEqual(
            row["evidence"]["proof_kind"], "validated_provider_exhaustion"
        )
        self.assertEqual(
            row["evidence"]["termination_reason"],
            "provider_pagination_exhausted",
        )
        self.assertEqual(row["evidence"]["pages_completed"], 5)
        self.assertEqual(row["evidence"]["raw_count"], 17)
        self.assertEqual(
            row["evidence"]["newest_top_level_at"],
            "2026-09-05T00:58:00.000000+00:00",
        )
        self.assertEqual(
            row["evidence"]["oldest_top_level_at"],
            "2026-09-05T00:01:00.000000+00:00",
        )

        retry_run, _, retry_row = self._run(
            "alpha",
            TraversalEvidence(pages=1, valid_response=True, exhausted=True),
        )
        self.assertEqual(retry_row["attempt_number"], 2)
        self.assertEqual(
            retry_row["cursor_before"],
            "2026-09-05T01:00:00.000000+00:00",
        )
        self.assertEqual(retry_row["cursor_after"], retry_row["cursor_before"])
        self.assertFalse(retry_row["cursor_advanced"])
        self.assertEqual(self.engine.report(run_id)["sources"][0], row)
        self.assertEqual(self.engine.report(retry_run)["sources"][0], retry_row)

    def test_empty_window_distinguishes_proven_empty_from_provider_error(self):
        _, _, proven = self._run(
            "quiet",
            TraversalEvidence(
                pages=1, raw_count=0, valid_response=True, exhausted=True
            ),
        )
        self.assertEqual(proven["status"], "complete")

        _, _, failed = self._run(
            "quiet-error",
            TraversalEvidence(),
            "ProviderUnavailable",
        )
        self.assertEqual(failed["status"], "unproven")
        self.assertFalse(failed["cursor_advanced"])


if __name__ == "__main__":
    unittest.main()
