from __future__ import annotations

import unittest

from app.channel_translation_playbook import (
    compact_style_examples,
    translation_demonstrations,
    unavailable_translation,
)


class _Example:
    def __init__(self, example_id: str, text: str, content_type: str = "OTHER"):
        self.example_id = example_id
        self.text = text
        self.content_type = content_type


class ChannelTranslationPlaybookTests(unittest.TestCase):
    def test_reaction_pairs_teach_colloquial_channel_persian(self):
        pairs = translation_demonstrations("VIDEO_REACTION", "en")
        target = "\n".join(item["target"] for item in pairs)
        self.assertIn("موهاشو", target)
        self.assertIn("چیکار می‌کنه", target)

    def test_japanese_gets_soft_paired_examples_without_extra_call(self):
        pairs = translation_demonstrations("MEMBER_QUOTE", "ja")
        self.assertLessEqual(len(pairs), 3)
        self.assertIn("منتظرم موندین", pairs[0]["target"])

    def test_historical_examples_are_small_and_monolingual_labeled(self):
        examples = [_Example(str(i), "x" * 900) for i in range(7)]
        payload = compact_style_examples(examples)
        self.assertEqual(len(payload), 3)
        self.assertTrue(all(len(item["persian_style_excerpt"]) == 420 for item in payload))
        self.assertNotIn("why_retrieved", payload[0])

    def test_unavailable_model_never_pretends_dictionary_fallback_is_translation(self):
        source = "캐럿들이 부끄러울 수도 있으니까 마스크 쓰고 있어요"
        result = unavailable_translation(source)
        self.assertIn("ترجمهٔ خودکار در دسترس نبود", result)
        self.assertIn(source, result)
        self.assertNotIn("قیراط", result)


if __name__ == "__main__":
    unittest.main()
