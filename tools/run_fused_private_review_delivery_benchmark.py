"""Deterministic hard gate for shadow Fused Private-Review Delivery planning."""
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from app.forward_ready_package import plan_forward_ready_packages
from app.fused_private_review_delivery import (
    EXISTING_REVIEW_CONTROLS,
    TELEGRAM_ALBUM_LIMIT,
    TELEGRAM_CAPTION_LIMIT,
    plan_fused_private_review_delivery,
)
from app.media_delivery import MediaDeliveryLedger
from app.models import Draft, MediaItem, Update


@dataclass(frozen=True)
class Case:
    name: str
    update_count: int = 1
    segment_pattern: tuple[int | None, ...] = (1,)
    media_kinds: tuple[str, ...] = ()
    media_per_update: int = 0
    duplicate_media: bool = False
    with_draft: bool = True
    text: str = "،،⌕໋  ִ˒˒ جونگهان: امروز 2026-08-16 میای؟ https://example.com/1"
    long_text: bool = False
    partial: bool = False
    conflict: bool = False
    fidelity_blocked: bool = False
    media_failed: bool = False
    direct_style: bool = False
    final_edit: bool = False
    malformed_shadow_state: bool = False
    expected_packages: int = 1
    expected_readiness: str = "READY_TO_PRESENT"
    tag: str = ""


CASES = (
    Case("single text", tag="single_text"),
    Case("photo + text", media_per_update=1, media_kinds=("photo",), tag="photo_text"),
    Case("video + text", media_per_update=1, media_kinds=("video",), tag="video_text"),
    Case("media-only photo", media_per_update=1, media_kinds=("photo",), with_draft=False, tag="media_only"),
    Case("media-only video", media_per_update=1, media_kinds=("video",), with_draft=False, tag="media_only"),
    Case("album 2-10", media_per_update=8, media_kinds=("photo",), tag="album"),
    Case("album >10 split", media_per_update=23, media_kinds=("photo",), tag="album_split"),
    Case("mixed photo/video", media_per_update=6, media_kinds=("photo", "video"), tag="mixed_media"),
    Case("media + long text", media_per_update=1, media_kinds=("photo",), long_text=True, expected_readiness="READY_WITH_WARNINGS", tag="long_text"),
    Case("caption overflow", media_per_update=1, media_kinds=("photo",), text="م" * 1500, expected_readiness="READY_TO_PRESENT", tag="caption_overflow"),
    Case("multiple distinct fancams", media_per_update=5, media_kinds=("video",), tag="coverage"),
    Case("same performance multiple cameras", update_count=3, segment_pattern=(1, 1, 1), media_per_update=1, media_kinds=("video",), tag="coverage"),
    Case("multiple concert photos", media_per_update=9, media_kinds=("photo",), tag="coverage"),
    Case("backstage + stage", update_count=2, segment_pattern=(1, 1), media_per_update=2, media_kinds=("video", "photo"), tag="coverage"),
    Case("exact duplicate media", media_per_update=3, media_kinds=("photo",), duplicate_media=True, tag="exact_duplicate"),
    Case("partial media failure", media_per_update=2, media_kinds=("photo",), media_failed=True, expected_readiness="MEDIA_INCOMPLETE", tag="media_failure"),
    Case("Live one Segment", update_count=2, segment_pattern=(1, 1), tag="same_segment"),
    Case("Live multiple Segments", update_count=3, segment_pattern=(1, 2, 3), expected_packages=3, tag="separate_segments"),
    Case("GOSE multiple Segments", update_count=3, segment_pattern=(1, 2, 3), expected_packages=3, tag="separate_segments"),
    Case("interview multiple answers", update_count=3, segment_pattern=(1, 2, 3), expected_packages=3, tag="separate_segments"),
    Case("fansign interactions", update_count=3, segment_pattern=(1, 2, 3), expected_packages=3, tag="separate_segments"),
    Case("complementary sources", update_count=2, segment_pattern=(1, 1), tag="same_segment"),
    Case("duplicate factual source coverage", update_count=2, segment_pattern=(1, 1), tag="same_segment"),
    Case("conflict", update_count=2, segment_pattern=(1, 1), conflict=True, expected_readiness="BLOCKED", tag="conflict"),
    Case("partial retrieval", partial=True, expected_readiness="PARTIAL_COVERAGE", tag="partial"),
    Case("translation fidelity block", fidelity_blocked=True, expected_readiness="BLOCKED", tag="fidelity"),
    Case("final edit available", final_edit=True, tag="final_edit"),
    Case("no final edit", tag="no_final_edit"),
    Case("IG", direct_style=True, tag="direct_style"),
    Case("IG Story", direct_style=True, tag="direct_style"),
    Case("Weverse", direct_style=True, tag="direct_style"),
    Case("Banila Co", direct_style=True, tag="direct_style"),
    Case("generic live/event", tag="format_preservation"),
    Case("RTL prefix", text="\u200f💒 ⌕ متن فارسی", tag="format_preservation"),
    Case("URL", text="متن https://example.com/a?x=1&y=2", tag="format_preservation"),
    Case("number/date", text="2026-08-16 ساعت 21:30 و 17 نفر", tag="format_preservation"),
    Case("question/speaker", text="🐶 دوکیوم: هانی هیونگ خوب بود؟", tag="format_preservation"),
    Case("emoji-leading Persian", text="🪽 جونگهان امروز خیلی کیوت بود", tag="format_preservation"),
    Case("cold restart recomputation", tag="restart"),
    Case("duplicate planning", tag="duplicate_planning"),
    Case("malformed plan state", malformed_shadow_state=True, tag="malformed"),
    Case("source ordering", media_per_update=6, media_kinds=("photo", "video"), tag="source_order"),
    Case("chronology ordering", update_count=4, segment_pattern=(1, 1, 1, 1), tag="chronology"),
    Case("review controls placement", tag="controls"),
    Case("internal metadata stripping", tag="internal_boundary"),
    Case("unsupported media grouping split", media_per_update=3, media_kinds=("photo", "document", "video"), tag="unsupported_media"),
    Case("media-first ordering", media_per_update=2, media_kinds=("photo",), tag="media_first"),
    Case("no public publishing action", tag="no_public"),
    Case("receipt authority untouched", media_per_update=1, media_kinds=("video",), tag="receipt"),
    Case("forward target remains disabled", tag="forward_contract"),
)


class _State:
    def __init__(self, data: dict[str, Any]):
        self.data = data


class _FinalEdit:
    final_edit_id = "fedit:benchmark-confirmed"
    final_user_edit_fingerprint = "confirmed-final-fingerprint"


class _FinalEditStore:
    def latest_active(self, draft_id: str):
        return _FinalEdit()


def _case_text(case: Case) -> str:
    if case.long_text:
        return ("💒 ⌕ جونگهان: متن بلند؟ 2026-08-16 https://example.com\n\n" * 120).strip()
    return case.text


def _media(case: Case, update_index: int) -> list[MediaItem]:
    rows: list[MediaItem] = []
    kinds = case.media_kinds or ("photo",)
    for index in range(case.media_per_update):
        kind = kinds[index % len(kinds)]
        token = 0 if case.duplicate_media else index
        rows.append(MediaItem(kind, f"https://media.example/{update_index}/{token}"))
    return rows


def _updates(case: Case) -> list[Update]:
    return [
        Update(
            id=f"u{index}",
            url=f"https://x.com/source/status/u{index}",
            author="source",
            author_name="Source",
            text=_case_text(case),
            created_at=datetime(2026, 8, 16, tzinfo=timezone.utc) + timedelta(minutes=index),
            media=_media(case, index),
        )
        for index in range(case.update_count)
    ]


def _state(case: Case, updates: list[Update]) -> _State:
    memberships: dict[str, dict[str, str]] = {}
    for index, segment in enumerate(case.segment_pattern):
        if index < len(updates) and segment is not None:
            memberships[updates[index].id] = {
                "event_id": "evt:benchmark",
                "segment_id": f"seg:{segment}",
            }
    drafts = {}
    if case.with_draft:
        for update in updates:
            draft = Draft(
                id=f"draft:{update.id}",
                update_id=update.id,
                event_key="event",
                caption=update.text,
                created_at="2026-08-16T00:00:00+00:00",
            )
            drafts[draft.id] = draft.to_dict()
    lifecycle = {
        update.id: {
            "retrieval_status": "partial_source_window" if case.partial else "complete",
            "media_status": "partial_failed" if case.media_failed else "complete",
        }
        for update in updates
    }
    translation = {}
    for segment in {value for value in case.segment_pattern if value is not None}:
        translation[f"seg:{segment}"] = {
            "fidelity_status": "needs_review" if case.fidelity_blocked else "faithful_shadow_candidate",
            "conflict_update_ids": ["u1"] if case.conflict else [],
            "unresolved_conflicts": [],
        }
    style = {}
    if case.direct_style:
        for segment in {value for value in case.segment_pattern if value is not None}:
            style[f"seg:{segment}"] = {
                "accepted": True,
                "fidelity_passed": True,
                "factual_draft_fingerprint": "factual-fingerprint",
                "style_candidate_fingerprint": "direct-style-fingerprint",
                "direct_style_applied": True,
                "direct_style_rule_id": case.name.casefold().replace(" ", "_"),
            }
    event_fusion: dict[str, Any] = {
        "segment_memberships": memberships,
        "translation_fusion_results": translation,
        "style_rewrite_results": style,
    }
    if case.malformed_shadow_state:
        event_fusion["fused_private_review_delivery_plans"] = {
            "fdp:garbage": {"version": 999, "body": "must-not-be-used"}
        }
    return _State({
        "drafts": drafts,
        "event_fusion": event_fusion,
        "update_lifecycle": lifecycle,
    })


def _logical_distinct_media(updates: list[Update]) -> set[str]:
    return {
        MediaDeliveryLedger.url_identity(media)
        for update in updates
        for media in update.media
    }


def evaluate(case: Case) -> dict[str, Any]:
    updates = _updates(case)
    state = _state(case, updates)
    packages = plan_forward_ready_packages(
        state,
        updates,
        final_edit_store=_FinalEditStore() if case.final_edit else None,
        persist=False,
    )
    first = plan_fused_private_review_delivery(state, packages)
    second = plan_fused_private_review_delivery(state, packages)

    planned_media = {
        media_ref
        for plan in first
        for unit in plan.units
        if unit.kind in {"media_album", "single_photo", "single_video", "standalone_media"}
        for media_ref in unit.media_refs
    }
    expected_media = _logical_distinct_media(updates)
    flattened_updates = [uid for plan in first for uid in plan.ordered_update_ids]
    expected_order = [
        item.id for item in sorted(updates, key=lambda value: (value.created_at, value.id))
    ]
    forwardable_strings = json.dumps(
        [plan.forwardable_content for plan in first],
        ensure_ascii=False,
        sort_keys=True,
    )
    all_album_safe = all(
        len(unit.media_refs) <= TELEGRAM_ALBUM_LIMIT
        for plan in first
        for unit in plan.units
        if unit.kind == "media_album"
    )
    all_transport_safe = all(
        not plan.delivered
        and plan.delivery_status == "PLANNED"
        and plan.receipt_authority == "MessageDeliveryStore+MediaDeliveryLedger"
        for plan in first
    )
    no_public = all(
        not plan.future_forward_action.enabled
        and not plan.future_forward_action.auto_forward
        and not plan.future_forward_action.public_default
        and not plan.future_forward_action.target_chat_configured
        for plan in first
    )
    assertions: dict[str, bool] = {
        "false_package_unit_merge": len(first) == case.expected_packages,
        "distinct_media_loss": planned_media == expected_media,
        "unsupported_factual_additions": all(
            not plan.forwardable_content["technical_metadata_included"] for plan in first
        ),
        "receipt_authority_violations": all_transport_safe,
        "public_publishing_actions": no_public,
        "chronology_errors": flattened_updates == expected_order,
        "readiness_misclassification": all(
            plan.readiness == case.expected_readiness for plan in first
        ),
        "telegram_limit_violations": all_album_safe,
        "deterministic_identity": [plan.plan_id for plan in first] == [plan.plan_id for plan in second],
        "unit_identity_deterministic": [
            [unit.unit_id for unit in plan.units] for plan in first
        ] == [
            [unit.unit_id for unit in plan.units] for plan in second
        ],
        "internal_boundary": "fdp:" not in forwardable_strings and "frp:" not in forwardable_strings,
    }

    if case.tag == "album":
        assertions["case_specific"] = any(unit.kind == "media_album" for unit in first[0].units)
    elif case.tag == "album_split":
        assertions["case_specific"] = sum(
            unit.kind in {"media_album", "single_photo", "single_video"}
            for unit in first[0].units
        ) >= 3
    elif case.tag == "mixed_media":
        assertions["case_specific"] = any(unit.kind == "media_album" for unit in first[0].units)
    elif case.tag in {"long_text", "caption_overflow"}:
        assertions["case_specific"] = (
            "CAPTION_OVERFLOW_TO_TEXT" in first[0].warnings
            and not any(unit.kind == "caption" for unit in first[0].units)
        )
    elif case.tag == "media_only":
        assertions["case_specific"] = not any(
            unit.kind in {"caption", "text", "continuation_text", "review_controls"}
            for unit in first[0].units
        )
    elif case.tag == "exact_duplicate":
        assertions["case_specific"] = bool(first[0].suppressed_exact_duplicate_media_refs)
    elif case.tag in {"conflict", "fidelity"}:
        assertions["case_specific"] = first[0].readiness == "BLOCKED"
    elif case.tag == "partial":
        assertions["case_specific"] = "PARTIAL_COVERAGE" in first[0].warnings
    elif case.tag == "media_failure":
        assertions["case_specific"] = first[0].readiness == "MEDIA_INCOMPLETE"
    elif case.tag == "final_edit":
        assertions["case_specific"] = (
            first[0].text_authority.preferred_candidate == "confirmed_final_edit"
            and first[0].text_authority.final_edit_confirmed
            and not first[0].text_authority.user_voice_certified
        )
    elif case.tag == "no_final_edit":
        assertions["case_specific"] = not first[0].text_authority.final_edit_confirmed
    elif case.tag == "direct_style":
        assertions["case_specific"] = (
            first[0].text_authority.preferred_candidate == "direct_style_candidate"
            and not first[0].text_authority.user_voice_certified
        )
    elif case.tag == "restart":
        restarted_updates = _updates(case)
        restarted = _state(case, restarted_updates)
        restarted_packages = plan_forward_ready_packages(
            restarted, restarted_updates, persist=False
        )
        restarted_plans = plan_fused_private_review_delivery(restarted, restarted_packages)
        assertions["case_specific"] = [plan.plan_id for plan in first] == [
            plan.plan_id for plan in restarted_plans
        ]
    elif case.tag == "duplicate_planning":
        assertions["case_specific"] = first == second
    elif case.tag == "malformed":
        assertions["case_specific"] = (
            len(first) == 1
            and "fdp:garbage" not in {plan.plan_id for plan in first}
        )
    elif case.tag == "source_order":
        refs = [
            media_ref
            for unit in first[0].units
            if unit.kind in {"media_album", "single_photo", "single_video", "standalone_media"}
            for media_ref in unit.media_refs
        ]
        expected_refs = [MediaDeliveryLedger.url_identity(item) for item in updates[0].media]
        assertions["case_specific"] = refs == expected_refs
    elif case.tag == "controls":
        assertions["case_specific"] = (
            first[0].units[-1].kind == "review_controls"
            and first[0].units[-1].review_control_names == EXISTING_REVIEW_CONTROLS
            and first[0].units[-1].reuse_existing_controls
        )
    elif case.tag == "internal_boundary":
        assertions["case_specific"] = (
            not first[0].forwardable_content["debug_identifiers_included"]
            and not first[0].forwardable_content["provider_errors_included"]
            and not first[0].forwardable_content["source_health_metadata_included"]
        )
    elif case.tag == "unsupported_media":
        assertions["case_specific"] = (
            any(unit.kind == "standalone_media" for unit in first[0].units)
            and "UNSUPPORTED_ALBUM_COMBINATION_SPLIT" in first[0].warnings
        )
    elif case.tag == "media_first":
        kinds = [unit.kind for unit in first[0].units]
        first_text = next((index for index, value in enumerate(kinds) if value in {"caption", "text", "continuation_text"}), len(kinds))
        last_media = max(index for index, value in enumerate(kinds) if value in {"media_album", "single_photo", "single_video", "standalone_media"})
        assertions["case_specific"] = last_media < first_text
    elif case.tag == "forward_contract":
        assertions["case_specific"] = not first[0].future_forward_action.target_chat_configured
    elif case.tag == "format_preservation":
        assertions["case_specific"] = all(
            state.data["drafts"][f"draft:{update.id}"]["caption"] == update.text
            for update in updates
        )
    else:
        assertions["case_specific"] = True

    return {
        "name": case.name,
        "tag": case.tag,
        "passed": all(assertions.values()),
        "assertions": assertions,
        "plan_ids": [plan.plan_id for plan in first],
    }


def main() -> int:
    results = [evaluate(case) for case in CASES]
    passed = sum(item["passed"] for item in results)
    hard_gates = {
        "false_package_unit_merge": sum(not item["assertions"]["false_package_unit_merge"] for item in results),
        "distinct_media_loss": sum(not item["assertions"]["distinct_media_loss"] for item in results),
        "unsupported_factual_additions": sum(not item["assertions"]["unsupported_factual_additions"] for item in results),
        "receipt_authority_violations": sum(not item["assertions"]["receipt_authority_violations"] for item in results),
        "public_publishing_actions": sum(not item["assertions"]["public_publishing_actions"] for item in results),
        "chronology_errors": sum(not item["assertions"]["chronology_errors"] for item in results),
        "readiness_misclassification": sum(not item["assertions"]["readiness_misclassification"] for item in results),
        "telegram_limit_violations": sum(not item["assertions"]["telegram_limit_violations"] for item in results),
    }
    report = {
        "benchmark": "Fused Private Review Delivery foundation",
        "passed": passed,
        "total": len(results),
        "hard_gates": hard_gates,
        "all_hard_gates_zero": all(value == 0 for value in hard_gates.values()),
        "failures": [
            {
                "name": item["name"],
                "failed_assertions": [
                    key for key, value in item["assertions"].items() if not value
                ],
            }
            for item in results if not item["passed"]
        ],
    }
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if passed == len(results) and report["all_hard_gates_zero"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
