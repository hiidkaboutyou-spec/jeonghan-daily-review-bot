from __future__ import annotations

import importlib
import json
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

    def test_registry_tracks_degraded_x_recovery_compatibility_shim(self) -> None:
        payload = self._payload()
        self.assertEqual(payload["schema_version"], 1)

        migration = self._migration("app.live_recovery_hardening")
        self.assertEqual(
            migration["canonical_module"],
            "app.x_degraded_recovery_runtime",
        )
        self.assertEqual(migration["status"], "compatibility-shim")
        self.assertTrue(migration["single_module_object_required"])
        self.assertFalse(migration["runtime_behavior_change"])
        self.assertGreaterEqual(len(migration["removal_gates"]), 4)

    def test_registry_tracks_lifecycle_correlation_compatibility_shim(self) -> None:
        migration = self._migration("app.phase2_correlation_stability")
        self.assertEqual(
            migration["canonical_module"],
            "app.lifecycle_correlation_runtime",
        )
        self.assertEqual(migration["status"], "compatibility-shim")
        self.assertTrue(migration["single_module_object_required"])
        self.assertFalse(migration["runtime_behavior_change"])
        self.assertGreaterEqual(len(migration["removal_gates"]), 5)

    def test_registered_module_paths_are_unique(self) -> None:
        migrations = self._payload()["migrations"]
        legacy = [item["legacy_module"] for item in migrations]
        canonical = [item["canonical_module"] for item in migrations]
        self.assertEqual(len(legacy), len(set(legacy)))
        self.assertEqual(len(canonical), len(set(canonical)))

    def test_registered_legacy_and_canonical_paths_share_one_module_object(self) -> None:
        for migration in self._payload()["migrations"]:
            with self.subTest(legacy_module=migration["legacy_module"]):
                legacy = importlib.import_module(migration["legacy_module"])
                canonical = importlib.import_module(migration["canonical_module"])
                self.assertIs(legacy, canonical)


if __name__ == "__main__":
    unittest.main()
