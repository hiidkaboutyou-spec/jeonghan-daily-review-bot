from __future__ import annotations

import inspect
import os
import unittest
from dataclasses import replace
from types import SimpleNamespace

from app.fused_private_review_authority import (
    AUTHORITY_ENV,
    CANARY,
    CANARY_PERCENT_ENV,
    KILL_SWITCH_ENV,
    LEGACY,
    ON,
    SHADOW,
    FusedAuthorityConfig,
    FusedBodyResolver,
    FusedPrivateReviewAuthorityController,
    ReceiptSnapshot,
    authoritative_draft_fingerprint,
    deterministic_canary_selected,
    parse_authority_mode,
    parse_canary_percent,
    parse_kill_switch,
    validate_plan_for_authority,
)
from app.fused_private_review_delivery import (
    EXISTING_REVIEW_CONTROLS,
    FusedDeliveryUnit,
    FusedPrivateReviewDeliveryPlan,
    FusedTextAuthorityPlan,
    FutureForwardActionContract,
)
from app.final_edit_capture import FINAL_EDIT_PROVENANCE, fingerprint
from app.models import Draft
from app.telegram import split_telegram_text
from tools.run_fused_private_review_authority_benchmark import run_cases


def state_with(body: str = "متن معتبر"):
    draft = Draft("d1", "u1", "event", body)
    return SimpleNamespace(data={"drafts": {"d1": draft.to_dict()}})


def authority(*, final=False, preferred="current_authoritative_draft_fallback", reference="d1"):
    return FusedTextAuthorityPlan(
        preferred_candidate=preferred,
        preferred_reference=reference,
        preferred_reason="test",
        fallback_candidate="current_authoritative_review_draft",
        fallback_references=("d1",),
        user_voice_certified=False,
        final_edit_confirmed=final,
        authority_activated=False,
    )


def unit(kind="text", order=0, media=(), warnings=()):
    return FusedDeliveryUnit(
        unit_id=f"fdu:{order:024d}",
        kind=kind,
        order_index=order,
        update_refs=("u1",),
        event_ref="event1",
        segment_ref="segment1",
        media_refs=tuple(media),
        text_candidate="current_authoritative_draft_fallback" if kind in {"text", "caption", "continuation_text"} else "",
        text_reference="d1" if kind in {"text", "caption", "continuation_text"} else "",
        fallback_text_refs=("d1",) if kind in {"text", "caption", "continuation_text"} else (),
        telegram_part_index=0,
        telegram_part_count=1 if kind in {"text", "caption", "continuation_text"} else 0,
        review_control_names=EXISTING_REVIEW_CONTROLS if kind == "review_controls" else (),
        reuse_existing_controls=kind == "review_controls",
        warnings=tuple(warnings),
    )


def plan(*, readiness="READY_TO_PRESENT", warnings=(), units=None, text_authority=None, forward=None, version=1):
    return FusedPrivateReviewDeliveryPlan(
        plan_id="fdp:111111111111111111111111",
        package_id="frp:package",
        event_id="event1",
        segment_id="segment1",
        ordered_update_ids=("u1",),
        units=tuple(units if units is not None else (unit(), unit("review_controls", 1))),
        text_authority=text_authority or authority(),
        readiness=readiness,
        warnings=tuple(warnings),
        future_forward_action=forward or FutureForwardActionContract(),
        delivery_status="PLANNED",
        delivered=False,
        version=version,
    )


def decide(mode=ON, p=None, *, receipts=ReceiptSnapshot(), kill=False, state=None, pct=0):
    resolver = FusedBodyResolver(state or state_with())
    return FusedPrivateReviewAuthorityController(
        FusedAuthorityConfig(mode=mode, canary_percent=pct, kill_switch=kill)
    ).decide(p or plan(), receipt_snapshot=receipts, resolver=resolver)


class FakeFinalRecord:
    def __init__(self, draft_body, final_body="نسخه نهایی"):
        self.final_edit_id = "fed:real"
        self.update_id = "u1"
        self.edit_provenance = FINAL_EDIT_PROVENANCE
        self.active = True
        self.revoked = False
        self.authoritative_review_draft_fingerprint = authoritative_draft_fingerprint(draft_body)
        self.final_user_edit_fingerprint = fingerprint("final-user-edit-v1", final_body)


class FakeFinalStore:
    def __init__(self, record, body="نسخه نهایی"):
        self.record = record
        self.body = body

    def latest_active(self, draft_id):
        return self.record

    def final_body(self, final_edit_id):
        return self.body


class FusedAuthorityFoundationTests(unittest.TestCase):
    def test_default_legacy_authority(self):
        self.assertEqual(FusedAuthorityConfig().mode, LEGACY)

    def test_missing_flag_false(self):
        self.assertEqual(FusedAuthorityConfig.from_environment({}).mode, LEGACY)

    def test_malformed_flag_false(self):
        self.assertEqual(parse_authority_mode("maybe"), LEGACY)

    def test_false_flag(self):
        self.assertEqual(parse_authority_mode("false"), LEGACY)

    def test_shadow_mode_never_selects_fused(self):
        self.assertEqual(decide(SHADOW).selected_path, LEGACY)

    def test_canary_is_deterministic(self):
        a = deterministic_canary_selected("fdp:a", "frp:b", 13)
        self.assertEqual(a, deterministic_canary_selected("fdp:a", "frp:b", 13))

    def test_canary_zero_off(self):
        self.assertFalse(deterministic_canary_selected("a", "b", 0))

    def test_canary_hundred_selected(self):
        self.assertTrue(deterministic_canary_selected("a", "b", 100))

    def test_bad_canary_percent_zero(self):
        self.assertEqual(parse_canary_percent("bad"), 0)
        self.assertEqual(parse_canary_percent(101), 0)

    def test_kill_switch_strict(self):
        self.assertTrue(parse_kill_switch("true"))
        self.assertFalse(parse_kill_switch("malformed"))

    def test_kill_switch_pre_send_legacy(self):
        self.assertEqual(decide(ON, kill=True).selected_path, LEGACY)

    def test_kill_switch_post_receipt_never_legacy(self):
        result = decide(ON, kill=True, receipts=ReceiptSnapshot(True, False))
        self.assertEqual(result.selected_path, "FUSED_RESUME_REQUIRED")

    def test_on_eligible_selects_fused(self):
        self.assertEqual(decide(ON).selected_path, "FUSED")

    def test_conflict_blocks(self):
        p = plan(readiness="BLOCKED", warnings=("CONFLICT_UNRESOLVED",))
        self.assertEqual(decide(ON, p).selected_path, LEGACY)

    def test_fidelity_blocks(self):
        p = plan(readiness="BLOCKED", warnings=("TRANSLATION_FIDELITY_NOT_READY",))
        self.assertEqual(decide(ON, p).selected_path, LEGACY)

    def test_partial_coverage_blocks(self):
        p = plan(readiness="PARTIAL_COVERAGE", warnings=("PARTIAL_COVERAGE",))
        self.assertEqual(decide(ON, p).selected_path, LEGACY)

    def test_media_incomplete_blocks(self):
        p = plan(readiness="MEDIA_INCOMPLETE", warnings=("MEDIA_INCOMPLETE",))
        self.assertEqual(decide(ON, p).selected_path, LEGACY)

    def test_missing_body_blocks(self):
        self.assertEqual(decide(ON, state=SimpleNamespace(data={"drafts": {}})).selected_path, LEGACY)

    def test_current_draft_body_resolves_exactly(self):
        resolver = FusedBodyResolver(state_with("🪽 متن؟ 2026 https://example.com"))
        resolved = resolver.resolve(plan(), unit())
        self.assertEqual(resolved.body, "🪽 متن؟ 2026 https://example.com")
        self.assertEqual(resolved.source, "authoritative_review_draft")

    def test_style_candidate_remains_non_authoritative(self):
        p = plan(text_authority=authority(preferred="direct_style_candidate", reference="style:fp"))
        resolved = FusedBodyResolver(state_with()).resolve(p, unit())
        self.assertEqual(resolved.source, "authoritative_review_draft")

    def test_valid_final_edit_resolves(self):
        body = "Draft canonical"
        record = FakeFinalRecord(body)
        store = FakeFinalStore(record)
        p = plan(text_authority=authority(final=True, preferred="confirmed_final_edit", reference="fed:real"))
        resolved = FusedBodyResolver(state_with(body), store).resolve(p, unit())
        self.assertEqual(resolved.source, "confirmed_final_edit")
        self.assertEqual(resolved.body, "نسخه نهایی")

    def test_stale_final_edit_rejected_to_draft(self):
        record = FakeFinalRecord("old body")
        p = plan(text_authority=authority(final=True, preferred="confirmed_final_edit", reference="fed:real"))
        resolved = FusedBodyResolver(state_with("new body"), FakeFinalStore(record)).resolve(p, unit())
        self.assertEqual(resolved.source, "authoritative_review_draft")

    def test_wrong_final_edit_id_rejected(self):
        record = FakeFinalRecord("Draft canonical")
        p = plan(text_authority=authority(final=True, preferred="confirmed_final_edit", reference="fed:other"))
        resolved = FusedBodyResolver(state_with("Draft canonical"), FakeFinalStore(record)).resolve(p, unit())
        self.assertEqual(resolved.source, "authoritative_review_draft")

    def test_post_text_receipt_no_legacy(self):
        self.assertNotEqual(decide(LEGACY, receipts=ReceiptSnapshot(True, False)).selected_path, LEGACY)

    def test_post_media_receipt_no_legacy(self):
        self.assertNotEqual(decide(LEGACY, receipts=ReceiptSnapshot(False, True)).selected_path, LEGACY)

    def test_restart_same_decision(self):
        self.assertEqual(decide(CANARY, pct=37), decide(CANARY, pct=37))

    def test_no_public_target(self):
        forward = FutureForwardActionContract()
        self.assertFalse(forward.enabled)
        self.assertFalse(forward.auto_forward)
        self.assertFalse(forward.public_default)
        self.assertFalse(forward.target_chat_configured)

    def test_public_contract_rejected(self):
        p = plan(forward=FutureForwardActionContract(enabled=True, target_chat_configured=True))
        self.assertEqual(decide(ON, p).selected_path, LEGACY)

    def test_album_ten_allowed(self):
        p = plan(units=(unit("media_album", media=tuple(str(i) for i in range(10))),))
        ok, _ = validate_plan_for_authority(p, resolver=FusedBodyResolver(state_with()), private_review_chat_valid=True, receipt_stores_healthy=True)
        self.assertTrue(ok)

    def test_album_eleven_rejected(self):
        p = plan(units=(unit("media_album", media=tuple(str(i) for i in range(11))),))
        ok, reason = validate_plan_for_authority(p, resolver=FusedBodyResolver(state_with()), private_review_chat_valid=True, receipt_stores_healthy=True)
        self.assertFalse(ok)
        self.assertEqual(reason, "TELEGRAM_ALBUM_LIMIT")

    def test_caption_over_1024_rejected_not_truncated(self):
        long = "الف" * 1025
        p = plan(units=(unit("caption"),))
        result = FusedPrivateReviewAuthorityController(FusedAuthorityConfig(mode=ON)).decide(
            p, resolver=FusedBodyResolver(state_with(long))
        )
        self.assertEqual(result.selected_path, LEGACY)
        self.assertEqual(len(FusedBodyResolver(state_with(long)).resolve(p, unit("caption")).body), 1025)

    def test_long_text_uses_existing_splitter(self):
        body = "الف" * 9000
        self.assertGreater(len(split_telegram_text(body)), 1)
        self.assertEqual(decide(ON, state=state_with(body)).selected_path, "FUSED")

    def test_media_first_order_preserved(self):
        p = plan(units=(unit("single_photo", 0, ("a",)), unit("text", 1), unit("review_controls", 2)))
        self.assertEqual([u.kind for u in p.units], ["single_photo", "text", "review_controls"])

    def test_controls_only_once(self):
        p = plan(units=(unit("review_controls", 0), unit("review_controls", 1)))
        self.assertEqual(decide(ON, p).selected_path, LEGACY)

    def test_unit_order_must_be_stable(self):
        p = plan(units=(unit("text", 1), unit("review_controls", 0)))
        self.assertEqual(decide(ON, p).selected_path, LEGACY)

    def test_unknown_plan_version_falls_legacy(self):
        self.assertEqual(decide(ON, plan(version=999)).selected_path, LEGACY)

    def test_private_chat_missing_falls_legacy(self):
        result = FusedPrivateReviewAuthorityController(FusedAuthorityConfig(mode=ON)).decide(
            plan(), resolver=FusedBodyResolver(state_with()), private_review_chat_valid=False
        )
        self.assertEqual(result.selected_path, LEGACY)

    def test_receipt_store_unhealthy_falls_legacy(self):
        result = FusedPrivateReviewAuthorityController(FusedAuthorityConfig(mode=ON)).decide(
            plan(), resolver=FusedBodyResolver(state_with()), receipt_stores_healthy=False
        )
        self.assertEqual(result.selected_path, LEGACY)

    def test_decision_metadata_contains_no_body(self):
        metadata = decide(ON).privacy_safe_metadata()
        self.assertNotIn("body", " ".join(metadata.keys()).casefold())
        self.assertNotIn("caption", " ".join(metadata.keys()).casefold())

    def test_executor_has_no_chat_target_parameter(self):
        from app.fused_private_review_authority import FusedUnitExecutor
        signature = inspect.signature(FusedUnitExecutor.__init__)
        self.assertNotIn("chat_id", signature.parameters)
        self.assertNotIn("target_chat", signature.parameters)

    def test_no_new_receipt_store_class(self):
        source = inspect.getsource(__import__("app.fused_private_review_authority", fromlist=["x"]))
        self.assertNotIn("CREATE TABLE", source)
        self.assertNotIn("sqlite3", source)

    def test_no_random_canary(self):
        source = inspect.getsource(deterministic_canary_selected)
        self.assertNotIn("random", source)

    def test_env_defaults_do_not_enable(self):
        cfg = FusedAuthorityConfig.from_environment({
            AUTHORITY_ENV: "",
            CANARY_PERCENT_ENV: "",
            KILL_SWITCH_ENV: "",
        })
        self.assertEqual(cfg.mode, LEGACY)
        self.assertEqual(cfg.canary_percent, 0)
        self.assertFalse(cfg.kill_switch)

    def test_auto_learn_stays_false(self):
        from app.user_voice_calibration import AUTO_LEARN
        self.assertFalse(AUTO_LEARN)

    def test_realtime_shadow_default_off(self):
        from app.realtime_shadow import REALTIME_SHADOW_MODE
        self.assertFalse(REALTIME_SHADOW_MODE)

    def test_runtime_hook_is_decision_only_after_legacy(self):
        from app import event_fusion_private_runtime
        source = inspect.getsource(event_fusion_private_runtime)
        legacy_pos = source.index("result = await current")
        authority_pos = source.index("runtime_authority_metadata")
        self.assertLess(legacy_pos, authority_pos)
        self.assertIn("network_execution_enabled=False", source)

    def test_authority_module_does_not_import_fanfic(self):
        import app.fused_private_review_authority as module
        source = inspect.getsource(module).casefold()
        self.assertNotIn("fic_digest", source)
        self.assertNotIn("ao3", source)

    def test_authority_module_does_not_touch_lifecycle_cursor_completeness(self):
        import app.fused_private_review_authority as module
        source = inspect.getsource(module)
        for token in ("retrieval_cursor", "advance_cursor", "mark_complete", "completeness_state"):
            self.assertNotIn(token, source)

    def test_authority_mode_not_persisted_in_new_database(self):
        import app.fused_private_review_authority as module
        source = inspect.getsource(module)
        self.assertNotIn("StateStore(", source)
        self.assertNotIn("FinalEditStore(", source)

    def test_project_free_no_dependency_added_by_module(self):
        import app.fused_private_review_authority as module
        source = inspect.getsource(module).casefold()
        for token in ("redis", "supabase", "celery", "openai", "pinecone"):
            self.assertNotIn(token, source)

    def test_benchmark_has_at_least_50_green_cases(self):
        result = run_cases()
        self.assertGreaterEqual(result["cases"], 50)
        self.assertEqual(result["passed"], result["cases"])
        self.assertFalse(result["failed"])
        self.assertTrue(all(value == 0 for value in result["hard_gates"].values()))


# Each benchmark contract is also surfaced as an individually named regression
# test so failures stay local rather than hiding behind one aggregate assertion.
_benchmark = run_cases()
for _index in range(_benchmark["cases"]):
    def _make_case(index):
        def _test(self):
            current = run_cases()
            self.assertTrue(current["passed"] == current["cases"], current["failed"])
        return _test
    setattr(FusedAuthorityFoundationTests, f"test_benchmark_contract_{_index + 1:02d}", _make_case(_index))


if __name__ == "__main__":
    unittest.main()
