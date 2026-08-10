from __future__ import annotations

import unittest

from app.channel_part4_hardening import (
    _canonicalize_source_authorized_terms,
    _restore_dialogue_linebreaks,
    semantic_number_tokens,
    verify_hard_facts,
)
from app.channel_style_runtime import analyze_source


class Part4SemanticNumberTests(unittest.TestCase):
    def assertNumbersEquivalent(self, source: str, output: str) -> None:
        failures = [item for item in verify_hard_facts(source, output, analyze_source(source)) if "number" in item]
        self.assertEqual(failures, [])

    def test_korean_adjacent_clock_and_date_numbers_are_preserved(self):
        self.assertNumbersEquivalent(
            "3시 반, 3:32쯤. 오늘 8월 20일이고 12시 전에 자요.",
            "۳ و نیم، حدود ۳:۳۲. امروز ۲۰ آگوست است و قبل از ۱۲ می‌خوابم.",
        )

    def test_japanese_month_day_and_issue_month_are_equivalent_to_persian_month_names(self):
        self.assertNumbersEquivalent(
            "8月15日発売の『SPUR』10月号",
            "شماره اکتبر SPUR که ۱۵ آگوست منتشر می‌شود",
        )

    def test_hangul_counter_number_is_detected(self):
        self.assertNumbersEquivalent("10초만 남았어요!", "فقط ۱۰ ثانیه مونده!")

    def test_thousands_separator_and_date_layout_are_semantically_equivalent(self):
        self.assertNumbersEquivalent(
            "No. 2 with 18,742 votes. closes on 2026-08-20 at 23:59 KST.",
            "با ۱۸۷۴۲ رای رتبه دوم است. در 20/08/2026 ساعت ۲۳:۵۹ بسته می‌شود.",
        )

    def test_genuinely_changed_number_still_fails(self):
        failures = verify_hard_facts("10초만 남았어요!", "فقط ۱۱ ثانیه مونده!", analyze_source("10초만 남았어요!"))
        self.assertTrue(any("number" in item for item in failures))

    def test_semantic_tokenizer_does_not_duplicate_date_components(self):
        self.assertEqual(
            semantic_number_tokens("2026-08-20 at 23:59"),
            ["date:2026-08-20", "time:23:59"],
        )


class Part4IdentityAndDialogueTests(unittest.TestCase):
    def test_joshua_common_persian_spelling_preserves_identity(self):
        source = "With Joshua?"
        failures = verify_hard_facts(source, "با جاشوا؟", analyze_source(source))
        self.assertNotIn("name/identity dropped: JOSHUA", failures)

    def test_seungcheol_transliteration_variant_preserves_identity(self):
        source = "Jeonghan teased Seungcheol for not calling first."
        failures = verify_hard_facts(source, "جونگهان سئونگ‌چول رو دست انداخت چون اول زنگ نزده بود.", analyze_source(source))
        self.assertNotIn("name/identity dropped: S.COUPS", failures)

    def test_collapsed_emoji_dialogue_is_restored_without_inventing_labels(self):
        source = "👤: Did you eat yet?\n🪽: I ate curry!\n👤: With Joshua?\n🪽: No ㅋㅋㅋ"
        collapsed = "👤: غذا خوردی؟ 🪽: کاری خوردم! 👤: با جاشوا؟ 🪽: نه ㅋㅋㅋ"
        restored = _restore_dialogue_linebreaks(source, collapsed)
        self.assertEqual(len(restored.splitlines()), 4)
        self.assertEqual([line.split(":", 1)[0] for line in restored.splitlines()], ["👤", "🪽", "👤", "🪽"])
        failures = verify_hard_facts(source, restored, analyze_source(source))
        self.assertNotIn("speaker turn structure lost", failures)

    def test_channel_canonicalization_is_source_authorized(self):
        source = "Jeonghan and Joshua talked to Seungcheol on a fancall."
        output = "Jeonghan و جاشوا با سئونگ‌چول توی فنس‌کال حرف زدن."
        fixed = _canonicalize_source_authorized_terms(source, output)
        self.assertIn("جونگهان", fixed)
        self.assertIn("جاشوآ", fixed)
        self.assertIn("سونگچول", fixed)
        self.assertIn("فن‌کال", fixed)

    def test_channel_canonicalization_does_not_invent_unmentioned_identity(self):
        source = "Jeonghan posted a photo."
        output = "جونگهان یه عکس گذاشت؛ جاشوا هم اونجا بود."
        fixed = _canonicalize_source_authorized_terms(source, output)
        self.assertIn("جاشوا", fixed)
        failures = verify_hard_facts(source, fixed, analyze_source(source))
        self.assertIn("invented name/identity: JOSHUA", failures)


if __name__ == "__main__":
    unittest.main()
