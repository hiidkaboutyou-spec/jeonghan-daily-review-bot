from __future__ import annotations

import json
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from app.fic_digest import (
    Fic,
    _chunks,
    _normalize_fic_summary_names,
    _rank_digest_fics,
    _translate_summary,
    format_digest,
    summarize_fics_persian,
)


class FanficDigestTests(unittest.TestCase):
    def test_adult_ao3_content_is_preserved_when_model_is_unavailable(self):
        source = "Jeonghan asks Seungcheol to have sex with him."
        fallback = _translate_summary(source)
        self.assertIn("have sex", fallback)
        self.assertIn("متن اصلی AO3", fallback)

    def test_fic_summary_names_use_channel_spellings(self):
        value = "جئونگان با سئونگ چئول حرف زد و Joshua هم آنجا بود."
        self.assertEqual(
            _normalize_fic_summary_names(value),
            "جونگهان با سونگچول حرف زد و جاشوآ هم آنجا بود.",
        )

    def test_fic_quota_failure_tries_each_supported_model_once_then_stops(self):
        fic = Fic(
            title="Adult fic",
            url="https://archiveofourown.org/works/9",
            author="writer",
            summary="Jeonghan asks Seungcheol to have sex with him.",
            relationships=["Choi Seungcheol/Yoon Jeonghan"],
        )
        client = SimpleNamespace(
            models=SimpleNamespace(
                generate_content=Mock(side_effect=RuntimeError("429 RESOURCE_EXHAUSTED quota"))
            )
        )
        settings = SimpleNamespace(gemini_api_key="key", gemini_model="first")
        with patch("google.genai.Client", return_value=client) as factory, patch(
            "app.fic_digest.time.sleep"
        ):
            result = summarize_fics_persian(settings, [fic])
        self.assertEqual(client.models.generate_content.call_count, 3)
        self.assertIn("have sex", result[fic.url])
        self.assertLessEqual(factory.call_args.kwargs["http_options"].timeout, 45_000)

    def test_partial_batch_isolates_missing_summary_without_poisoning_later_batches(self):
        fics = [
            Fic(
                title=f"Fic {index}",
                url=f"https://archiveofourown.org/works/{index}",
                author="writer",
                summary=f"Source summary {index}",
                relationships=["Choi Seungcheol/Yoon Jeonghan"],
            )
            for index in range(1, 4)
        ]
        translated_second = json.dumps(
            {"items": [{"url": fics[1].url, "summary_fa": "جونگهان رفت خونه."}]},
            ensure_ascii=False,
        )
        translated_third = json.dumps(
            {"items": [{"url": fics[2].url, "summary_fa": "جونگهان برگشت خونه."}]},
            ensure_ascii=False,
        )
        client = SimpleNamespace(
            models=SimpleNamespace(
                generate_content=Mock(
                    side_effect=[
                        SimpleNamespace(text=translated_second),
                        SimpleNamespace(text=""),
                        SimpleNamespace(text=""),
                        SimpleNamespace(text=translated_third),
                    ]
                )
            )
        )
        settings = SimpleNamespace(gemini_api_key="key", gemini_model="gemini-3.5-flash-lite")
        with patch("google.genai.Client", return_value=client), patch(
            "app.fic_digest.FIC_SUMMARY_BATCH_SIZE", 2
        ), patch("app.fic_digest.time.sleep"):
            result = summarize_fics_persian(settings, fics)

        self.assertIn("متن اصلی AO3", result[fics[0].url])
        self.assertEqual(result[fics[1].url], "جونگهان رفت خونه.")
        self.assertEqual(result[fics[2].url], "جونگهان برگشت خونه.")
        self.assertEqual(client.models.generate_content.call_count, 4)

    def test_blocked_fic_batch_is_split_so_other_summaries_still_translate(self):
        fics = [
            Fic(
                title=f"Fic {index}",
                url=f"https://archiveofourown.org/works/{index}",
                author="writer",
                summary=f"Source summary {index}",
                relationships=["Choi Seungcheol/Yoon Jeonghan"],
            )
            for index in range(1, 3)
        ]
        blocked = SimpleNamespace(
            parsed=None,
            text="",
            prompt_feedback=SimpleNamespace(block_reason=SimpleNamespace(name="PROHIBITED_CONTENT")),
        )
        first = SimpleNamespace(
            parsed={"items": [{"url": fics[0].url, "summary_fa": "خلاصهٔ اول."}]},
            text="",
        )
        second = SimpleNamespace(
            parsed={"items": [{"url": fics[1].url, "summary_fa": "خلاصهٔ دوم."}]},
            text="",
        )
        client = SimpleNamespace(
            models=SimpleNamespace(
                generate_content=Mock(side_effect=[blocked, blocked, first, second])
            )
        )
        settings = SimpleNamespace(gemini_api_key="key", gemini_model="gemini-3.5-flash-lite")

        with patch("google.genai.Client", return_value=client), patch(
            "app.fic_digest.time.sleep"
        ):
            result = summarize_fics_persian(settings, fics)

        self.assertEqual(result[fics[0].url], "خلاصهٔ اول.")
        self.assertEqual(result[fics[1].url], "خلاصهٔ دوم.")
        self.assertEqual(client.models.generate_content.call_count, 4)

    def test_temporary_fic_provider_failure_retries_once(self):
        fic = Fic(
            title="Fic",
            url="https://archiveofourown.org/works/1",
            author="writer",
            summary="Jeonghan returns home.",
            relationships=["Choi Seungcheol/Yoon Jeonghan"],
        )
        translated = json.dumps(
            {"items": [{"url": fic.url, "summary_fa": "جونگهان برمی‌گرده خونه."}]},
            ensure_ascii=False,
        )
        client = SimpleNamespace(
            models=SimpleNamespace(
                generate_content=Mock(
                    side_effect=[RuntimeError("503 high demand"), SimpleNamespace(text=translated)]
                )
            )
        )
        settings = SimpleNamespace(gemini_api_key="key", gemini_model="gemini-stable")
        with patch("google.genai.Client", return_value=client), patch("app.fic_digest.time.sleep") as sleep:
            result = summarize_fics_persian(settings, [fic])

        self.assertEqual(result[fic.url], "جونگهان برمی‌گرده خونه.")
        self.assertEqual(client.models.generate_content.call_count, 2)
        self.assertGreaterEqual(sleep.call_count, 1)

    def test_chunks_never_truncate_long_block(self):
        text = "عنوان\n\n" + ("الف" * 9000) + "\n\nپایان"
        chunks = _chunks(text, max_len=3800)
        self.assertTrue(chunks)
        self.assertTrue(all(0 < len(chunk) <= 3800 for chunk in chunks))
        reconstructed = "\n\n".join(chunks)
        self.assertIn("الف" * 9000, reconstructed.replace("\n\n", ""))
        self.assertTrue(reconstructed.endswith("پایان"))

    def test_digest_keeps_separate_ship_sections(self):
        fics = [
            Fic(
                title="A",
                url="https://archiveofourown.org/works/1",
                author="one",
                summary="summary A",
                relationships=["Yoon Jeonghan/Choi Seungcheol"],
            ),
            Fic(
                title="B",
                url="https://archiveofourown.org/works/2",
                author="two",
                summary="summary B",
                relationships=["Yoon Jeonghan/Hong Jisoo"],
            ),
        ]
        text = format_digest("title", fics, {f.url: f.summary for f in fics}, "ao3")
        self.assertIn("━━ Jeongcheol ━━", text)
        self.assertIn("━━ Jihan ━━", text)
        self.assertIn("https://archiveofourown.org/works/1", text)
        self.assertIn("https://archiveofourown.org/works/2", text)

    def test_digest_shows_update_completion_and_safety_metadata(self):
        fic = Fic(
            title="A",
            url="https://archiveofourown.org/works/1",
            author="one",
            summary="summary",
            relationships=["Yoon Jeonghan/Choi Seungcheol"],
            rating="Explicit",
            chapters="4/4",
            observation_status="updated",
            warnings=["Creator Chose Not To Use Archive Warnings"],
            freeforms=["Enemies to Lovers", "Happy Ending"],
        )
        text = format_digest("title", [fic], {fic.url: fic.summary}, "ao3")
        self.assertIn("🔄 تازه آپدیت شده", text)
        self.assertIn("✅ کامل", text)
        self.assertIn("رده‌بندی: صریح / بزرگسال", text)
        self.assertIn("نویسنده هشدارهای آرشیو را مشخص نکرده", text)
        self.assertIn("Enemies to Lovers", text)

    def test_updates_and_new_works_rank_before_unchanged_popular_works(self):
        unchanged = Fic("old", "https://archiveofourown.org/works/1", "a", "s", ["Yoon Jeonghan/Choi Seungcheol"], kudos=999, observation_status="unchanged")
        new = Fic("new", "https://archiveofourown.org/works/2", "a", "s", ["Yoon Jeonghan/Choi Seungcheol"], kudos=1, observation_status="new")
        updated = Fic("updated", "https://archiveofourown.org/works/3", "a", "s", ["Yoon Jeonghan/Choi Seungcheol"], kudos=1, observation_status="updated")
        ranked = _rank_digest_fics([unchanged, new, updated], "ao3")
        self.assertEqual([fic.title for fic in ranked], ["updated", "new", "old"])


if __name__ == "__main__":
    unittest.main()
