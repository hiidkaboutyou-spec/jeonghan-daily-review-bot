from pathlib import Path
import unittest

from tools.run_translation_benchmark_human import _ensure_refresh_pacing


ROOT = Path(__file__).resolve().parents[1]


class HumanBenchmarkEntrypointTests(unittest.TestCase):
    def test_ci_uses_human_gate_freshness_entrypoint(self):
        workflow = (ROOT / ".github" / "workflows" / "main.yml").read_text(encoding="utf-8")
        self.assertIn("python -m tools.run_translation_benchmark_human", workflow)
        self.assertNotIn("python -m tools.run_translation_benchmark_cached \\", workflow)

    def test_human_entrypoint_explicitly_installs_resume_fingerprint_patch(self):
        source = (ROOT / "tools" / "run_translation_benchmark_human.py").read_text(encoding="utf-8")
        self.assertIn("humanfix._patch_cached_benchmark_resume()", source)
        self.assertIn("return cached.main()", source)

    def test_refresh_pacing_raises_only_too_small_batch(self):
        argv = ["runner", "--batch-size", "1", "--batch-cooldown-seconds", "65"]
        _ensure_refresh_pacing(argv)
        self.assertEqual(argv[2], "4")

        already_safe = ["runner", "--batch-size=8"]
        _ensure_refresh_pacing(already_safe)
        self.assertEqual(already_safe[1], "--batch-size=8")


if __name__ == "__main__":
    unittest.main()
