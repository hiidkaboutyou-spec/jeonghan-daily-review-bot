from __future__ import annotations

import asyncio
import os
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from app.completeness_engine import CompletenessEngine, core_request, proof_inputs
from app.completeness_evidence import TraversalEvidence, active_evidence
from app.source_ledger import SourceLedgerStore


class CompletenessEngineTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.ledger = SourceLedgerStore(Path(self.temp.name) / "state.sqlite3")
        self.engine = CompletenessEngine(self.ledger)
        self.start = "2026-09-05T00:00:00Z"
        self.end = "2026-09-05T01:00:00Z"
        self.binary = Path(__file__).resolve().parents[1] / "target/debug/jeonghan-editorial-core"

    def tearDown(self):
        self.ledger.close()
        self.temp.cleanup()

    def plan(self, sources=("alpha", "beta"), start=None, end=None):
        return self.engine.plan([{"handle": source} for source in sources], start or self.start, end or self.end)

    def test_unattempted_sources_and_empty_configuration_are_not_healthy(self):
        run = self.plan()
        self.engine.close_run(run)
        report = self.engine.report(run)
        self.assertEqual(report["configured"], 2)
        self.assertEqual(report["attempted"], 0)
        self.assertFalse(report["healthy"])
        self.assertTrue(all(row["error_class"] == "NotAttempted" for row in report["sources"]))
        self.assertFalse(self.engine.report(self.plan(()))["healthy"])

    def test_missing_core_fails_closed_and_retry_keeps_old_evidence(self):
        run = self.plan(("alpha",))
        attempt = self.engine.start(run, "alpha")
        with patch.dict(os.environ, {"EDITORIAL_CORE_BINARY": "/nonexistent/editorial-core"}):
            self.engine.finish(attempt, TraversalEvidence(), 0)
        row = self.engine.report(run)["sources"][0]
        self.assertEqual(row["status"], "unproven")
        self.assertEqual(row["error_class"], "EditorialCoreUnavailable")
        retry = self.plan(("alpha",))
        self.engine.start(retry, "alpha")
        self.assertEqual(self.engine.report(retry)["sources"][0]["retry_count"], 1)
        self.assertEqual(self.engine.report(run)["sources"][0], row)

    def test_interrupted_attempt_recovery_preserves_other_sources(self):
        run = self.plan()
        self.engine.start(run, "alpha")
        self.engine.close_run(run)
        rows = self.engine.report(run)["sources"]
        self.assertEqual([row["status"] for row in rows], ["unproven", "unproven"])
        self.assertEqual([row["error_class"] for row in rows], ["Interrupted", "NotAttempted"])
        reopened = CompletenessEngine(self.ledger)
        self.assertEqual(reopened.report(run), self.engine.report(run))

    def test_core_proof_decisions_and_atomic_cursor(self):
        if not self.binary.exists():
            self.skipTest("Rust binary integration runs in Rust CI after cargo build")
        with patch.dict(os.environ, {"EDITORIAL_CORE_BINARY": str(self.binary)}):
            for evidence, expected in [
                (TraversalEvidence(), "unproven"),
                (TraversalEvidence(pages=1, valid_response=True), "unproven"),
                (TraversalEvidence(pages=1, raw_count=2), "partial"),
                (TraversalEvidence(pages=1, valid_response=True, exhausted=True, resumed=True), "unproven"),
                (TraversalEvidence(pages=1, valid_response=True, exhausted=True), "complete"),
            ]:
                run = self.plan(("alpha",))
                attempt = self.engine.start(run, "alpha")
                self.engine.finish(attempt, evidence, 0)
                self.assertEqual(self.engine.report(run)["sources"][0]["status"], expected)
            cursor = dict(self.ledger.conn.execute("SELECT * FROM completeness_shadow_cursors").fetchone())
            self.assertEqual(cursor["complete_through"], "2026-09-05T01:00:00.000000+00:00")
            self.engine.finish(attempt, TraversalEvidence(), 0, "LateFailure")
            self.assertEqual(self.engine.report(run)["sources"][0]["status"], "complete")
            self.assertEqual(dict(self.ledger.conn.execute("SELECT * FROM completeness_shadow_cursors").fetchone()), cursor)

    def test_structural_lower_boundary_requires_observation_coverage(self):
        evidence = TraversalEvidence(
            pages=2,
            raw_count=5,
            valid_response=True,
            lower_boundary=True,
            lower_boundary_proven=True,
            expected_window_ids={"1", "2"},
            observation_ids={"1", "2"},
        )
        proof, detail = proof_inputs(evidence)
        self.assertTrue(proof["exhausted"])
        self.assertFalse(proof["lower_boundary"])
        self.assertTrue(detail["expected_coverage_complete"])
        self.assertEqual(detail["missing_expected_ids"], [])

        evidence.observation_ids.remove("2")
        proof, detail = proof_inputs(evidence)
        self.assertFalse(proof["exhausted"])
        self.assertTrue(proof["lower_boundary"])
        self.assertFalse(detail["expected_coverage_complete"])
        self.assertEqual(detail["missing_expected_ids"], ["2"])

    def test_provider_structure_ignores_pinned_boundary_and_proves_ordered_normal_boundary(self):
        from app.phase3_recovery import _provider_page
        from app.completeness_provider_proof import _timeline_structure

        payload = {
            "data": {
                "user": {
                    "result": {
                        "timeline": {
                            "timeline": {
                                "instructions": [
                                    {"type": "TimelinePinEntry", "entry": {"entryId": "tweet-10"}},
                                    {
                                        "type": "TimelineAddEntries",
                                        "entries": [
                                            {"entryId": "tweet-30"},
                                            {"entryId": "tweet-20"},
                                            {"entryId": "cursor-bottom-x"},
                                        ],
                                    },
                                ]
                            }
                        }
                    }
                }
            }
        }

        async def stream(*args, **kwargs):
            yield SimpleNamespace(json=lambda: payload)

        user = SimpleNamespace(username="alpha")
        tweets = [
            SimpleNamespace(id=10, id_str="10", user=user, date=datetime(2020, 1, 1, tzinfo=timezone.utc)),
            SimpleNamespace(id=30, id_str="30", user=user, date=datetime(2026, 9, 5, 0, 30, tzinfo=timezone.utc)),
            SimpleNamespace(id=20, id_str="20", user=user, date=datetime(2026, 9, 4, 23, 0, tzinfo=timezone.utc)),
        ]
        api = SimpleNamespace(user_tweets_raw=stream, _get_cursor=lambda *args: "next")
        evidence = TraversalEvidence(source_handle="alpha", window_start=self.start, window_end=self.end)
        token = active_evidence.set(evidence)
        try:
            with patch("twscrape.models.parse_tweets", return_value=tweets):
                page = asyncio.run(_provider_page(api, 1, include_replies=False, cursor=None))
        finally:
            active_evidence.reset(token)

        _, ordered_ids, pinned_ids, _ = _timeline_structure(payload)
        self.assertTrue(page.valid_response)
        self.assertFalse(page.exhausted)
        self.assertEqual(ordered_ids, ["30", "20"])
        self.assertEqual(pinned_ids, {"10"})
        self.assertTrue(evidence.lower_boundary_proven)
        self.assertEqual(evidence.expected_window_ids, {"30"})

    def test_non_monotonic_top_level_page_cannot_prove_boundary(self):
        from app.phase3_recovery import _provider_page

        payload = {"data": {"timeline": {"instructions": [{
            "type": "TimelineAddEntries",
            "entries": [{"entryId": "tweet-20"}, {"entryId": "tweet-30"}],
        }]}}}

        async def stream(*args, **kwargs):
            yield SimpleNamespace(json=lambda: payload)

        user = SimpleNamespace(username="alpha")
        tweets = [
            SimpleNamespace(id=20, id_str="20", user=user, date=datetime(2026, 9, 4, 23, 0, tzinfo=timezone.utc)),
            SimpleNamespace(id=30, id_str="30", user=user, date=datetime(2026, 9, 5, 0, 30, tzinfo=timezone.utc)),
        ]
        api = SimpleNamespace(user_tweets_raw=stream, _get_cursor=lambda *args: "next")
        evidence = TraversalEvidence(source_handle="alpha", window_start=self.start, window_end=self.end)
        token = active_evidence.set(evidence)
        try:
            with patch("twscrape.models.parse_tweets", return_value=tweets):
                page = asyncio.run(_provider_page(api, 1, include_replies=False, cursor=None))
        finally:
            active_evidence.reset(token)
        self.assertFalse(page.valid_response)
        self.assertFalse(evidence.lower_boundary_proven)
        self.assertFalse(evidence.timeline_order_valid)

    def test_old_result_and_gap_do_not_advance_cursor(self):
        if not self.binary.exists():
            self.skipTest("Rust binary integration runs in Rust CI after cargo build")
        proof = TraversalEvidence(pages=1, valid_response=True, exhausted=True)
        with patch.dict(os.environ, {"EDITORIAL_CORE_BINARY": str(self.binary)}):
            old = self.plan(("alpha",))
            old_id = self.engine.start(old, "alpha")
            new = self.plan(("alpha",))
            self.engine.finish(self.engine.start(new, "alpha"), proof, 0)
            before = dict(self.ledger.conn.execute("SELECT * FROM completeness_shadow_cursors").fetchone())
            self.engine.finish(old_id, proof, 0)
            self.assertEqual(dict(self.ledger.conn.execute("SELECT * FROM completeness_shadow_cursors").fetchone()), before)
            gap = self.plan(("alpha",), "2026-09-05T02:00:00Z", "2026-09-05T03:00:00Z")
            self.engine.finish(self.engine.start(gap, "alpha"), proof, 0)
            self.assertTrue(self.engine.report(gap)["sources"][0]["evidence"]["cursor_gap"])
            self.assertEqual(dict(self.ledger.conn.execute("SELECT * FROM completeness_shadow_cursors").fetchone()), before)

    def test_transaction_rolls_back_cursor_when_attempt_write_fails(self):
        if not self.binary.exists():
            self.skipTest("Rust binary integration runs in Rust CI after cargo build")
        run = self.plan(("alpha",))
        attempt = self.engine.start(run, "alpha")
        self.ledger.conn.execute("CREATE TRIGGER fail_finish BEFORE UPDATE OF finalized ON completeness_attempts BEGIN SELECT RAISE(ABORT, 'test rollback'); END")
        with patch.dict(os.environ, {"EDITORIAL_CORE_BINARY": str(self.binary)}):
            with self.assertRaises(Exception):
                self.engine.finish(attempt, TraversalEvidence(pages=1, valid_response=True, exhausted=True), 0)
        self.assertIsNone(self.ledger.conn.execute("SELECT * FROM completeness_shadow_cursors").fetchone())
        self.assertEqual(self.engine.report(run)["sources"][0]["status"], "attempting")

    def test_runtime_empty_success_requires_valid_raw_terminal_page(self):
        from app.x_client import XCollector
        from app.phase3_recovery import _ProviderPage
        if not self.binary.exists():
            self.skipTest("Rust binary integration runs in Rust CI after cargo build")
        collector = XCollector({}, [{"handle": "alpha", "enabled": True}], [])
        collector.source_ledger_store = self.ledger
        collector._get_api = AsyncMock(return_value=SimpleNamespace(
            user_tweets_raw=lambda: None,
            user_by_login=AsyncMock(return_value=SimpleNamespace(id=1)),
        ))
        with patch.dict(os.environ, {"EDITORIAL_CORE_BINARY": str(self.binary)}):
            for valid, expected in ((False, "unproven"), (True, "complete")):
                with patch("app.phase3_recovery._fetch_page_with_retry", new=AsyncMock(return_value=_ProviderPage([], None, True, valid))):
                    result = asyncio.run(collector._collect_source_timeline(
                        "alpha", self.start, self.end, limit=20, include_replies=False))
                self.assertEqual(result, [])
                self.assertEqual(collector.last_completeness_report["sources"][0]["status"], expected)
                self.assertEqual(collector.last_completeness_report["sources"][0]["legacy_status"], "complete")

    def test_page_and_observation_evidence_survive_interruption(self):
        run = self.plan(("alpha",))
        attempt = self.engine.start(run, "alpha")
        evidence = TraversalEvidence(
            pages=2,
            raw_count=4,
            provider_cursor="next",
            expected_window_ids={"post-1"},
            lower_boundary_proven=True,
        )
        self.engine.checkpoint(attempt, evidence)
        self.engine.link_observation(attempt, "post-1")
        self.engine.link_observation(attempt, "post-1")
        self.engine.close_run(run)
        row = CompletenessEngine(self.ledger).report(run)["sources"][0]
        self.assertEqual(row["status"], "unproven")
        self.assertEqual(row["evidence"]["pages"], 2)
        self.assertEqual(row["evidence"]["expected_window_ids"], ["post-1"])
        self.assertEqual(row["evidence"]["observation_ids"], ["post-1"])
        self.assertEqual(row["evidence"]["raw_observation_count"], 1)

    def test_provider_error_or_missing_response_is_not_terminal_proof(self):
        from app.phase3_recovery import _provider_page
        for payload, expected in [
            (None, False),
            ({"errors": [{"message": "unavailable"}]}, False),
            ({"data": {}}, False),
            ({"data": {"timeline": {"instructions": [{"type": "TimelinePinEntry", "entry": {"entryId": "tweet-1"}}]}}}, False),
            ({"data": {"timeline": {"instructions": [{"type": "TimelineAddEntries", "entries": []}]}}}, True),
            ({"data": {"timeline": {"instructions": [{"type": "TimelineTerminateTimeline", "direction": "Bottom"}]}}}, True),
        ]:
            async def stream(*args, **kwargs):
                if payload is not None:
                    yield SimpleNamespace(json=lambda: payload)
            api = SimpleNamespace(user_tweets_raw=stream, _get_cursor=lambda *args: None)
            with patch("twscrape.models.parse_tweets", return_value=[]):
                page = asyncio.run(_provider_page(api, 1, include_replies=False, cursor=None))
            self.assertEqual(page.valid_response, expected)


if __name__ == "__main__":
    unittest.main()
