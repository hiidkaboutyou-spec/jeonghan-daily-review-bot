from __future__ import annotations

import subprocess
import sys
import unittest

from app.config import ROOT


class TranslationBenchmarkEntrypointTests(unittest.TestCase):
    def test_module_entrypoint_imports_app_from_repository_root(self) -> None:
        completed = subprocess.run(
            [sys.executable, "-m", "tools.run_translation_benchmark", "--help"],
            cwd=ROOT,
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )

        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertIn("--output", completed.stdout)
        self.assertNotIn("ModuleNotFoundError", completed.stderr)


if __name__ == "__main__":
    unittest.main()
