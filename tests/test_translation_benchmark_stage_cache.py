from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from tools.run_translation_benchmark_cached import (
    DEFAULT_QUOTA_FAIL_FAST_CASES,
    _QuotaFailFastController,
    _StageCache,
)


def _payload(*, quota: bool, successful: bool = False) -> dict:
    case = {
        "case_id": "B01",
        "api_diagnostics": {
            "old_legacy": {"quota_429": False},
            "new_pipeline": {"quota_429": quota},
        },
    }
    if successful:
        case.update(
            {
                "output_mode": "styled",
                "verifier_result": "PASS",
                "verifier_failures": [],
            }
        )
    return {"cases": [case]}


class TranslationBenchmarkStageCacheTests(unittest.TestCase):
    def test_successful_stage_response_survives_independent_runner(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "benchmark.json"
            cache = _StageCache(path, "gemini-2.5-flash-lite")
            key = cache.response_key(
                writer_model="gemini-2.5-flash-lite",
                purpose="neutral fidelity",
                prompt="source A",
                schema={"type": "object"},
                temperature=0.08,
            )
            cache.put_response(key, {"title": "x", "items": [{"id": "B01", "body": "ok"}]})

            restored = _StageCache(path, "gemini-2.5-flash-lite")
            self.assertEqual(restored.get_response(key)["items"][0]["body"], "ok")

    def test_model_change_invalidates_stage_cache(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "benchmark.json"
            cache = _StageCache(path, "model-a")
            key = cache.response_key(
                writer_model="model-a",
                purpose="channel style/default",
                prompt="same prompt",
                schema={"type": "object"},
                temperature=0.24,
            )
            cache.put_response(key, {"items": [{"id": "B01", "body": "styled"}]})
            self.assertIsNone(_StageCache(path, "model-b").get_response(key))

    def test_cache_persists_without_secret_or_prompt_text(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "benchmark.json"
            path.write_text(json.dumps({"cases": [{"case_id": "B01"}]}), encoding="utf-8")
            cache = _StageCache(path, "gemini-2.5-flash-lite")
            prompt = "PRIVATE PROMPT CONTENT SHOULD NOT BE STORED"
            key = cache.response_key(
                writer_model="gemini-2.5-flash-lite",
                purpose="fidelity verifier",
                prompt=prompt,
                schema={"type": "object"},
                temperature=0.02,
            )
            cache.put_response(key, {"items": [{"id": "B01", "body": "safe"}]})
            text = path.read_text(encoding="utf-8")
            self.assertNotIn(prompt, text)
            self.assertNotIn("api_key", text.lower())
            self.assertIn('"cases"', text)
            self.assertIn('"api_stage_cache"', text)

    def test_default_fail_fast_stops_after_first_checkpointed_quota_case(self):
        self.assertEqual(DEFAULT_QUOTA_FAIL_FAST_CASES, 1)
        controller = _QuotaFailFastController()
        self.assertTrue(controller.observe_checkpoint(_payload(quota=True), complete=False))

    def test_explicit_two_case_threshold_still_supported(self):
        controller = _QuotaFailFastController(threshold=2)
        self.assertFalse(controller.observe_checkpoint(_payload(quota=True), complete=False))
        self.assertTrue(controller.observe_checkpoint(_payload(quota=True), complete=False))

    def test_successful_case_resets_fail_fast_streak(self):
        controller = _QuotaFailFastController(threshold=2)
        self.assertFalse(controller.observe_checkpoint(_payload(quota=True), complete=False))
        self.assertFalse(controller.observe_checkpoint(_payload(quota=False), complete=False))
        self.assertFalse(controller.observe_checkpoint(_payload(quota=True), complete=False))
        self.assertEqual(controller.consecutive_quota_cases, 1)

    def test_styled_pass_resets_streak_even_if_transient_quota_was_seen(self):
        controller = _QuotaFailFastController(threshold=2)
        self.assertFalse(controller.observe_checkpoint(_payload(quota=True), complete=False))
        self.assertFalse(controller.observe_checkpoint(_payload(quota=True, successful=True), complete=False))
        self.assertEqual(controller.consecutive_quota_cases, 0)
        self.assertFalse(controller.observe_checkpoint(_payload(quota=True), complete=False))
        self.assertEqual(controller.consecutive_quota_cases, 1)

    def test_complete_checkpoint_never_triggers_fail_fast(self):
        controller = _QuotaFailFastController(threshold=1)
        self.assertFalse(controller.observe_checkpoint(_payload(quota=True), complete=True))
        self.assertEqual(controller.consecutive_quota_cases, 0)

    def test_checkpoint_file_and_stage_cache_survive_fail_fast_decision(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "benchmark.json"
            checkpoint = _payload(quota=True)
            path.write_text(json.dumps(checkpoint), encoding="utf-8")
            cache = _StageCache(path, "gemini-2.5-flash-lite")
            key = cache.response_key(
                writer_model="gemini-2.5-flash-lite",
                purpose="neutral fidelity",
                prompt="source B",
                schema={"type": "object"},
                temperature=0.08,
            )
            cache.put_response(key, {"items": [{"id": "B01", "body": "saved"}]})
            controller = _QuotaFailFastController(threshold=1)
            payload = json.loads(path.read_text(encoding="utf-8"))
            self.assertTrue(controller.observe_checkpoint(payload, complete=False))
            restored = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(restored["cases"][0]["case_id"], "B01")
            self.assertIn("api_stage_cache", restored)
            self.assertEqual(_StageCache(path, "gemini-2.5-flash-lite").get_response(key)["items"][0]["body"], "saved")


if __name__ == "__main__":
    unittest.main()
