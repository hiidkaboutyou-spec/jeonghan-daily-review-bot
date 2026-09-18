from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from tools.protected_import_bypass_audit import DEFAULT_TARGET, audit_repository


class ProtectedImportBypassAuditTests(unittest.TestCase):
    def test_clean_static_owner_does_not_trigger_dynamic_audit(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "app").mkdir()
            (root / "tools").mkdir()
            (root / "tests").mkdir()
            (root / "app" / "__init__.py").write_text(
                "from . import x_recovery_integrity_runtime\n",
                encoding="utf-8",
            )
            (root / "app" / "x_recovery_integrity_runtime.py").write_text(
                "VALUE = 1\n",
                encoding="utf-8",
            )

            report = audit_repository(root)

        self.assertEqual(report["violation_count"], 0)
        self.assertEqual(report["parse_error_count"], 0)

    def test_detects_literal_dynamic_loading_and_sys_modules_bypasses(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "app").mkdir()
            (root / "tools").mkdir()
            (root / "tests").mkdir()
            (root / "tools" / "bad_dynamic.py").write_text(
                "\n".join(
                    [
                        "import builtins as b",
                        "import importlib as il",
                        "import importlib.util",
                        "import runpy",
                        "import sys",
                        "from importlib import import_module as im",
                        "TARGET = 'app.' + 'x_recovery_integrity_runtime'",
                        "il.import_module(TARGET)",
                        "im('.x_recovery_integrity_runtime', package='app')",
                        "b.__import__('app.x_recovery_integrity_runtime')",
                        "runpy.run_module('app.x_recovery_integrity_runtime')",
                        "importlib.util.spec_from_file_location('app.x_recovery_integrity_runtime', '/tmp/x.py')",
                        "exec('import app.x_recovery_integrity_runtime')",
                        "sys.modules['app.x_recovery_integrity_runtime']",
                        "sys.modules.get('app.x_recovery_integrity_runtime')",
                        "",
                    ]
                ),
                encoding="utf-8",
            )

            report = audit_repository(root)

        self.assertEqual(report["parse_error_count"], 0)
        self.assertEqual(report["violation_count"], 8)
        kinds = {item["kind"] for item in report["violations"]}
        self.assertEqual(
            kinds,
            {"dynamic-import-call", "dynamic-code-import", "sys-modules-access"},
        )
        self.assertTrue(all(item["target"] for item in report["violations"]))

    def test_detects_direct_import_module_alias_and_pkgutil_resolve_name(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "app").mkdir()
            (root / "tools").mkdir()
            (root / "tests").mkdir()
            (root / "app" / "bad.py").write_text(
                "\n".join(
                    [
                        "from importlib import import_module",
                        "from pkgutil import resolve_name",
                        f"import_module('{DEFAULT_TARGET}')",
                        f"resolve_name('{DEFAULT_TARGET}:VALUE')",
                        "",
                    ]
                ),
                encoding="utf-8",
            )

            report = audit_repository(root)

        self.assertEqual(report["violation_count"], 2)

    def test_reports_parse_errors_instead_of_skipping_them(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "app").mkdir()
            (root / "tools").mkdir()
            (root / "tests").mkdir()
            (root / "app" / "broken.py").write_text("def broken(:\n", encoding="utf-8")

            report = audit_repository(root)

        self.assertEqual(report["violation_count"], 0)
        self.assertEqual(report["parse_error_count"], 1)
        self.assertEqual(report["parse_errors"][0]["path"], "app/broken.py")


if __name__ == "__main__":
    unittest.main()
