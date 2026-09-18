from __future__ import annotations

import importlib
import json
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
REGISTRY = ROOT / "config" / "module_migrations.json"


class ModuleMigrationRegistryTests(unittest.TestCase):
    def _payload(self) -> dict:
        return json.loads(REGISTRY.read_text(encoding="utf-8"))

    def _migration(self, legacy_module: str) -> dict:
        return next(
            item
            for item in self._payload()["migrations"]
            if item["legacy_module"] == legacy_module
        )

    def test_registry_tracks_retired_degraded_x_recovery_path(self) -> None:
        payload = self._payload()
        self.assertEqual(payload["schema_version"], 1)

        migration = self._migration("app.live_recovery_hardening")
        self.assertEqual(migration["canonical_module"], "app.x_degraded_recovery_runtime")
        self.assertEqual(migration["status"], "retired")
        self.assertEqual(migration["retired_on"], "2026-09-17")
        self.assertEqual(
            migration["retirement_record"],
            "docs/research/live-recovery-hardening-shim-retirement-2026-09-17.md",
        )
        self.assertFalse(migration["runtime_behavior_change"])
        self.assertGreaterEqual(len(migration["removal_gates"]), 4)

    def test_registry_tracks_retired_lifecycle_correlation_path(self) -> None:
        migration = self._migration("app.phase2_correlation_stability")
        self.assertEqual(migration["canonical_module"], "app.lifecycle_correlation_runtime")
        self.assertEqual(migration["status"], "retired")
        self.assertEqual(migration["retired_on"], "2026-09-17")
        self.assertEqual(
            migration["retirement_record"],
            "docs/research/phase2-correlation-stability-shim-retirement-2026-09-17.md",
        )
        self.assertFalse(migration["runtime_behavior_change"])
        self.assertGreaterEqual(len(migration["removal_gates"]), 5)

    def test_registry_tracks_retired_lifecycle_outcome_visibility_path(self) -> None:
        migration = self._migration("app.phase2_final_visibility")
        self.assertEqual(migration["canonical_module"], "app.lifecycle_outcome_visibility_runtime")
        self.assertEqual(migration["status"], "retired")
        self.assertEqual(migration["retired_on"], "2026-09-17")
        self.assertEqual(
            migration["retirement_record"],
            "docs/research/phase2-final-visibility-shim-retirement-2026-09-17.md",
        )
        self.assertFalse(migration["runtime_behavior_change"])
        self.assertGreaterEqual(len(migration["removal_gates"]), 5)

    def test_registry_tracks_retired_channel_source_fact_normalization_path(self) -> None:
        migration = self._migration("app.channel_part4_finalfix")
        self.assertEqual(migration["canonical_module"], "app.channel_source_fact_normalization_runtime")
        self.assertEqual(migration["status"], "retired")
        self.assertEqual(migration["retired_on"], "2026-09-17")
        self.assertEqual(
            migration["retirement_record"],
            "docs/research/channel-part4-finalfix-shim-retirement-2026-09-17.md",
        )
        self.assertFalse(migration["runtime_behavior_change"])
        self.assertGreaterEqual(len(migration["removal_gates"]), 6)

    def test_registry_tracks_retired_human_quality_gate_path(self) -> None:
        migration = self._migration("app.channel_part4_humanfix")
        self.assertEqual(
            migration["canonical_module"],
            "app.channel_human_quality_gate_runtime",
        )
        self.assertEqual(migration["status"], "retired")
        self.assertEqual(migration["introduced_on"], "2026-09-18")
        self.assertEqual(migration["retired_on"], "2026-09-18")
        self.assertEqual(
            migration["retirement_record"],
            "docs/research/channel-human-quality-gate-shim-retirement-2026-09-18.md",
        )
        self.assertTrue(migration["single_module_object_required"])
        self.assertFalse(migration["runtime_behavior_change"])
        self.assertGreaterEqual(len(migration["removal_gates"]), 7)

    def test_registry_tracks_retired_channel_quality_repair_path(self) -> None:
        migration = self._migration("app.channel_part4_qualityfix")
        self.assertEqual(
            migration["canonical_module"],
            "app.channel_quality_repair_runtime",
        )
        self.assertEqual(migration["status"], "retired")
        self.assertEqual(migration["introduced_on"], "2026-09-18")
        self.assertEqual(migration["retired_on"], "2026-09-18")
        self.assertEqual(
            migration["retirement_record"],
            "docs/research/channel-quality-repair-shim-retirement-2026-09-18.md",
        )
        self.assertTrue(migration["single_module_object_required"])
        self.assertFalse(migration["runtime_behavior_change"])
        self.assertGreaterEqual(len(migration["removal_gates"]), 8)

    def test_registry_tracks_active_x_resumable_recovery_path(self) -> None:
        migration = self._migration("app.phase3_recovery")
        self.assertEqual(
            migration["canonical_module"],
            "app.x_resumable_recovery_runtime",
        )
        self.assertEqual(migration["status"], "compatibility-shim")
        self.assertEqual(migration["introduced_on"], "2026-09-18")
        self.assertTrue(migration["single_module_object_required"])
        self.assertFalse(migration["runtime_behavior_change"])
        self.assertGreaterEqual(len(migration["removal_gates"]), 9)

    def test_registered_module_paths_are_unique(self) -> None:
        migrations = self._payload()["migrations"]
        legacy = [item["legacy_module"] for item in migrations]
        canonical = [item["canonical_module"] for item in migrations]
        self.assertEqual(len(legacy), len(set(legacy)))
        self.assertEqual(len(canonical), len(set(canonical)))

    def test_registered_migration_state_has_only_x_resumable_recovery_shim(self) -> None:
        active = [
            item["legacy_module"]
            for item in self._payload()["migrations"]
            if item["status"] == "compatibility-shim"
        ]
        self.assertEqual(active, ["app.phase3_recovery"])

    def test_active_legacy_and_canonical_paths_share_one_module_object(self) -> None:
        for migration in self._payload()["migrations"]:
            if migration["status"] != "compatibility-shim":
                continue
            with self.subTest(legacy_module=migration["legacy_module"]):
                legacy = importlib.import_module(migration["legacy_module"])
                canonical = importlib.import_module(migration["canonical_module"])
                self.assertIs(legacy, canonical)

    def test_retired_legacy_paths_are_absent_while_canonical_paths_import(self) -> None:
        for migration in self._payload()["migrations"]:
            if migration["status"] != "retired":
                continue
            legacy_module = migration["legacy_module"]
            legacy_path = ROOT / (legacy_module.replace(".", "/") + ".py")
            with self.subTest(legacy_module=legacy_module):
                self.assertFalse(legacy_path.exists())
                importlib.invalidate_caches()
                sys.modules.pop(legacy_module, None)
                with self.assertRaises(ModuleNotFoundError):
                    importlib.import_module(legacy_module)
                canonical = importlib.import_module(migration["canonical_module"])
                self.assertIsNotNone(canonical)


if __name__ == "__main__":
    unittest.main()
