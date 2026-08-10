from __future__ import annotations

import unittest

from app.channel_part4_humanfix import (
    _canonicalize_speaker_labels,
    _metadata_line_failures,
    _needs_human_polish,
    _restore_metadata_linebreaks,
    _restore_metadata_labels,
    _restore_speaker_labels,
    _source_emoji_failures,
)
from app.channel_style_runtime import analyze_source, verify_hard_facts


class Part4HumanGateHardeningTests(unittest.TestCase):
    def test_missing_source_emoji_is_hard_failure(self):
        source = "jeonghan looks pretty 😂🤣"
        output = "جونگهان خیلی خوشگله"
        failures = verify_hard_facts(source, output, analyze_source(source))
        self.assertTrue(any("missing source emoji" in item for item in failures))

    def test_repeated_source_emoji_count_is_preserved(self):
        self.assertEqual(_source_emoji_failures("😭😭😭", "😭😭"), ["missing source emoji: 😭 x1"])

    def test_metadata_label_must_remain_on_own_line(self):
        source = "🪽: 쉿 ㅎㅎㅎ\nfan trans: translated line\nsource: https://example.com"
        glued = "🪽: هیس ㅎㅎㅎ fan trans: translated line\nsource: https://example.com"
        self.assertIn("metadata line boundary lost: fan trans", _metadata_line_failures(source, glued))
        repaired = _restore_metadata_linebreaks(source, glued)
        self.assertIn("\nfan trans:", repaired)

    def test_translated_metadata_labels_are_restored_exactly(self):
        source = 'fan trans: "try"\nsource: https://example.com'
        output = 'فن ترنس: «سعی کن»\nمنبع: https://example.com'
        self.assertEqual(
            _restore_metadata_labels(source, output),
            'fan trans: «سعی کن»\nsource: https://example.com',
        )

    def test_speaker_emoji_labels_are_restored_by_turn_order(self):
        source = "🍒: one\n🪽: two\n🐶: three"
        output = "🍒: یک\n🗽: دو\n🐶: سه"
        self.assertEqual(_restore_speaker_labels(source, output), "🍒: یک\n🪽: دو\n🐶: سه")

    def test_changed_calendar_date_requests_human_polish(self):
        source = "🐶: 오늘 8월 20일 스케줄도 있잖아요\n🪽: 맞아"
        output = "🐶: امروز ۳۰ مرداد برنامه داری\n🪽: آره"
        self.assertTrue(_needs_human_polish(source, output, analyze_source(source)))

    def test_known_speaker_labels_use_channel_canonical_spelling(self):
        source = "정한: today was fun\nJoshua: 맞아"
        output = "정한: امروز خوش گذشت\nJoshua: آره"
        fixed = _canonicalize_speaker_labels(source, output)
        self.assertEqual(fixed, "جونگهان: امروز خوش گذشت\nجاشوآ: آره")

    def test_hashtag_is_not_changed_by_speaker_canonicalization(self):
        source = "Jeonghan: hi #JEONGHAN"
        output = "Jeonghan: سلام #JEONGHAN"
        fixed = _canonicalize_speaker_labels(source, output)
        self.assertEqual(fixed, "جونگهان: سلام #JEONGHAN")

    def test_japanese_and_known_nuance_cases_request_human_polish(self):
        ja = "ジョンハン: ありがとね"
        self.assertTrue(_needs_human_polish(ja, "جونگهان: ممنون‌ها", analyze_source(ja)))
        ko = "정한: 나도 모르겠다 ㅋㅋㅋ"
        self.assertTrue(_needs_human_polish(ko, "جونگهان: منم بلد نیستم ㅋㅋㅋ", analyze_source(ko)))

    def test_informal_reaction_with_bookish_output_requests_polish(self):
        source = "why does jeonghan look this pretty 😭"
        output = "چرا جونگهان این‌قدر زیبا به نظر می‌رسد 😭"
        self.assertTrue(_needs_human_polish(source, output, analyze_source(source)))


if __name__ == "__main__":
    unittest.main()
