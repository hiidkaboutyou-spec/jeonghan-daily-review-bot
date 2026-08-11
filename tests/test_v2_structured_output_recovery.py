from __future__ import annotations

import unittest

from app.channel_translation_v2_install import _parse_v2_bodies


class V2StructuredOutputRecoveryTests(unittest.TestCase):
    def test_exact_id_path_is_unchanged(self):
        parsed = {"items": [{"id": "A", "body": "ترجمه"}]}
        self.assertEqual(_parse_v2_bodies(parsed, ["A"]), {"A": "ترجمه"})

    def test_single_item_wrong_model_id_recovers_by_unambiguous_order(self):
        parsed = {"items": [{"id": "1", "body": "ترجمهٔ درست"}]}
        self.assertEqual(_parse_v2_bodies(parsed, ["B01"]), {"B01": "ترجمهٔ درست"})

    def test_single_item_missing_id_recovers_by_unambiguous_order(self):
        parsed = {"items": [{"body": "ترجمهٔ درست"}]}
        self.assertEqual(_parse_v2_bodies(parsed, ["B01"]), {"B01": "ترجمهٔ درست"})

    def test_multi_item_id_mismatch_stays_fail_closed(self):
        parsed = {
            "items": [
                {"id": "1", "body": "اول"},
                {"id": "2", "body": "دوم"},
            ]
        }
        self.assertIsNone(_parse_v2_bodies(parsed, ["A", "B"]))

    def test_empty_body_stays_fail_closed(self):
        parsed = {"items": [{"id": "wrong", "body": "   "}]}
        self.assertIsNone(_parse_v2_bodies(parsed, ["A"]))


if __name__ == "__main__":
    unittest.main()
