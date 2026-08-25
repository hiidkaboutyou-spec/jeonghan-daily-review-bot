from __future__ import annotations

import json
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from app.fic_digest import (
    Fic,
    SPOILER_FULL,
    SPOILER_MEDIUM,
    SPOILER_NO,
    _assign_relative_tiers,
    _chunks,
    _fic_prompt_prefix,
    _normalize_fic_summary_names,
    _rank_digest_fics,
    _translate_summary,
    compute_fic_quality_score,
    format_digest,
    summarize_fics_persian,
)


class FanficDigestTests(unittest.TestCase):
    def test_prompt_forbids_hallucinated_plot_and_unsourced_ending(self):
        prompt = _fic_prompt_prefix()
        self.assertIn("هیچ پایان", prompt)
        self.assertIn("حدس نزن", prompt)
        self.assertIn("پایان دیده‌نشده را هرگز نساز", prompt)

    def test_three_spoiler_modes_use_grounded_structured_outputs(self):
        fic = Fic(
            title="Dark romance",
            url="https://archiveofourown.org/works/77",
            author="writer",
            summary="Jeonghan and Seungcheol confront their attraction during a violent crisis.",
            relationships=["Choi Seungcheol/Yoon Jeonghan"],
            rating="Explicit",
            warnings=["Graphic Depictions Of Violence"],
            freeforms=["Sexual Tension", "Angst"],
        )
        item = {
            "url": fic.url,
            "summary_fa_nospoiler": "جونگهان و سونگچول وسط یک بحران با کشش بین‌شون روبه‌رو می‌شن.",
            "summary_fa_medium": "جونگهان و سونگچول وسط یک بحران خشونت‌آمیز با کشش بین‌شون روبه‌رو می‌شن.",
            "summary_fa_full": "تمام اطلاعات عمومی فقط همین بحران خشونت‌آمیز و کشش بین جونگهان و سونگچوله.",
            "relationship_dynamic_fa": "کشش دوطرفه بین جونگهان و سونگچول",
            "warnings_fa": ["خشونت با توصیف صریح"],
            "emotional_tone": "پرتنش و تلخ",
            "themes": ["کشش عاطفی"],
            "tropes": ["sexual tension"],
            "why_read": "برای تنش عاطفی دقیق بین این دو نفر.",
        }
        response = SimpleNamespace(parsed={"items": [item]}, text="")
        client = SimpleNamespace(models=SimpleNamespace(generate_content=Mock(return_value=response)))
        settings = SimpleNamespace(gemini_api_key="key", gemini_model="gemini-3.5-flash-lite")

        with patch("google.genai.Client", return_value=client), patch("app.fic_digest.time.sleep"):
            medium = summarize_fics_persian(settings, [fic], SPOILER_MEDIUM)

        self.assertEqual(medium[fic.url], item["summary_fa_medium"])
        self.assertEqual(fic.summary_fa_nospoiler, item["summary_fa_nospoiler"])
        self.assertEqual(fic.summary_fa_full, item["summary_fa_full"])
        self.assertEqual(fic.relationship_dynamic_fa, item["relationship_dynamic_fa"])
        self.assertEqual(fic.warnings_fa, item["warnings_fa"])
        prompt = client.models.generate_content.call_args.kwargs["contents"]
        self.assertIn("Graphic Depictions Of Violence", prompt)
        self.assertIn("Choi Seungcheol/Yoon Jeonghan", prompt)
        self.assertIn("Sexual Tension", prompt)

        self.assertIn(item["summary_fa_nospoiler"], format_digest("t", [fic], medium, "ao3", SPOILER_NO))
        self.assertIn(item["summary_fa_medium"], format_digest("t", [fic], medium, "ao3", SPOILER_MEDIUM))
        self.assertIn(item["summary_fa_full"], format_digest("t", [fic], medium, "ao3", SPOILER_FULL))

    def test_invalid_spoiler_mode_is_rejected(self):
        settings = SimpleNamespace(gemini_api_key="", gemini_model="")
        with self.assertRaises(ValueError):
            summarize_fics_persian(settings, [], "surprise-ending")

    def test_incomplete_relationship_interpretation_is_not_published(self):
        fic = Fic(
            title="Fic",
            url="https://archiveofourown.org/works/78",
            author="writer",
            summary="Jeonghan meets Seungcheol again.",
            relationships=["Choi Seungcheol/Yoon Jeonghan"],
        )
        item = {
            "url": fic.url,
            "summary_fa_nospoiler": "جونگهان دوباره سونگچول رو می‌بینه.",
            "summary_fa_medium": "جونگهان دوباره سونگچول رو می‌بینه.",
            "summary_fa_full": "جونگهان دوباره سونگچول رو می‌بینه.",
            "relationship_dynamic_fa": "",
            "warnings_fa": [],
            "emotional_tone": "",
            "themes": [],
            "tropes": [],
            "why_read": "دیدار دوبارهٔ این دو نفر.",
        }
        client = SimpleNamespace(
            models=SimpleNamespace(
                generate_content=Mock(return_value=SimpleNamespace(parsed={"items": [item]}, text=""))
            )
        )
        settings = SimpleNamespace(gemini_api_key="key", gemini_model="gemini-3.5-flash-lite")
        with patch("google.genai.Client", return_value=client), patch("app.fic_digest.time.sleep"):
            result = summarize_fics_persian(settings, [fic])

        self.assertIn("متن اصلی AO3", result[fic.url])
        self.assertEqual(fic.relationship_dynamic_fa, "")

    def test_missing_mode_output_falls_back_without_inventing_content(self):
        fic = Fic(
            title="Fic",
            url="https://archiveofourown.org/works/1",
            author="writer",
            summary="Jeonghan returns home.",
            relationships=["Yoon Jeonghan/Choi Seungcheol"],
        )
        fallback = "⚠️ ترجمهٔ خلاصه در دسترس نبود؛ متن اصلی AO3:\nJeonghan returns home."
        self.assertEqual(
            format_digest("t", [fic], {fic.url: fallback}, "ao3", SPOILER_FULL).split("خلاصه: ", 1)[1],
            fallback,
        )

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

    def test_quota_exhausted_model_is_not_retried_for_later_fic_batches(self):
        fics = [
            Fic(
                title=f"Fic {index}",
                url=f"https://archiveofourown.org/works/{index}",
                author="writer",
                summary=f"Jeonghan returns home {index}.",
                relationships=["Choi Seungcheol/Yoon Jeonghan"],
            )
            for index in (1, 2)
        ]
        first = SimpleNamespace(
            parsed={"items": [{"url": fics[0].url, "summary_fa": "جونگهان برگشت خونه."}]},
            text="",
        )
        second = SimpleNamespace(
            parsed={"items": [{"url": fics[1].url, "summary_fa": "جونگهان دوباره برگشت."}]},
            text="",
        )
        generate = Mock(side_effect=[RuntimeError("429 RESOURCE_EXHAUSTED quota"), first, second])
        client = SimpleNamespace(models=SimpleNamespace(generate_content=generate))
        settings = SimpleNamespace(
            gemini_api_key="key", gemini_model="gemini-3.1-flash-lite"
        )

        with patch("google.genai.Client", return_value=client), patch(
            "app.fic_digest.FIC_SUMMARY_BATCH_SIZE", 1
        ), patch("app.fic_digest.time.sleep"):
            result = summarize_fics_persian(settings, fics)

        self.assertEqual(result[fics[0].url], "جونگهان برگشت خونه.")
        self.assertEqual(result[fics[1].url], "جونگهان دوباره برگشت.")
        models = [call.kwargs["model"] for call in generate.call_args_list]
        self.assertEqual(
            models,
            ["gemini-3.1-flash-lite", "gemini-3.5-flash-lite", "gemini-3.5-flash-lite"],
        )

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

    # ── Quality scoring tests ───────────────────────────────────────────

    def test_quality_score_popular_fic_gets_gem_tier(self):
        fic = Fic(
            title="Popular",
            url="https://archiveofourown.org/works/1",
            author="writer",
            summary="summary",
            relationships=["Yoon Jeonghan/Choi Seungcheol"],
            kudos=5000, bookmarks=200, hits=20000,
            chapters="10/10", words="100000",
            observation_status="unchanged",
        )
        score, tier = compute_fic_quality_score(fic, "ao3")
        self.assertGreaterEqual(score, 50)
        self.assertEqual(tier, "gem")

    def test_quality_score_low_kudos_fic_gets_lower_tier(self):
        fic = Fic(
            title="Small",
            url="https://archiveofourown.org/works/2",
            author="writer",
            summary="summary",
            relationships=["Yoon Jeonghan/Choi Seungcheol"],
            kudos=2, bookmarks=0, hits=10,
            words="500",
        )
        score, tier = compute_fic_quality_score(fic, "ao3")
        self.assertLess(score, 10)
        self.assertIn(tier, ("", "fresh"))

    def test_quality_score_new_observation_gets_fresh_bonus(self):
        fic = Fic(
            title="New",
            url="https://archiveofourown.org/works/3",
            author="writer",
            summary="summary",
            relationships=["Yoon Jeonghan/Choi Seungcheol"],
            kudos=1, observation_status="new",
        )
        score_new, _ = compute_fic_quality_score(fic, "ao3")
        fic2 = Fic(
            title="Old",
            url="https://archiveofourown.org/works/4",
            author="writer",
            summary="summary",
            relationships=["Yoon Jeonghan/Choi Seungcheol"],
            kudos=1, observation_status="unchanged",
        )
        score_old, _ = compute_fic_quality_score(fic2, "ao3")
        self.assertGreater(score_new, score_old)
        # Fresh bonus of +10 pushes score to 10+, which is 'popular' tier
        self.assertGreaterEqual(score_new, 10)

    def test_quality_score_x_source_includes_x_score(self):
        fic = Fic(
            title="X fic",
            url="https://archiveofourown.org/works/5",
            author="writer",
            summary="summary",
            relationships=["Yoon Jeonghan/Choi Seungcheol"],
            kudos=10, x_score=50,
        )
        score_x, _ = compute_fic_quality_score(fic, "x")
        score_ao3, _ = compute_fic_quality_score(fic, "ao3")
        self.assertGreater(score_x, score_ao3)

    def test_quality_score_complete_fic_gets_bonus(self):
        fic_complete = Fic(
            title="Done",
            url="https://archiveofourown.org/works/6",
            author="writer",
            summary="summary",
            relationships=["Yoon Jeonghan/Choi Seungcheol"],
            kudos=10, chapters="5/5",
        )
        fic_wip = Fic(
            title="WIP",
            url="https://archiveofourown.org/works/7",
            author="writer",
            summary="summary",
            relationships=["Yoon Jeonghan/Choi Seungcheol"],
            kudos=10, chapters="3/10",
        )
        score_c, _ = compute_fic_quality_score(fic_complete, "ao3")
        score_w, _ = compute_fic_quality_score(fic_wip, "ao3")
        self.assertGreater(score_c, score_w)

    # ── Relative tier assignment tests ───────────────────────────────────

    def test_assign_relative_tiers_top_is_gem(self):
        fics = [
            Fic(f"f{i}", f"https://archiveofourown.org/works/{i}", "a", "s",
                ["Yoon Jeonghan/Choi Seungcheol"], kudos=i * 100)
            for i in range(1, 21)
        ]
        for f in fics:
            f.quality_score, _ = compute_fic_quality_score(f, "ao3")
        _assign_relative_tiers(fics)
        # The fic with highest kudos should be gem
        gem_titles = {f.title for f in fics if f.quality_tier == "gem"}
        self.assertGreaterEqual(len(gem_titles), 1)  # at least top 10%
        self.assertIn("f20", gem_titles)  # highest kudos must be gem

    def test_assign_relative_tiers_empty_list(self):
        _assign_relative_tiers([])  # should not raise

    def test_assign_relative_tiers_preserves_gem_solid(self):
        fic = Fic(
            title="Already gem",
            url="https://archiveofourown.org/works/99",
            author="a",
            summary="s",
            relationships=["Yoon Jeonghan/Choi Seungcheol"],
            kudos=1, quality_tier="gem",
        )
        _assign_relative_tiers([fic])
        self.assertEqual(fic.quality_tier, "gem")

    # ── Format digest with quality tiers and why_read ────────────────────

    def test_format_digest_shows_quality_tier_badge(self):
        fic = Fic(
            title="Gem fic",
            url="https://archiveofourown.org/works/1",
            author="writer",
            summary="summary",
            relationships=["Yoon Jeonghan/Choi Seungcheol"],
            quality_tier="gem",
        )
        text = format_digest("title", [fic], {fic.url: fic.summary}, "ao3")
        self.assertIn("💎", text)
        self.assertIn("Gem fic", text)

    def test_format_digest_shows_why_read(self):
        fic = Fic(
            title="Nice fic",
            url="https://archiveofourown.org/works/2",
            author="writer",
            summary="summary",
            relationships=["Yoon Jeonghan/Choi Seungcheol"],
            why_read="کیفیت نوشتن عالیه و رابطه خیلی قشنگ پرداخت شده",
        )
        text = format_digest("title", [fic], {fic.url: fic.summary}, "ao3")
        self.assertIn("چرا بخوانیم:", text)
        self.assertIn("کیفیت نوشتن عالیه", text)

    def test_format_digest_no_why_read_section_when_empty(self):
        fic = Fic(
            title="Plain fic",
            url="https://archiveofourown.org/works/3",
            author="writer",
            summary="summary",
            relationships=["Yoon Jeonghan/Choi Seungcheol"],
            why_read="",
        )
        text = format_digest("title", [fic], {fic.url: fic.summary}, "ao3")
        self.assertNotIn("چرا بخوانیم:", text)

    def test_format_digest_empty_list_message(self):
        text = format_digest("title", [], {}, "ao3")
        self.assertIn("لیست خالی", text)

    # ── Spoiler mode and nospoiler extraction ─────────────────────────────

    def test_summarize_stores_nospoiler_and_why_read_on_fic(self):
        fic = Fic(
            title="Fic",
            url="https://archiveofourown.org/works/1",
            author="writer",
            summary="Jeonghan returns home.",
            relationships=["Choi Seungcheol/Yoon Jeonghan"],
        )
        response_data = {
            "items": [{
                "url": fic.url,
                "summary_fa": "جونگهان برمی‌گرده خونه.",
                "summary_fa_nospoiler": "داستان دربارهٔ برگشتن جونگهانه.",
                "why_read": "خیلی احساسی و قشنگ نوشته شده",
            }]
        }
        client = SimpleNamespace(
            models=SimpleNamespace(
                generate_content=Mock(return_value=SimpleNamespace(text=json.dumps(response_data, ensure_ascii=False)))
            )
        )
        settings = SimpleNamespace(gemini_api_key="key", gemini_model="gemini-stable")
        with patch("google.genai.Client", return_value=client), patch("app.fic_digest.time.sleep"):
            summarize_fics_persian(settings, [fic])
        self.assertEqual(fic.summary_fa_nospoiler, "داستان دربارهٔ برگشتن جونگهانه.")
        self.assertEqual(fic.why_read, "خیلی احساسی و قشنگ نوشته شده")

    def test_summarize_handles_missing_nospoiler_fields_gracefully(self):
        fic = Fic(
            title="Fic",
            url="https://archiveofourown.org/works/2",
            author="writer",
            summary="Summary.",
            relationships=["Choi Seungcheol/Yoon Jeonghan"],
        )
        response_data = {
            "items": [{
                "url": fic.url,
                "summary_fa": "خلاصه.",
                # no summary_fa_nospoiler or why_read
            }]
        }
        client = SimpleNamespace(
            models=SimpleNamespace(
                generate_content=Mock(return_value=SimpleNamespace(text=json.dumps(response_data, ensure_ascii=False)))
            )
        )
        settings = SimpleNamespace(gemini_api_key="key", gemini_model="gemini-stable")
        with patch("google.genai.Client", return_value=client), patch("app.fic_digest.time.sleep"):
            result = summarize_fics_persian(settings, [fic])
        self.assertEqual(fic.summary_fa_nospoiler, "")
        self.assertEqual(fic.why_read, "")
        self.assertEqual(result[fic.url], "خلاصه.")

    # ── Enhanced metadata fields on Fic ──────────────────────────────────

    def test_fic_dataclass_supports_enhanced_metadata(self):
        fic = Fic(
            title="Enhanced",
            url="https://archiveofourown.org/works/10",
            author="writer",
            summary="summary",
            relationships=["Yoon Jeonghan/Choi Seungcheol"],
            emotional_tone="angst",
            themes=["enemies-to-lovers", "found-family"],
            tropes=["slow-burn", "pining"],
        )
        self.assertEqual(fic.emotional_tone, "angst")
        self.assertEqual(fic.themes, ["enemies-to-lovers", "found-family"])
        self.assertEqual(fic.tropes, ["slow-burn", "pining"])
        self.assertEqual(fic.quality_score, 0.0)
        self.assertEqual(fic.quality_tier, "")

    def test_format_digest_no_spoiler_mode_hint(self):
        """Verify nospoiler summary can be used in output when available."""
        fic = Fic(
            title="Fic",
            url="https://archiveofourown.org/works/1",
            author="writer",
            summary="Full summary here.",
            relationships=["Yoon Jeonghan/Choi Seungcheol"],
            summary_fa_nospoiler="Short premise only.",
        )
        # Normal mode uses summaries dict
        text_normal = format_digest("t", [fic], {fic.url: fic.summary}, "ao3")
        self.assertIn("Full summary here", text_normal)
        # The nospoiler field is available on the fic object for callers to use
        self.assertEqual(fic.summary_fa_nospoiler, "Short premise only.")


if __name__ == "__main__":
    unittest.main()
