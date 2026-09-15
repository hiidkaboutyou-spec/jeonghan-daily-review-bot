from __future__ import annotations

import importlib
import json
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
REGISTRY = ROOT / "config" / "module_migrations.json"


class ModuleMigrationRegistryTests(unittest.TestCase):
    def test_registry_tracks_degraded_x_recovery_compatibility_shim(self) -> None:
        payload = json.loads(REGISTRY.read_text(encoding="utf-8"))
        self.assertEqual(payload["schema_version"], 1)

        migration = next(
            item
            for item in payload["migrations"]
            if item["legacy_module"] == "app.live_recovery_hardening"
        )
        self.assertEqual(
            migration["canonical_module"],
            "app.x_degraded_recovery_runtime",
        )
        self.assertEqual(migration["status"], "compatibility-shim")
        self.assertTrue(migration["single_module_object_required"])
        self.assertFalse(migration["runtime_behavior_change"])
        self.assertGreaterEqual(len(migration["removal_gates"]), 4)

    def test_registered_legacy_and_canonical_paths_share_one_module_object(self) -> None:
        payload = json.loads(REGISTRY.read_text(encoding="utf-8"))
        migration = payload["migrations"][0]

        legacy = importlib.import_module(migration["legacy_module"])
        canonical = importlib.import_module(migration["canonical_module"])
        self.assertIs(legacy, canonical)


if __name__ == "__main__":
    unittest.main()
