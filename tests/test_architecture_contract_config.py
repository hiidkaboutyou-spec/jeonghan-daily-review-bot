from __future__ import annotations

import configparser
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CONFIG = ROOT / ".importlinter"
CONTRACT = "importlinter:contract:stage-d-x-recovery-integrity-owner"


class ArchitectureContractConfigTests(unittest.TestCase):
    def _config(self) -> configparser.ConfigParser:
        parser = configparser.ConfigParser()
        loaded = parser.read(CONFIG, encoding="utf-8")
        self.assertEqual(loaded, [str(CONFIG)])
        return parser

    def test_stage_d_contract_is_narrow_ignore_free_and_actionable(self) -> None:
        parser = self._config()
        self.assertEqual(parser.get("importlinter", "root_package"), "app")
        self.assertTrue(parser.getboolean("importlinter", "exclude_type_checking_imports"))

        self.assertEqual(
            [section for section in parser.sections() if section.startswith("importlinter:contract:")],
            [CONTRACT],
        )
        self.assertEqual(parser.get(CONTRACT, "type"), "protected")
        self.assertEqual(
            parser.get(CONTRACT, "protected_modules").split(),
            ["app.x_recovery_integrity_runtime"],
        )
        self.assertEqual(parser.get(CONTRACT, "allowed_importers").split(), ["app"])
        self.assertFalse(parser.getboolean(CONTRACT, "as_packages"))
        self.assertFalse(parser.has_option(CONTRACT, "ignore_imports"))
        guidance = parser.get(CONTRACT, "broken_contract_guidance")
        self.assertIn("Do not import app.x_recovery_integrity_runtime directly.", guidance)
        self.assertIn("app/__init__.py", guidance)


if __name__ == "__main__":
    unittest.main()
