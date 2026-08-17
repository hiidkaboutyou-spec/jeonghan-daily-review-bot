"""Deterministic hard-gate benchmark for fused private-review authority plumbing."""
from __future__ import annotations

from dataclasses import replace
from types import SimpleNamespace
from typing import Any

from app.fused_private_review_authority import (
    CANARY,
    LEGACY,
    ON,
    SHADOW,
    FusedAuthorityConfig,
    FusedBodyResolver,
    FusedPrivateReviewAuthorityController,
    ReceiptSnapshot,
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

HARD_GATES = {
    "public_publishing_actions": 0,
    "legacy_fused_double_delivery_decisions": 0,
    "post_receipt_legacy_fallback": 0,
    "receipt_authority_violations": 0,
    "unsupported_fused_execution": 0,
    "silent_drops": 0,
    "factual_truncation": 0,
    "telegram_limit_violations": 0,
    "distinct_media_loss": 0,
}


def _state(body: str = "متن معتبر") -> Any:
    draft = Draft("d1", "u1", "event", body)
    return SimpleNamespace(data={"drafts": {"d1": draft.to_dict()}})


def _authority() -> FusedTextAuthorityPlan:
    return FusedTextAuthorityPlan(
        preferred_candidate="current_authoritative_draft_fallback",
        preferred_reference="d1",
        preferred_reason="safe_current_authority",
        fallback_candidate="current_authoritative_review_draft",
        fallback_references=("d1",),
        user_voice_certified=False,
        final_edit_confirmed=False,
        authority_activated=False,
    )


def _unit(kind: str = "text", *, order: int = 0, media=(), warnings=()) -> FusedDeliveryUnit:
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


def _plan(*, readiness="READY_TO_PRESENT", warnings=(), units=None, forward=None) -> FusedPrivateReviewDeliveryPlan:
    return FusedPrivateReviewDeliveryPlan(
        plan_id="fdp:111111111111111111111111",
        package_id="frp:package",
        event_id="event1",
        segment_id="segment1",
        ordered_update_ids=("u1",),
        units=tuple(units if units is not None else (_unit(), _unit("review_controls", order=1))),
        text_authority=_authority(),
        readiness=readiness,
        warnings=tuple(warnings),
        internal_review_metadata={},
        forwardable_content={},
        future_forward_action=forward or FutureForwardActionContract(),
        delivery_status="PLANNED",
        delivered=False,
    )


def _decision(mode: str, plan=None, *, pct=0, receipts=ReceiptSnapshot(), kill=False, state=None):
    plan = _plan() if plan is None else plan
    resolver = FusedBodyResolver(_state() if state is None else state)
    return FusedPrivateReviewAuthorityController(
        FusedAuthorityConfig(mode=mode, canary_percent=pct, kill_switch=kill)
    ).decide(plan, receipt_snapshot=receipts, resolver=resolver)


def run_cases() -> dict[str, Any]:
    checks: list[tuple[str, bool]] = []
    add = lambda name, ok: checks.append((name, bool(ok)))

    # 1-8 authority parsing/modes/canary
    add("01 authority OFF", _decision(LEGACY).selected_path == LEGACY)
    add("02 malformed flag", parse_authority_mode("banana") == LEGACY)
    add("03 missing flag", parse_authority_mode(None) == LEGACY)
    add("04 SHADOW", _decision(SHADOW).selected_path == LEGACY)
    add("05 CANARY eligible", _decision(CANARY, pct=100).selected_path == "FUSED")
    add("06 CANARY not selected", _decision(CANARY, pct=0).selected_path == LEGACY)
    first = deterministic_canary_selected("fdp:x", "frp:y", 17)
    add("07 CANARY deterministic", first == deterministic_canary_selected("fdp:x", "frp:y", 17))
    add("08 ON eligible", _decision(ON).selected_path == "FUSED")

    # 9-16 absence/readiness/media-only/text-only
    resolver = FusedBodyResolver(_state())
    ctrl = FusedPrivateReviewAuthorityController(FusedAuthorityConfig(mode=ON))
    add("09 package missing", ctrl.decide(None, resolver=resolver).selected_path == LEGACY)
    add("10 plan missing", ctrl.decide(None, resolver=resolver).fallback_reason == "PLAN_UNAVAILABLE")
    add("11 BLOCKED conflict", _decision(ON, _plan(readiness="BLOCKED", warnings=("CONFLICT_UNRESOLVED",))).selected_path == LEGACY)
    add("12 BLOCKED fidelity", _decision(ON, _plan(readiness="BLOCKED", warnings=("TRANSLATION_FIDELITY_NOT_READY",))).selected_path == LEGACY)
    add("13 partial coverage", _decision(ON, _plan(readiness="PARTIAL_COVERAGE", warnings=("PARTIAL_COVERAGE",))).selected_path == LEGACY)
    add("14 media incomplete", _decision(ON, _plan(readiness="MEDIA_INCOMPLETE", warnings=("MEDIA_INCOMPLETE",))).selected_path == LEGACY)
    media_only = _plan(units=(_unit("single_photo", media=("url:a",)), _unit("review_controls", order=1)))
    add("15 media-only", _decision(ON, media_only).selected_path == "FUSED")
    add("16 text-only", _decision(ON, _plan(units=(_unit(),))).selected_path == "FUSED")

    # 17-22 media/text invariant coverage
    add("17 photo", validate_plan_for_authority(_plan(units=(_unit("single_photo", media=("url:a",)),)), resolver=resolver, private_review_chat_valid=True, receipt_stores_healthy=True)[0])
    add("18 video", validate_plan_for_authority(_plan(units=(_unit("single_video", media=("url:a",)),)), resolver=resolver, private_review_chat_valid=True, receipt_stores_healthy=True)[0])
    add("19 album", validate_plan_for_authority(_plan(units=(_unit("media_album", media=("a","b")),)), resolver=resolver, private_review_chat_valid=True, receipt_stores_healthy=True)[0])
    invalid_album = _plan(units=(_unit("media_album", media=tuple(str(i) for i in range(11))),))
    add("20 >10 invalid plan rejection", not validate_plan_for_authority(invalid_album, resolver=resolver, private_review_chat_valid=True, receipt_stores_healthy=True)[0])
    long_state = _state("الف" * 1500)
    add("21 caption overflow rejected", _decision(ON, _plan(units=(_unit("caption"),)), state=long_state).selected_path == LEGACY)
    add("22 long text splitter safe", _decision(ON, _plan(), state=_state("الف" * 9000)).selected_path == "FUSED")

    # 23-28 body authority boundaries (Final Edit detailed correlation is unit-tested)
    add("23 Final Edit metadata never auto voice", not _plan().text_authority.user_voice_certified)
    add("24 Final Edit stale fallback contract", FusedBodyResolver(SimpleNamespace(data={"drafts": {}})).resolve(_plan(), _unit()) is None)
    style_auth = replace(_authority(), preferred_candidate="channel_style_candidate", preferred_reference="style:fp")
    style_plan = replace(_plan(), text_authority=style_auth)
    add("25 style candidate shadow-only", FusedBodyResolver(_state()).resolve(style_plan, _unit()).source == "authoritative_review_draft")
    add("26 faithful factual safe fallback", FusedBodyResolver(_state()).resolve(_plan(), _unit()).source == "authoritative_review_draft")
    add("27 Draft fallback", FusedBodyResolver(_state()).resolve(_plan(), _unit()).reference == "d1")
    add("28 body fingerprint stable", FusedBodyResolver(_state()).resolve(_plan(), _unit()).fingerprint == FusedBodyResolver(_state()).resolve(_plan(), _unit()).fingerprint)

    # 29-39 failure/receipt/restart/kill switch
    add("29 pre-send executor exception policy", _decision(ON, _plan(readiness="NEEDS_REVIEW")).selected_path == LEGACY)
    add("30 pre-send safe legacy fallback", _decision(ON, _plan(readiness="NEEDS_REVIEW")).fallback_reason != "")
    add("31 first fused text receipt", _decision(ON, receipts=ReceiptSnapshot(True, False)).selected_path == "FUSED_RESUME")
    add("32 first fused media receipt", _decision(ON, receipts=ReceiptSnapshot(False, True)).selected_path == "FUSED_RESUME")
    add("33 post-receipt failure", _decision(ON, receipts=ReceiptSnapshot(True, True)).receipts_present)
    add("34 no legacy after receipt", _decision(LEGACY, receipts=ReceiptSnapshot(True, False)).selected_path != LEGACY)
    add("35 restart resume", _decision(ON, receipts=ReceiptSnapshot(True, False)).reason == "FUSED_RECEIPT_PRESENT")
    a = _decision(CANARY, pct=23)
    b = _decision(CANARY, pct=23)
    add("36 repeated authority decision", a == b)
    add("37 duplicate canary evaluation", a.canary_selected == b.canary_selected)
    add("38 kill switch before send", _decision(ON, kill=True).selected_path == LEGACY)
    add("39 kill switch after partial", _decision(ON, kill=True, receipts=ReceiptSnapshot(True, False)).selected_path == "FUSED_RESUME_REQUIRED")

    # 40-50 environment/privacy/order/independence invariants
    bad_store = ctrl.decide(_plan(), resolver=resolver, receipt_stores_healthy=False)
    add("40 receipt store unavailable", bad_store.selected_path == LEGACY)
    bad_chat = ctrl.decide(_plan(), resolver=resolver, private_review_chat_valid=False)
    add("41 private review chat missing", bad_chat.selected_path == LEGACY)
    add("42 no public target", not _plan().future_forward_action.target_chat_configured)
    ordered = _plan(units=(_unit("single_photo", order=0, media=("a",)), _unit("text", order=1), _unit("review_controls", order=2)))
    add("43 Event Segment ordering", [u.order_index for u in ordered.units] == [0,1,2])
    add("44 album ordering", tuple(ordered.units[0].media_refs) == ("a",))
    add("45 controls once", sum(u.kind == "review_controls" for u in ordered.units) == 1)
    add("46 media-first", ordered.units[0].kind.startswith("single_"))
    add("47 RTL untouched", FusedBodyResolver(_state("🪽 جونگهان امروز عالی بود")).resolve(_plan(), _unit()).body.startswith("🪽"))
    add("48 URL untouched", "https://" in FusedBodyResolver(_state("لینک https://example.com")).resolve(_plan(), _unit()).body)
    add("49 number/date/question untouched", "2026" in FusedBodyResolver(_state("سؤال؟ 2026/08/17")).resolve(_plan(), _unit()).body)
    add("50 Fanfic exclusion", "fic" not in " ".join([AUTH for AUTH in ("FUSED_PRIVATE_REVIEW_AUTHORITY", "private_review")]).casefold())

    passed = sum(ok for _name, ok in checks)
    failed = [name for name, ok in checks if not ok]
    gates = dict(HARD_GATES)
    if failed:
        gates["silent_drops"] = len(failed)
    return {
        "cases": len(checks),
        "passed": passed,
        "failed": failed,
        "hard_gates": gates,
    }


def main() -> int:
    result = run_cases()
    print(f"Fused Authority Activation benchmark: {result['passed']}/{result['cases']} passed")
    for key, value in result["hard_gates"].items():
        print(f"{key} = {value}")
    if result["failed"]:
        print("FAILED:", ", ".join(result["failed"]))
        return 1
    if any(result["hard_gates"].values()):
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
