from __future__ import annotations

import configparser
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CONFIG = ROOT / ".importlinter"
BLOCKING_CONTRACT = "importlinter:contract:stage-d-x-recovery-integrity-owner"
PILOT_CONTRACT = "importlinter:contract:stage-d-x-resumable-recovery-owner"


class ArchitectureContractConfigTests(unittest.TestCase):
    def _config(self) -> configparser.ConfigParser:
        parser = configparser.ConfigParser()
        loaded = parser.read(CONFIG, encoding="utf-8")
        self.assertEqual(loaded, [str(CONFIG)])
        return parser

    def test_stage_d_contracts_are_narrow_ignore_free_and_actionable(self) -> None:
        parser = self._config()
        self.assertEqual(parser.get("importlinter", "root_package"), "app")
        self.assertTrue(parser.getboolean("importlinter", "exclude_type_checking_imports"))

        self.assertEqual(
            [section for section in parser.sections() if section.startswith("importlinter:contract:")],
            [BLOCKING_CONTRACT, PILOT_CONTRACT],
        )

        self.assertEqual(parser.get(BLOCKING_CONTRACT, "type"), "protected")
        self.assertEqual(
            parser.get(BLOCKING_CONTRACT, "protected_modules").split(),
            ["app.x_recovery_integrity_runtime"],
        )
        self.assertEqual(parser.get(BLOCKING_CONTRACT, "allowed_importers").split(), ["app"])
        self.assertFalse(parser.getboolean(BLOCKING_CONTRACT, "as_packages"))
        self.assertFalse(parser.has_option(BLOCKING_CONTRACT, "ignore_imports"))
        blocking_guidance = parser.get(BLOCKING_CONTRACT, "broken_contract_guidance")
        self.assertIn("Do not import app.x_recovery_integrity_runtime directly.", blocking_guidance)
        self.assertIn("app/__init__.py", blocking_guidance)

        self.assertEqual(parser.get(PILOT_CONTRACT, "type"), "protected")
        self.assertEqual(
            parser.get(PILOT_CONTRACT, "protected_modules").split(),
            ["app.x_resumable_recovery_runtime"],
        )
        self.assertEqual(
            parser.get(PILOT_CONTRACT, "allowed_importers").split(),
            ["app", "app.completeness_provider_proof", "app.x_recovery_integrity_runtime"],
        )
        self.assertFalse(parser.getboolean(PILOT_CONTRACT, "as_packages"))
        self.assertFalse(parser.has_option(PILOT_CONTRACT, "ignore_imports"))
        pilot_guidance = parser.get(PILOT_CONTRACT, "broken_contract_guidance")
        self.assertIn("Do not couple new application modules directly", pilot_guidance)
        self.assertIn("completeness provider proof", pilot_guidance)


if __name__ == "__main__":
    unittest.main()
