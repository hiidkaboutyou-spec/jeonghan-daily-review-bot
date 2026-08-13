from __future__ import annotations

import json
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from app.fic_digest import Fic, summarize_fics_persian
from app.fic_summary_quality import fic_summary_is_publishable, fic_summary_quality_issues


class FicSummaryQualityTests(unittest.TestCase):
    def test_rejects_source_echo_and_mostly_english(self):
        source = "Jeonghan thinks this is only a normal date until Seungcheol appears again."
        self.assertIn("source_echo", fic_summary_quality_issues(source, source))
        self.assertFalse(fic_summary_is_publishable(source, "Jeonghan returns home after the date ends."))

    def test_rejects_machine_bookish_register(self):
        source = "Jeonghan tries to pretend seeing Seungcheol again does not affect him."
        candidate = "جونگهان درصدد آن است که وانمود نماید دیدن دوبارهٔ سونگچول بر او اثری ندارد."
        self.assertIn("bookish_register", fic_summary_quality_issues(source, candidate))

    def test_accepts_natural_colloquial_persian(self):
        source = "Jeonghan thinks this is only a normal date until Seungcheol appears again after years."
        candidate = "جونگهان فکر می‌کنه این فقط یه قرار معمولیه، تا وقتی سونگچول بعد از سال‌ها دوباره جلوی روش ظاهر می‌شه."
        self.assertTrue(fic_summary_is_publishable(source, candidate))

    def test_rejects_severe_overcompression_of_long_summary(self):
        source = ("Jeonghan has spent years convincing himself that the past is over and that seeing "
                  "Seungcheol again would not matter. When a mutual friend brings them together at a party, "
                  "old feelings surface, along with the reasons they stopped talking in the first place. "
                  "Neither of them is ready to admit how much has changed or how much has not.")
        self.assertIn("overcompressed", fic_summary_quality_issues(source, "جونگهان دوباره سونگچول رو می‌بینه."))

    def test_bad_model_output_falls_back_instead_of_shipping(self):
        fic = Fic(
            title="Fic",
            url="https://archiveofourown.org/works/1",
            author="writer",
            summary="Jeonghan tries to pretend seeing Seungcheol again does not affect him.",
            relationships=["Choi Seungcheol/Yoon Jeonghan"],
        )
        bad = json.dumps(
            {"items": [{"url": fic.url, "summary_fa": "جونگهان درصدد آن است که وانمود نماید دیدن سونگچول اثری ندارد."}]},
            ensure_ascii=False,
        )
        client = SimpleNamespace(models=SimpleNamespace(generate_content=Mock(return_value=SimpleNamespace(text=bad))))
        settings = SimpleNamespace(gemini_api_key="key", gemini_model="gemini-3.5-flash-lite")
        with patch("google.genai.Client", return_value=client), patch("app.fic_digest.time.sleep"):
            result = summarize_fics_persian(settings, [fic])
        self.assertIn("متن اصلی AO3", result[fic.url])
        self.assertIn("Jeonghan tries", result[fic.url])


if __name__ == "__main__":
    unittest.main()
