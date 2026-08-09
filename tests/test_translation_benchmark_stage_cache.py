from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from tools.run_translation_benchmark_cached import _StageCache


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


if __name__ == "__main__":
    unittest.main()
