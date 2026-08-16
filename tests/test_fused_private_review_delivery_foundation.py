from __future__ import annotations

import copy
import json
import os
import tempfile
import unittest
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path

from app.forward_ready_package import plan_forward_ready_packages
from app.fused_private_review_delivery import (
    EXISTING_REVIEW_CONTROLS,
    FUSED_DELIVERY_MODE,
    FUSED_DELIVERY_PLAN_VERSION,
    PLAN_READINESS_STATES,
    TELEGRAM_ALBUM_LIMIT,
    TELEGRAM_CAPTION_LIMIT,
    make_fused_plan_id,
    plan_fused_private_review_delivery,
    privacy_safe_observation,
)
from app.media_delivery import MediaDeliveryLedger
from app.message_delivery import MessageDeliveryStore
from app.models import Draft, MediaItem, Update
from app.realtime_ingest import realtime_shadow_enabled
from app.user_voice_calibration import AUTO_LEARN
from tools.run_fused_private_review_delivery_benchmark import CASES, evaluate

ROOT = Path(__file__).resolve().parents[1]


class MemoryState:
    def __init__(self, data):
        self.data = data


def update(uid: str, minute: int = 0, *, media=(), text="💒 ⌕ جونگهان: 2026-08-16 میای؟ https://example.com"):
    return Update(
        id=uid,
        url=f"https://x.com/source/status/{uid}",
        author="source",
        author_name="Source",
        text=text,
        created_at=datetime(2026, 8, 16, tzinfo=timezone.utc) + timedelta(minutes=minute),
        media=list(media),
    )


def state_for(updates, *, segments=None, drafts=True, translation=None, lifecycle=None, style=None):
    segments = segments or {}
    stored = {}
    if drafts:
        for item in updates:
            draft = Draft(f"draft:{item.id}", item.id, "event", item.text, created_at="2026-08-16")
            stored[draft.id] = draft.to_dict()
    return MemoryState({
        "drafts": stored,
        "event_fusion": {
            "segment_memberships": {
                uid: {"event_id": "evt:one", "segment_id": segment}
                for uid, segment in segments.items()
            },
            "translation_fusion_results": translation or {},
            "style_rewrite_results": style or {},
        },
        "update_lifecycle": lifecycle or {},
        "seen": {"sentinel": "keep"},
        "pending_delivery": [{"id": "keep"}],
        "x_retrieval_checkpoints": {"source": {"cursor": "keep"}},
    })


class FinalRecord:
    final_edit_id = "fedit:confirmed"
    final_user_edit_fingerprint = "final-fingerprint"


class FinalStore:
    def latest_active(self, draft_id):
        return FinalRecord()


def plans_for(items, **kwargs):
    state = state_for(
        items,
        segments=kwargs.get("segments"),
        drafts=kwargs.get("drafts", True),
        translation=kwargs.get("translation"),
        lifecycle=kwargs.get("lifecycle"),
        style=kwargs.get("style"),
    )
    packages = plan_forward_ready_packages(
        state,
        items,
        final_edit_store=kwargs.get("final_store"),
        persist=False,
    )
    return state, packages, plan_fused_private_review_delivery(state, packages)


class FusedPrivateReviewDeliveryFoundationTests(unittest.TestCase):
    def test_001_fused_identity_is_independent_and_deterministic(self):
        item = update("u1")
        state, packages, plans = plans_for([item])
        renamed = replace(packages[0], package_id="frp:other")
        self.assertTrue(plans[0].plan_id.startswith("fdp:"))
        self.assertEqual(make_fused_plan_id(packages[0]), make_fused_plan_id(renamed))
        self.assertEqual(plans, plan_fused_private_review_delivery(state, packages))

    def test_002_package_update_event_segment_references_are_preserved(self):
        items = [update("u2", 2), update("u1", 1)]
        _state, packages, plans = plans_for(
            items, segments={"u1": "seg:a", "u2": "seg:a"}
        )
        self.assertEqual(plans[0].package_id, packages[0].package_id)
        self.assertEqual(plans[0].ordered_update_ids, ("u1", "u2"))
        self.assertEqual((plans[0].event_id, plans[0].segment_id), ("evt:one", "seg:a"))

    def test_003_planning_never_mutates_lifecycle_cursor_completeness_or_seen(self):
        item = update("u1")
        state = state_for(
            [item],
            lifecycle={"u1": {"retrieval_status": "complete", "media_status": "complete"}},
        )
        packages = plan_forward_ready_packages(state, [item], persist=False)
        before = copy.deepcopy(state.data)
        plan_fused_private_review_delivery(state, packages)
        self.assertEqual(state.data, before)
        self.assertNotIn("fused_private_review_delivery_plans", state.data["event_fusion"])

    def test_004_malformed_old_plan_state_is_ignored(self):
        item = update("u1")
        state = state_for([item])
        state.data["event_fusion"]["fused_private_review_delivery_plans"] = {
            "fdp:garbage": {"version": 999, "body": "ignore"}
        }
        packages = plan_forward_ready_packages(state, [item], persist=False)
        plans = plan_fused_private_review_delivery(state, packages)
        self.assertEqual(len(plans), 1)
        self.assertNotEqual(plans[0].plan_id, "fdp:garbage")

    def test_005_receipt_authorities_are_untouched(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "private-review.sqlite3"
            messages = MessageDeliveryStore(db)
            media_ledger = MediaDeliveryLedger(db)
            item = update("u1", media=[MediaItem("photo", "https://media/a")])
            _state, _packages, plans = plans_for([item])
            self.assertEqual(messages.conn.execute("select count(*) from message_delivery_parts").fetchone()[0], 0)
            self.assertEqual(media_ledger.conn.execute("select count(*) from telegram_media_delivery").fetchone()[0], 0)
            self.assertEqual(plans[0].delivery_status, "PLANNED")
            self.assertFalse(plans[0].delivered)
            self.assertEqual(plans[0].receipt_authority, "MessageDeliveryStore+MediaDeliveryLedger")
            messages.close()
            media_ledger.close()

    def test_006_media_identity_coverage_exact_dedupe_and_album_limit(self):
        repeated = MediaItem("photo", "https://media/same")
        media = [repeated, repeated] + [MediaItem("video", f"https://media/{i}") for i in range(21)]
        item = update("u1", media=media)
        _state, _packages, plans = plans_for([item])
        plan = plans[0]
        planned = [
            ref for unit in plan.units
            if unit.kind in {"media_album", "single_photo", "single_video", "standalone_media"}
            for ref in unit.media_refs
        ]
        self.assertEqual(len(set(planned)), 22)
        self.assertTrue(plan.suppressed_exact_duplicate_media_refs)
        self.assertTrue(all(len(unit.media_refs) <= TELEGRAM_ALBUM_LIMIT for unit in plan.units if unit.kind == "media_album"))
        self.assertEqual(TELEGRAM_ALBUM_LIMIT, 10)

    def test_007_media_first_caption_overflow_and_long_text_reuse_existing_split(self):
        text = ("جونگهان: متن بلند؟ https://example.com\n\n" * 180).strip()
        item = update("u1", media=[MediaItem("photo", "https://media/a")], text=text)
        state, packages, plans = plans_for([item])
        plan = plans[0]
        kinds = [unit.kind for unit in plan.units]
        self.assertLess(kinds.index("single_photo"), kinds.index("text"))
        self.assertIn("CAPTION_OVERFLOW_TO_TEXT", plan.warnings)
        self.assertEqual(
            len([unit for unit in plan.units if unit.kind in {"text", "continuation_text"}]),
            packages[0].text_plan.telegram_part_count,
        )
        self.assertEqual(state.data["drafts"]["draft:u1"]["caption"], text)
        self.assertEqual(TELEGRAM_CAPTION_LIMIT, 1024)

    def test_008_separate_segments_never_collapse_for_live_gose_interview_fansign(self):
        for label in ("live", "gose", "interview", "fansign"):
            with self.subTest(label=label):
                items = [update(f"{label}1"), update(f"{label}2", 1)]
                _state, _packages, plans = plans_for(
                    items,
                    segments={items[0].id: "seg:a", items[1].id: "seg:b"},
                )
                self.assertEqual(len(plans), 2)

    def test_009_conflict_partial_and_media_failure_readiness(self):
        item = update("u1", media=[MediaItem("photo", "https://media/a")])
        _s, _p, conflict = plans_for(
            [item],
            segments={"u1": "seg:a"},
            translation={"seg:a": {"fidelity_status": "faithful_shadow_candidate", "conflict_update_ids": ["u1"]}},
        )
        self.assertEqual(conflict[0].readiness, "BLOCKED")
        _s, _p, partial = plans_for(
            [item], lifecycle={"u1": {"retrieval_status": "partial_source_window"}}
        )
        self.assertEqual(partial[0].readiness, "PARTIAL_COVERAGE")
        _s, _p, media_failed = plans_for(
            [item], lifecycle={"u1": {"media_status": "partial_failed"}}
        )
        self.assertEqual(media_failed[0].readiness, "MEDIA_INCOMPLETE")

    def test_010_final_edit_precedence_and_no_fake_user_voice(self):
        item = update("u1")
        _s, _p, with_final = plans_for([item], final_store=FinalStore())
        self.assertEqual(with_final[0].text_authority.preferred_candidate, "confirmed_final_edit")
        self.assertTrue(with_final[0].text_authority.final_edit_confirmed)
        self.assertFalse(with_final[0].text_authority.user_voice_certified)
        _s, _p, without_final = plans_for([item])
        self.assertFalse(without_final[0].text_authority.final_edit_confirmed)
        self.assertFalse(without_final[0].text_authority.authority_activated)

    def test_011_direct_style_is_consumed_without_reimplementation(self):
        item = update("u1")
        style = {"seg:a": {
            "accepted": True,
            "fidelity_passed": True,
            "factual_draft_fingerprint": "fact",
            "style_candidate_fingerprint": "style",
            "direct_style_applied": True,
            "direct_style_rule_id": "instagram-update",
        }}
        _s, _p, plans = plans_for([item], segments={"u1": "seg:a"}, style=style)
        self.assertEqual(plans[0].text_authority.preferred_candidate, "direct_style_candidate")
        self.assertEqual(plans[0].text_authority.preferred_reference, "style")

    def test_012_existing_review_controls_are_reused_and_no_public_forward_exists(self):
        item = update("u1")
        _s, _p, plans = plans_for([item])
        controls = plans[0].units[-1]
        self.assertEqual(controls.kind, "review_controls")
        self.assertEqual(controls.review_control_names, EXISTING_REVIEW_CONTROLS)
        self.assertTrue(controls.reuse_existing_controls)
        forward = plans[0].future_forward_action
        self.assertFalse(forward.enabled)
        self.assertFalse(forward.auto_forward)
        self.assertFalse(forward.public_default)
        self.assertFalse(forward.target_chat_configured)

    def test_013_internal_forwardable_and_observability_privacy_boundaries(self):
        item = update("u1", text="PRIVATE BODY https://secret.example/token")
        _s, _p, plans = plans_for([item])
        plan = plans[0]
        forwardable = json.dumps(plan.forwardable_content, ensure_ascii=False)
        observation = json.dumps(privacy_safe_observation(plan), ensure_ascii=False)
        metadata = json.dumps(plan.metadata(), ensure_ascii=False)
        for encoded in (forwardable, observation, metadata):
            self.assertNotIn("PRIVATE BODY", encoded)
            self.assertNotIn("secret.example", encoded)
        self.assertFalse(plan.forwardable_content["technical_metadata_included"])
        self.assertFalse(plan.forwardable_content["debug_identifiers_included"])

    def test_014_shadow_runtime_runs_after_authoritative_delivery_and_fails_open(self):
        source = (ROOT / "app" / "event_fusion_private_runtime.py").read_text(encoding="utf-8")
        current_index = source.index("result = await current")
        fused_index = source.index("plan_fused_private_review_delivery", current_index)
        self.assertLess(current_index, fused_index)
        self.assertIn("shadow_fused_private_review_delivery", source)
        self.assertIn("except Exception as exc", source)

    def test_015_planner_has_no_send_callback_receipt_write_or_paid_dependency(self):
        source = (ROOT / "app" / "fused_private_review_delivery.py").read_text(encoding="utf-8").casefold()
        for forbidden in (
            "send_message(", "send_media(", "callback_data", "mark_delivered(",
            "target_chat_id", "gemini", "openai", "anthropic", "supabase", "redis",
            "stripe", "aws",
        ):
            self.assertNotIn(forbidden, source)

    def test_016_fanfic_ao3_remains_independent(self):
        source = (ROOT / "app" / "fic_digest.py").read_text(encoding="utf-8")
        self.assertNotIn("fused_private_review_delivery", source)
        self.assertNotIn("plan_fused_private_review_delivery", source)

    def test_017_safety_defaults_and_source_count_remain_intact(self):
        sources = json.loads((ROOT / "config" / "sources.json").read_text(encoding="utf-8"))
        settings = json.loads((ROOT / "config" / "settings.json").read_text(encoding="utf-8"))
        self.assertEqual(len(sources["sources"]), 24)
        self.assertTrue(settings["runtime"]["review_only"])
        self.assertFalse(AUTO_LEARN)
        previous = os.environ.pop("REALTIME_SHADOW_MODE", None)
        try:
            self.assertFalse(realtime_shadow_enabled())
        finally:
            if previous is not None:
                os.environ["REALTIME_SHADOW_MODE"] = previous

    def test_018_version_mode_and_readiness_are_explicit(self):
        item = update("u1")
        _s, _p, plans = plans_for([item])
        self.assertEqual(FUSED_DELIVERY_PLAN_VERSION, 1)
        self.assertEqual(plans[0].version, 1)
        self.assertEqual(plans[0].mode, FUSED_DELIVERY_MODE)
        self.assertIn(plans[0].readiness, PLAN_READINESS_STATES)

    def test_019_restart_recomputation_requires_no_saved_fused_state(self):
        media = [MediaItem("video", "https://media/a")]
        _s1, _p1, first = plans_for([update("u1", media=media)])
        _s2, _p2, second = plans_for([update("u1", media=media)])
        self.assertEqual(first[0].plan_id, second[0].plan_id)

    def test_020_fused_benchmark_has_50_cases_and_all_hard_gates_pass(self):
        self.assertEqual(len(CASES), 50)
        results = [evaluate(case) for case in CASES]
        failed = [item["name"] for item in results if not item["passed"]]
        self.assertFalse(failed, failed)


# Each difficult benchmark scenario is also an independently named regression test.
# This gives 50 scenario regressions in addition to the structural tests above.
def _install_case_regressions():
    for index, case in enumerate(CASES, start=1):
        safe = "".join(ch if ch.isalnum() else "_" for ch in case.name.casefold()).strip("_")
        name = f"test_case_{index:03d}_{safe}"

        def test(self, case=case):
            result = evaluate(case)
            self.assertTrue(
                result["passed"],
                {
                    "case": case.name,
                    "failed": [key for key, value in result["assertions"].items() if not value],
                },
            )

        setattr(FusedPrivateReviewDeliveryFoundationTests, name, test)


_install_case_regressions()


if __name__ == "__main__":
    unittest.main()
