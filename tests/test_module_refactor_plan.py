from __future__ import annotations

import importlib.util
import tempfile
import unittest
from pathlib import Path

from tools.module_refactor_plan import build_plan


LIBCST_AVAILABLE = importlib.util.find_spec("libcst") is not None


@unittest.skipUnless(LIBCST_AVAILABLE, "LibCST is installed only in the maintenance environment")
class ModuleRefactorPlanTests(unittest.TestCase):
    def test_plan_distinguishes_structural_dynamic_and_import_order_references(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "app").mkdir()
            (root / "tools").mkdir()
            (root / "tests").mkdir()
            (root / "app" / "__init__.py").write_text(
                "from . import old_mod  # installation order matters\n",
                encoding="utf-8",
            )
            (root / "app" / "old_mod.py").write_text("VALUE = 1\n", encoding="utf-8")
            (root / "app" / "consumer.py").write_text(
                "from app.old_mod import VALUE\nimport app.old_mod as legacy\n",
                encoding="utf-8",
            )
            (root / "tools" / "dynamic.py").write_text(
                "import importlib\nTARGET = 'app.old_mod'\n",
                encoding="utf-8",
            )
            (root / "tests" / "test_consumer.py").write_text(
                "from app import old_mod, consumer\n",
                encoding="utf-8",
            )

            before = {
                path.relative_to(root): path.read_text(encoding="utf-8")
                for path in root.rglob("*.py")
            }
            plan = build_plan(root, "app.old_mod", "app.recovery.runtime")
            after = {
                path.relative_to(root): path.read_text(encoding="utf-8")
                for path in root.rglob("*.py")
            }

        self.assertEqual(before, after, "planning must never mutate source files")
        self.assertTrue(plan["old_module_exists"])
        self.assertFalse(plan["new_module_exists"])
        self.assertTrue(plan["compatibility_shim_required"])
        self.assertEqual(plan["summary"]["parse_error_count"], 0)
        self.assertGreaterEqual(plan["summary"]["structurally_safe_reference_count"], 3)
        self.assertGreaterEqual(plan["summary"]["manual_review_reference_count"], 2)
        self.assertEqual(plan["summary"]["dynamic_string_reference_count"], 1)
        self.assertEqual(plan["summary"]["import_order_sensitive_reference_count"], 1)

        by_kind: dict[str, list[dict]] = {}
        for item in plan["references"]:
            by_kind.setdefault(item["kind"], []).append(item)
        self.assertIn("from-module", by_kind)
        self.assertIn("import", by_kind)
        self.assertIn("from-imported-module", by_kind)
        self.assertIn("dynamic-string", by_kind)
        self.assertTrue(any(item["import_order_sensitive"] for item in by_kind["from-imported-module"]))
        self.assertFalse(by_kind["dynamic-string"][0]["automatic_safe"])

    def test_plan_reports_parse_errors_instead_of_hiding_them(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "app").mkdir()
            (root / "app" / "old_mod.py").write_text("VALUE = 1\n", encoding="utf-8")
            (root / "app" / "broken.py").write_text("def broken(:\n", encoding="utf-8")
            plan = build_plan(root, "app.old_mod", "app.semantic_mod")

        self.assertEqual(plan["summary"]["parse_error_count"], 1)
        self.assertEqual(plan["parse_errors"][0]["path"], "app/broken.py")


if __name__ == "__main__":
    unittest.main()
