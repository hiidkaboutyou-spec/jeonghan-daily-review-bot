from __future__ import annotations

import unittest

from app.channel_translation_v2 import (
    MAX_GLOSSARY_ENTRIES,
    _compact_glossary,
)


class TokenOptimizerTests(unittest.TestCase):
    def test_compact_glossary_is_bounded_and_keeps_spelling_fields(self):
        entries = [
            {
                "canonical_form": "Jeonghan",
                "alternatives": ["정한", "윤정한", "جونگهان", "extra"],
                "category": "member_names",
                "irrelevant_large_field": "x" * 5000,
            }
            for _ in range(MAX_GLOSSARY_ENTRIES + 4)
        ]

        compact = _compact_glossary(entries)

        self.assertLessEqual(len(compact), MAX_GLOSSARY_ENTRIES)
        self.assertEqual(compact[0]["canonical_form"], "Jeonghan")
        self.assertEqual(compact[0]["alternatives"], ["정한", "윤정한", "جونگهان", "extra"])
        self.assertNotIn("irrelevant_large_field", compact[0])

    def test_compact_glossary_ignores_malformed_entries(self):
        self.assertEqual(_compact_glossary([None, "bad", {}, {"canonical_form": "جونگهان"}]), [{"canonical_form": "جونگهان"}])


if __name__ == "__main__":
    unittest.main()
