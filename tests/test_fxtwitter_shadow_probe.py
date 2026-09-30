from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from tools.fxtwitter_shadow_probe import evaluate_pages, load_enabled_sources


def _row(identifier: str, timestamp: float, *, repost: bool = False) -> dict:
    return {
        "id": identifier,
        "created_timestamp": timestamp,
        "reposted_by": {"screen_name": "source"} if repost else None,
    }


class FxTwitterShadowProbeTests(unittest.TestCase):
    def test_lower_boundary_proves_complete_when_authority_rows_are_chronological(self):
        result = evaluate_pages(
            "source",
            [
                (
                    200,
                    {
                        "code": 200,
                        "results": [
                            _row("3", 300),
                            _row("2", 200),
                            _row("1", 90),
                        ],
                        "cursor": {"bottom": "next"},
                    },
                )
            ],
            start_epoch=100,
            max_pages=6,
        )
        self.assertTrue(result.complete)
        self.assertEqual(result.reason, "lower_boundary_crossed")
        self.assertTrue(result.chronological)

    def test_repost_timestamp_cannot_prove_lower_boundary(self):
        result = evaluate_pages(
            "source",
            [
                (
                    200,
                    {
                        "code": 200,
                        "results": [
                            _row("3", 300),
                            _row("old-retweet", 10, repost=True),
                        ],
                        "cursor": {"bottom": "next"},
                    },
                )
            ],
            start_epoch=100,
            max_pages=1,
        )
        self.assertFalse(result.complete)
        self.assertFalse(result.lower_boundary_crossed)
        self.assertEqual(result.reason, "page_budget_exhausted")

    def test_non_chronological_rows_fail_closed_until_cursor_exhaustion(self):
        result = evaluate_pages(
            "source",
            [
                (
                    200,
                    {
                        "code": 200,
                        "results": [
                            _row("2", 80),
                            _row("3", 150),
                        ],
                        "cursor": {"bottom": "next"},
                    },
                )
            ],
            start_epoch=100,
            max_pages=1,
        )
        self.assertFalse(result.complete)
        self.assertFalse(result.chronological)

    def test_cursor_exhaustion_proves_complete(self):
        result = evaluate_pages(
            "source",
            [
                (
                    200,
                    {
                        "code": 200,
                        "results": [_row("3", 300)],
                        "cursor": {"bottom": None},
                    },
                )
            ],
            start_epoch=100,
            max_pages=6,
        )
        self.assertTrue(result.complete)
        self.assertTrue(result.cursor_exhausted)
        self.assertEqual(result.reason, "cursor_exhausted")

    def test_404_after_successful_page_is_pagination_end(self):
        result = evaluate_pages(
            "source",
            [
                (
                    200,
                    {
                        "code": 200,
                        "results": [_row("3", 300), _row("2", 200)],
                        "cursor": {"bottom": "next"},
                    },
                ),
                (404, {"code": 404, "results": [], "cursor": {"bottom": None}}),
            ],
            start_epoch=100,
            max_pages=10,
        )
        self.assertTrue(result.complete)
        self.assertTrue(result.cursor_exhausted)
        self.assertEqual(result.reason, "pagination_ended_404")

    def test_schema_error_prevents_complete_even_when_cursor_exhausts(self):
        result = evaluate_pages(
            "source",
            [
                (
                    200,
                    {
                        "code": 200,
                        "results": [{"id": "bad", "created_timestamp": "not-a-number"}],
                        "cursor": {"bottom": None},
                    },
                )
            ],
            start_epoch=100,
            max_pages=6,
        )
        self.assertFalse(result.complete)
        self.assertEqual(result.status, "error")
        self.assertEqual(result.reason, "schema_errors")

    def test_load_sources_combines_and_dedupes_configs(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "config").mkdir()
            (root / "config" / "sources.json").write_text(
                json.dumps(
                    {
                        "sources": [
                            {"handle": "alpha", "enabled": True, "include_replies": True},
                            {"handle": "off", "enabled": False},
                        ]
                    }
                ),
                encoding="utf-8",
            )
            (root / "config" / "jeonghan_priority_x_sources.json").write_text(
                json.dumps({"sources": [{"handle": "beta", "enabled": True}]}),
                encoding="utf-8",
            )
            sources = load_enabled_sources(root)
        self.assertEqual([item["handle"] for item in sources], ["alpha", "beta"])


if __name__ == "__main__":
    unittest.main()
