from __future__ import annotations

import unittest

from app.channel_part4_hardening import verify_hard_facts
from app.channel_part4_finalfix import _safe_canonicalize_source_authorized_terms
from app.channel_style_runtime import analyze_source


class Part4FinalEdgeTests(unittest.TestCase):
    def test_first_and_persian_ordinal_are_semantically_equivalent(self):
        source = "Jeonghan teased Seungcheol for not calling him first."
        output = "جونگهان سونگچول رو دست انداخت چون اول بهش زنگ نزده بود."
        failures = verify_hard_facts(source, output, analyze_source(source))
        self.assertFalse(any("semantic numbers" in item for item in failures), failures)

    def test_source_hashtag_is_never_canonicalized(self):
        source = "source: https://example.com/live/820 #JEONGHAN #SCOUPS"
        output = "source: https://example.com/live/820 #JEONGHAN #SCOUPS"
        fixed = _safe_canonicalize_source_authorized_terms(source, output)
        self.assertIn("#JEONGHAN", fixed)
        self.assertNotIn("#جونگهان", fixed)
        self.assertEqual(verify_hard_facts(source, fixed, analyze_source(source)), [])

    def test_prose_jeonghan_still_canonicalizes_without_touching_hashtag(self):
        source = "Jeonghan posted #JEONGHAN"
        output = "Jeonghan پست گذاشت #JEONGHAN"
        fixed = _safe_canonicalize_source_authorized_terms(source, output)
        self.assertTrue(fixed.startswith("جونگهان"), fixed)
        self.assertTrue(fixed.endswith("#JEONGHAN"), fixed)

    def test_changed_ordinal_still_fails(self):
        source = "He called first."
        output = "اون سوم زنگ زد."
        failures = verify_hard_facts(source, output, analyze_source(source))
        self.assertTrue(any("semantic numbers" in item for item in failures), failures)


if __name__ == "__main__":
    unittest.main()
