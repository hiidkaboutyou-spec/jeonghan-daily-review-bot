from __future__ import annotations

import copy
import json
import os
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

from app import event_fusion, event_timeline
from app.event_fusion_private_runtime import ensure_translation_fusion_shadow
from app.fic_state import FicObservation, FicStateStore
from app.message_delivery import MessageDeliveryStore
from app.models import Update
from app.realtime_ingest import realtime_shadow_enabled
from app.state import StateStore
from app.translation_fusion import (
    TRANSLATION_FUSION_MODE,
    TranslationEvidence,
    build_evidence_for_segment,
    evidence_score,
    fidelity_failures,
    fuse_evidence_items,
    shadow_fuse_translations,
)
from app.zero_silent_miss import media_asset_id
from tools.run_translation_fusion_benchmark import run as run_benchmark

ROOT = Path(__file__).resolve().parents[1]
CONFIGURED = {"hani_berry_1004", "honeyya_hanihae", "pledis_17", "pledis_17jp"}


def update(
    update_id: str,
    *,
    author: str = "hani_berry_1004",
    text: str = "جونگهان گفت امروز خوب بوده.",
    created_at: str = "2026-08-15T01:00:00+00:00",
    lang: str = "fa",
) -> Update:
    return Update(
        id=update_id,
        url=f"https://x.com/{author}/status/{update_id}",
        author=author,
        author_name=author,
        text=text,
        created_at=datetime.fromisoformat(created_at.replace("Z", "+00:00")),
        lang=lang,
    )


def evidence(
    update_id: str,
    source: str,
    candidate: str,
    *,
    kind: str = "direct_translation",
    relationship: str = "same_moment",
    strength: float = 0.9,
    language: str = "en",
) -> TranslationEvidence:
    return TranslationEvidence(
        update_id=update_id,
        source="hani_berry_1004",
        source_language=language,
        evidence_kind=kind,
        original_text=source,
        candidate_text=candidate,
        event_id="evt:test",
        segment_id="seg:test",
        relationship=relationship,
        relationship_confidence=0.95,
        evidence_strength=strength,
        matching_signals=("fixture",),
    )


class TranslationFusionFoundationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        # Translation Fusion is intentionally lazy for Fanfic independence. Explicitly
        # activate the non-Fanfic shadow layer for this foundation test suite.
        ensure_translation_fusion_shadow()

    def state(self, directory: str) -> StateStore:
        return StateStore(Path(directory) / "state.json")

    def seed_segment(
        self,
        state: StateStore,
        items: list[Update],
        *,
        relationships: dict[str, str] | None = None,
        segment_id: str = "seg:test",
        event_id: str = "evt:test",
    ) -> tuple[str, str]:
        relationships = relationships or {}
        fusion = event_fusion._event_state(state)
        now = "2026-08-15T01:00:00+00:00"
        fusion["events"][event_id] = {
            "event_id": event_id,
            "event_type": "live",
            "created_at": now,
            "updated_at": now,
            "member_update_ids": sorted(item.id for item in items),
            "confidence": 0.95,
            "status": "shadow_candidate",
            "subject_key": "subject:test",
        }
        for item in items:
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
            "member_update_ids": sorted(item.id for item in items),
            "confidence": 0.95,
            "status": "shadow_candidate",
            "order_index": 1,
            "order_evidence": {"kind": "source_created_at", "value": now, "confidence": "source_time"},
        }
        for index, item in enumerate(items):
            fusion["segment_memberships"][item.id] = {
                "event_id": event_id,
                "segment_id": segment_id,
                "confidence": 0.95,
                "relationship": relationships.get(item.id, "seed" if index == 0 else "same_moment"),
                "matching_signals": ["fixture"],
                "conflicts": [],
                "updated_at": now,
            }
        return event_id, segment_id

    def test_configured_source_evidence_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            state = self.state(tmp)
            good = update("u1")
            rogue = update("u2", author="random_external")
            _, sid = self.seed_segment(state, [good, rogue])
            rows = build_evidence_for_segment(state, sid, CONFIGURED, {"u1": good, "u2": rogue})
            self.assertEqual([item.update_id for item in rows], ["u1"])

    def test_event_and_segment_scope_respected(self):
        with tempfile.TemporaryDirectory() as tmp:
            state = self.state(tmp)
            a = update("u1")
            b = update("u2", author="honeyya_hanihae")
            _, sid = self.seed_segment(state, [a], segment_id="seg:one", event_id="evt:one")
            self.seed_segment(state, [b], segment_id="seg:two", event_id="evt:two")
            rows = build_evidence_for_segment(state, sid, CONFIGURED, {"u1": a, "u2": b})
            self.assertEqual([item.update_id for item in rows], ["u1"])
            self.assertTrue(all(item.segment_id == "seg:one" and item.event_id == "evt:one" for item in rows))

    def test_separate_relation_never_auto_fused(self):
        result = fuse_evidence_items([
            evidence("u1", "Jeonghan ate dinner.", "جونگهان شام خورد.", strength=1.0),
            evidence("u2", "Jeonghan went to the airport.", "جونگهان رفت فرودگاه.", relationship="separate"),
        ])
        self.assertNotIn("فرودگاه", result.fused_factual_text)
        self.assertIn("u2", result.withheld_update_ids)
        self.assertTrue(result.review_required)

    def test_ambiguous_relation_never_auto_fused(self):
        result = fuse_evidence_items([
            evidence("u1", "Jeonghan ate dinner.", "جونگهان شام خورد.", strength=1.0),
            evidence("u2", "He mentioned practice.", "درباره تمرین گفت.", relationship="ambiguous"),
        ])
        self.assertNotIn("تمرین", result.fused_factual_text)
        self.assertIn("u2", result.withheld_update_ids)
        self.assertTrue(result.review_required)

    def test_stable_backbone_selection_is_factual_not_style(self):
        direct = evidence("u1", "Jeonghan met Dokyeom.", "جونگهان دوکیوم رو دید.", strength=1.0)
        pretty_summary = evidence(
            "u9",
            "He met Dokyeom.",
            "بالاخره دیدار خیلی قشنگشون اتفاق افتاد.",
            kind="summary_or_paraphrase",
            strength=1.0,
        )
        first = fuse_evidence_items([pretty_summary, direct])
        second = fuse_evidence_items([direct, pretty_summary])
        self.assertEqual(first.backbone_update_id, "u1")
        self.assertEqual(second.backbone_update_id, "u1")

    def test_complementary_fact_is_preserved(self):
        result = fuse_evidence_items([
            evidence("u1", "Jeonghan met Dokyeom yesterday.", "جونگهان گفت دیروز دوکیوم رو دیده.", strength=1.0),
            evidence("u2", "They ate dinner after practice.", "بعد تمرین باهم شام خوردن.", relationship="complementary"),
        ])
        self.assertEqual(result.complementary_update_ids, ("u2",))
        self.assertIn("شام خوردن", result.fused_factual_text)

    def test_unsupported_relationship_inference_is_blocked(self):
        result = fuse_evidence_items([
            evidence("u1", "Jeonghan smiled at him.", "جونگهان بهش لبخند زد.", strength=1.0),
            evidence(
                "u2",
                "He smiled at him.",
                "حتماً عاشقشه چون بهش لبخند زد.",
                kind="summary_or_paraphrase",
                relationship="complementary",
                strength=0.5,
            ),
        ])
        self.assertNotIn("عاشقشه", result.fused_factual_text)
        self.assertIn("u2", result.withheld_update_ids)
        self.assertTrue(result.review_required)

    def test_number_conflict_is_preserved(self):
        result = fuse_evidence_items([
            evidence("u1", "Jeonghan said 3 people came.", "جونگهان گفت 3 نفر اومدن.", strength=1.0),
            evidence("u2", "Jeonghan said 5 people came.", "جونگهان گفت 5 نفر اومدن.", kind="summary_or_paraphrase"),
        ])
        self.assertEqual(result.conflict_update_ids, ("u2",))
        self.assertTrue(result.review_required)
        self.assertIn("number_or_date_conflict", result.unresolved_conflicts)
        self.assertNotIn("5 نفر", result.fused_factual_text)

    def test_unresolved_conflict_requires_review(self):
        result = fuse_evidence_items([
            evidence("u1", "Jeonghan did not go.", "جونگهان نرفت.", strength=1.0),
            evidence("u2", "Jeonghan went.", "جونگهان رفت.", relationship="conflicting", kind="summary_or_paraphrase"),
        ])
        self.assertEqual(result.fidelity_status, "needs_review")
        self.assertTrue(result.review_required)

    def test_name_preservation_gate(self):
        self.assertFalse(fidelity_failures("Jeonghan met Mingyu.", "جونگهان مینگیو رو دید."))

    def test_number_preservation_gate(self):
        self.assertFalse(fidelity_failures("There were 3 people.", "3 نفر بودن."))
        self.assertTrue(any("number" in item for item in fidelity_failures("There were 3 people.", "5 نفر بودن.")))

    def test_negation_preservation_gate(self):
        self.assertFalse(fidelity_failures("I did not go.", "من نرفتم."))
        self.assertIn("negation_dropped", fidelity_failures("I did not go.", "من رفتم."))

    def test_speaker_preservation_gate(self):
        self.assertFalse(fidelity_failures("🐶: Hello\n🪽: Hi", "🐶: سلام\n🪽: سلام"))

    def test_question_statement_preservation_gate(self):
        self.assertFalse(fidelity_failures("Are you okay?", "خوبی؟"))
        self.assertIn("question_statement_polarity_changed", fidelity_failures("Are you okay?", "خوبی."))

    def test_modality_preservation_gate(self):
        self.assertFalse(fidelity_failures("He might come later.", "شاید بعداً بیاد."))
        self.assertIn("modality_dropped", fidelity_failures("He might come later.", "بعداً میاد."))

    def test_original_language_evidence_is_weighted_above_summary(self):
        original = evidence("u1", "정한: 오늘 즐거웠어요.", "", kind="original_language", relationship="seed", language="ko", strength=1.0)
        summary = evidence("u2", "He had fun.", "خوش گذشت.", kind="summary_or_paraphrase", strength=0.5)
        self.assertGreater(evidence_score(original)[0], evidence_score(summary)[0])

    def test_direct_translation_is_distinguished_from_summary(self):
        direct = evidence("u1", "Jeonghan said hello.", "جونگهان سلام کرد.", kind="direct_translation", strength=0.8)
        summary = evidence("u2", "He greeted them.", "سلام کرد.", kind="summary_or_paraphrase", strength=0.8)
        self.assertGreater(evidence_score(direct)[0], evidence_score(summary)[0])

    def test_evidence_traceability_without_body_duplication(self):
        row = evidence("u1", "Jeonghan said hello.", "جونگهان سلام کرد.").metadata()
        self.assertEqual(row["update_id"], "u1")
        self.assertNotIn("original_text", row)
        self.assertNotIn("candidate_text", row)
        self.assertIn("source_text_hash", row)
        self.assertIn("candidate_text_hash", row)

    def test_shadow_state_persists_bounded_metadata_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "state.json"
            state = StateStore(path)
            a = update("u1")
            b = update("u2", author="honeyya_hanihae", text="بعد تمرین هم شام خوردن.")
            _, sid = self.seed_segment(state, [a, b], relationships={"u2": "complementary"})
            shadow_fuse_translations(state, [a, b], CONFIGURED)
            metadata = state.data["event_fusion"]["translation_fusion_results"][sid]
            self.assertNotIn("fused_factual_text", metadata)
            self.assertIn("fused_text_hash", metadata)
            self.assertTrue(metadata["evidence_update_ids"])
            state.save()
            restored = StateStore(path)
            self.assertIn(sid, restored.data["event_fusion"]["translation_fusion_results"])

    def test_update_lifecycle_and_phase3_are_untouched(self):
        with tempfile.TemporaryDirectory() as tmp:
            state = self.state(tmp)
            a = update("u1")
            b = update("u2", author="honeyya_hanihae")
            self.seed_segment(state, [a, b])
            state.data["seen"] = {"old": "keep"}
            state.data["pending_delivery"] = [{"id": "pending"}]
            state.data["update_lifecycle"] = {"u1": {"status": "pending_delivery"}}
            state.data["x_retrieval_checkpoints"] = {"cp": {"complete": False}}
            before = copy.deepcopy({
                key: state.data.get(key)
                for key in ("seen", "pending_delivery", "update_lifecycle", "x_retrieval_checkpoints")
            })
            shadow_fuse_translations(state, [a, b], CONFIGURED)
            after = {key: state.data.get(key) for key in before}
            self.assertEqual(after, before)

    def test_event_and_timeline_membership_are_untouched(self):
        with tempfile.TemporaryDirectory() as tmp:
            state = self.state(tmp)
            a = update("u1")
            b = update("u2", author="honeyya_hanihae")
            self.seed_segment(state, [a, b])
            fusion = state.data["event_fusion"]
            before = copy.deepcopy({
                key: fusion.get(key)
                for key in ("events", "memberships", "segments", "segment_memberships", "segment_relationships")
            })
            shadow_fuse_translations(state, [a, b], CONFIGURED)
            after = {key: fusion.get(key) for key in before}
            self.assertEqual(after, before)

    def test_telegram_receipts_are_untouched(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = MessageDeliveryStore(Path(tmp) / "delivery.sqlite3")
            db.save_plan("draft:test", "hello", ["hello"])
            db.confirm("draft:test", 0, "hello", 123)
            state = self.state(tmp)
            a = update("u1")
            b = update("u2", author="honeyya_hanihae")
            self.seed_segment(state, [a, b])
            shadow_fuse_translations(state, [a, b], CONFIGURED)
            self.assertEqual(db.confirmed_message_id("draft:test", 0, "hello"), 123)
            self.assertEqual(db.get_plan("draft:test"), ("hello", ["hello"]))
            db.close()

    def test_runtime_wrapper_is_shadow_only(self):
        self.assertEqual(TRANSLATION_FUSION_MODE, "shadow")
        self.assertTrue(getattr(event_fusion.shadow_group_updates, "_translation_fusion_shadow_installed", False))
        self.assertTrue(getattr(event_fusion.shadow_group_updates, "_event_timeline_shadow_installed", False))

    def test_fanfic_import_does_not_load_translation_fusion_runtime(self):
        code = """
import json
import sys
import app.fic_digest
blocked = sorted(
    name for name in sys.modules
    if name in {
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

    def test_concert_text_identity_never_becomes_media_identity(self):
        event_id = event_fusion.make_event_id(("u1", "u2"))
        segment_id = event_timeline.make_segment_id(event_id, ("u1",))
        media_a = media_asset_id("video", "https://media.example/fancam-a.mp4")
        media_b = media_asset_id("video", "https://media.example/fancam-b.mp4")
        self.assertNotEqual(segment_id, media_a)
        self.assertNotEqual(media_a, media_b)

    def test_fanfic_state_remains_independent(self):
        with tempfile.TemporaryDirectory() as tmp:
            fic = FicStateStore(Path(tmp) / "fic.sqlite3")
            observation = FicObservation("123", "2/5", "2026-08-15")
            self.assertEqual(fic.classify(observation), "new")
            state = self.state(tmp)
            a = update("u1")
            b = update("u2", author="honeyya_hanihae")
            self.seed_segment(state, [a, b])
            shadow_fuse_translations(state, [a, b], CONFIGURED)
            self.assertEqual(fic.classify(observation), "unchanged")
            fic.close()

    def test_private_review_sources_and_realtime_invariants(self):
        settings = json.loads((ROOT / "config" / "settings.json").read_text(encoding="utf-8"))
        sources = json.loads((ROOT / "config" / "sources.json").read_text(encoding="utf-8"))
        enabled = [str(item["handle"]).lstrip("@").casefold() for item in sources["sources"] if item.get("enabled", True)]
        self.assertIs(settings["runtime"]["review_only"], True)
        self.assertEqual(len(sources["sources"]), 24)
        self.assertEqual(len(enabled), 23)
        self.assertEqual(len(set(enabled)), 23)
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("REALTIME_SHADOW_MODE", None)
            self.assertFalse(realtime_shadow_enabled())

    def test_no_paid_or_new_queue_database_dependency(self):
        requirements = "\n".join(
            (ROOT / path).read_text(encoding="utf-8").casefold()
            for path in ("requirements.txt", "requirements-optional-media.txt")
        )
        for name in ("supabase", "redis", "celery", "pinecone", "weaviate", "qdrant", "openai", "anthropic"):
            self.assertNotIn(name, requirements)

    def test_benchmark_has_30_cases_and_zero_unsupported_additions(self):
        result = run_benchmark(ROOT / "data" / "translation_fusion_benchmark.json")
        self.assertTrue(result["passed"], result)
        self.assertGreaterEqual(result["case_count"], 30)
        self.assertEqual(result["unsupported_addition_count"], 0)
        self.assertEqual(result["factual_precision"], 1.0)
        self.assertEqual(result["factual_recall"], 1.0)
        self.assertEqual(result["contradiction_preservation"], 1.0)
        self.assertEqual(result["speaker_attribution_accuracy"], 1.0)
        self.assertEqual(result["name_accuracy"], 1.0)
        self.assertEqual(result["number_date_accuracy"], 1.0)
        self.assertEqual(result["negation_accuracy"], 1.0)
        self.assertEqual(result["review_required_correctness"], 1.0)


if __name__ == "__main__":
    unittest.main()
