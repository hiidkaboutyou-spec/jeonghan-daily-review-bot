from __future__ import annotations

import configparser
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CONFIG = ROOT / ".importlinter"
BLOCKING_CONTRACT = "importlinter:contract:stage-d-x-recovery-integrity-owner"
SECOND_BLOCKING_CONTRACT = "importlinter:contract:stage-d-x-resumable-recovery-owner"
FANFIC_PILOT_CONTRACT = "importlinter:contract:stage-d-fanfic-daily-shadow-isolation"
FANFIC_FORBIDDEN = [
    "app.translation_fusion",
    "app.translation_fusion_runtime",
    "app.translation_fusion_state_compat",
    "app.channel_style_rewrite",
    "app.channel_style_rewrite_state_compat",
    "app.user_voice_calibration",
    "app.user_voice_calibration_state_compat",
    "app.forward_ready_package",
    "app.forward_ready_state_compat",
    "app.fused_private_review_delivery",
]
WORKFLOW = ROOT / ".github" / "workflows" / "maintenance-diagnostics.yml"


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
            [BLOCKING_CONTRACT, SECOND_BLOCKING_CONTRACT, FANFIC_PILOT_CONTRACT],
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

        self.assertEqual(parser.get(SECOND_BLOCKING_CONTRACT, "type"), "protected")
        self.assertEqual(
            parser.get(SECOND_BLOCKING_CONTRACT, "protected_modules").split(),
            ["app.x_resumable_recovery_runtime"],
        )
        self.assertEqual(
            parser.get(SECOND_BLOCKING_CONTRACT, "allowed_importers").split(),
            ["app", "app.completeness_provider_proof", "app.x_recovery_integrity_runtime"],
        )
        self.assertFalse(parser.getboolean(SECOND_BLOCKING_CONTRACT, "as_packages"))
        self.assertFalse(parser.has_option(SECOND_BLOCKING_CONTRACT, "ignore_imports"))
        pilot_guidance = parser.get(SECOND_BLOCKING_CONTRACT, "broken_contract_guidance")
        self.assertIn("Do not couple new application modules directly", pilot_guidance)
        self.assertIn("completeness provider proof", pilot_guidance)

        self.assertEqual(parser.get(FANFIC_PILOT_CONTRACT, "type"), "forbidden")
        self.assertEqual(
            parser.get(FANFIC_PILOT_CONTRACT, "source_modules").split(),
            ["app.fic_digest"],
        )
        self.assertEqual(
            parser.get(FANFIC_PILOT_CONTRACT, "forbidden_modules").split(),
            FANFIC_FORBIDDEN,
        )
        self.assertFalse(parser.getboolean(FANFIC_PILOT_CONTRACT, "as_packages"))
        self.assertFalse(parser.has_option(FANFIC_PILOT_CONTRACT, "ignore_imports"))
        self.assertFalse(parser.has_option(FANFIC_PILOT_CONTRACT, "allow_indirect_imports"))
        fanfic_guidance = parser.get(FANFIC_PILOT_CONTRACT, "broken_contract_guidance")
        self.assertIn("Keep app.fic_digest independent", fanfic_guidance)
        self.assertIn("Shared primitives", fanfic_guidance)

        workflow = WORKFLOW.read_text(encoding="utf-8")
        self.assertIn("--contract stage-d-x-recovery-integrity-owner", workflow)
        self.assertIn("--contract stage-d-x-resumable-recovery-owner", workflow)
        self.assertIn("Enforce Stage D Import Linter contract", workflow)
        self.assertIn("Enforce resumable recovery Import Linter boundary", workflow)
        self.assertIn("Enforce resumable recovery dynamic-import boundary", workflow)
        self.assertNotIn("Resumable recovery protected contract is report-only", workflow)
        self.assertNotIn("Resumable recovery dynamic-import candidate is report-only", workflow)

        self.assertIn("--contract stage-d-fanfic-daily-shadow-isolation", workflow)
        self.assertIn("Enforce Fanfic static and indirect Import Linter boundary", workflow)
        self.assertIn("Enforce Fanfic runtime and literal dynamic-import isolation", workflow)
        self.assertIn("fanfic_import_isolation_audit.py", workflow)
        self.assertIn("import-linter-fanfic-report.txt", workflow)
        self.assertNotIn("report_only_exit_status=$status", workflow)
        self.assertIn("blocking_exit_status=$status", workflow)
        self.assertIn('if [ "$status" -ne 0 ]; then', workflow)
        self.assertIn("Stage D Fanfic/AO3 isolation: blocking", workflow)
        for name in (
            "Enforce Fanfic static and indirect Import Linter boundary",
            "Enforce Fanfic runtime and literal dynamic-import isolation",
        ):
            step = workflow.split(f"- name: {name}", 1)[1].split("\n      - name:", 1)[0]
            self.assertIn('exit "$status"', step)
            self.assertNotIn("exit 0", step)


if __name__ == "__main__":
    unittest.main()
