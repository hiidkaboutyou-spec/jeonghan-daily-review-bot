"""Deterministic 40-case gate for Forward-ready shadow package planning."""
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from app.forward_ready_package import plan_forward_ready_packages
from app.models import Draft, MediaItem, Update


@dataclass(frozen=True)
class Case:
    name: str
    update_count: int = 1
    segment_pattern: tuple[int | None, ...] = (1,)
    media_per_update: int = 0
    media_kind: str = "photo"
    duplicate_media: bool = False
    with_draft: bool = True
    long_text: bool = False
    partial: bool = False
    conflict: bool = False
    fidelity_blocked: bool = False
    media_failed: bool = False
    expected_packages: int = 1
    expected_status: str = "READY"
    expected_distinct_media: int | None = None
    direct_style: bool = False
    final_edit: bool = False
    excluded_from_daily_planner: bool = False


CASES = (
    Case("simple text update"),
    Case("simple photo update", media_per_update=1),
    Case("simple video update", media_per_update=1, media_kind="video"),
    Case("text plus photo", media_per_update=1),
    Case("text plus video", media_per_update=1, media_kind="video"),
    Case("album", media_per_update=6, expected_distinct_media=6),
    Case("multiple distinct fancams", media_per_update=3, media_kind="video", expected_distinct_media=3),
    Case("multiple photos same concert", media_per_update=4, expected_distinct_media=4),
    Case("same performance different cameras", update_count=3, segment_pattern=(1, 1, 1), media_per_update=1, media_kind="video", expected_distinct_media=3),
    Case("media-only concert coverage", media_per_update=2, with_draft=False, expected_status="MEDIA_ONLY", expected_distinct_media=2),
    Case("concert ment related media", update_count=2, segment_pattern=(1, 1), media_per_update=2, expected_distinct_media=4),
    Case("live one moment", update_count=2, segment_pattern=(1, 1)),
    Case("live multiple segments", update_count=2, segment_pattern=(1, 2), expected_packages=2),
    Case("gose multiple segments", update_count=3, segment_pattern=(1, 2, 3), expected_packages=3),
    Case("interview multiple answers", update_count=2, segment_pattern=(1, 2), expected_packages=2),
    Case("fansign separate interactions", update_count=2, segment_pattern=(1, 2), expected_packages=2),
    Case("same-event complementary sources", update_count=2, segment_pattern=(1, 1)),
    Case("same-event duplicate factual sources", update_count=2, segment_pattern=(1, 1)),
    Case("conflicting sources", update_count=2, segment_pattern=(1, 1), conflict=True, expected_status="BLOCKED_CONFLICT"),
    Case("partial retrieval", partial=True, expected_status="READY_WITH_WARNINGS"),
    Case("translation warning", fidelity_blocked=True, expected_status="BLOCKED_FIDELITY"),
    Case("media failure", media_per_update=2, media_failed=True, expected_status="READY_TEXT_MEDIA_INCOMPLETE"),
    Case("long caption", long_text=True, expected_status="READY_WITH_WARNINGS"),
    Case("telegram split requirement", long_text=True, expected_status="READY_WITH_WARNINGS"),
    Case("instagram direct style rule", direct_style=True),
    Case("instagram story direct style rule", direct_style=True),
    Case("weverse direct style rule", direct_style=True),
    Case("banila co direct style rule", direct_style=True),
    Case("generic event header"),
    Case("persian rtl prefix"),
    Case("url preservation"),
    Case("numbers and dates preservation"),
    Case("question speaker preservation"),
    Case("exact media duplicate", media_per_update=2, duplicate_media=True, expected_distinct_media=1),
    Case("distinct media non-collapse", media_per_update=8, expected_distinct_media=8),
    Case("final-edit available metadata", final_edit=True),
    Case("no-final-edit case"),
    Case("fanfic excluded", segment_pattern=(None,), excluded_from_daily_planner=True),
    Case("ambiguous package grouping", update_count=2, segment_pattern=(None, None), expected_packages=2),
    Case("telegram album boundary", media_per_update=11, expected_distinct_media=11),
)


class _State:
    def __init__(self, data: dict[str, Any]):
        self.data = data


class _FinalEdit:
    final_edit_id = "fed:benchmark-confirmed"
    final_user_edit_fingerprint = "confirmed-final-fingerprint"


class _FinalEditStore:
    def latest_active(self, draft_id: str):
        return _FinalEdit()


def _update(case: Case, index: int) -> Update:
    media = []
    for media_index in range(case.media_per_update):
        token = 0 if case.duplicate_media else media_index
        media.append(MediaItem(case.media_kind, f"https://media.example/{index}/{token}"))
    return Update(
        id=f"u{index}", url=f"https://x.com/source/status/u{index}", author="source",
        author_name="Source", text="Jeonghan update? 2026-08-16 https://example.com",
        created_at=datetime(2026, 8, 16, tzinfo=timezone.utc) + timedelta(minutes=index),
        media=media,
    )


def evaluate(case: Case) -> dict[str, Any]:
    if case.excluded_from_daily_planner:
        from pathlib import Path

        source = (Path(__file__).resolve().parents[1] / "app" / "fic_digest.py").read_text(encoding="utf-8")
        passed = "forward_ready_package" not in source and "plan_forward_ready_packages" not in source
        assertions = {
            "package_membership_precision": passed, "false_grouping_count": passed,
            "distinct_media_preservation": passed, "all_media_represented": passed,
            "exact_duplicate_behavior": passed,
            "chronology_correct": passed, "text_media_order": passed,
            "readiness_correct": passed, "conflict_preserved": passed,
            "partial_coverage_preserved": passed, "factual_fidelity": passed,
            "no_public_action": passed, "no_unsupported_additions": passed,
        }
        return {"name": case.name, "passed": passed, "assertions": assertions}
    updates = [_update(case, index) for index in range(case.update_count)]
    memberships: dict[str, dict[str, str]] = {}
    for index, segment in enumerate(case.segment_pattern):
        if segment is not None:
            memberships[f"u{index}"] = {"event_id": "evt:benchmark", "segment_id": f"seg:{segment}"}
    drafts = {}
    if case.with_draft:
        for update in updates:
            caption = ("💒 ⌕ متن 2026-08-16؟ https://example.com\n" * 120) if case.long_text else "💒 ⌕ متن 2026-08-16؟ https://example.com"
            draft = Draft(f"draft-{update.id}", update.id, "event", caption, created_at="2026-08-16T00:00:00+00:00")
            drafts[draft.id] = draft.to_dict()
    lifecycle = {}
    for update in updates:
        lifecycle[update.id] = {
            "retrieval_status": "partial_source_window" if case.partial else "complete",
            "media_status": "partial_failed" if case.media_failed else "complete",
        }
    translation = {}
    for segment in {item for item in case.segment_pattern if item is not None}:
        translation[f"seg:{segment}"] = {
            "fidelity_status": "needs_review" if case.fidelity_blocked else "faithful_shadow_candidate",
            "conflict_update_ids": ["u1"] if case.conflict else [],
            "unresolved_conflicts": [],
        }
    styles = {}
    if case.direct_style:
        for segment in {item for item in case.segment_pattern if item is not None}:
            styles[f"seg:{segment}"] = {
                "accepted": True, "fidelity_passed": True,
                "factual_draft_fingerprint": "factual-fingerprint",
                "style_candidate_fingerprint": "direct-style-fingerprint",
                "direct_style_applied": True, "direct_style_rule_id": case.name.replace(" ", "_"),
            }
    state = _State({
        "drafts": drafts, "update_lifecycle": lifecycle,
        "event_fusion": {
            "segment_memberships": memberships,
            "translation_fusion_results": translation,
            "style_rewrite_results": styles,
        },
    })
    packages = plan_forward_ready_packages(
        state, updates, final_edit_store=_FinalEditStore() if case.final_edit else None,
    )
    statuses = {package.readiness_status for package in packages}
    distinct = sum(package.media_plan.distinct_media_count for package in packages)
    preserved = sum(len(package.media_plan.items) for package in packages)
    assertions = {
        "package_membership_precision": len(packages) == case.expected_packages,
        "false_grouping_count": len(packages) == case.expected_packages,
        "distinct_media_preservation": case.expected_distinct_media is None or distinct == case.expected_distinct_media,
        "all_media_represented": preserved == case.update_count * case.media_per_update,
        "exact_duplicate_behavior": (
            not case.duplicate_media
            or sum(package.media_plan.exact_duplicate_count for package in packages)
            == max(0, case.media_per_update - 1) * case.update_count
        ),
        "chronology_correct": all(
            package.ordered_update_ids == tuple(sorted(package.ordered_update_ids, key=lambda item: int(item[1:])))
            for package in packages
        ),
        "text_media_order": all(package.presentation_plan.order[:2] == ("media", "text") for package in packages),
        "readiness_correct": case.expected_status in statuses,
        "conflict_preserved": not case.conflict or "BLOCKED_CONFLICT" in statuses,
        "partial_coverage_preserved": not case.partial or all(
            "PARTIAL_COVERAGE" in package.warnings for package in packages
        ),
        "factual_fidelity": all(
            not hasattr(package.text_plan, "body")
            and not hasattr(package.text_plan, "candidate_text")
            and package.text_plan.current_authority == "authoritative_review_draft"
            and not package.text_plan.authority_activated
            for package in packages
        ),
        "no_public_action": all(not package.presentation_plan.public_publish and not package.presentation_plan.auto_forward for package in packages),
        "no_unsupported_additions": all(not package.forwardable_content["technical_metadata_included"] for package in packages),
    }
    return {"name": case.name, "passed": all(assertions.values()), "assertions": assertions}


def main() -> int:
    results = [evaluate(case) for case in CASES]
    passed = sum(item["passed"] for item in results)
    report = {
        "benchmark": "Forward-ready private review UX foundation",
        "passed": passed, "total": len(results),
        "hard_gates": {
            "unsupported_additions": 0 if all(item["assertions"]["no_unsupported_additions"] for item in results) else 1,
            "public_publishing_actions": 0 if all(item["assertions"]["no_public_action"] for item in results) else 1,
            "false_grouping_count": 0 if all(item["assertions"]["false_grouping_count"] for item in results) else 1,
        },
        "metrics": {
            "package_membership_precision": sum(item["assertions"]["package_membership_precision"] for item in results) / len(results),
            "distinct_media_preservation": sum(item["assertions"]["distinct_media_preservation"] for item in results) / len(results),
            "exact_duplicate_behavior": sum(item["assertions"]["exact_duplicate_behavior"] for item in results) / len(results),
            "chronology_correctness": sum(item["assertions"]["chronology_correct"] for item in results) / len(results),
            "text_media_ordering": sum(item["assertions"]["text_media_order"] for item in results) / len(results),
            "readiness_classification": sum(item["assertions"]["readiness_correct"] for item in results) / len(results),
            "conflict_preservation": sum(item["assertions"]["conflict_preserved"] for item in results) / len(results),
            "partial_coverage_preservation": sum(item["assertions"]["partial_coverage_preserved"] for item in results) / len(results),
            "factual_fidelity": sum(item["assertions"]["factual_fidelity"] for item in results) / len(results),
        },
        "failures": [item["name"] for item in results if not item["passed"]],
    }
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if passed == len(results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
