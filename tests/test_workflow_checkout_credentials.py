from __future__ import annotations

import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
WORKFLOWS = ROOT / ".github" / "workflows"


class WorkflowCheckoutCredentialTests(unittest.TestCase):
    def test_every_checkout_disables_persisted_credentials(self):
        checkout_count = 0
        missing: list[str] = []

        for path in sorted(WORKFLOWS.glob("*.yml")):
            lines = path.read_text(encoding="utf-8").splitlines()
            for index, line in enumerate(lines):
                if "uses: actions/checkout@" not in line:
                    continue
                checkout_count += 1
                nearby = "\n".join(lines[index : index + 6])
                if "persist-credentials: false" not in nearby:
                    missing.append(f"{path.name}:{index + 1}")

        self.assertGreater(checkout_count, 0)
        self.assertEqual(
            missing,
            [],
            "Every checkout step must set persist-credentials: false: "
            + ", ".join(missing),
        )


if __name__ == "__main__":
    unittest.main()
