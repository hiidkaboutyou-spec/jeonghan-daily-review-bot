from __future__ import annotations

import copy
import json
import os
import sqlite3
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from app.forward_ready_package import (
    FORWARD_READY_MODE,
    READINESS_STATES,
    TELEGRAM_ALBUM_LIMIT,
    TELEGRAM_TEXT_LIMIT,
    make_package_id,
    plan_forward_ready_packages,
)
from app.media_delivery import MediaDeliveryLedger
from app.models import Draft, MediaItem, Update
from app.realtime_ingest import realtime_shadow_enabled
from app.user_voice_calibration import AUTO_LEARN
from app.state import StateStore
from app.event_fusion_private_runtime import ensure_translation_fusion_shadow
from tools.run_forward_ready_benchmark import CASES, evaluate

ROOT = Path(__file__).resolve().parents[1]


class MemoryState:
    def __init__(self, data=None):
        self.data = data or {"drafts": {}, "event_fusion": {}, "update_lifecycle": {}}


def update(uid: str, minute: int = 0, media=(), text: str = "متن؟ 2026-08-16 https://example.com") -> Update:
    return Update(
        id=uid, url=f"https://x.com/source/status/{uid}", author="source",
        author_name="Source", text=text,
        created_at=datetime(2026, 8, 16, tzinfo=timezone.utc) + timedelta(minutes=minute),
        media=list(media),
    )


def state_for(updates, segments=None, drafts=True, translation=None, lifecycle=None, style=None):
    segments = segments or {}
    memberships = {
        uid: {"event_id": "evt:one", "segment_id": segment}
        for uid, segment in segments.items()
    }
    stored_drafts = {}
    if drafts:
        for item in updates:
            draft = Draft(f"draft:{item.id}", item.id, "event", item.text, created_at="2026-08-16")
            stored_drafts[draft.id] = draft.to_dict()
    return MemoryState({
        "drafts": stored_drafts,
        "event_fusion": {
            "segment_memberships": memberships,
            "translation_fusion_results": translation or {},
            "style_rewrite_results": style or {},
        },
        "update_lifecycle": lifecycle or {},
        "seen": {"sentinel": "unchanged"},
        "pending_delivery": [{"id": "pending"}],
        "x_retrieval_checkpoints": {"source": {"cursor": "keep"}},
    })


class FinalRecord:
    final_edit_id = "fedit:confirmed"
    final_user_edit_fingerprint = "final-fingerprint"


class FinalStore:
    def latest_active(self, draft_id):
        return FinalRecord() if draft_id == "draft:u1" else None


class ForwardReadyFoundationTests(unittest.TestCase):
    def test_01_package_identity_is_namespaced_and_independent(self):
        package_id = make_package_id("segment", "seg:one", ["u1"])
        self.assertTrue(package_id.startswith("frp:"))
        self.assertNotIn(package_id, {"u1", "seg:one", "evt:one", "tr:u1", "url:u1"})

    def test_02_identity_is_deterministic_but_membership_sensitive(self):
        self.assertEqual(make_package_id("segment", "seg:x", ["b", "a"]), make_package_id("segment", "seg:x", ["a", "b"]))
        self.assertNotEqual(make_package_id("segment", "seg:x", ["a"]), make_package_id("segment", "seg:x", ["a", "b"]))

    def test_03_single_update_is_standalone_without_invented_event(self):
        item = update("u1")
        package = plan_forward_ready_packages(state_for([item]), [item])[0]
        self.assertEqual(package.context_kind, "standalone_update")
        self.assertEqual(package.ordered_update_ids, ("u1",))

    def test_04_only_existing_same_segment_members_are_grouped(self):
        items = [update("u2", 2), update("u1", 1)]
        packages = plan_forward_ready_packages(state_for(items, {"u1": "seg:a", "u2": "seg:a"}), items)
        self.assertEqual(len(packages), 1)
        self.assertEqual(packages[0].ordered_update_ids, ("u1", "u2"))

    def test_05_same_event_separate_segments_stay_separate(self):
        items = [update("u1"), update("u2", 1)]
        packages = plan_forward_ready_packages(state_for(items, {"u1": "seg:a", "u2": "seg:b"}), items)
        self.assertEqual(len(packages), 2)

    def test_06_unrelated_nearby_updates_never_group(self):
        items = [update("u1"), update("u2", 1)]
        self.assertEqual(len(plan_forward_ready_packages(state_for(items), items)), 2)

    def test_07_recomputation_does_not_mutate_canonical_entities(self):
        items = [update("u1"), update("u2", 1)]
        state = state_for(items, {"u1": "seg:a", "u2": "seg:a"})
        before = copy.deepcopy(state.data["event_fusion"]["segment_memberships"])
        first = plan_forward_ready_packages(state, items)
        second = plan_forward_ready_packages(state, reversed(items))
        self.assertEqual(first, second)
        self.assertEqual(state.data["event_fusion"]["segment_memberships"], before)

    def test_08_reclassification_is_reversible_without_update_mutation(self):
        items = [update("u1"), update("u2", 1)]
        state = state_for(items, {"u1": "seg:a", "u2": "seg:a"})
        original = [item.to_dict() for item in items]
        self.assertEqual(len(plan_forward_ready_packages(state, items)), 1)
        state.data["event_fusion"]["segment_memberships"]["u2"]["segment_id"] = "seg:b"
        self.assertEqual(len(plan_forward_ready_packages(state, items)), 2)
        self.assertEqual([item.to_dict() for item in items], original)

    def test_09_live_gose_interview_and_fansign_separation_uses_segments(self):
        for label in ("live", "gose", "interview", "fansign"):
            with self.subTest(label=label):
                items = [update(f"{label}1"), update(f"{label}2", 1)]
                packages = plan_forward_ready_packages(state_for(items, {items[0].id: "seg:a", items[1].id: "seg:b"}), items)
                self.assertEqual(len(packages), 2)

    def test_10_distinct_concert_media_is_never_collapsed(self):
        media = [MediaItem("video", f"https://media/{index}") for index in range(5)]
        item = update("u1", media=media)
        plan = plan_forward_ready_packages(state_for([item]), [item])[0].media_plan
        self.assertEqual(len(plan.items), 5)
        self.assertEqual(plan.distinct_media_count, 5)

    def test_11_distinct_photos_and_cameras_across_updates_are_preserved(self):
        items = [
            update("u1", media=[MediaItem("photo", "https://media/a")]),
            update("u2", 1, media=[MediaItem("video", "https://media/b")]),
        ]
        package = plan_forward_ready_packages(state_for(items, {"u1": "seg:a", "u2": "seg:a"}), items)[0]
        self.assertEqual(package.media_plan.distinct_media_count, 2)
        self.assertEqual([item.update_id for item in package.media_plan.items], ["u1", "u2"])

    def test_12_exact_media_duplicate_is_compatible_with_existing_ledger_identity(self):
        media = MediaItem("photo", "https://media/same")
        item = update("u1", media=[media, media])
        plan = plan_forward_ready_packages(state_for([item]), [item])[0].media_plan
        self.assertEqual(plan.items[0].media_id, MediaDeliveryLedger.url_identity(media))
        self.assertEqual(plan.exact_duplicate_count, 1)
        self.assertEqual(plan.items[1].delivery_disposition, "existing_exact_dedupe")

    def test_13_media_only_concert_coverage_remains_eligible(self):
        item = update("u1", media=[MediaItem("video", "https://media/fancam")])
        package = plan_forward_ready_packages(state_for([item], drafts=False), [item])[0]
        self.assertEqual(package.readiness_status, "MEDIA_ONLY")
        self.assertEqual(package.media_plan.items[0].delivery_disposition, "eligible")

    def test_14_media_first_and_deterministic_source_order(self):
        item = update("u1", media=[MediaItem("photo", "https://media/2"), MediaItem("photo", "https://media/1")])
        package = plan_forward_ready_packages(state_for([item]), [item])[0]
        self.assertEqual(package.presentation_plan.order[:2], ("media", "text"))
        self.assertEqual([row.source_media_order for row in package.media_plan.items], [0, 1])

    def test_15_telegram_album_limit_is_planned_without_dropping_media(self):
        item = update("u1", media=[MediaItem("photo", f"https://media/{index}") for index in range(23)])
        plan = plan_forward_ready_packages(state_for([item]), [item])[0].media_plan
        self.assertEqual([len(batch) for batch in plan.telegram_batches], [10, 10, 3])
        self.assertEqual(TELEGRAM_ALBUM_LIMIT, 10)

    def test_16_long_text_records_split_requirement_without_changing_draft(self):
        text = "ا" * (TELEGRAM_TEXT_LIMIT + 10)
        item = update("u1", text=text)
        state = state_for([item])
        package = plan_forward_ready_packages(state, [item])[0]
        self.assertEqual(package.text_plan.telegram_part_count, 2)
        self.assertIn("TELEGRAM_TEXT_SPLIT_REQUIRED", package.warnings)
        self.assertEqual(state.data["drafts"]["draft:u1"]["caption"], text)

    def test_17_internal_metadata_is_not_forwardable_content(self):
        item = update("u1")
        package = plan_forward_ready_packages(state_for([item]), [item])[0]
        self.assertIn("source_update_refs", package.internal_review_metadata)
        self.assertFalse(package.forwardable_content["technical_metadata_included"])
        self.assertNotIn("source_update_refs", package.forwardable_content)

    def test_18_partial_retrieval_warns_but_keeps_discovered_update(self):
        item = update("u1")
        lifecycle = {"u1": {"retrieval_status": "partial_source_window"}}
        package = plan_forward_ready_packages(state_for([item], lifecycle=lifecycle), [item])[0]
        self.assertEqual(package.readiness_status, "READY_WITH_WARNINGS")
        self.assertIn("PARTIAL_COVERAGE", package.warnings)

    def test_19_translation_conflict_cannot_be_hidden_by_style(self):
        item = update("u1")
        translation = {"seg:a": {"fidelity_status": "faithful_shadow_candidate", "conflict_update_ids": ["u2"]}}
        style = {"seg:a": {"accepted": True, "fidelity_passed": True, "style_candidate_fingerprint": "styled"}}
        package = plan_forward_ready_packages(state_for([item], {"u1": "seg:a"}, translation=translation, style=style), [item])[0]
        self.assertEqual(package.readiness_status, "BLOCKED_CONFLICT")

    def test_20_fidelity_warning_blocks_polished_certainty(self):
        item = update("u1")
        translation = {"seg:a": {"fidelity_status": "needs_review"}}
        package = plan_forward_ready_packages(state_for([item], {"u1": "seg:a"}, translation=translation), [item])[0]
        self.assertEqual(package.readiness_status, "BLOCKED_FIDELITY")

    def test_21_media_failure_is_not_labeled_ready(self):
        item = update("u1", media=[MediaItem("photo", "https://media/a")])
        package = plan_forward_ready_packages(state_for([item], lifecycle={"u1": {"media_status": "partial_failed"}}), [item])[0]
        self.assertEqual(package.readiness_status, "READY_TEXT_MEDIA_INCOMPLETE")

    def test_22_confirmed_final_edit_is_reference_only_and_future_preferred(self):
        item = update("u1")
        package = plan_forward_ready_packages(state_for([item]), [item], final_edit_store=FinalStore())[0]
        self.assertEqual(package.text_plan.future_preferred_candidate, "confirmed_final_edit")
        self.assertEqual(package.text_plan.confirmed_final_edit_id, "fedit:confirmed")
        self.assertFalse(hasattr(package.text_plan, "final_body"))

    def test_23_no_fake_final_edit_evidence(self):
        item = update("u1")
        package = plan_forward_ready_packages(state_for([item]), [item])[0]
        self.assertEqual(package.text_plan.confirmed_final_edit_id, "")

    def test_24_direct_style_output_is_consumed_not_reimplemented(self):
        item = update("u1")
        style = {"seg:a": {
            "accepted": True, "fidelity_passed": True,
            "style_candidate_fingerprint": "style-fp", "factual_draft_fingerprint": "fact-fp",
            "direct_style_applied": True, "direct_style_rule_id": "instagram-update",
        }}
        package = plan_forward_ready_packages(state_for([item], {"u1": "seg:a"}, style=style), [item])[0]
        self.assertEqual(package.text_plan.future_preferred_candidate, "direct_style_candidate")
        self.assertEqual(package.text_plan.direct_style_rule_id, "instagram-update")

    def test_25_current_authoritative_draft_never_changes_to_shadow_candidate(self):
        item = update("u1")
        style = {"seg:a": {"accepted": True, "fidelity_passed": True, "style_candidate_fingerprint": "style"}}
        package = plan_forward_ready_packages(state_for([item], {"u1": "seg:a"}, style=style), [item])[0]
        self.assertEqual(package.text_plan.current_authority, "authoritative_review_draft")
        self.assertFalse(package.text_plan.authority_activated)

    def test_26_rtl_url_speaker_question_number_and_date_survive_unchanged(self):
        text = "،،⌕໋  ִ˒˒ جونگهان: امروز 2026-08-16 میای؟ https://example.com/1"
        item = update("u1", text=text)
        state = state_for([item])
        plan_forward_ready_packages(state, [item])
        self.assertEqual(state.data["drafts"]["draft:u1"]["caption"], text)

    def test_27_lifecycle_seen_cursor_and_completeness_authorities_are_unchanged(self):
        item = update("u1")
        state = state_for([item], lifecycle={"u1": {"status": "pending_delivery", "retrieval_status": "complete"}})
        before = {key: copy.deepcopy(state.data[key]) for key in ("seen", "pending_delivery", "x_retrieval_checkpoints", "update_lifecycle")}
        plan_forward_ready_packages(state, [item])
        self.assertEqual({key: state.data[key] for key in before}, before)

    def test_28_receipt_authority_is_unchanged(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "private-review.sqlite3"
            ledger = MediaDeliveryLedger(db)
            before = ledger.conn.execute("SELECT count(*) FROM telegram_media_delivery").fetchone()[0]
            item = update("u1", media=[MediaItem("photo", "https://media/a")])
            plan_forward_ready_packages(state_for([item]), [item])
            after = ledger.conn.execute("SELECT count(*) FROM telegram_media_delivery").fetchone()[0]
            self.assertEqual((before, after), (0, 0))
            ledger.close()

    def test_29_shadow_plan_has_no_forward_or_publish_action(self):
        item = update("u1")
        package = plan_forward_ready_packages(state_for([item]), [item])[0]
        self.assertEqual(package.mode, FORWARD_READY_MODE)
        self.assertFalse(package.presentation_plan.auto_forward)
        self.assertFalse(package.presentation_plan.public_publish)

    def test_30_existing_review_actions_are_reused(self):
        item = update("u1")
        plan = plan_forward_ready_packages(state_for([item]), [item])[0].presentation_plan
        self.assertTrue(plan.existing_review_actions_only)
        self.assertIn("existing_review_actions", plan.order)

    def test_31_persisted_metadata_has_no_bodies_urls_or_media_bytes(self):
        secret_text = "PRIVATE-BODY-SENTINEL"
        item = update("u1", media=[MediaItem("photo", "https://media/private-token")], text=secret_text)
        state = state_for([item])
        plan_forward_ready_packages(state, [item])
        encoded = json.dumps(state.data["event_fusion"]["forward_ready_packages"], ensure_ascii=False)
        self.assertNotIn(secret_text, encoded)
        self.assertNotIn("https://media/private-token", encoded)
        self.assertIn('"bytes_persisted": false', encoded)

    def test_32_readiness_values_are_explicit_and_known(self):
        self.assertEqual({"READY", "READY_WITH_WARNINGS", "NEEDS_REVIEW", "BLOCKED_FIDELITY", "BLOCKED_CONFLICT", "READY_TEXT_MEDIA_INCOMPLETE", "MEDIA_ONLY"}, set(READINESS_STATES))

    def test_33_benchmark_has_at_least_40_difficult_cases_and_all_pass(self):
        self.assertGreaterEqual(len(CASES), 40)
        results = [evaluate(case) for case in CASES]
        self.assertTrue(all(item["passed"] for item in results), [item["name"] for item in results if not item["passed"]])

    def test_34_twenty_four_configured_sources_remain_intact(self):
        data = json.loads((ROOT / "config" / "sources.json").read_text(encoding="utf-8"))
        self.assertEqual(len(data["sources"]), 24)

    def test_35_review_only_auto_learn_and_realtime_defaults_remain_safe(self):
        settings = json.loads((ROOT / "config" / "settings.json").read_text(encoding="utf-8"))
        self.assertTrue(settings["runtime"]["review_only"])
        self.assertFalse(AUTO_LEARN)
        previous = os.environ.pop("REALTIME_SHADOW_MODE", None)
        try:
            self.assertFalse(realtime_shadow_enabled())
        finally:
            if previous is not None:
                os.environ["REALTIME_SHADOW_MODE"] = previous

    def test_36_fanfic_ao3_has_no_forward_ready_runtime_dependency(self):
        source = (ROOT / "app" / "fic_digest.py").read_text(encoding="utf-8")
        self.assertNotIn("forward_ready_package", source)
        self.assertNotIn("plan_forward_ready_packages", source)

    def test_37_no_paid_or_external_infrastructure_added(self):
        source = (ROOT / "app" / "forward_ready_package.py").read_text(encoding="utf-8").casefold()
        for forbidden in ("supabase", "redis", "celery", "stripe", "aws", "openai"):
            self.assertNotIn(forbidden, source)

    def test_38_runtime_integrates_after_unchanged_delivery_and_fails_independently(self):
        source = (ROOT / "app" / "event_fusion_private_runtime.py").read_text(encoding="utf-8")
        self.assertLess(source.index("result = await current"), source.index("plan_forward_ready_packages(", source.index("result = await current")))
        self.assertIn("except Exception as exc", source)

    def test_39_no_new_button_or_second_review_system(self):
        source = (ROOT / "app" / "forward_ready_package.py").read_text(encoding="utf-8")
        self.assertNotIn("callback_data", source)
        self.assertNotIn("send_message(", source)

    def test_40_package_metadata_is_bounded_and_replaced_on_recompute(self):
        item = update("u1")
        state = state_for([item])
        plan_forward_ready_packages(state, [item])
        first = copy.deepcopy(state.data["event_fusion"]["forward_ready_packages"])
        plan_forward_ready_packages(state, [item])
        self.assertEqual(state.data["event_fusion"]["forward_ready_packages"], first)
        self.assertEqual(len(first), 1)

    def test_41_reference_only_metadata_survives_durable_state_restart(self):
        ensure_translation_fusion_shadow()
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "state.json"
            state = StateStore(path)
            fusion = state.data["event_fusion"]
            fusion["events"] = {"evt:one": {
                "event_id": "evt:one", "event_type": "live", "created_at": "2026-08-16",
                "updated_at": "2026-08-16", "member_update_ids": ["u1"], "confidence": 1.0,
                "status": "shadow_candidate", "subject_key": "subject:test",
            }}
            fusion["memberships"] = {"u1": {
                "event_id": "evt:one", "confidence": 1.0, "matching_signals": [],
                "conflicts": [], "decision": "confident_same_event", "updated_at": "2026-08-16",
            }}
            fusion["segments"] = {"seg:one": {
                "segment_id": "seg:one", "event_id": "evt:one", "created_at": "2026-08-16",
                "updated_at": "2026-08-16", "member_update_ids": ["u1"], "confidence": 1.0,
                "status": "shadow_candidate", "order_index": 0,
                "order_evidence": {"kind": "chronology", "value": 0, "confidence": "high"},
            }}
            fusion["segment_memberships"] = {"u1": {
                "event_id": "evt:one", "segment_id": "seg:one", "confidence": 1.0,
                "relationship": "same_moment", "matching_signals": [], "conflicts": [],
                "updated_at": "2026-08-16",
            }}
            item = update("u1")
            state.save_draft(Draft("draft:u1", "u1", "event", item.text, created_at="2026-08-16"))
            plan_forward_ready_packages(state, [item])
            package_id = next(iter(fusion["forward_ready_packages"]))
            state.save()
            restored = StateStore(path)
            metadata = restored.data["event_fusion"]["forward_ready_packages"][package_id]
            self.assertEqual(metadata["ordered_update_ids"], ["u1"])
            self.assertFalse(metadata["media_plan"]["bytes_persisted"])
            self.assertNotIn(item.text, json.dumps(metadata, ensure_ascii=False))

    def test_42_incremental_recompute_preserves_unrelated_packages_only(self):
        first, second = update("u1"), update("u2", 1)
        state = state_for([first, second])
        plans = plan_forward_ready_packages(state, [first, second])
        ids = {plan.ordered_update_ids[0]: plan.package_id for plan in plans}
        state.data["drafts"]["draft:u1"]["caption"] = "updated caption"
        plan_forward_ready_packages(state, [first])
        stored = state.data["event_fusion"]["forward_ready_packages"]
        self.assertIn(ids["u2"], stored)
        self.assertIn(ids["u1"], stored)
        self.assertEqual(len(stored), 2)


if __name__ == "__main__":
    unittest.main()
