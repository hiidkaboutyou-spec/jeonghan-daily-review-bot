from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from tools.benchmark_status import BenchmarkStatusError, validate_and_record


class BenchmarkStatusTests(unittest.TestCase):
    def write(self, root: str, payload: dict) -> Path:
        path = Path(root) / "benchmark.json"
        path.write_text(json.dumps(payload), encoding="utf-8")
        return path

    def test_success_requires_pass(self):
        with tempfile.TemporaryDirectory() as root:
            self.assertEqual(validate_and_record(self.write(root, {"quality_status": "PASS"}), 0), "PASS")

    def test_validated_quota_block_is_visible_and_non_passing(self):
        with tempfile.TemporaryDirectory() as root:
            path = self.write(root, {
                "quality_status": "INCOMPLETE",
                "cases": [{"api_diagnostics": {"new_pipeline": {
                    "quota_429": True, "api_status": 429, "exception_class": "RESOURCE_EXHAUSTED"
                }}}],
            })
            self.assertEqual(validate_and_record(path, 3), "BLOCKED_BY_EXTERNAL_QUOTA")
            payload = json.loads(path.read_text(encoding="utf-8"))
            self.assertTrue(payload["quota_blocked"])
            self.assertEqual(payload["human_gate_status"], "NOT_PASSED")
            self.assertEqual(payload["merge_authorization"], "NOT_GRANTED")

    def test_exit_three_without_quota_evidence_fails(self):
        with tempfile.TemporaryDirectory() as root:
            path = self.write(root, {"quality_status": "INCOMPLETE", "cases": []})
            with self.assertRaises(BenchmarkStatusError):
                validate_and_record(path, 3)

    def test_genuine_failure_stays_failure(self):
        with tempfile.TemporaryDirectory() as root:
            path = self.write(root, {"quality_status": "INCOMPLETE", "cases": []})
            with self.assertRaises(BenchmarkStatusError):
                validate_and_record(path, 2)


if __name__ == "__main__":
    unittest.main()
