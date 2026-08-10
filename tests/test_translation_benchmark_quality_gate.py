from __future__ import annotations

import unittest

from tools.run_translation_benchmark import (
    MIN_STYLED_FRACTION,
    _group,
    _output_mode,
    _quality_gate,
    _resume_case_order,
    _retry_after_seconds,
    _run_quota_aware,
    _summary,
)


class TranslationBenchmarkQualityGateTests(unittest.TestCase):
    def test_real_case_builder_uses_detection_and_preserves_media_quote_context(self):
        group = _group({
            "id": "B37",
            "content_type": "VIDEO_REACTION",
            "source": "Jeonghan starts the BAD challenge",
            "media": [{"kind": "video", "url": "https://example.invalid/video.mp4"}],
            "quoted_text": "Seungcheol sent the challenge.",
            "quoted_author": "source",
        })
        self.assertEqual(group.category, "general")
        self.assertEqual(group.updates[0].media[0].kind, "video")
        self.assertIn("[QUOTED POST]", group.updates[0].translation_source())

    def test_retry_after_parser_uses_server_seconds(self):
        messages = [
            "429 RESOURCE_EXHAUSTED: quota exceeded. Please retry in 20.677995706s.",
            "retryDelay: 1500ms",
        ]
        self.assertAlmostEqual(_retry_after_seconds(messages), 20.677995706)

    def test_quota_retry_waits_and_retries_boundedly(self):
        calls = []
        sleeps = []

        def fn():
            calls.append(1)
            if len(calls) == 1:
                import logging
                logging.getLogger("benchmark-test").warning(
                    "429 RESOURCE_EXHAUSTED quota exceeded; Please retry in 2s."
                )
                return "fallback"
            return "styled"

        result, diagnostics = _run_quota_aware(
            fn,
            retry_if=lambda value, _messages: value == "fallback",
            max_quota_retries=2,
            sleep_fn=sleeps.append,
        )
        self.assertEqual(result, "styled")
        self.assertEqual(len(calls), 2)
        self.assertEqual(sleeps, [5.0])
        self.assertTrue(diagnostics["quota_429"])
        self.assertEqual(diagnostics["api_status"], 429)
        self.assertEqual(diagnostics["exception_class"], "RESOURCE_EXHAUSTED")

    def test_resume_rotates_after_last_attempted_case(self):
        cases = [{"id": f"B{i:02d}"} for i in range(1, 6)]
        prior = [
            {"case_id": "B01", "output_mode": "neutral_fallback", "verifier_result": "FAIL"},
            {"case_id": "B02", "output_mode": "neutral_fallback", "verifier_result": "PASS"},
        ]
        ordered = _resume_case_order(cases, prior)
        self.assertEqual([case["id"] for case in ordered], ["B03", "B04", "B05", "B01", "B02"])

    def test_output_mode_is_explicit(self):
        self.assertEqual(_output_mode("", {}, "ترجمه", "source"), "styled")
        self.assertEqual(
            _output_mode("style_transfer_unavailable_neutral", {}, "ترجمه", "source"),
            "neutral_fallback",
        )
        self.assertEqual(
            _output_mode("source_preserving", {}, "source", "source"),
            "source_fallback",
        )

    def test_all_neutral_fallbacks_are_rejected(self):
        results = [
            {"output_mode": "neutral_fallback", "verifier_result": "PASS"}
            for _ in range(36)
        ]
        summary = _summary(results)
        passed, reasons = _quality_gate(summary, case_count=36)
        self.assertFalse(passed)
        self.assertEqual(summary["styled_successes"], 0)
        self.assertEqual(summary["neutral_fallbacks"], 36)
        self.assertIn("styled_output_success_count_zero", reasons)

    def test_majority_threshold_is_enforced(self):
        styled = int(36 * MIN_STYLED_FRACTION) - 1
        results = [
            {"output_mode": "styled", "verifier_result": "PASS"}
            for _ in range(styled)
        ] + [
            {"output_mode": "neutral_fallback", "verifier_result": "PASS"}
            for _ in range(36 - styled)
        ]
        passed, reasons = _quality_gate(_summary(results), case_count=36)
        self.assertFalse(passed)
        self.assertTrue(any(reason.startswith("styled_fraction_below_") for reason in reasons))

    def test_hard_verifier_failure_rejects_benchmark(self):
        results = [
            {"output_mode": "styled", "verifier_result": "PASS"}
            for _ in range(35)
        ] + [{"output_mode": "styled", "verifier_result": "FAIL"}]
        passed, reasons = _quality_gate(_summary(results), case_count=36)
        self.assertFalse(passed)
        self.assertIn("unresolved_verifier_failures:1", reasons)

    def test_structural_pass_cannot_claim_human_voice_or_publishability(self):
        results = [
            {
                "output_mode": "styled",
                "verifier_result": "PASS",
                "quality_metrics": {
                    "deterministic": {
                        "name_accuracy": "PASS",
                        "factual_fidelity": "PASS",
                        "semantic_coherence_precheck": "PASS",
                        "category_accuracy": "PASS",
                        "natural_persian_precheck": "PASS",
                    },
                    "human": {"review_status": "PENDING_HUMAN_REVIEW"},
                },
            }
            for _ in range(36)
        ]
        summary = _summary(results)
        passed, reasons = _quality_gate(summary, case_count=36)
        self.assertFalse(passed)
        self.assertIsNone(summary["human_publishable_fraction"])
        self.assertIn("human_quality_review_incomplete:0/36", reasons)


if __name__ == "__main__":
    unittest.main()
