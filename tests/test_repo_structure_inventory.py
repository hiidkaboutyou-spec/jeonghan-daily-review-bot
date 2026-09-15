from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from tools.repo_structure_inventory import build_inventory, render_markdown


class RepoStructureInventoryTests(unittest.TestCase):
    def test_inventory_maps_categories_imports_and_historical_names(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "app").mkdir()
            (root / "tools").mkdir()
            (root / "app" / "__init__.py").write_text(
                "from . import channel_part4_finalfix\n",
                encoding="utf-8",
            )
            (root / "app" / "channel_part4_finalfix.py").write_text(
                "from app import source_ledger_runtime\n",
                encoding="utf-8",
            )
            (root / "app" / "source_ledger_runtime.py").write_text(
                "VALUE = 1\n",
                encoding="utf-8",
            )
            (root / "tools" / "daily_watchdog.py").write_text(
                "from app.source_ledger_runtime import VALUE\n",
                encoding="utf-8",
            )

            inventory = build_inventory(root)

        summary = inventory["summary"]
        self.assertEqual(summary["module_count"], 4)
        self.assertEqual(summary["historical_name_count"], 1)
        self.assertIn("app.channel_part4_finalfix", inventory["historical_name_modules"])

        by_module = {entry["module"]: entry for entry in inventory["modules"]}
        self.assertEqual(by_module["app.channel_part4_finalfix"]["category"], "editorial")
        self.assertEqual(by_module["app.source_ledger_runtime"]["category"], "sources")
        self.assertEqual(by_module["tools.daily_watchdog"]["category"], "observability")
        self.assertIn("app.channel_part4_finalfix", by_module["app"]["internal_imports"])
        self.assertIn("app.source_ledger_runtime", by_module["tools.daily_watchdog"]["internal_imports"])

        markdown = render_markdown(inventory)
        self.assertIn("Historical phase/fix-style names: 1", markdown)
        self.assertIn("app.channel_part4_finalfix", markdown)

    def test_syntax_error_is_reported_instead_of_hidden(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "app").mkdir()
            (root / "app" / "broken.py").write_text("def broken(:\n", encoding="utf-8")
            inventory = build_inventory(root)

        self.assertEqual(inventory["summary"]["parse_error_count"], 1)
        self.assertEqual(inventory["parse_errors"][0]["path"], "app/broken.py")


if __name__ == "__main__":
    unittest.main()
