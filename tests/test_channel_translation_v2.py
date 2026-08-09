from __future__ import annotations

import unittest
from datetime import datetime, timezone

from app.ai import CaptionWriter, GroupCopy
from app.channel_entities import (
    canonicalize_jeonghan,
    entity_failures,
    preferred_jeonghan_form,
    source_names_jeonghan,
)
from app.channel_translation_v2 import DIRECT_PIPELINE_VERSION
from app.channel_translation_v2_install import harden_legacy_instance
from app.models import EventGroup, Update


class _Memory:
    profile = {}

    def retrieve(self, *args, **kwargs):
        return []


class ContextualJeonghanTests(unittest.TestCase):
    def test_plain_name_uses_jeonghan(self):
        for source in (
            "Jeonghan arrived",
            "정한 왔어",
            "정한이 왔어",
            "정한아 뭐해",
            "정한이형 왔어",
            "ジョンハンが来た",
        ):
            with self.subTest(source=source):
                self.assertTrue(source_names_jeonghan(source))
                self.assertEqual(preferred_jeonghan_form(source), "جونگهان")
                self.assertEqual(canonicalize_jeonghan(source, "جیونگهان اومد"), "جونگهان اومد")
                self.assertEqual(entity_failures(source, "جونگهان اومد"), [])

    def test_explicit_full_name_uses_yoon_jeonghan(self):
        for source in (
            "Yoon Jeonghan arrived",
            "윤정한 왔어",
            "윤정한이 왔어",
            "ユン・ジョンハンが来た",
        ):
            with self.subTest(source=source):
                self.assertTrue(source_names_jeonghan(source))
                self.assertEqual(preferred_jeonghan_form(source), "یون جونگهان")
                self.assertEqual(canonicalize_jeonghan(source, "جونگهان اومد"), "یون جونگهان اومد")
                self.assertEqual(entity_failures(source, "یون جونگهان اومد"), [])

    def test_hani_nickname_uses_hani(self):
        for source in (
            "Hani happy birthday",
            "Hannie happy birthday",
            "하니 생일 축하한다",
            "정하니형 사랑해",
            "윤정하니 생일축하해",
            "ハニ お誕生日おめでとう",
        ):
            with self.subTest(source=source):
                self.assertTrue(source_names_jeonghan(source))
                self.assertEqual(preferred_jeonghan_form(source), "هانی")
                self.assertEqual(canonicalize_jeonghan(source, "جونگهان تولدت مبارک"), "هانی تولدت مبارک")
                self.assertEqual(entity_failures(source, "هانی تولدت مبارک"), [])

    def test_contextual_forms_are_not_treated_as_errors(self):
        self.assertEqual(entity_failures("Yoon Jeonghan arrived", "یون جونگهان اومد"), [])
        self.assertEqual(entity_failures("Jeonghan arrived", "جونگهان اومد"), [])
        self.assertEqual(entity_failures("하니 생일 축하한다", "هانی تولدت مبارک"), [])

    def test_wrong_contextual_form_is_normalized(self):
        self.assertEqual(canonicalize_jeonghan("Yoon Jeonghan arrived", "هانی اومد"), "یون جونگهان اومد")
        self.assertEqual(canonicalize_jeonghan("하니 왔어", "یون جونگهان اومد"), "هانی اومد")
        self.assertEqual(canonicalize_jeonghan("정한 왔어", "یون جونگهان اومد"), "جونگهان اومد")

    def test_untranslated_source_script_forms_are_canonicalized(self):
        cases = (
            ("Jeonghan arrived", "jeonghan اومد", "جونگهان اومد"),
            ("Jeonghan arrived", "Jeonghan's گوشی", "جونگهان گوشی"),
            ("정한 왔어", "정한 اومد", "جونگهان اومد"),
            ("ジョンハンが来た", "ジョンハン اومد", "جونگهان اومد"),
            ("Yoon Jeonghan arrived", "Yoon Jeonghan اومد", "یون جونگهان اومد"),
            ("하니 왔어", "Hani اومد", "هانی اومد"),
        )
        for source, output, expected in cases:
            with self.subTest(source=source, output=output):
                self.assertEqual(canonicalize_jeonghan(source, output), expected)

    def test_real_b01_quota_fallback_repairs_every_untranslated_jeonghan(self):
        source = (
            "THAT’S JEONGHAN’S ORANGE IPHONE ㅋㅋㅋ bae jeewan took his mirror selca at the clothing store "
            "in jeju using jeonghan’s phone😂 it’s clearly jeonghan’s because he keeps his bank card inside "
            "the clear phone case🤣"
        )
        fallback = (
            "این آیفون نارنجی جونگهان است ㅋㅋㅋ باe jeewan سلکای آینه خود را در فروشگاه لباس در ججو "
            "با استفاده از تلفن jeonghan گرفت 😂 این به وضوح متعلق به jeonghan است زیرا کارت بانکی خود را "
            "داخل قاب گوشی تمیز نگه می دارد. 🤣"
        )
        fixed = canonicalize_jeonghan(source, fallback)
        self.assertNotRegex(fixed, r"(?i)(?<![#@])jeonghan")
        self.assertEqual(fixed.count("جونگهان"), 3)

    def test_mixed_source_forms_preserve_valid_translation_family(self):
        source = "Yoon Jeonghan introduced himself, then everyone called him Hani."
        self.assertIsNone(preferred_jeonghan_form(source))
        output = "یون جونگهان خودش رو معرفی کرد، بعد همه صداش کردن هانی."
        self.assertEqual(canonicalize_jeonghan(source, output), output)
        self.assertEqual(entity_failures(source, output), [])

    def test_hashtag_alone_does_not_authorize_prose_entity_invention(self):
        source = "new photos #JEONGHAN"
        self.assertFalse(source_names_jeonghan(source))
        self.assertEqual(canonicalize_jeonghan(source, "جیونگهان خیلی خوشگله"), "جیونگهان خیلی خوشگله")

    def test_hashtag_and_url_are_never_rewritten(self):
        source = "Yoon Jeonghan update #JEONGHAN https://example.com/Jeonghan"
        output = "jeonghan آپدیت #JEONGHAN https://example.com/Jeonghan"
        self.assertEqual(
            canonicalize_jeonghan(source, output),
            "یون جونگهان آپدیت #JEONGHAN https://example.com/Jeonghan",
        )

    def test_missing_name_is_a_hard_entity_failure(self):
        self.assertEqual(
            entity_failures("정한 왔어", "اومد"),
            ["missing contextual source entity: جونگهان"],
        )
        self.assertEqual(
            entity_failures("윤정한 왔어", "اومد"),
            ["missing contextual source entity: یون جونگهان"],
        )
        self.assertEqual(
            entity_failures("하니 왔어", "اومد"),
            ["missing contextual source entity: هانی"],
        )


class HardenedLegacyTests(unittest.TestCase):
    def test_legacy_instance_is_canonicalized_before_delivery(self):
        class Probe(CaptionWriter):
            def _client_or_none(self):
                return object()

            def _fallback_group(self, group):
                return GroupCopy(group.title, group.category, {"1": "jeonghan اومد"})

            def _model_candidates(self):
                return []

        update = Update(
            id="1",
            url="https://x.com/source/status/1",
            author="source",
            author_name="Source",
            text="Jeonghan arrived",
            created_at=datetime(2026, 8, 9, tzinfo=timezone.utc),
            lang="en",
        )
        group = EventGroup(key="x", category="general", title="title", updates=[update])
        writer = harden_legacy_instance(Probe("key", "model", _Memory()))
        out = writer.write_group(group)
        self.assertEqual(out.bodies["1"], "جونگهان اومد")

    def test_pipeline_version_is_explicit(self):
        self.assertEqual(DIRECT_PIPELINE_VERSION, "channel-direct-v2")


if __name__ == "__main__":
    unittest.main()
