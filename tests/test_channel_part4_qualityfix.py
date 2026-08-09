import unittest

from app import channel_part4_humanfix as humanfix
from app.channel_part4_qualityfix import (
    _laughter_count_failures,
    _normalize_source_authorized_japanese,
)


class Part4QualityFixTests(unittest.TestCase):
    def test_laughter_count_must_match_exactly(self):
        source = "정한: 모르겠다 ㅋㅋㅋ"
        self.assertEqual(_laughter_count_failures(source, "جونگهان: نمی‌دونم ㅋㅋㅋ"), [])
        self.assertTrue(_laughter_count_failures(source, "جونگهان: نمی‌دونم ㅋㅋㅋ ㅋㅋㅋ"))
        self.assertTrue(_laughter_count_failures(source, "جونگهان: نمی‌دونم"))

    def test_different_laughter_sequence_is_not_equivalent(self):
        source = "정한: 아 ㅎㅎㅎ"
        self.assertTrue(_laughter_count_failures(source, "جونگهان: آه ㅋㅋㅋ"))

    def test_okairi_literalism_is_normalized_only_when_source_authorizes_it(self):
        awkward = "وقتی میگید «به بازگشت خوش اومدی» خیلی حس خوبیه"
        fixed = _normalize_source_authorized_japanese("おかえり〜って言ってくれるの", awkward)
        self.assertEqual(fixed, "وقتی میگید «خوش اومدی» خیلی حس خوبیه")
        self.assertEqual(_normalize_source_authorized_japanese("ありがとう", awkward), awkward)

    def test_writer_fingerprint_is_bumped_for_fresh_evidence(self):
        self.assertEqual(humanfix.HUMAN_GATE_FINGERPRINT, "channel-human-gate-v2")


if __name__ == "__main__":
    unittest.main()
