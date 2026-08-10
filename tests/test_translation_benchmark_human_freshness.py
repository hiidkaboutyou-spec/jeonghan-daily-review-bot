from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from app import channel_part4_benchmark_hook as hook
from app.channel_part4_humanfix import HUMAN_GATE_FINGERPRINT
from tools import run_translation_benchmark as benchmark
from tools.run_translation_benchmark_human import _production_fingerprint


class TranslationBenchmarkHumanFreshnessTests(unittest.TestCase):
    def test_production_fingerprint_changes_with_quality_code(self):
        with tempfile.TemporaryDirectory() as tmp:
            first = Path(tmp) / "first.py"
            second = Path(tmp) / "second.py"
            first.write_text("quality = 1\n", encoding="utf-8")
            second.write_text("writer = 1\n", encoding="utf-8")
            before = _production_fingerprint((first, second))
            second.write_text("writer = 2\n", encoding="utf-8")
            after = _production_fingerprint((first, second))

        self.assertRegex(before, r"^channel-direct-v2-human-gate-[0-9a-f]{16}$")
        self.assertNotEqual(before, after)

    def test_python_m_hook_rejects_pre_human_gate_completed_checkpoint(self):
        main = sys.modules["__main__"]
        old_spec = getattr(main, "__spec__", None)
        old_load = benchmark._load_resume
        old_write = benchmark._write_checkpoint
        had_marker = hasattr(benchmark, "_human_gate_main_hook")
        old_marker = getattr(benchmark, "_human_gate_main_hook", None)
        try:
            main.__spec__ = SimpleNamespace(name="tools.run_translation_benchmark_cached")
            benchmark._human_gate_main_hook = False
            self.assertTrue(hook.install_for_current_process())
            with tempfile.TemporaryDirectory() as tmp:
                path = Path(tmp) / "benchmark.json"
                path.write_text(
                    json.dumps(
                        {
                            "quality_status": "PASS",
                            "cases": [
                                {
                                    "case_id": "B01",
                                    "output_mode": "styled",
                                    "verifier_result": "PASS",
                                }
                            ],
                        }
                    ),
                    encoding="utf-8",
                )
                invalidated = benchmark._load_resume(path)
                self.assertEqual(invalidated[0]["case_id"], "B01")
                self.assertEqual(invalidated[0]["output_mode"], "invalidated_resume_cursor")
                self.assertNotEqual(invalidated[0]["verifier_result"], "PASS")

                payload = json.loads(path.read_text(encoding="utf-8"))
                payload["production_writer_fingerprint"] = HUMAN_GATE_FINGERPRINT
                path.write_text(json.dumps(payload), encoding="utf-8")
                self.assertEqual(benchmark._load_resume(path)[0]["case_id"], "B01")
        finally:
            benchmark._load_resume = old_load
            benchmark._write_checkpoint = old_write
            if had_marker:
                benchmark._human_gate_main_hook = old_marker
            elif hasattr(benchmark, "_human_gate_main_hook"):
                delattr(benchmark, "_human_gate_main_hook")
            main.__spec__ = old_spec

    def test_normal_bot_process_does_not_install_benchmark_hook(self):
        main = sys.modules["__main__"]
        old_spec = getattr(main, "__spec__", None)
        try:
            main.__spec__ = SimpleNamespace(name="app")
            self.assertFalse(hook._is_cached_benchmark_main())
        finally:
            main.__spec__ = old_spec


if __name__ == "__main__":
    unittest.main()
