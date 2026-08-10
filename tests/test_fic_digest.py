from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from app.fic_digest import (
    Fic,
    _chunks,
    _normalize_fic_summary_names,
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

    def test_fic_quota_failure_stops_after_one_bounded_model_call(self):
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
        with patch("google.genai.Client", return_value=client) as factory:
            result = summarize_fics_persian(settings, [fic])
        self.assertEqual(client.models.generate_content.call_count, 1)
        self.assertIn("have sex", result[fic.url])
        self.assertLessEqual(factory.call_args.kwargs["http_options"].timeout, 45_000)

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


if __name__ == "__main__":
    unittest.main()
