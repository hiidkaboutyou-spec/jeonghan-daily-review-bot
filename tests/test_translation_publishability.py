from __future__ import annotations

import json
import unittest
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

from app.ai import GroupCopy
from app.channel_entities import canonicalize_entities
from app.channel_translation_v2_install import _finalize_output
from app.models import EventGroup, MediaItem, Update
from app.organizer import detect_category, fallback_title
from app.translation_safety import (
    metadata_only,
    natural_persian_failures,
    safe_metadata_body,
    semantic_quality_failures,
)


def update(text: str, *, media=None, quoted_text="", quoted_author="") -> Update:
    return Update(
        id="1", url="https://x.com/source/status/1", author="source", author_name="Source",
        text=text, created_at=datetime(2026, 8, 9, 20, 15, tzinfo=timezone.utc),
        media=media or [], quoted_text=quoted_text, quoted_author=quoted_author,
    )


class TranslationPublishabilityTests(unittest.TestCase):
    def test_channel_entities_are_source_authorized_and_protected(self):
        source = "Jeonghan met Seungcheol, Dokyeom and Mingyu from SEVENTEEN"
        bad = "جیونگان با سئونگ چئول، DK و Mingyu از هفده دیدار کرد #SEVENTEEN"
        self.assertEqual(
            canonicalize_entities(source, bad),
            "جونگهان با سونگچول، دوکیوم و مینگیو از سونتین دیدار کرد #SEVENTEEN",
        )
        self.assertEqual(canonicalize_entities("someone arrived", "هفده نفر اومدن"), "هفده نفر اومدن")

    def test_bad_challenge_is_not_misclassified_as_ad(self):
        item = update("Jeonghan showed his BAD side in the challenge video")
        self.assertEqual(detect_category(item), "general")
        self.assertEqual(fallback_title("general", item), "آپدیت جونگهان")
        item.text = "Sponsored ad campaign with Jeonghan"
        self.assertEqual(detect_category(item), "brand")

    def test_quoted_post_is_explicit_translation_context(self):
        item = update("His reply", quoted_text="Jeonghan called Seungcheol", quoted_author="svt")
        self.assertEqual(
            item.translation_source(),
            "His reply\n\n[QUOTED POST]\n@svt: Jeonghan called Seungcheol",
        )
        self.assertFalse(metadata_only(item))

    def test_media_and_hashtag_only_are_not_translation_failures(self):
        photo = MediaItem(kind="photo", url="https://pbs.twimg.com/a.jpg")
        for item in (update("", media=[photo]), update("#JEONGHAN #정한", media=[photo])):
            self.assertTrue(metadata_only(item))
            self.assertEqual(semantic_quality_failures(item, ""), [])
            self.assertIn("پست تصویری بدون متن", safe_metadata_body(item))

    def test_reported_2345_failures_are_closed_to_manual_review(self):
        fixture = Path(__file__).parent / "fixtures" / "translation_quality_2345.json"
        cases = json.loads(fixture.read_text(encoding="utf-8"))
        for case in cases:
            for bad in case["bad_outputs"]:
                with self.subTest(case=case["id"], output=bad):
                    item = update(case["source"])
                    normalized = canonicalize_entities(case["source"], bad)
                    failures = semantic_quality_failures(item, normalized)
                    # Entity-only defects are repaired deterministically; literal,
                    # nonsense and untranslated residues must fail closed.
                    if case["id"] != "reported-name-variants":
                        self.assertTrue(failures)

    def test_failed_output_is_visibly_manual_not_ready_copy(self):
        item = update("Please set up the phone for Jeonghan")
        group = EventGroup("x", "general", "آپدیت جونگهان", [item])
        writer = SimpleNamespace(last_diagnostics={})
        result = _finalize_output(writer, group, GroupCopy(group.title, "general", {"1": "گوشی را راه‌اندازی کنید"}))
        self.assertTrue(result.bodies["1"].startswith("⚠️ نیاز به بازبینی دستی"))
        self.assertIn("1", writer.last_manual_review)

    def test_en_ko_ja_and_mixed_untranslated_output_fail_closed(self):
        cases = (
            ("Jeonghan posted a new video today", "Jeonghan posted new video"),
            ("정한이 오늘 새 영상을 올렸어", "정한이 오늘 새 영상을 올렸어"),
            ("ジョンハンが新しい動画を投稿した", "ジョンハン new video"),
            ("정한 said this is funny", "정한 said funny"),
        )
        for source, output in cases:
            with self.subTest(source=source):
                self.assertIn("substantial untranslated source language", semantic_quality_failures(update(source), output))

    def test_real_b13_bookish_reaction_is_not_publishable(self):
        item = update("jeonghan fixing his hair and then immediately smiling at the camera 😭")
        bad = "جونگهان موهایش را مرتب می کند و بلافاصله به دوربین لبخند می زند 😭"
        self.assertEqual(
            natural_persian_failures(item, bad),
            ["bookish or machine-like register for informal source"],
        )
        self.assertIn("bookish or machine-like register for informal source", semantic_quality_failures(item, bad))

    def test_real_live_artifact_formal_fallback_and_broken_clitic_are_rejected(self):
        interview = update(
            "Jeonghan: I think I’m happiest when I can make the people around me laugh."
        )
        formal = "جونگهان: فکر می‌کنم وقتی می‌توانم اطرافیانم را بخندانم خوشحال می‌شوم."
        self.assertTrue(natural_persian_failures(interview, formal))

        reaction = update("THAT’S JEONGHAN’S ORANGE IPHONE ㅋㅋㅋ mirror selca 😂")
        broken = "این آیفون نارنجی مال جونگهان ـه ㅋㅋㅋ 😂"
        self.assertTrue(natural_persian_failures(reaction, broken))


if __name__ == "__main__":
    unittest.main()
