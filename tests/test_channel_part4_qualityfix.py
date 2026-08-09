import unittest

from app import channel_part4_humanfix as humanfix
from app.channel_part4_qualityfix import (
    _laughter_count_failures,
    _normalize_source_authorized_identity,
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

    def test_bad_neutral_jeonghan_spellings_are_source_authorized(self):
        source = "정한: 오랜만이에요 캐럿들"
        self.assertEqual(
            _normalize_source_authorized_identity(source, "جئونگان: خیلی وقته ندیدمتون"),
            "جونگهان: خیلی وقته ندیدمتون",
        )
        self.assertEqual(
            _normalize_source_authorized_identity(source, "جونگان: سلام"),
            "جونگهان: سلام",
        )

    def test_identity_repair_cannot_be_authorized_by_output_alone(self):
        output = "جئونگان: سلام"
        self.assertEqual(_normalize_source_authorized_identity("Joshua: hi", output), output)

    def test_identity_repair_does_not_touch_hashtag(self):
        source = "정한 update #JEONGHAN"
        output = "جئونگان آپدیت #JEONGHAN"
        self.assertEqual(
            _normalize_source_authorized_identity(source, output),
            "جونگهان آپدیت #JEONGHAN",
        )

    def test_writer_fingerprint_is_bumped_for_fresh_evidence(self):
        self.assertEqual(humanfix.HUMAN_GATE_FINGERPRINT, "channel-human-gate-v3")


if __name__ == "__main__":
    unittest.main()
