from __future__ import annotations

import copy
import json
import os
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

from app import event_fusion, event_timeline
from app.channel_style_rewrite import (
    MAX_STYLE_EXAMPLES,
    STYLE_REWRITE_MODE,
    ConservativeLocalStyleProvider,
    StyleEditFeedback,
    StyleProfile,
    ai_like_findings,
    audit_requested_profiles,
    build_style_rewrite_input,
    evaluate_style_candidate,
    profile_for_content_type,
    retrieve_structural_examples,
    rewrite_shadow_candidate,
    shadow_style_rewrite,
    style_fidelity_failures,
)
from app.channel_style_runtime import RetrievedStyleExample
from app.event_fusion_private_runtime import ensure_translation_fusion_shadow
from app.fic_state import FicObservation, FicStateStore
from app.message_delivery import MessageDeliveryStore
from app.models import MediaItem, Update
from app.realtime_ingest import realtime_shadow_enabled
from app.state import StateStore
from app.zero_silent_miss import media_asset_id
from tools.run_channel_style_rewrite_benchmark import run as run_style_benchmark
from tools.run_translation_fusion_benchmark import run as run_translation_benchmark

ROOT = Path(__file__).resolve().parents[1]
CONFIGURED = {"hani_berry_1004", "honeyya_hanihae", "pledis_17", "pledis_17jp"}


class SyntheticMemory:
    def __init__(self, rows: list[tuple[str, str]] | None = None):
        self.conn = sqlite3.connect(":memory:")
        self.conn.row_factory = sqlite3.Row
        self.conn.execute(
            "CREATE TABLE channel_style_examples("
            "example_id TEXT PRIMARY KEY,text TEXT NOT NULL,content_type TEXT NOT NULL,"
            "source_language TEXT NOT NULL,date TEXT NOT NULL,char_count INTEGER NOT NULL,"
            "has_dialogue INTEGER NOT NULL)"
        )
        rows = rows or [("OTHER", f"نمونه سبک {i}") for i in range(16)]
        for index, (content_type, text) in enumerate(rows):
            self.conn.execute(
                "INSERT INTO channel_style_examples VALUES(?,?,?,?,?,?,?)",
                (
                    f"e:{index}",
                    text,
                    content_type,
                    "fa",
                    f"2026-01-{(index % 28) + 1:02d}",
                    len(text),
                    int(":" in text or "：" in text),
                ),
            )
        self.conn.commit()

    def close(self):
        self.conn.close()


class FailingProvider:
    name = "failing_fixture"

    def rewrite(self, rewrite_input, examples, profile):
        raise RuntimeError("unavailable")


def style_example(text: str, content_type: str = "MEMBER_QUOTE", example_id: str = "hist:1"):
    return RetrievedStyleExample(
        example_id=example_id,
        text=text,
        content_type=content_type,
        source_language="fa",
        date="2024-01-01",
        score=1.0,
        reasons=["fixture"],
    )


def profile(content_type: str = "OTHER", text: str = "جونگهان امروز خوب بود.") -> StyleProfile:
    return StyleProfile(
        key=content_type,
        content_type=content_type,
        example_count=100,
        register="factual",
        median_chars=float(len(text)),
        multiline_pct=1.0 if "\n" in text else 0.0,
        dialogue_pct=1.0 if ":" in text or "：" in text else 0.0,
        emoji_pct=0.0,
        reaction_pct=0.0,
        formal_connector_pct=0.0,
        supported=True,
    )


def update(
    update_id: str = "u1",
    *,
    text: str = "جونگهان امروز خوب بود.",
    author: str = "hani_berry_1004",
    created_at: str = "2026-08-15T01:00:00+00:00",
) -> Update:
    return Update(
        id=update_id,
        url=f"https://x.com/{author}/status/{update_id}",
        author=author,
        author_name=author,
        text=text,
        created_at=datetime.fromisoformat(created_at),
        lang="fa",
    )


class ChannelStyleRewriteFoundationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        ensure_translation_fusion_shadow()

    def make_input(self, factual: str, content_type: str = "OTHER", examples=()):
        return build_style_rewrite_input(
            factual,
            event_id="evt:test",
            segment_id="seg:test",
            content_type=content_type,
            style_profile=content_type,
            selected_example_ids=[getattr(item, "example_id", item) for item in examples],
        )

    def seed_segment(self, state: StateStore, item: Update) -> tuple[str, str]:
        fusion = event_fusion._event_state(state)
        event_id = "evt:test"
        segment_id = "seg:test"
        now = "2026-08-15T01:00:00+00:00"
        fusion["events"][event_id] = {
            "event_id": event_id,
            "event_type": "other",
            "created_at": now,
            "updated_at": now,
            "member_update_ids": [item.id],
            "confidence": 0.95,
            "status": "shadow_candidate",
            "subject_key": "subject:test",
        }
        fusion["memberships"][item.id] = {
            "event_id": event_id,
            "confidence": 0.95,
            "matching_signals": ["fixture"],
            "conflicts": [],
            "decision": "probable_same_event",
            "updated_at": now,
        }
        fusion["segments"][segment_id] = {
            "segment_id": segment_id,
            "event_id": event_id,
            "created_at": now,
            "updated_at": now,
            "member_update_ids": [item.id],
            "confidence": 0.95,
            "status": "shadow_candidate",
            "order_index": 1,
            "order_evidence": {"kind": "source_created_at", "value": now, "confidence": "source_time"},
        }
        fusion["segment_memberships"][item.id] = {
            "event_id": event_id,
            "segment_id": segment_id,
            "confidence": 0.95,
            "relationship": "seed",
            "matching_signals": ["fixture"],
            "conflicts": [],
            "updated_at": now,
        }
        return event_id, segment_id

    def test_style_input_contract_contains_no_historical_text(self):
        historical = style_example("جونگهان گفت رفت ژاپن.")
        value = self.make_input("جونگهان گفت رفت بوسان.", examples=[historical])
        self.assertEqual(value.faithful_factual_text, "جونگهان گفت رفت بوسان.")
        self.assertEqual(value.selected_style_example_ids, ("hist:1",))
        self.assertFalse(hasattr(value, "historical_text"))
        self.assertNotIn("ژاپن", repr(value.hard_factual_invariants))

    def test_style_example_ids_are_bounded(self):
        value = build_style_rewrite_input(
            "متن",
            event_id="e",
            segment_id="s",
            selected_example_ids=[f"x:{i}" for i in range(50)],
        )
        self.assertEqual(len(value.selected_style_example_ids), MAX_STYLE_EXAMPLES)

    def test_structural_retrieval_is_bounded(self):
        memory = SyntheticMemory([("OTHER", f"نمونه {i}") for i in range(30)])
        try:
            result = retrieve_structural_examples(memory, self.make_input("متن کوتاه"), limit=99)
            self.assertLessEqual(len(result), MAX_STYLE_EXAMPLES)
        finally:
            memory.close()

    def test_structural_retrieval_rejects_unrelated_family(self):
        rows = [("OFFICIAL_NEWS", f"اعلام رسمی {i}") for i in range(20)]
        rows += [("LIVE_DIALOGUE", f"🪽: لایو {i}") for i in range(20)]
        memory = SyntheticMemory(rows)
        try:
            value = self.make_input("اعلام رسمی منتشر شد.", "OFFICIAL_NEWS")
            result = retrieve_structural_examples(memory, value)
            self.assertTrue(result)
            self.assertTrue(all(item.content_type != "LIVE_DIALOGUE" for item in result))
        finally:
            memory.close()

    def test_retrieval_does_not_use_topic_similarity_reason(self):
        memory = SyntheticMemory([("OTHER", f"ژاپن موضوع قدیمی {i}") for i in range(20)])
        try:
            result = retrieve_structural_examples(memory, self.make_input("بوسان اتفاق جدید"))
            self.assertTrue(result)
            self.assertTrue(all("topic/lexical similarity excluded" in item.reasons for item in result))
            self.assertTrue(all("FTS" not in " ".join(item.reasons) for item in result))
        finally:
            memory.close()

    def test_content_type_profile_requires_real_support(self):
        memory = SyntheticMemory([("INTERVIEW", f"مصاحبه {i}") for i in range(14)])
        try:
            result = profile_for_content_type(memory, "INTERVIEW")
            self.assertTrue(result.supported)
            self.assertEqual(result.example_count, 14)
        finally:
            memory.close()

    def test_requested_profiles_are_evidence_counted(self):
        rows = [("LIVE_DIALOGUE", f"🪽: لایو {i}") for i in range(14)]
        rows += [("BRAND_AD", f"brand campaign {i}") for i in range(14)]
        memory = SyntheticMemory(rows)
        try:
            audit = audit_requested_profiles(memory)
            self.assertGreaterEqual(audit["live_translation"].example_count, 14)
            self.assertTrue(audit["live_translation"].supported)
            self.assertGreaterEqual(audit["brand_event"].example_count, 14)
        finally:
            memory.close()

    def test_factual_input_is_preserved_for_safe_candidate(self):
        factual = "جونگهان امروز خوب بود."
        result = evaluate_style_candidate(self.make_input(factual), factual, [], profile(text=factual))
        self.assertTrue(result.accepted)
        self.assertEqual(result.final_text, factual)

    def test_historical_fact_leak_japan_vs_busan_is_rejected(self):
        factual = "جونگهان گفت رفت بوسان."
        historical = style_example("جونگهان گفت رفت ژاپن.")
        result = evaluate_style_candidate(
            self.make_input(factual, "MEMBER_QUOTE", [historical]),
            "جونگهان گفت رفت ژاپن.",
            [historical],
            profile("MEMBER_QUOTE", factual),
        )
        self.assertFalse(result.accepted)
        self.assertEqual(result.final_text, factual)
        self.assertTrue(any("historical_example_exclusive_token:ژاپن" == item for item in result.fidelity_failures))

    def test_name_preservation(self):
        self.assertFalse(style_fidelity_failures("جونگهان مینگیو رو دید.", "جونگهان مینگیو رو دید."))

    def test_name_drop_rejected(self):
        self.assertTrue(style_fidelity_failures("جونگهان مینگیو رو دید.", "جونگهان دید."))

    def test_actor_role_swap_rejected(self):
        failures = style_fidelity_failures("جونگهان مینگیو رو دید.", "مینگیو جونگهان رو دید.")
        self.assertIn("actor_identity_order_changed", failures)

    def test_number_preservation(self):
        self.assertFalse(style_fidelity_failures("3 نفر اومدن.", "3 نفر اومدن."))
        self.assertTrue(style_fidelity_failures("3 نفر اومدن.", "5 نفر اومدن."))

    def test_date_preservation(self):
        self.assertFalse(style_fidelity_failures("18 اوت 2026 منتشر می‌شود.", "18 اوت 2026 منتشر می‌شود."))
        self.assertTrue(style_fidelity_failures("18 اوت 2026 منتشر می‌شود.", "19 اوت 2026 منتشر می‌شود."))

    def test_negation_preservation(self):
        self.assertFalse(style_fidelity_failures("جونگهان نرفت.", "جونگهان نرفت."))
        self.assertTrue(style_fidelity_failures("جونگهان نرفت.", "جونگهان رفت."))

    def test_question_statement_preservation(self):
        self.assertFalse(style_fidelity_failures("خوبی؟", "خوبی؟"))
        self.assertTrue(style_fidelity_failures("خوبی؟", "خوبی."))

    def test_modality_preservation(self):
        self.assertFalse(style_fidelity_failures("شاید بعداً بیاد.", "شاید بعداً بیاد."))
        self.assertTrue(style_fidelity_failures("شاید بعداً بیاد.", "بعداً میاد."))

    def test_speaker_preservation(self):
        factual = "🪽: سلام\n🐶: خوبی؟"
        self.assertFalse(style_fidelity_failures(factual, factual))
        self.assertTrue(style_fidelity_failures(factual, "🪽: سلام"))

    def test_chronology_preservation(self):
        factual = "اول تمرین کرد و بعد رفت خونه."
        self.assertFalse(style_fidelity_failures(factual, factual))
        failures = style_fidelity_failures(factual, "بعد رفت خونه و اول تمرین کرد.")
        self.assertIn("chronology_changed", failures)

    def test_url_preservation(self):
        factual = "لینک https://example.com/a"
        self.assertFalse(style_fidelity_failures(factual, factual))
        self.assertTrue(style_fidelity_failures(factual, "لینک https://example.com/b"))

    def test_unsupported_relationship_interpretation_rejected(self):
        factual = "جونگهان به سونگچول لبخند زد."
        candidate = "جونگهان به سونگچول لبخند زد چون عاشقشه."
        self.assertTrue(style_fidelity_failures(factual, candidate))

    def test_safe_punctuation_change_is_allowed(self):
        factual = "جونگهان گفت : امروز خوب بود !"
        candidate = "جونگهان گفت: امروز خوب بود!"
        result = evaluate_style_candidate(
            self.make_input(factual, "MEMBER_QUOTE"),
            candidate,
            [],
            profile("MEMBER_QUOTE", factual),
        )
        self.assertTrue(result.accepted)

    def test_high_style_score_never_overrides_fact_failure(self):
        factual = "3 نفر اومدن."
        p = profile("FACTUAL_INFORMATION", "5 نفر اومدن.")
        result = evaluate_style_candidate(
            self.make_input(factual, "FACTUAL_INFORMATION"),
            "5 نفر اومدن.",
            [],
            p,
        )
        self.assertFalse(result.accepted)
        self.assertEqual(result.final_text, factual)

    def test_style_provider_failure_falls_back(self):
        memory = SyntheticMemory()
        try:
            result = rewrite_shadow_candidate(
                memory,
                "جونگهان امروز خوب بود.",
                event_id="e",
                segment_id="s",
                content_type="OTHER",
                provider=FailingProvider(),
            )
            self.assertFalse(result.accepted)
            self.assertEqual(result.fallback_reason, "style_provider_failed")
        finally:
            memory.close()

    def test_style_retrieval_failure_falls_back(self):
        memory = SyntheticMemory()
        try:
            with patch("app.channel_style_rewrite.retrieve_structural_examples", return_value=[]):
                result = rewrite_shadow_candidate(
                    memory,
                    "جونگهان امروز خوب بود.",
                    event_id="e",
                    segment_id="s",
                    content_type="OTHER",
                )
            self.assertFalse(result.accepted)
            self.assertEqual(result.fallback_reason, "style_example_retrieval_failed")
        finally:
            memory.close()

    def test_unsupported_profile_falls_back(self):
        memory = SyntheticMemory([("OTHER", "فقط یک نمونه")])
        try:
            result = rewrite_shadow_candidate(
                memory,
                "جونگهان امروز خوب بود.",
                event_id="e",
                segment_id="s",
                content_type="OTHER",
            )
            self.assertFalse(result.accepted)
            self.assertEqual(result.fallback_reason, "unsupported_style_profile")
        finally:
            memory.close()

    def test_metadata_persists_no_text(self):
        factual = "جونگهان امروز خوب بود."
        result = evaluate_style_candidate(self.make_input(factual), factual, [], profile(text=factual))
        metadata = result.state_metadata()
        serialized = json.dumps(metadata, ensure_ascii=False)
        self.assertNotIn(factual, serialized)
        self.assertNotIn("factual_text", metadata)
        self.assertNotIn("candidate_text", metadata)
        self.assertFalse(metadata["text_persisted"])
        self.assertTrue(metadata["factual_draft_fingerprint"])

    def test_feedback_interface_does_not_auto_learn(self):
        feedback = StyleEditFeedback(
            event_id="e",
            segment_id="s",
            content_type="LIVE_DIALOGUE",
            factual_draft_fingerprint="f",
            bot_style_fingerprint="b",
            final_edit_fingerprint="u",
            feedback_kind="style_preference",
            confirmed=True,
        ).metadata()
        self.assertEqual(feedback["feedback_kind"], "style_preference")
        self.assertFalse(feedback["auto_learn"])

    def test_feedback_kinds_are_bounded_not_global_rules(self):
        feedback = StyleEditFeedback(
            event_id="e",
            segment_id="s",
            content_type="OTHER",
            factual_draft_fingerprint="f",
            bot_style_fingerprint="b",
            final_edit_fingerprint="u",
            feedback_kind="invent_global_rule",
        ).metadata()
        self.assertEqual(feedback["feedback_kind"], "unclassified")
        self.assertFalse(feedback["auto_learn"])

    def test_ai_like_formal_connector_is_detected(self):
        self.assertIn("formal_connector_overuse", ai_like_findings("لازم به ذکر است این متن خوب است."))

    def test_ai_like_generic_filler_is_detected(self):
        self.assertIn(
            "generic_emotional_or_explanatory_filler",
            ai_like_findings("در نهایت می‌توان گفت این اتفاق افتاد."),
        )

    def test_overcute_control_for_factual_profile(self):
        p = StyleProfile("OFFICIAL_NEWS", "OFFICIAL_NEWS", 100, "factual", 40, 0, 0, 0.1, 0.1, 0, True)
        self.assertIn("over_cute_for_profile", ai_like_findings("جونگهان عسلم اومد.", p))

    def test_shadow_runtime_uses_faithful_translation_result_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            state = StateStore(Path(tmp) / "state.json")
            item = update()
            self.seed_segment(state, item)
            memory = SyntheticMemory()
            try:
                results = shadow_style_rewrite(state, memory, [item], CONFIGURED)
                self.assertEqual(len(results), 1)
                self.assertEqual(results[0].factual_text, item.text)
                self.assertEqual(results[0].final_text, item.text)
            finally:
                memory.close()

    def test_shadow_state_is_bounded_metadata_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            state = StateStore(Path(tmp) / "state.json")
            item = update()
            _, sid = self.seed_segment(state, item)
            memory = SyntheticMemory()
            try:
                shadow_style_rewrite(state, memory, [item], CONFIGURED)
                metadata = state.data["event_fusion"]["style_rewrite_results"][sid]
                serialized = json.dumps(metadata, ensure_ascii=False)
                self.assertNotIn(item.text, serialized)
                self.assertFalse(metadata["text_persisted"])
                self.assertLessEqual(len(metadata["selected_style_example_ids"]), MAX_STYLE_EXAMPLES)
            finally:
                memory.close()

    def test_event_and_timeline_are_untouched(self):
        with tempfile.TemporaryDirectory() as tmp:
            state = StateStore(Path(tmp) / "state.json")
            item = update()
            self.seed_segment(state, item)
            fusion = state.data["event_fusion"]
            before = copy.deepcopy({
                key: fusion.get(key)
                for key in ("events", "memberships", "segments", "segment_memberships", "segment_relationships")
            })
            memory = SyntheticMemory()
            try:
                shadow_style_rewrite(state, memory, [item], CONFIGURED)
            finally:
                memory.close()
            after = {key: fusion.get(key) for key in before}
            self.assertEqual(after, before)

    def test_phase2_phase3_lifecycle_are_untouched(self):
        with tempfile.TemporaryDirectory() as tmp:
            state = StateStore(Path(tmp) / "state.json")
            item = update()
            self.seed_segment(state, item)
            state.data["seen"] = {"old": "keep"}
            state.data["pending_delivery"] = [{"id": "pending"}]
            state.data["update_lifecycle"] = {"u1": {"status": "pending_delivery"}}
            state.data["x_retrieval_checkpoints"] = {"cp": {"complete": False}}
            before = copy.deepcopy({
                key: state.data.get(key)
                for key in ("seen", "pending_delivery", "update_lifecycle", "x_retrieval_checkpoints")
            })
            memory = SyntheticMemory()
            try:
                shadow_style_rewrite(state, memory, [item], CONFIGURED)
            finally:
                memory.close()
            self.assertEqual({key: state.data.get(key) for key in before}, before)

    def test_translation_fusion_metadata_is_untouched(self):
        with tempfile.TemporaryDirectory() as tmp:
            state = StateStore(Path(tmp) / "state.json")
            item = update()
            self.seed_segment(state, item)
            fusion = state.data["event_fusion"]
            fusion["translation_fusion_results"] = {"sentinel": {"keep": True}}
            before = copy.deepcopy(fusion["translation_fusion_results"])
            memory = SyntheticMemory()
            try:
                shadow_style_rewrite(state, memory, [item], CONFIGURED)
            finally:
                memory.close()
            self.assertEqual(fusion["translation_fusion_results"], before)

    def test_telegram_receipts_are_untouched(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = MessageDeliveryStore(Path(tmp) / "delivery.sqlite3")
            db.save_plan("draft:test", "hello", ["hello"])
            db.confirm("draft:test", 0, "hello", 123)
            state = StateStore(Path(tmp) / "state.json")
            item = update()
            self.seed_segment(state, item)
            memory = SyntheticMemory()
            try:
                shadow_style_rewrite(state, memory, [item], CONFIGURED)
                self.assertEqual(db.confirmed_message_id("draft:test", 0, "hello"), 123)
                self.assertEqual(db.get_plan("draft:test"), ("hello", ["hello"]))
            finally:
                memory.close()
                db.close()

    def test_media_is_untouched(self):
        with tempfile.TemporaryDirectory() as tmp:
            state = StateStore(Path(tmp) / "state.json")
            item = update()
            item.media = [MediaItem(kind="video", url="https://media.example/a.mp4")]
            self.seed_segment(state, item)
            before = copy.deepcopy(item.media)
            memory = SyntheticMemory()
            try:
                shadow_style_rewrite(state, memory, [item], CONFIGURED)
            finally:
                memory.close()
            self.assertEqual(item.media, before)

    def test_concert_media_identity_stays_independent(self):
        event_id = event_fusion.make_event_id(("u1", "u2"))
        segment_id = event_timeline.make_segment_id(event_id, ("u1",))
        media_a = media_asset_id("video", "https://media.example/fancam-a.mp4")
        media_b = media_asset_id("video", "https://media.example/fancam-b.mp4")
        value = self.make_input("🪽: امشب ممنونم.", "MEMBER_QUOTE")
        self.assertFalse(hasattr(value, "media"))
        self.assertNotEqual(segment_id, media_a)
        self.assertNotEqual(media_a, media_b)

    def test_fanfic_import_does_not_load_style_rewrite(self):
        code = """
import json
import sys
import app.fic_digest
blocked = sorted(
    name for name in sys.modules
    if name in {
        'app.channel_style_rewrite',
        'app.channel_style_rewrite_state_compat',
        'app.translation_fusion',
        'app.translation_fusion_runtime',
        'app.translation_fusion_state_compat',
    }
)
print(json.dumps(blocked))
"""
        completed = subprocess.run(
            [sys.executable, "-c", code],
            cwd=ROOT,
            check=True,
            capture_output=True,
            text=True,
        )
        self.assertEqual(json.loads(completed.stdout.strip().splitlines()[-1]), [])

    def test_fanfic_state_remains_independent(self):
        with tempfile.TemporaryDirectory() as tmp:
            fic = FicStateStore(Path(tmp) / "fic.sqlite3")
            observation = FicObservation("123", "2/5", "2026-08-15")
            self.assertEqual(fic.classify(observation), "new")
            state = StateStore(Path(tmp) / "state.json")
            item = update()
            self.seed_segment(state, item)
            memory = SyntheticMemory()
            try:
                shadow_style_rewrite(state, memory, [item], CONFIGURED)
                self.assertEqual(fic.classify(observation), "unchanged")
            finally:
                memory.close()
                fic.close()

    def test_private_review_realtime_and_source_invariants(self):
        settings = json.loads((ROOT / "config" / "settings.json").read_text(encoding="utf-8"))
        sources = json.loads((ROOT / "config" / "sources.json").read_text(encoding="utf-8"))
        enabled = [
            str(item["handle"]).lstrip("@").casefold()
            for item in sources["sources"]
            if item.get("enabled", True)
        ]
        self.assertIs(settings["runtime"]["review_only"], True)
        self.assertEqual(len(enabled), 23)
        self.assertEqual(len(set(enabled)), 23)
        self.assertNotIn("flamehanie", enabled)
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("REALTIME_SHADOW_MODE", None)
            self.assertFalse(realtime_shadow_enabled())

    def test_style_corpus_authority_remains_16306(self):
        manifest = json.loads((ROOT / "data" / "channel_style" / "manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(manifest["authority_message_count"], 16306)
        self.assertEqual(sum(item["example_count"] for item in manifest["shards"]), 16306)

    def test_no_paid_or_new_queue_database_dependency(self):
        requirements = "\n".join(
            (ROOT / path).read_text(encoding="utf-8").casefold()
            for path in ("requirements.txt", "requirements-optional-media.txt")
        )
        for name in ("supabase", "redis", "celery", "pinecone", "weaviate", "qdrant", "openai", "anthropic"):
            self.assertNotIn(name, requirements)

    def test_style_rewrite_mode_is_shadow(self):
        self.assertEqual(STYLE_REWRITE_MODE, "shadow")

    def test_existing_local_provider_is_free_and_deterministic(self):
        provider = ConservativeLocalStyleProvider()
        value = self.make_input("جونگهان گفت : خوب بود !")
        result = provider.rewrite(value, [], profile(text=value.faithful_factual_text))
        self.assertEqual(result, "جونگهان گفت: خوب بود!")

    def test_channel_style_benchmark_has_40_cases(self):
        result = run_style_benchmark(include_corpus_audit=False)
        self.assertTrue(result["passed"], result)
        self.assertEqual(result["case_count"], 40)
        self.assertEqual(result["final_fidelity_failure_count"], 0)
        self.assertEqual(result["historical_fact_leakage_protection"], 1.0)
        self.assertEqual(result["fallback_correctness"], 1.0)
        self.assertEqual(result["ai_like_rejection"], 1.0)

    def test_translation_fusion_benchmark_remains_green(self):
        result = run_translation_benchmark(ROOT / "data" / "translation_fusion_benchmark.json")
        self.assertTrue(result["passed"], result)
        self.assertEqual(result["unsupported_addition_count"], 0)


if __name__ == "__main__":
    unittest.main()
