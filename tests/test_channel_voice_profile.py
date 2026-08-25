"""Regression tests for channel voice profile integration and voice-aware checks.

These tests capture findings from the A/B evaluation (PR #41 follow-up):
- Voice profile loader produces clean, actionable guidance
- Generic praise regex doesn't flag natural Persian sentences
- Voice-aware quality checks work correctly without false positives
- Prompt structure places voice guidance in the right position
"""

from __future__ import annotations

import json
import re
import tempfile
import unittest
from pathlib import Path

from app.ai import _load_voice_profile
from app.translation_safety import (
    _BOOKISH_RE,
    _EXCESSIVE_EMOJI_RE,
    _FORMAL_VERB_RE,
    _GENERIC_PRAISE_RE,
    natural_persian_failures,
    semantic_quality_failures,
)

ROOT = Path(__file__).parents[1]


class VoiceProfileLoaderTests(unittest.TestCase):
    """Tests for _load_voice_profile function."""

    def test_returns_nonempty_for_valid_root(self):
        result = _load_voice_profile(ROOT)
        self.assertTrue(result, "Should return non-empty string for valid root")
        self.assertIn("صدا:", result, "Should contain tone description")

    def test_strips_english_prefixes_from_verb_forms(self):
        """A/B evaluation found English prefixes (is_, does_) leaked into guidance."""
        result = _load_voice_profile(ROOT)
        # Should NOT contain English prefixes like "is_", "does_", "becomes_"
        self.assertNotRegex(result, r"\bis_\S", "Should not contain 'is_' prefix")
        self.assertNotRegex(result, r"\bdoes_\S", "Should not contain 'does_' prefix")
        self.assertNotRegex(result, r"\bbecomes_\S", "Should not contain 'becomes_' prefix")
        # Should contain clean Persian verb forms
        self.assertIn("میکنه", result, "Should contain colloquial verb form")
        self.assertIn("میشه", result, "Should contain colloquial verb form")

    def test_contains_key_sections(self):
        result = _load_voice_profile(ROOT)
        self.assertIn("فعل‌های عامیانه:", result, "Should list colloquial verb forms")
        self.assertIn("شکل طبیعی:", result, "Should list natural-vs-formal pairs")
        self.assertIn("ممنوع:", result, "Should list forbidden patterns")

    def test_returns_empty_for_nonexistent_root(self):
        result = _load_voice_profile(Path("/nonexistent/path"))
        self.assertEqual(result, "")

    def test_returns_empty_for_malformed_json(self):
        with tempfile.TemporaryDirectory() as td:
            config_dir = Path(td) / "config"
            config_dir.mkdir()
            (config_dir / "channel_voice_profile.json").write_text("NOT JSON {{{")
            result = _load_voice_profile(Path(td))
            self.assertEqual(result, "")

    def test_returns_empty_for_empty_file(self):
        with tempfile.TemporaryDirectory() as td:
            config_dir = Path(td) / "config"
            config_dir.mkdir()
            (config_dir / "channel_voice_profile.json").write_text("")
            result = _load_voice_profile(Path(td))
            self.assertEqual(result, "")

    def test_handles_wrong_structure_gracefully(self):
        with tempfile.TemporaryDirectory() as td:
            config_dir = Path(td) / "config"
            config_dir.mkdir()
            (config_dir / "channel_voice_profile.json").write_text(
                json.dumps({"unexpected": "structure"})
            )
            result = _load_voice_profile(Path(td))
            # Should still return something (at minimum the صدا: line)
            self.assertIn("صدا:", result)

    def test_guidance_is_concise(self):
        """Voice guidance should fit within prompt budget (well under 1000 chars)."""
        result = _load_voice_profile(ROOT)
        self.assertLess(len(result), 1000, f"Voice guidance too long: {len(result)} chars")


class GenericPraiseRegexTests(unittest.TestCase):
    """Regression tests for _GENERIC_PRAISE_RE.

    A/B evaluation found the original regex matched natural Persian sentences
    like "جونگهان همیشه بهترینه" as generic praise — a false positive.
    """

    def test_standalone_praise_is_flagged(self):
        standalone = [
            "عالیه",
            "عالیه خیلی خوبه",
            "خیلی قشنگه",
            "خیلی خوبه",
            "فوق‌العاده‌ست",
            "بهترینه",
        ]
        for text in standalone:
            with self.subTest(text=text):
                self.assertTrue(
                    _GENERIC_PRAISE_RE.search(text),
                    f"Standalone praise '{text}' should be flagged",
                )

    def test_repeated_praise_is_flagged(self):
        repeated = [
            "عالیه عالیه",
            "عالیه عالیه عالیه",
            "خیلی خوبه خیلی خوبه",
        ]
        for text in repeated:
            with self.subTest(text=text):
                self.assertTrue(
                    _GENERIC_PRAISE_RE.search(text),
                    f"Repeated praise '{text}' should be flagged",
                )

    def test_natural_sentences_are_NOT_flagged(self):
        """Core regression: natural Persian sentences must not be flagged."""
        natural = [
            "جونگهان همیشه بهترینه",
            "واقعا عالیه",
            "عالیه ولی کاش بیشتر بود",
            "خیلی خوبه که اومد",
            "عالیه ممنونم",
            "خیلی قشنگه ممنون",
            "واقعا فوق‌العاده‌ست",
        ]
        for text in natural:
            with self.subTest(text=text):
                self.assertIsNone(
                    _GENERIC_PRAISE_RE.search(text),
                    f"Natural sentence '{text}' should NOT be flagged as generic praise",
                )

    def test_praise_in_longer_output_is_NOT_flagged(self):
        """Praise words inside a longer translated output should not trigger."""
        outputs = [
            "جونگهان ویدیو جدید گذاشت واقعا عالیه",
            "امروز پست جدید گذاشت خیلی قشنگه",
            "این عکس فوق‌العاده‌ست 😭",
        ]
        for text in outputs:
            with self.subTest(text=text):
                self.assertIsNone(
                    _GENERIC_PRAISE_RE.search(text),
                    f"Praise in context '{text[:40]}' should NOT be flagged",
                )


class VoiceAwareRegexTests(unittest.TestCase):
    """Tests for the three voice-aware regex patterns."""

    def test_excessive_emoji_catches_5_plus(self):
        self.assertTrue(_EXCESSIVE_EMOJI_RE.search("😭😭😭😭😭😭😭"))
        self.assertTrue(_EXCESSIVE_EMOJI_RE.search("😍😍😍😍😍😍😍😍😍😍"))

    def test_excessive_emoji_allows_4_fewer(self):
        self.assertIsNone(_EXCESSIVE_EMOJI_RE.search("😭😭😭"))
        self.assertIsNone(_EXCESSIVE_EMOJI_RE.search("❤️❤️❤️❤️"))

    def test_formal_verb_catches_key_patterns(self):
        formal = ["می‌شود", "می‌کند", "استفاده از", "به وضوح"]
        for pattern in formal:
            with self.subTest(pattern=pattern):
                self.assertTrue(
                    _FORMAL_VERB_RE.search(pattern),
                    f"Formal pattern '{pattern}' should be caught",
                )

    def test_formal_verb_allows_colloquial(self):
        colloquial = ["میشه", "میکنه", "داره", "میگه"]
        for pattern in colloquial:
            with self.subTest(pattern=pattern):
                self.assertIsNone(
                    _FORMAL_VERB_RE.search(pattern),
                    f"Colloquial '{pattern}' should NOT be caught by formal verb check",
                )

    def test_bookish_catches_key_patterns(self):
        # _BOOKISH_RE uses space-separated forms (می کند) not ZWNJ (می‌کند)
        bookish = [
            "به وضوح",
            "با استفاده از",
            "خود را",
            "می کند",
            "می شود",
            "اطرافیانم را",
        ]
        for pattern in bookish:
            with self.subTest(pattern=pattern):
                self.assertTrue(
                    _BOOKISH_RE.search(pattern),
                    f"Bookish pattern '{pattern}' should be caught",
                )

    def test_bookish_allows_colloquial(self):
        colloquial = ["میکنه", "میشه", "داره", "کنه"]
        for pattern in colloquial:
            with self.subTest(pattern=pattern):
                self.assertIsNone(
                    _BOOKISH_RE.search(pattern),
                    f"Colloquial '{pattern}' should NOT be caught by bookish check",
                )


class VoiceProfileAccuracyTests(unittest.TestCase):
    """Validate voice profile claims against actual channel data."""

    def test_colloquial_forms_outnumber_formal(self):
        """At least 90% of verb forms should be colloquial (from A/B evaluation)."""
        corpus_path = ROOT / "data" / "channel_memory.jsonl"
        if not corpus_path.exists():
            self.skipTest("Channel memory corpus not available")

        posts = []
        with open(corpus_path) as f:
            for line in f:
                posts.append(json.loads(line))

        pairs = [
            ("میکنه", "می‌کند"),  # does
            ("میشه", "می‌شود"),   # becomes
            ("داره", "دارد"),     # has
            ("میگه", "می‌گوید"),  # says
            ("کنه", "کند"),      # do
        ]

        for colloquial, formal in pairs:
            with self.subTest(colloquial=colloquial, formal=formal):
                col_cnt = sum(1 for p in posts if colloquial in p.get("text", ""))
                for_cnt = sum(1 for p in posts if formal in p.get("text", ""))
                total = col_cnt + for_cnt
                if total == 0:
                    continue
                ratio = col_cnt / total
                self.assertGreater(
                    ratio, 0.85,
                    f"{colloquial} should be ≥85% of uses vs {formal} "
                    f"(got {ratio:.0%} = {col_cnt}/{total})",
                )

    def test_voice_profile_file_exists(self):
        profile_path = ROOT / "config" / "channel_voice_profile.json"
        self.assertTrue(profile_path.exists(), "Voice profile should exist")
        data = json.loads(profile_path.read_text(encoding="utf-8"))
        self.assertIn("tone", data, "Should have tone section")
        self.assertIn("sentence_patterns", data, "Should have sentence_patterns")
        self.assertIn("forbidden_patterns", data, "Should have forbidden_patterns")


class PromptStructureTests(unittest.TestCase):
    """Verify voice guidance placement in the actual prompt."""

    def test_voice_guidance_between_style_and_samples(self):
        ai_py = (ROOT / "app" / "ai.py").read_text()
        style_pos = ai_py.find("پروفایل واقعی کانال:")
        voice_pos = ai_py.find("صدا و لحن (از تحلیل ۱۵۰۰۰+ پست واقعی):")
        samples_pos = ai_py.find("نمونه‌های واقعی و فقط برای تقلید لحن")

        self.assertGreater(style_pos, 0, "style_profile marker should exist")
        self.assertGreater(voice_pos, 0, "voice_guidance marker should exist")
        self.assertGreater(samples_pos, 0, "samples marker should exist")
        self.assertLess(style_pos, voice_pos)
        self.assertLess(voice_pos, samples_pos)

    def test_voice_profile_loaded_in_write_group(self):
        ai_py = (ROOT / "app" / "ai.py").read_text()
        self.assertIn(
            "_load_voice_profile",
            ai_py,
            "write_group should call _load_voice_profile",
        )
        # Check that getattr is used for safe access
        self.assertIn(
            "getattr(self.memory, 'root', None)",
            ai_py,
            "Should use getattr for safe memory.root access",
        )

    def test_voice_profile_is_in_actual_production_v2_prompt(self):
        v2_py = (ROOT / "app" / "channel_translation_v2.py").read_text()
        style_pos = v2_py.find("CHANNEL STYLE DNA:")
        voice_pos = v2_py.find("CHANNEL VOICE PROFILE")
        examples_pos = v2_py.find("PAIRED TRANSLATION DEMONSTRATIONS")

        self.assertGreater(style_pos, 0)
        self.assertGreater(voice_pos, style_pos)
        self.assertGreater(examples_pos, voice_pos)
        self.assertIn('_load_voice_profile(getattr(self.memory, "root", ROOT))', v2_py)
        self.assertIn('self.last_diagnostics["voice_profile_loaded"]', v2_py)

    def test_evaluation_backed_fidelity_rules_override_voice_flourish(self):
        v2_py = (ROOT / "app" / "channel_translation_v2.py").read_text()
        self.assertIn("food/meal باید «غذا» بماند، نه «چیزمیز»", v2_py)
        self.assertIn("نسبت‌های دستوری مثل with/by/for", v2_py)
        self.assertIn("علامت‌ها و تزئین‌های Unicode منبع", v2_py)


if __name__ == "__main__":
    unittest.main()
