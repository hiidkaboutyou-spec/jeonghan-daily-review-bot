from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from tools.module_family_evidence import (
    classify_review_hint,
    coverage_evidence,
    historical_modules,
    render_markdown,
)


class ModuleFamilyEvidenceTests(unittest.TestCase):
    def test_historical_modules_only_selects_cleanup_name_patterns(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "app").mkdir()
            (root / "app" / "channel_part4_finalfix.py").write_text("VALUE = 1\n", encoding="utf-8")
            (root / "app" / "phase3_recovery_hardening.py").write_text("VALUE = 1\n", encoding="utf-8")
            (root / "app" / "daily_watchdog.py").write_text("VALUE = 1\n", encoding="utf-8")

            modules = historical_modules(root)

        self.assertEqual(
            modules,
            ["app.channel_part4_finalfix", "app.phase3_recovery_hardening"],
        )

    def test_coverage_evidence_collects_test_contexts(self) -> None:
        payload = {
            "files": {
                "app/channel_part4_finalfix.py": {
                    "summary": {
                        "percent_covered": 75.0,
                        "covered_lines": 30,
                        "num_statements": 40,
                    },
                    "contexts": {
                        "10": ["tests.test_channel.Part4Tests.test_edge", ""],
                        "11": ["tests.test_channel.Part4Tests.test_edge", "tests.test_channel.Part4Tests.test_other"],
                    },
                }
            }
        }

        result = coverage_evidence("app.channel_part4_finalfix", payload)

        self.assertTrue(result["available"])
        self.assertEqual(result["percent_covered"], 75.0)
        self.assertEqual(result["test_context_count"], 2)
        self.assertEqual(len(result["test_context_sample"]), 2)

    def test_runtime_chain_is_high_risk(self) -> None:
        risk, hint = classify_review_hint(
            {
                "entrypoint_chains": {"app": ["app", "app.channel_part4_finalfix"]},
                "direct_importers": ["app"],
                "downstream_importer_count": 1,
            },
            {"available": True, "test_context_count": 0},
        )

        self.assertEqual(risk, "high")
        self.assertIn("runtime-linked", hint)

    def test_no_static_or_coverage_evidence_is_not_called_dead(self) -> None:
        risk, hint = classify_review_hint(
            {
                "entrypoint_chains": {},
                "direct_importers": [],
                "downstream_importer_count": 0,
            },
            {"available": True, "test_context_count": 0},
        )

        self.assertEqual(risk, "low-investigation")
        self.assertIn("not proof of dead code", hint)

    def test_markdown_states_non_destructive_policy(self) -> None:
        report = {
            "candidate_count": 1,
            "coverage_available": True,
            "candidates": [
                {
                    "module": "app.phase3_recovery_hardening",
                    "risk": "high",
                    "review_hint": "runtime-linked",
                    "graph": {
                        "direct_importers": ["app"],
                        "downstream_importer_count": 1,
                        "entrypoint_chains": {"app": ["app", "app.phase3_recovery_hardening"]},
                    },
                    "coverage": {
                        "percent_covered": 50.0,
                        "test_context_count": 1,
                        "test_context_sample": ["tests.test_recovery.RecoveryTests.test_backup"],
                    },
                }
            ],
        }

        rendered = render_markdown(report)

        self.assertIn("never proves that a module is obsolete", rendered)
        self.assertIn("app.phase3_recovery_hardening", rendered)
        self.assertIn("tests.test_recovery", rendered)


if __name__ == "__main__":
    unittest.main()
