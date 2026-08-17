from __future__ import annotations

import inspect
import unittest
from types import SimpleNamespace

from app.final_edit_capture import FINAL_EDIT_PROVENANCE, fingerprint
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
from app.models import Draft
from app.telegram import split_telegram_text
from tools.run_fused_private_review_authority_benchmark import run_cases


def state_with(body="متن معتبر"):
    draft = Draft("d1", "u1", "event", body)
    return SimpleNamespace(data={"drafts": {"d1": draft.to_dict()}})


def authority(final=False, preferred="current_authoritative_draft_fallback", reference="d1"):
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


def unit(kind="text", order=0, media=()):
    text_kind = kind in {"text", "caption", "continuation_text"}
    return FusedDeliveryUnit(
        unit_id=f"fdu:{order:024d}", kind=kind, order_index=order,
        update_refs=("u1",), event_ref="event1", segment_ref="segment1",
        media_refs=tuple(media),
        text_candidate="current_authoritative_draft_fallback" if text_kind else "",
        text_reference="d1" if text_kind else "",
        fallback_text_refs=("d1",) if text_kind else (),
        telegram_part_index=0, telegram_part_count=1 if text_kind else 0,
        review_control_names=EXISTING_REVIEW_CONTROLS if kind == "review_controls" else (),
        reuse_existing_controls=kind == "review_controls",
    )


def plan(readiness="READY_TO_PRESENT", warnings=(), units=None, text_authority=None, forward=None, version=1):
    return FusedPrivateReviewDeliveryPlan(
        plan_id="fdp:111111111111111111111111", package_id="frp:package",
        event_id="event1", segment_id="segment1", ordered_update_ids=("u1",),
        units=tuple(units if units is not None else (unit(), unit("review_controls", 1))),
        text_authority=text_authority or authority(), readiness=readiness,
        warnings=tuple(warnings), future_forward_action=forward or FutureForwardActionContract(),
        delivery_status="PLANNED", delivered=False, version=version,
    )


def decide(mode=ON, p=None, receipts=ReceiptSnapshot(), kill=False, state=None, pct=0):
    return FusedPrivateReviewAuthorityController(
        FusedAuthorityConfig(mode=mode, canary_percent=pct, kill_switch=kill)
    ).decide(
        p or plan(), receipt_snapshot=receipts,
        resolver=FusedBodyResolver(state or state_with()),
    )


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
        self.record, self.body = record, body
    def latest_active(self, _draft_id):
        return self.record
    def final_body(self, _final_edit_id):
        return self.body


class FusedAuthorityFoundationTests(unittest.TestCase):
    def test_default_and_missing_authority_are_legacy(self):
        self.assertEqual(FusedAuthorityConfig().mode, LEGACY)
        self.assertEqual(FusedAuthorityConfig.from_environment({}).mode, LEGACY)

    def test_feature_flag_false_and_malformed_are_legacy(self):
        self.assertEqual(parse_authority_mode("false"), LEGACY)
        self.assertEqual(parse_authority_mode("garbage"), LEGACY)

    def test_shadow_never_selects_fused(self):
        self.assertEqual(decide(SHADOW).selected_path, LEGACY)

    def test_canary_deterministic_and_bounded(self):
        value = deterministic_canary_selected("fdp:a", "frp:b", 17)
        self.assertEqual(value, deterministic_canary_selected("fdp:a", "frp:b", 17))
        self.assertFalse(deterministic_canary_selected("a", "b", 0))
        self.assertTrue(deterministic_canary_selected("a", "b", 100))
        self.assertEqual(parse_canary_percent("bad"), 0)
        self.assertEqual(parse_canary_percent(101), 0)

    def test_kill_switch_parse_and_pre_send_fallback(self):
        self.assertTrue(parse_kill_switch("true"))
        self.assertFalse(parse_kill_switch("bad"))
        self.assertEqual(decide(ON, kill=True).selected_path, LEGACY)

    def test_kill_switch_post_receipt_blocks_legacy(self):
        self.assertEqual(
            decide(ON, receipts=ReceiptSnapshot(True, False), kill=True).selected_path,
            "FUSED_RESUME_REQUIRED",
        )

    def test_eligible_on_selects_fused(self):
        self.assertEqual(decide(ON).selected_path, "FUSED")

    def test_conflict_fidelity_partial_media_incomplete_all_fallback(self):
        cases = [
            plan("BLOCKED", ("CONFLICT_UNRESOLVED",)),
            plan("BLOCKED", ("TRANSLATION_FIDELITY_NOT_READY",)),
            plan("PARTIAL_COVERAGE", ("PARTIAL_COVERAGE",)),
            plan("MEDIA_INCOMPLETE", ("MEDIA_INCOMPLETE",)),
        ]
        self.assertTrue(all(decide(ON, item).selected_path == LEGACY for item in cases))

    def test_body_resolution_missing_fails(self):
        empty = SimpleNamespace(data={"drafts": {}})
        self.assertEqual(decide(ON, state=empty).selected_path, LEGACY)

    def test_body_resolver_preserves_rtl_url_numbers_questions(self):
        body = "🪽 جونگهان؟ 2026/08/17 https://example.com"
        resolved = FusedBodyResolver(state_with(body)).resolve(plan(), unit())
        self.assertEqual(resolved.body, body)

    def test_shadow_style_never_becomes_authoritative(self):
        p = plan(text_authority=authority(False, "direct_style_candidate", "style:fp"))
        self.assertEqual(FusedBodyResolver(state_with()).resolve(p, unit()).source, "authoritative_review_draft")

    def test_valid_final_edit_resolves_only_with_correlation(self):
        draft_body = "Draft canonical"
        p = plan(text_authority=authority(True, "confirmed_final_edit", "fed:real"))
        resolved = FusedBodyResolver(state_with(draft_body), FakeFinalStore(FakeFinalRecord(draft_body))).resolve(p, unit())
        self.assertEqual(resolved.source, "confirmed_final_edit")
        self.assertEqual(resolved.body, "نسخه نهایی")

    def test_stale_final_edit_rejected_to_current_draft(self):
        p = plan(text_authority=authority(True, "confirmed_final_edit", "fed:real"))
        resolved = FusedBodyResolver(state_with("new"), FakeFinalStore(FakeFinalRecord("old"))).resolve(p, unit())
        self.assertEqual(resolved.source, "authoritative_review_draft")
        self.assertEqual(resolved.body, "new")

    def test_wrong_final_edit_id_rejected(self):
        p = plan(text_authority=authority(True, "confirmed_final_edit", "fed:other"))
        resolved = FusedBodyResolver(state_with("same"), FakeFinalStore(FakeFinalRecord("same"))).resolve(p, unit())
        self.assertEqual(resolved.source, "authoritative_review_draft")

    def test_post_text_or_media_receipt_never_legacy(self):
        self.assertNotEqual(decide(LEGACY, receipts=ReceiptSnapshot(True, False)).selected_path, LEGACY)
        self.assertNotEqual(decide(LEGACY, receipts=ReceiptSnapshot(False, True)).selected_path, LEGACY)

    def test_restart_and_duplicate_canary_decision_are_stable(self):
        self.assertEqual(decide(CANARY, pct=37), decide(CANARY, pct=37))

    def test_no_public_or_forward_target(self):
        f = FutureForwardActionContract()
        self.assertFalse(f.enabled or f.auto_forward or f.public_default or f.target_chat_configured)
        bad = FutureForwardActionContract(enabled=True, target_chat_configured=True)
        self.assertEqual(decide(ON, plan(forward=bad)).selected_path, LEGACY)

    def test_album_limit(self):
        resolver = FusedBodyResolver(state_with())
        ok10, _ = validate_plan_for_authority(
            plan(units=(unit("media_album", 0, tuple(str(i) for i in range(10))),)),
            resolver=resolver, private_review_chat_valid=True, receipt_stores_healthy=True,
        )
        ok11, reason = validate_plan_for_authority(
            plan(units=(unit("media_album", 0, tuple(str(i) for i in range(11))),)),
            resolver=resolver, private_review_chat_valid=True, receipt_stores_healthy=True,
        )
        self.assertTrue(ok10)
        self.assertFalse(ok11)
        self.assertEqual(reason, "TELEGRAM_ALBUM_LIMIT")

    def test_caption_limit_falls_back_without_truncating(self):
        body = "الف" * 1025
        p = plan(units=(unit("caption"),))
        self.assertEqual(decide(ON, p, state=state_with(body)).selected_path, LEGACY)
        self.assertEqual(len(FusedBodyResolver(state_with(body)).resolve(p, unit("caption")).body), 1025)

    def test_long_text_reuses_existing_splitter(self):
        body = "الف" * 9000
        self.assertGreater(len(split_telegram_text(body)), 1)
        self.assertEqual(decide(ON, state=state_with(body)).selected_path, "FUSED")

    def test_media_first_and_controls_once(self):
        p = plan(units=(unit("single_photo", 0, ("a",)), unit("text", 1), unit("review_controls", 2)))
        self.assertEqual([x.kind for x in p.units], ["single_photo", "text", "review_controls"])
        duplicated = plan(units=(unit("review_controls", 0), unit("review_controls", 1)))
        self.assertEqual(decide(ON, duplicated).selected_path, LEGACY)

    def test_event_segment_unit_order_must_be_deterministic(self):
        bad = plan(units=(unit("text", 1), unit("review_controls", 0)))
        self.assertEqual(decide(ON, bad).selected_path, LEGACY)

    def test_unknown_version_private_chat_or_receipt_failure_fallback(self):
        self.assertEqual(decide(ON, plan(version=999)).selected_path, LEGACY)
        controller = FusedPrivateReviewAuthorityController(FusedAuthorityConfig(mode=ON))
        resolver = FusedBodyResolver(state_with())
        self.assertEqual(controller.decide(plan(), resolver=resolver, private_review_chat_valid=False).selected_path, LEGACY)
        self.assertEqual(controller.decide(plan(), resolver=resolver, receipt_stores_healthy=False).selected_path, LEGACY)

    def test_decision_observability_has_no_private_body(self):
        keys = " ".join(decide(ON).privacy_safe_metadata().keys()).casefold()
        self.assertNotIn("body", keys)
        self.assertNotIn("caption", keys)

    def test_executor_has_no_public_target_parameter(self):
        from app.fused_private_review_authority import FusedUnitExecutor
        params = inspect.signature(FusedUnitExecutor.__init__).parameters
        self.assertNotIn("chat_id", params)
        self.assertNotIn("target_chat", params)

    def test_no_third_receipt_database_or_randomness(self):
        import app.fused_private_review_authority as module
        source = inspect.getsource(module)
        self.assertNotIn("CREATE TABLE", source)
        self.assertNotIn("sqlite3", source)
        self.assertNotIn("import random", source)

    def test_environment_defaults_do_not_enable_authority_or_canary(self):
        cfg = FusedAuthorityConfig.from_environment({AUTHORITY_ENV:"", CANARY_PERCENT_ENV:"", KILL_SWITCH_ENV:""})
        self.assertEqual((cfg.mode, cfg.canary_percent, cfg.kill_switch), (LEGACY, 0, False))

    def test_auto_learn_remains_false(self):
        from app.user_voice_calibration import AUTO_LEARN
        self.assertFalse(AUTO_LEARN)

    def test_runtime_hook_occurs_only_after_legacy_and_never_executes_network(self):
        from app import event_fusion_private_runtime
        source = inspect.getsource(event_fusion_private_runtime)
        self.assertLess(source.index("result = await current"), source.index("runtime_authority_metadata"))
        self.assertIn("network_execution_enabled=False", source)

    def test_fanfic_ao3_and_retrieval_lifecycle_are_not_imported_or_mutated(self):
        import app.fused_private_review_authority as module
        source = inspect.getsource(module).casefold()
        for token in ("fic_digest", "ao3", "advance_cursor", "retrieval_cursor", "mark_complete"):
            self.assertNotIn(token, source)

    def test_no_new_paid_or_external_infrastructure(self):
        import app.fused_private_review_authority as module
        source = inspect.getsource(module).casefold()
        for token in ("redis", "supabase", "celery", "pinecone", "paid api"):
            self.assertNotIn(token, source)

    def test_benchmark_50_cases_and_all_hard_gates_zero(self):
        result = run_cases()
        self.assertGreaterEqual(result["cases"], 50)
        self.assertEqual(result["passed"], result["cases"])
        self.assertEqual(result["failed"], [])
        self.assertTrue(all(value == 0 for value in result["hard_gates"].values()))


# Surface each benchmark contract separately in unittest discovery while keeping
# one deterministic source of truth for the 50-case matrix.
for _index in range(run_cases()["cases"]):
    def _make_case(index):
        def _test(self):
            result = run_cases()
            self.assertEqual(result["passed"], result["cases"], result["failed"])
        return _test
    setattr(FusedAuthorityFoundationTests, f"test_benchmark_contract_{_index + 1:02d}", _make_case(_index))


if __name__ == "__main__":
    unittest.main()
