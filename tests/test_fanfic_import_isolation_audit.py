from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from tools.fanfic_import_isolation_audit import (
    audit_literal_dynamic_imports,
    audit_repository,
    runtime_import_probe,
)


class FanficImportIsolationAuditTests(unittest.TestCase):
    def _package(self, root: Path, *, eager_forbidden: bool = False, dynamic_forbidden: bool = False) -> None:
        app = root / "app"
        app.mkdir()
        init_lines = ["from . import witness"]
        if eager_forbidden:
            init_lines.append("from . import daily_only")
        (app / "__init__.py").write_text("\n".join(init_lines) + "\n", encoding="utf-8")
        (app / "witness.py").write_text("VALUE = 1\n", encoding="utf-8")
        (app / "daily_only.py").write_text("VALUE = 2\n", encoding="utf-8")
        if dynamic_forbidden:
            fic = "import importlib\n\ndef load():\n    return importlib.import_module('app.daily_only')\n"
        else:
            fic = "VALUE = 3\n"
        (app / "fic_digest.py").write_text(fic, encoding="utf-8")

    def test_literal_dynamic_scan_is_source_scoped_and_clean(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            self._package(root)
            findings, parse_errors = audit_literal_dynamic_imports(
                root,
                source_module="app.fic_digest",
                forbidden_modules=("app.daily_only",),
            )

        self.assertEqual(findings, [])
        self.assertEqual(parse_errors, [])

    def test_literal_dynamic_scan_detects_forbidden_target(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            self._package(root, dynamic_forbidden=True)
            findings, parse_errors = audit_literal_dynamic_imports(
                root,
                source_module="app.fic_digest",
                forbidden_modules=("app.daily_only",),
            )

        self.assertEqual(parse_errors, [])
        self.assertEqual(len(findings), 1)
        self.assertEqual(findings[0]["kind"], "dynamic-import-call")
        self.assertEqual(findings[0]["target"], "app.daily_only")
        self.assertEqual(findings[0]["importer"], "app.fic_digest")

    def test_clean_process_probe_executes_package_init_without_loading_forbidden_module(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            self._package(root)
            report = runtime_import_probe(
                root,
                source_module="app.fic_digest",
                forbidden_modules=("app.daily_only",),
                witness_module="app.witness",
            )

        self.assertTrue(report["ok"])
        self.assertTrue(report["source_loaded"])
        self.assertTrue(report["package_init_witness_loaded"])
        self.assertEqual(report["forbidden_loaded"], [])

    def test_clean_process_probe_detects_package_init_eager_loading(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            self._package(root, eager_forbidden=True)
            report = runtime_import_probe(
                root,
                source_module="app.fic_digest",
                forbidden_modules=("app.daily_only",),
                witness_module="app.witness",
            )

        self.assertTrue(report["ok"])
        self.assertEqual(report["forbidden_loaded"], ["app.daily_only"])

    def test_combined_report_counts_static_and_runtime_violations(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            self._package(root, eager_forbidden=True, dynamic_forbidden=True)
            report = audit_repository(
                root,
                source_module="app.fic_digest",
                forbidden_modules=("app.daily_only",),
                witness_module="app.witness",
            )

        self.assertEqual(report["literal_dynamic_violation_count"], 1)
        self.assertEqual(report["runtime_loaded_forbidden_count"], 1)
        self.assertEqual(report["violation_count"], 2)
        self.assertEqual(report["error_count"], 0)
        self.assertTrue(report["scope"]["report_only"])


if __name__ == "__main__":
    unittest.main()
