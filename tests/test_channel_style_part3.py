from __future__ import annotations

import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from app.ai import GroupCopy
from app.channel_quality import CONTENT_TYPES, commentary_policy, language_guidance, rerank_for_mode
from app.channel_style_runtime import (
    ChannelStyleMemory, RetrievedStyleExample, analyze_source, chronological_style_bonus,
    classify_content_type, historical_base_style_weight, verify_hard_facts,
)
from app.channel_translation import ChannelStyleCaptionWriter
from app.models import EventGroup, Update


class _PromptMemory:
    def __init__(self):
        self.profile = {key: {} for key in ("register", "syntax", "lexicon", "emotion", "formatting", "code_switching", "dialogue", "explanation")}
        self.glossary = {"categories": {
            "member_names": [{"canonical_form": "جونگهان", "alternatives": ["یون جونگهان"]}, {"canonical_form": "جاشوآ", "alternatives": []}],
            "brands": [{"canonical_form": "بانیلاکو", "alternatives": []}],
            "fan_events": [{"canonical_form": "فن‌ساین", "alternatives": ["فن ساین"]}],
            "platforms": [{"canonical_form": "ویورس", "alternatives": []}],
        }}
        self.feedback_calls = 0

    def retrieve_examples(self, neutral, analysis, limit=8, **kwargs):
        return [
            RetrievedStyleExample("fun", "ㅋㅋㅋ دارم میمیرم این بچه چرا اینجوریه", "SHORT_REACTION", "fa", "2023", 8.0, []),
            RetrievedStyleExample("soft", "قربون این ناز و نگاهش برم 🥺💗", "SHORT_REACTION", "fa", "2024", 7.9, []),
            RetrievedStyleExample("fact", "آپدیت رسمی برنامه منتشر شد.", "FACTUAL_INFORMATION", "fa", "2025", 7.8, []),
        ][:limit]

    def relevant_glossary(self, source, neutral):
        low = f"{source}\n{neutral}".casefold(); out = []
        if "jeonghan" in low or "جونگهان" in low:
            out.append({"category": "member_names", "canonical_form": "جونگهان", "alternatives": ["یون جونگهان"]})
        if "banila" in low or "بانیلا" in low:
            out.append({"category": "brands", "canonical_form": "بانیلاکو", "alternatives": []})
        if "weverse" in low or "ویورس" in low:
            out.append({"category": "platforms", "canonical_form": "ویورس", "alternatives": []})
        return out

    def add_confirmed_feedback(self, **kwargs):
        self.feedback_calls += 1
        return bool(kwargs.get("confirmed"))


def _group(text: str, *, category: str = "general", update_id: str = "1") -> EventGroup:
    update = Update(id=update_id, url=f"https://x.com/source/status/{update_id}", author="source", author_name="source", text=text, created_at=datetime(2026, 8, 8, tzinfo=timezone.utc), lang="", category=category)
    return EventGroup(key="event", category=category, title="title", updates=[update])


def _row(eid: str, text: str, content_type: str, *, date: str = "2023-01-01", language: str = "fa") -> dict:
    return {"example_id": eid, "channel_id": "1", "message_id": eid, "text": text, "content_type": content_type, "source_language": language, "date": date, "line_count": max(1, text.count("\n") + 1), "char_count": len(text), "has_dialogue": ":" in text, "has_laughter": "ㅋㅋ" in text or "ㅎㅎ" in text, "has_media": False, "format_prefix": "", "base_style_weight": 1.0}


class _RuntimeRoot:
    def __init__(self, rows: list[dict], glossary: dict | None = None):
        self.td = tempfile.TemporaryDirectory(); self.root = Path(self.td.name)
        corpus = self.root / "data" / "channel_style"; config = self.root / "config"
        corpus.mkdir(parents=True); config.mkdir(parents=True)
        shard = corpus / "part-00001.jsonl"
        shard.write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows), encoding="utf-8")
        (corpus / "manifest.json").write_text(json.dumps({"shards": [{"filename": shard.name}], "recency_weighting": "NONE", "date_score_contribution": 0.0}), encoding="utf-8")
        (config / "channel_style_profile.json").write_text("{}", encoding="utf-8")
        (config / "channel_glossary.json").write_text(json.dumps(glossary or {"categories": {}}, ensure_ascii=False), encoding="utf-8")
    def close(self): self.td.cleanup()


class ClassifierTests(unittest.TestCase):
    def test_supported_content_types_are_complete(self):
        expected = {"LIVE_DIALOGUE", "WEVERSE_POST", "WEVERSE_LIVE", "FANSIGN", "INTERVIEW", "MAGAZINE", "OFFICIAL_NEWS", "BRAND_AD", "FASHION_EVENT", "AIRPORT", "INSTAGRAM_UPDATE", "X_FANBASE_UPDATE", "FAN_ACCOUNT_OR_OP_STORY", "PHOTO_REACTION", "VIDEO_REACTION", "MEMBER_QUOTE", "MEMBER_INTERACTION", "KOREAN_LANGUAGE_NUANCE", "JAPANESE_LANGUAGE_NUANCE", "WORDPLAY", "THREAD_OR_LONG_EXPLANATION", "SHORT_REACTION", "FACTUAL_INFORMATION", "FANFIC_UPDATE", "OTHER"}
        self.assertEqual(set(CONTENT_TYPES), expected)

    def test_realistic_content_classification(self):
        cases = {
            "WEVERSE_POST": "Jeonghan posted on Weverse: good night",
            "WEVERSE_LIVE": "위버스 라이브에서 정한이 말했어",
            "FANSIGN": "fansign OP said Jeonghan waved",
            "INTERVIEW": "GQ interview with Jeonghan",
            "MAGAZINE": "ELLE magazine pictorial with Jeonghan",
            "OFFICIAL_NEWS": "PLEDIS official notice about the schedule",
            "BRAND_AD": "Jeonghan is in a Banila Co ambassador campaign",
            "FASHION_EVENT": "Jeonghan at Paris Fashion Week runway event",
            "AIRPORT": "Jeonghan at Incheon airport today",
            "INSTAGRAM_UPDATE": "Jeonghan Instagram update 📸",
            "X_FANBASE_UPDATE": "UPDATE: Jeonghan schedule via @source",
            "FAN_ACCOUNT_OR_OP_STORY": "OP: I met Jeonghan and he smiled at me",
            "PHOTO_REACTION": "this photo of Jeonghan is so cute 😭",
            "VIDEO_REACTION": "this video clip of Jeonghan help 😭",
            "MEMBER_QUOTE": "Jeonghan said “I missed you.”",
            "MEMBER_INTERACTION": "Jeonghan and S.Coups together member interaction",
            "KOREAN_LANGUAGE_NUANCE": "정한이 쓴 '-지' ending nuance means something softer here",
            "JAPANESE_LANGUAGE_NUANCE": "ジョンハンの「ね」ending nuance means it sounds softer",
            "WORDPLAY": "정한 made a wordplay pun here",
            "THREAD_OR_LONG_EXPLANATION": "thread:\n" + "\n".join(f"line {i}" for i in range(8)),
            "SHORT_REACTION": "he is so cute 😭",
            "FACTUAL_INFORMATION": "The event starts at 19:30 on 2026-08-20.",
            "FANFIC_UPDATE": "AO3 fanfic update: new chapter posted",
            "LIVE_DIALOGUE": "Jeonghan: hi\nJoshua: hello",
            "OTHER": "a simple sentence without a special context",
        }
        for expected, text in cases.items():
            with self.subTest(expected=expected): self.assertEqual(classify_content_type(text), expected)

    def test_mixed_language_input(self):
        self.assertEqual(analyze_source("정한 오늘 너무 cute honestly").source_language, "mixed")

    def test_short_factual_and_long_paths(self):
        self.assertEqual(classify_content_type("too cute 😭"), "SHORT_REACTION")
        self.assertEqual(classify_content_type("Release date: 2026-08-20 at 18:00"), "FACTUAL_INFORMATION")
        self.assertEqual(classify_content_type("\n".join(["explanation"] * 8)), "THREAD_OR_LONG_EXPLANATION")


class LanguageAndPromptTests(unittest.TestCase):
    def _neutral_prompt(self, source: str) -> str:
        memory = _PromptMemory(); captured = []
        class Probe(ChannelStyleCaptionWriter):
            def _generate_json(self, client, prompt, schema, *, temperature, purpose):
                captured.append(prompt); return {"title": "title", "items": [{"id": "1", "body": "ترجمه دقیق"}]}
        Probe("key", "model", memory)._neutral_group(_group(source), analyze_source(source), object())
        self.assertTrue(captured); return captured[0]

    def test_english_translation_behavior(self):
        prompt = self._neutral_prompt("Jeonghan explained that he had set an alarm because he really did not want to forget the birthday message, and he laughed while telling the story." * 2)
        self.assertIn("ساختار نحوی انگلیسی را کپی نکن", prompt); self.assertIn("بدون خلاصه‌سازی", prompt)

    def test_korean_behavior_and_laughter(self):
        prompt = self._neutral_prompt("정한: 아 그게요... 진짜 오래 생각했어요 ㅋㅋㅋ\n" * 4)
        self.assertIn("honorific", prompt); self.assertIn("ㅋㅋㅋ/ㅎㅎㅎ", prompt); self.assertIn("مکث", prompt)

    def test_japanese_behavior(self):
        prompt = self._neutral_prompt("ジョンハンは『ただいま〜』って、とてもやわらかく丁寧に話していました。" * 5)
        self.assertIn("نرمی", prompt); self.assertIn("ژاپنی", prompt); self.assertIn("honorific", prompt)

    def test_mixed_language_behavior(self):
        prompt = self._neutral_prompt(("정한 said this was really cute و بعد دوباره گفت ㅋㅋㅋ because he couldn't stop laughing. ") * 4)
        self.assertIn("code-switching", prompt)

    def test_optional_commentary_separation(self):
        self.assertIn("حداقلی یا صفر", commentary_policy("OFFICIAL_NEWS")); self.assertIn("پررنگ‌تر", commentary_policy("PHOTO_REACTION"))


class RetrievalTests(unittest.TestCase):
    def _rows(self):
        rows = [_row(f"r{i}", f"جونگهان خیلی کیوت بود {'ㅋㅋㅋ' if i % 2 else '🥺'} نشد دیگه {i}", "SHORT_REACTION") for i in range(8)]
        rows += [_row("d1", "این بچه چرا انقدر نازه 😭", "SHORT_REACTION"), _row("d2", "نه من واقعاً با این نگاهش مشکل دارم ㅋㅋㅋ", "SHORT_REACTION"), _row("d3", "آپدیت رسمی برنامه منتشر شد.", "FACTUAL_INFORMATION"), _row("d4", "ویورس: جونگهان گفت شب بخیر.", "WEVERSE_POST")]
        return rows

    def test_retrieval_content_type_and_diversity(self):
        rr = _RuntimeRoot(self._rows())
        try:
            memory = ChannelStyleMemory(rr.root, rr.root / "style.sqlite3")
            result = memory.retrieve_examples("جونگهان خیلی کیوت بود 😭", analyze_source("he is so cute 😭"), limit=8)
            self.assertGreaterEqual(len(result), 6); self.assertLessEqual(len(result), 8)
            self.assertEqual(result[0].content_type, "SHORT_REACTION")
            self.assertGreaterEqual(sum(x.content_type == "SHORT_REACTION" for x in result), 4)
            self.assertEqual(len({x.example_id for x in result}), len(result)); self.assertGreaterEqual(len({x.text[:24] for x in result}), 4)
            memory.close()
        finally: rr.close()

    def test_2023_vs_2026_equal_score_and_no_recency(self):
        self.assertEqual(historical_base_style_weight("2023"), historical_base_style_weight("2026")); self.assertEqual(chronological_style_bonus("2023"), 0.0); self.assertEqual(chronological_style_bonus("2026"), 0.0)
        scores = []
        for date in ("2023-01-01", "2026-12-31"):
            rr = _RuntimeRoot([_row("same", "جونگهان خیلی کیوت بود 😭", "SHORT_REACTION", date=date)])
            try:
                memory = ChannelStyleMemory(rr.root, rr.root / "style.sqlite3")
                result = memory.retrieve_examples("جونگهان خیلی کیوت بود 😭", analyze_source("he is so cute 😭"), limit=6)
                self.assertEqual(len(result), 1); scores.append(result[0].score); memory.close()
            finally: rr.close()
        self.assertEqual(scores[0], scores[1])

    def test_glossary_selection_is_relevant_only(self):
        glossary = {"categories": {"member_names": [{"canonical_form": "جونگهان", "alternatives": ["یون جونگهان"]}, {"canonical_form": "جاشوآ", "alternatives": []}], "brands": [{"canonical_form": "بانیلاکو", "alternatives": []}], "platforms": [{"canonical_form": "ویورس", "alternatives": []}]}}
        rr = _RuntimeRoot([_row("a", "نمونه", "OTHER")], glossary)
        try:
            memory = ChannelStyleMemory(rr.root, rr.root / "style.sqlite3")
            selected = memory.relevant_glossary("Jeonghan is back with Banila Co", "جونگهان با بانیلا کو برگشته")
            canon = {x["canonical_form"] for x in selected}
            self.assertIn("جونگهان", canon); self.assertIn("بانیلاکو", canon); self.assertNotIn("جاشوآ", canon); self.assertNotIn("ویورس", canon); self.assertLess(len(selected), 4)
            memory.close()
        finally: rr.close()


class FidelityTests(unittest.TestCase):
    def test_names_numbers_dates_urls_hashtags_laughter(self):
        source = "Jeonghan 2026-08-20 19:30 https://example.com #JEONGHAN ㅋㅋㅋ"; bad = "جونگهان 2026-08-21 19:30 #JEONGHAN"
        issues = verify_hard_facts(source, bad, analyze_source(source))
        self.assertTrue(any("URL" in x for x in issues)); self.assertTrue(any("laughter" in x for x in issues)); self.assertTrue(any("number" in x for x in issues))

    def test_multi_speaker_dialogue_order(self):
        issues = verify_hard_facts("Jeonghan: hi ㅋㅋㅋ\nJoshua: hello", "جاشوآ: سلام\nجونگهان: سلام ㅋㅋㅋ", analyze_source("Jeonghan: hi ㅋㅋㅋ\nJoshua: hello"))
        self.assertIn("speaker identity/order changed", issues)

    def test_quote_structure(self):
        self.assertIn("quoted material/attribution structure lost", verify_hard_facts("Jeonghan said “I missed you.”", "جونگهان گفت دلم براتون تنگ شده بود.", analyze_source("Jeonghan said “I missed you.”")))

    def test_invented_member_identity(self):
        issues = verify_hard_facts("Jeonghan arrived.", "جونگهان با جاشوآ رسید.", analyze_source("Jeonghan arrived."))
        self.assertIn("invented name/identity: JOSHUA", issues)

    def test_brand_glossary_fact(self):
        writer = ChannelStyleCaptionWriter("", "model", _PromptMemory())
        self.assertTrue(writer._glossary_fact_failures("Jeonghan for Banila Co", "جونگهان برای بانیلاکو", "جونگهان برای یک برند"))

    def test_no_historical_fact_leakage(self):
        memory = _PromptMemory(); writer = ChannelStyleCaptionWriter("", "model", memory); group = _group("جونگهان امروز اومد.")
        neutral = GroupCopy("title", "general", {"1": "جونگهان امروز اومد."}); candidate = GroupCopy("title", "general", {"1": "جونگهان امروز با جاشوآ برای بانیلاکو در فن‌ساین 2030 گفت «سلام»."})
        self.assertTrue(writer._contains_historical_fact_leak(group, neutral, candidate))

    def test_verifier_repair_or_fallback(self):
        class Probe(ChannelStyleCaptionWriter):
            def _generate_json(self, client, prompt, schema, *, temperature, purpose): return {"title": "title", "items": [{"id": "1", "body": "جونگهان 2026-08-21"}]}
        writer = Probe("key", "model", _PromptMemory()); group = _group("Jeonghan 2026-08-20")
        result = writer._verify_and_repair(group, GroupCopy("title", "general", {"1": "جونگهان 2026-08-20"}), GroupCopy("title", "general", {"1": "جونگهان 2026-08-21"}), analyze_source(group.updates[0].text), object())
        self.assertEqual(result.bodies["1"], "جونگهان 2026-08-20")


class RewriteAndFeedbackTests(unittest.TestCase):
    def _style_prompt(self, mode: str) -> str:
        memory = _PromptMemory(); captured = []
        class Probe(ChannelStyleCaptionWriter):
            def _generate_json(self, client, prompt, schema, *, temperature, purpose): captured.append(prompt); return {"title": "title", "items": [{"id": "1", "body": "خیلی کیوته 😭"}]}
        writer = Probe("key", "model", memory); group = _group("he is so cute 😭"); neutral = GroupCopy("title", "general", {"1": "خیلی کیوته 😭"})
        writer._style_group(group, neutral, analyze_source(group.updates[0].text), memory.retrieve_examples("", analyze_source(group.updates[0].text)), [], mode, object()); return captured[0]

    def test_funnier(self):
        self.assertIn("نمونه‌های تاریخی بامزه/شیطون", self._style_prompt("funnier")); self.assertEqual(rerank_for_mode(_PromptMemory().retrieve_examples("", analyze_source("cute 😭")), "funnier")[0].example_id, "fun")
    def test_softer(self):
        self.assertIn("نمونه‌های تاریخی نرم/عاطفی", self._style_prompt("softer")); self.assertEqual(rerank_for_mode(_PromptMemory().retrieve_examples("", analyze_source("cute 😭")), "softer")[0].example_id, "soft")
    def test_more_precise(self):
        prompt = self._style_prompt("precise"); self.assertIn("source closeness", prompt); self.assertIn("commentary اختیاری", prompt)

    def test_generated_drafts_not_auto_learned(self):
        memory = _PromptMemory()
        class Probe(ChannelStyleCaptionWriter):
            def _client_or_none(self): return None
            def _neutral_group(self, group, analysis, client): return GroupCopy(group.title, group.category, {"1": group.updates[0].text})
        Probe("", "model", memory).write_group(_group("جونگهان امروز اومد.")); self.assertEqual(memory.feedback_calls, 0)

    def test_rejected_drafts_not_learned_and_confirmed_feedback_isolated(self):
        rr = _RuntimeRoot([_row("hist", "نمونه تاریخی", "OTHER")])
        try:
            memory = ChannelStyleMemory(rr.root, rr.root / "style.sqlite3"); before = memory._count_examples()
            kwargs = dict(source_text="source", source_language="en", content_type="OTHER", generated_text="draft", final_user_text="human edit", timestamp="2026-08-08")
            self.assertFalse(memory.add_confirmed_feedback(**kwargs, confirmed=False)); self.assertEqual(memory.conn.execute("SELECT count(*) FROM translation_feedback").fetchone()[0], 0)
            self.assertTrue(memory.add_confirmed_feedback(**kwargs, confirmed=True)); self.assertEqual(memory.conn.execute("SELECT count(*) FROM translation_feedback").fetchone()[0], 1)
            self.assertEqual(memory._count_examples(), before); self.assertEqual(historical_base_style_weight("2026"), 1.0); memory.close()
        finally: rr.close()


if __name__ == "__main__": unittest.main()
