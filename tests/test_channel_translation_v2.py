from __future__ import annotations

import unittest
from datetime import datetime, timezone

from app.ai import CaptionWriter, GroupCopy
from app.channel_entities import canonicalize_jeonghan, entity_failures, source_names_jeonghan
from app.channel_translation_v2 import DIRECT_PIPELINE_VERSION
from app.channel_translation_v2_install import harden_legacy_instance
from app.models import EventGroup, Update


class _Memory:
    profile = {}

    def retrieve(self, *args, **kwargs):
        return []


class CanonicalJeonghanTests(unittest.TestCase):
    def test_all_supported_source_scripts_authorize_exact_channel_spelling(self):
        for source in ("Jeonghan arrived", "Yoon Jeonghan arrived", "정한 왔어", "윤정한 왔어", "ジョンハン おかえり"):
            with self.subTest(source=source):
                self.assertTrue(source_names_jeonghan(source))
                self.assertEqual(canonicalize_jeonghan(source, "جیونگهان اومد"), "جونگهان اومد")
                self.assertEqual(entity_failures(source, "جونگهان اومد"), [])

    def test_untranslated_source_script_forms_are_canonicalized(self):
        source = "Jeonghan called 정한 and ジョンハン"
        for output in ("jeonghan اومد", "Jeonghan's گوشی", "정한 اومد", "ジョンハン اومد"):
            with self.subTest(output=output):
                self.assertIn("جونگهان", canonicalize_jeonghan(source, output))
                self.assertNotRegex(canonicalize_jeonghan(source, output), r"(?i)jeonghan|정한|ジョンハン")

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

    def test_hashtag_alone_does_not_authorize_prose_entity_invention(self):
        source = "new photos #JEONGHAN"
        self.assertFalse(source_names_jeonghan(source))
        self.assertEqual(canonicalize_jeonghan(source, "جیونگهان خیلی خوشگله"), "جیونگهان خیلی خوشگله")

    def test_hashtag_and_url_are_never_rewritten(self):
        source = "Jeonghan update #JEONGHAN https://example.com/Jeonghan"
        output = "jeonghan آپدیت #JEONGHAN https://example.com/Jeonghan"
        self.assertEqual(
            canonicalize_jeonghan(source, output),
            "جونگهان آپدیت #JEONGHAN https://example.com/Jeonghan",
        )

    def test_missing_canonical_name_is_a_hard_entity_failure(self):
        self.assertEqual(entity_failures("정한 왔어", "اومد"), ["missing canonical source entity: جونگهان"])


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
