"""Shadow-only fused private-review Telegram delivery planning.

This module converts already-derived ForwardReadyPackage objects into a deterministic,
reference-only Telegram presentation plan. It never sends Telegram requests, marks
receipts, mutates lifecycle/cursor/completeness state, or activates text/style authority.
"""
from __future__ import annotations

import hashlib
from dataclasses import asdict, dataclass, field
from typing import Any, Iterable, Mapping, Sequence

from .forward_ready_package import ForwardReadyMediaItem, ForwardReadyPackage, TELEGRAM_ALBUM_LIMIT
from .models import Draft
from .telegram import TELEGRAM_TEXT_LIMIT, split_telegram_text

FUSED_DELIVERY_PLAN_VERSION = 1
FUSED_DELIVERY_MODE = "shadow"
TELEGRAM_CAPTION_LIMIT = 1024

PLAN_READINESS_STATES = frozenset({
    "READY_TO_PRESENT",
    "READY_WITH_WARNINGS",
    "NEEDS_REVIEW",
    "BLOCKED",
    "MEDIA_INCOMPLETE",
    "PARTIAL_COVERAGE",
})

DELIVERY_UNIT_KINDS = frozenset({
    "media_album",
    "single_photo",
    "single_video",
    "standalone_media",
    "caption",
    "text",
    "continuation_text",
    "review_controls",
})

EXISTING_REVIEW_CONTROLS = (
    "copy",
    "reject",
    "funnier",
    "softer",
    "precise",
    "final_edit",
)


def _hash(namespace: str, *parts: object, length: int = 24) -> str:
    raw = "\x1f".join(str(part or "") for part in parts)
    return hashlib.sha256(f"{namespace}\x1f{raw}".encode("utf-8")).hexdigest()[:length]


def _canonical_plan_parts(package: ForwardReadyPackage) -> tuple[str, ...]:
    text = package.text_plan
    media_refs = tuple(
        f"{item.media_id}:{item.update_id}:{item.kind}:{item.package_order}:{item.delivery_disposition}"
        for item in package.media_plan.items
    )
    return (
        str(FUSED_DELIVERY_PLAN_VERSION),
        package.context_kind,
        package.event_id,
        package.segment_id,
        *package.ordered_update_ids,
        text.confirmed_final_edit_id,
        text.confirmed_final_edit_fingerprint,
        text.channel_style_candidate_fingerprint,
        text.faithful_factual_fingerprint,
        text.authoritative_draft_fingerprint,
        str(text.character_count),
        str(text.telegram_part_count),
        str(bool(text.telegram_split_required)),
        *text.authoritative_draft_ids,
        *media_refs,
        package.readiness_status,
        *package.warnings,
    )


def make_fused_plan_id(package: ForwardReadyPackage) -> str:
    """Return an fdp identity derived from canonical refs, never the FRP identifier."""
    return f"fdp:{_hash('fused-private-review-delivery-v1', *_canonical_plan_parts(package))}"


def make_delivery_unit_id(plan_id: str, index: int, kind: str, refs: Iterable[str]) -> str:
    return f"fdu:{_hash('fused-private-review-unit-v1', plan_id, index, kind, *tuple(refs))}"


@dataclass(frozen=True, slots=True)
class FusedTextAuthorityPlan:
    preferred_candidate: str
    preferred_reference: str
    preferred_reason: str
    fallback_candidate: str
    fallback_references: tuple[str, ...]
    split_basis: str = "authoritative_review_draft_fallback"
    user_voice_certified: bool = False
    final_edit_confirmed: bool = False
    authority_activated: bool = False


@dataclass(frozen=True, slots=True)
class FusedDeliveryUnit:
    unit_id: str
    kind: str
    order_index: int
    update_refs: tuple[str, ...] = ()
    event_ref: str = ""
    segment_ref: str = ""
    media_refs: tuple[str, ...] = ()
    text_candidate: str = ""
    text_reference: str = ""
    fallback_text_refs: tuple[str, ...] = ()
    telegram_part_index: int = 0
    telegram_part_count: int = 0
    caption_mode: str = "none"
    reply_to_unit_id: str = ""
    review_control_names: tuple[str, ...] = ()
    reuse_existing_controls: bool = False
    internal_only: bool = False
    warnings: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class FutureForwardActionContract:
    enabled: bool = False
    explicit_user_action_required: bool = True
    package_specific: bool = True
    private_review_controlled: bool = True
    auto_forward: bool = False
    public_default: bool = False
    target_chat_configured: bool = False


@dataclass(frozen=True, slots=True)
class FusedPrivateReviewDeliveryPlan:
    plan_id: str
    package_id: str
    event_id: str
    segment_id: str
    ordered_update_ids: tuple[str, ...]
    units: tuple[FusedDeliveryUnit, ...]
    text_authority: FusedTextAuthorityPlan
    readiness: str
    warnings: tuple[str, ...] = ()
    suppressed_exact_duplicate_media_refs: tuple[str, ...] = ()
    internal_review_metadata: Mapping[str, Any] = field(default_factory=dict)
    forwardable_content: Mapping[str, Any] = field(default_factory=dict)
    future_forward_action: FutureForwardActionContract = field(default_factory=FutureForwardActionContract)
    delivery_status: str = "PLANNED"
    delivered: bool = False
    receipt_authority: str = "MessageDeliveryStore+MediaDeliveryLedger"
    mode: str = FUSED_DELIVERY_MODE
    version: int = FUSED_DELIVERY_PLAN_VERSION

    def metadata(self) -> dict[str, Any]:
        """Return the in-memory reference-only plan; generic durable state is not used."""
        return asdict(self)


def _drafts_by_id(state: Any) -> dict[str, Draft]:
    raw = getattr(state, "data", {}).get("drafts", {})
    if not isinstance(raw, dict):
        return {}
    result: dict[str, Draft] = {}
    for draft_id, value in raw.items():
        if not isinstance(value, dict):
            continue
        try:
            draft = Draft.from_dict(value)
        except (TypeError, ValueError):
            continue
        result[str(draft_id)] = draft
    return result


def _text_authority(package: ForwardReadyPackage) -> FusedTextAuthorityPlan:
    text = package.text_plan
    if text.confirmed_final_edit_id:
        preferred = "confirmed_final_edit"
        reference = text.confirmed_final_edit_id
        reason = "confirmed_private_user_final_edit"
        final_confirmed = True
    elif (
        text.future_preferred_candidate in {"direct_style_candidate", "channel_style_candidate"}
        and text.channel_style_candidate_fingerprint
    ):
        preferred = text.future_preferred_candidate
        reference = text.channel_style_candidate_fingerprint
        reason = text.preference_reason or "fidelity_safe_shadow_candidate"
        final_confirmed = False
    elif text.faithful_factual_fingerprint:
        preferred = "faithful_factual"
        reference = text.faithful_factual_fingerprint
        reason = "faithful_factual_candidate"
        final_confirmed = False
    else:
        preferred = "current_authoritative_draft_fallback"
        reference = text.authoritative_draft_id
        reason = "no_higher_reference_only_candidate"
        final_confirmed = False
    return FusedTextAuthorityPlan(
        preferred_candidate=preferred,
        preferred_reference=reference,
        preferred_reason=reason,
        fallback_candidate="current_authoritative_review_draft",
        fallback_references=tuple(text.authoritative_draft_ids),
        user_voice_certified=False,
        final_edit_confirmed=final_confirmed,
        authority_activated=False,
    )


def _map_readiness(package: ForwardReadyPackage) -> tuple[str, list[str]]:
    warnings = list(dict.fromkeys(str(item) for item in package.warnings if str(item)))
    if package.readiness_status in {"BLOCKED_FIDELITY", "BLOCKED_CONFLICT"}:
        return "BLOCKED", warnings
    if package.readiness_status == "READY_TEXT_MEDIA_INCOMPLETE" or "MEDIA_INCOMPLETE" in warnings:
        return "MEDIA_INCOMPLETE", warnings
    if "PARTIAL_COVERAGE" in warnings:
        return "PARTIAL_COVERAGE", warnings
    if package.readiness_status == "NEEDS_REVIEW":
        return "NEEDS_REVIEW", warnings
    if package.readiness_status == "READY_WITH_WARNINGS" or warnings:
        return "READY_WITH_WARNINGS", warnings
    if package.readiness_status in {"READY", "MEDIA_ONLY"}:
        return "READY_TO_PRESENT", warnings
    warnings.append("UNKNOWN_FORWARD_READY_STATUS")
    return "NEEDS_REVIEW", warnings


def _eligible_media(package: ForwardReadyPackage) -> tuple[list[ForwardReadyMediaItem], list[str]]:
    eligible: list[ForwardReadyMediaItem] = []
    suppressed: list[str] = []
    for item in sorted(package.media_plan.items, key=lambda value: value.package_order):
        if item.exact_duplicate_of or item.delivery_disposition == "existing_exact_dedupe":
            suppressed.append(item.media_id)
            continue
        eligible.append(item)
    return eligible, suppressed


def _append_unit(
    units: list[FusedDeliveryUnit],
    plan_id: str,
    kind: str,
    package: ForwardReadyPackage,
    *,
    media_refs: Sequence[str] = (),
    text_candidate: str = "",
    text_reference: str = "",
    fallback_text_refs: Sequence[str] = (),
    telegram_part_index: int = 0,
    telegram_part_count: int = 0,
    caption_mode: str = "none",
    reply_to_unit_id: str = "",
    review_control_names: Sequence[str] = (),
    reuse_existing_controls: bool = False,
    internal_only: bool = False,
    warnings: Sequence[str] = (),
) -> FusedDeliveryUnit:
    index = len(units)
    refs = tuple(media_refs) + tuple(fallback_text_refs) + ((text_reference,) if text_reference else ())
    unit = FusedDeliveryUnit(
        unit_id=make_delivery_unit_id(plan_id, index, kind, refs),
        kind=kind,
        order_index=index,
        update_refs=tuple(package.ordered_update_ids),
        event_ref=package.event_id,
        segment_ref=package.segment_id,
        media_refs=tuple(media_refs),
        text_candidate=text_candidate,
        text_reference=text_reference,
        fallback_text_refs=tuple(fallback_text_refs),
        telegram_part_index=telegram_part_index,
        telegram_part_count=telegram_part_count,
        caption_mode=caption_mode,
        reply_to_unit_id=reply_to_unit_id,
        review_control_names=tuple(review_control_names),
        reuse_existing_controls=reuse_existing_controls,
        internal_only=internal_only,
        warnings=tuple(warnings),
    )
    units.append(unit)
    return unit


def _plan_media_units(
    package: ForwardReadyPackage,
    plan_id: str,
    units: list[FusedDeliveryUnit],
    warnings: list[str],
) -> tuple[list[str], str]:
    eligible, suppressed = _eligible_media(package)
    if suppressed:
        warnings.append("EXACT_MEDIA_DEDUPE_COMPAT")
    last_media_unit = ""
    cursor = 0
    while cursor < len(eligible):
        item = eligible[cursor]
        if not item.album_compatible or item.kind not in {"photo", "video"}:
            unit = _append_unit(
                units, plan_id, "standalone_media", package,
                media_refs=(item.media_id,), warnings=("UNSUPPORTED_ALBUM_COMBINATION_SPLIT",),
            )
            warnings.append("UNSUPPORTED_ALBUM_COMBINATION_SPLIT")
            last_media_unit = unit.unit_id
            cursor += 1
            continue

        run: list[ForwardReadyMediaItem] = []
        while cursor < len(eligible):
            candidate = eligible[cursor]
            if not candidate.album_compatible or candidate.kind not in {"photo", "video"}:
                break
            run.append(candidate)
            cursor += 1
            if len(run) == TELEGRAM_ALBUM_LIMIT:
                break
        refs = tuple(item.media_id for item in run)
        if len(run) == 1:
            kind = "single_photo" if run[0].kind == "photo" else "single_video"
        else:
            kind = "media_album"
        unit = _append_unit(units, plan_id, kind, package, media_refs=refs)
        last_media_unit = unit.unit_id
    return suppressed, last_media_unit


def _fallback_split_parts(package: ForwardReadyPackage, drafts: Mapping[str, Draft]) -> list[tuple[str, int, int]]:
    parts: list[tuple[str, int, int]] = []
    for draft_id in package.text_plan.authoritative_draft_ids:
        draft = drafts.get(draft_id)
        if draft is None or not str(draft.caption or ""):
            continue
        split = split_telegram_text(str(draft.caption))
        for index, _part in enumerate(split):
            parts.append((draft_id, index, len(split)))
    return parts


def _plan_text_units(
    state: Any,
    package: ForwardReadyPackage,
    plan_id: str,
    authority: FusedTextAuthorityPlan,
    units: list[FusedDeliveryUnit],
    warnings: list[str],
    last_media_unit: str,
) -> None:
    if not package.text_plan.forwardable_text_present:
        return
    drafts = _drafts_by_id(state)
    fallback_parts = _fallback_split_parts(package, drafts)
    known_character_count = int(package.text_plan.character_count or 0)
    has_media = bool(last_media_unit)

    # Existing production media sends have no caption authority. This only records
    # a future-safe attachment plan. Overflow always falls back to existing text
    # splitting; no body is truncated or copied into generic state.
    preferred_body_is_current_fallback = (
        authority.preferred_candidate == "current_authoritative_draft_fallback"
    )
    caption_eligible = (
        has_media
        and preferred_body_is_current_fallback
        and known_character_count > 0
        and known_character_count <= TELEGRAM_CAPTION_LIMIT
        and len(fallback_parts) <= 1
        and len(package.text_plan.authoritative_draft_ids) <= 1
    )
    if caption_eligible:
        _append_unit(
            units, plan_id, "caption", package,
            text_candidate=authority.preferred_candidate,
            text_reference=authority.preferred_reference,
            fallback_text_refs=authority.fallback_references,
            telegram_part_index=0,
            telegram_part_count=1,
            caption_mode="attach_to_preceding_media_future_only",
            reply_to_unit_id=last_media_unit,
        )
        return

    if has_media and known_character_count > TELEGRAM_CAPTION_LIMIT:
        warnings.append("CAPTION_OVERFLOW_TO_TEXT")
    if package.text_plan.telegram_split_required:
        warnings.append("TELEGRAM_TEXT_SPLIT_REQUIRED")

    if preferred_body_is_current_fallback and fallback_parts:
        total = len(fallback_parts)
        for global_index, (draft_id, _local_index, _local_total) in enumerate(fallback_parts):
            kind = "text" if global_index == 0 else "continuation_text"
            previous = units[-1].unit_id if global_index else last_media_unit
            _append_unit(
                units, plan_id, kind, package,
                text_candidate=authority.preferred_candidate,
                text_reference=authority.preferred_reference,
                fallback_text_refs=(draft_id,),
                telegram_part_index=global_index,
                telegram_part_count=total,
                reply_to_unit_id=previous,
            )
    else:
        # A semantic fused/style/factual/final candidate is one coherent text plan.
        # Its private body stays in the canonical subsystem. Future authority must
        # resolve that body and re-run the same Telegram splitter before sending.
        _append_unit(
            units, plan_id, "text", package,
            text_candidate=authority.preferred_candidate,
            text_reference=authority.preferred_reference,
            fallback_text_refs=authority.fallback_references,
            telegram_part_index=0,
            telegram_part_count=1,
            reply_to_unit_id=last_media_unit,
            warnings=("BODY_RESOLUTION_REQUIRED_BEFORE_AUTHORITY",),
        )
        warnings.append("BODY_RESOLUTION_REQUIRED_BEFORE_AUTHORITY")


def _plan_review_controls(
    package: ForwardReadyPackage,
    plan_id: str,
    units: list[FusedDeliveryUnit],
) -> None:
    draft_refs = tuple(package.text_plan.authoritative_draft_ids)
    if not draft_refs:
        return
    reply_to = units[-1].unit_id if units else ""
    _append_unit(
        units, plan_id, "review_controls", package,
        fallback_text_refs=draft_refs,
        reply_to_unit_id=reply_to,
        review_control_names=EXISTING_REVIEW_CONTROLS,
        reuse_existing_controls=True,
        internal_only=True,
    )


def _forwardable_summary(units: Sequence[FusedDeliveryUnit]) -> dict[str, Any]:
    media_count = sum(len(unit.media_refs) for unit in units if unit.kind in {
        "media_album", "single_photo", "single_video", "standalone_media",
    })
    return {
        "media_item_count": media_count,
        "has_text": any(unit.kind in {"caption", "text", "continuation_text"} for unit in units),
        "has_review_controls": any(unit.kind == "review_controls" for unit in units),
        "technical_metadata_included": False,
        "debug_identifiers_included": False,
        "provider_errors_included": False,
        "source_health_metadata_included": False,
    }


def plan_fused_private_review_delivery(
    state: Any,
    packages: Iterable[ForwardReadyPackage],
) -> list[FusedPrivateReviewDeliveryPlan]:
    """Derive deterministic shadow plans without mutating durable or transport state."""
    plans: list[FusedPrivateReviewDeliveryPlan] = []
    for package in packages:
        plan_id = make_fused_plan_id(package)
        authority = _text_authority(package)
        readiness, warnings = _map_readiness(package)
        units: list[FusedDeliveryUnit] = []
        suppressed, last_media_unit = _plan_media_units(package, plan_id, units, warnings)
        _plan_text_units(state, package, plan_id, authority, units, warnings, last_media_unit)
        _plan_review_controls(package, plan_id, units)

        warnings = list(dict.fromkeys(warnings))
        if any(len(unit.media_refs) > TELEGRAM_ALBUM_LIMIT for unit in units if unit.kind == "media_album"):
            warnings.append("TELEGRAM_ALBUM_LIMIT_VIOLATION")
            readiness = "NEEDS_REVIEW"
        if any(
            unit.telegram_part_count < 0 or unit.telegram_part_index < 0
            for unit in units if unit.kind in {"caption", "text", "continuation_text"}
        ):
            warnings.append("TELEGRAM_TEXT_PLAN_INVALID")
            readiness = "NEEDS_REVIEW"

        internal = {
            "package_ref": package.package_id,
            "event_ref": package.event_id,
            "segment_ref": package.segment_id,
            "source_update_refs": tuple(package.ordered_update_ids),
            "readiness_evidence": tuple(warnings),
            "plan_version": FUSED_DELIVERY_PLAN_VERSION,
        }
        plans.append(FusedPrivateReviewDeliveryPlan(
            plan_id=plan_id,
            package_id=package.package_id,
            event_id=package.event_id,
            segment_id=package.segment_id,
            ordered_update_ids=tuple(package.ordered_update_ids),
            units=tuple(units),
            text_authority=authority,
            readiness=readiness,
            warnings=tuple(warnings),
            suppressed_exact_duplicate_media_refs=tuple(suppressed),
            internal_review_metadata=internal,
            forwardable_content=_forwardable_summary(units),
        ))
    return plans


def privacy_safe_observation(plan: FusedPrivateReviewDeliveryPlan) -> dict[str, Any]:
    """Metadata safe for logs/Sentry breadcrumbs; never includes private bodies or URLs."""
    media_units = sum(unit.kind in {"media_album", "single_photo", "single_video", "standalone_media"} for unit in plan.units)
    text_units = sum(unit.kind in {"caption", "text", "continuation_text"} for unit in plan.units)
    return {
        "plan_id": plan.plan_id,
        "package_id": plan.package_id,
        "unit_count": len(plan.units),
        "media_unit_count": media_units,
        "text_unit_count": text_units,
        "readiness": plan.readiness,
        "warning_count": len(plan.warnings),
        "plan_version": plan.version,
        "mode": plan.mode,
    }


__all__ = [
    "DELIVERY_UNIT_KINDS",
    "EXISTING_REVIEW_CONTROLS",
    "FUSED_DELIVERY_MODE",
    "FUSED_DELIVERY_PLAN_VERSION",
    "PLAN_READINESS_STATES",
    "TELEGRAM_ALBUM_LIMIT",
    "TELEGRAM_CAPTION_LIMIT",
    "TELEGRAM_TEXT_LIMIT",
    "FutureForwardActionContract",
    "FusedDeliveryUnit",
    "FusedPrivateReviewDeliveryPlan",
    "FusedTextAuthorityPlan",
    "make_delivery_unit_id",
    "make_fused_plan_id",
    "plan_fused_private_review_delivery",
    "privacy_safe_observation",
]
