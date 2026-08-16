"""Shadow-only Forward-ready private-review package planning.

Packages are bounded, recomputable presentation metadata over canonical evidence.
They never own Updates, Events, Segments, text/media bodies, delivery receipts,
completeness, or lifecycle state and never perform Telegram actions.
"""
from __future__ import annotations

import hashlib
from dataclasses import asdict, dataclass, field
from typing import Any, Iterable, Mapping

from .media_delivery import MediaDeliveryLedger
from .models import Draft, MediaItem, Update, ensure_utc
from .telegram import TELEGRAM_TEXT_LIMIT, split_telegram_text

FORWARD_READY_VERSION = 1
FORWARD_READY_MODE = "shadow"
TELEGRAM_ALBUM_LIMIT = 10
MAX_PACKAGES = 2000

READINESS_STATES = frozenset({
    "READY", "READY_WITH_WARNINGS", "NEEDS_REVIEW", "BLOCKED_FIDELITY",
    "BLOCKED_CONFLICT", "READY_TEXT_MEDIA_INCOMPLETE", "MEDIA_ONLY",
})


def _fresh_forward_ready_fields() -> dict[str, Any]:
    return {
        "forward_ready_version": FORWARD_READY_VERSION,
        "forward_ready_mode": FORWARD_READY_MODE,
        "forward_ready_packages": {},
    }


def _prune_forward_ready(event_state: dict[str, Any]) -> None:
    packages = event_state.get("forward_ready_packages")
    if not isinstance(packages, dict):
        event_state["forward_ready_packages"] = {}
    elif len(packages) > MAX_PACKAGES:
        event_state["forward_ready_packages"] = dict(list(packages.items())[-MAX_PACKAGES:])


def _hash(namespace: str, *parts: object, length: int = 24) -> str:
    raw = "\x1f".join(str(part or "") for part in parts)
    return hashlib.sha256(f"{namespace}\x1f{raw}".encode("utf-8")).hexdigest()[:length]


def make_package_id(context_kind: str, context_id: str, update_ids: Iterable[str]) -> str:
    """Return a dedicated identity; canonical entity IDs remain untouched."""
    members = sorted({str(item) for item in update_ids if str(item)})
    if not members:
        raise ValueError("ForwardReadyPackage requires canonical Update references")
    return f"frp:{_hash('forward-ready-package-v1', context_kind, context_id, *members)}"


@dataclass(frozen=True, slots=True)
class ForwardReadyTextPlan:
    authoritative_draft_id: str = ""
    authoritative_draft_ids: tuple[str, ...] = ()
    authoritative_draft_fingerprint: str = ""
    faithful_factual_fingerprint: str = ""
    channel_style_candidate_fingerprint: str = ""
    direct_style_rule_id: str = ""
    direct_style_applied: bool = False
    confirmed_final_edit_id: str = ""
    confirmed_final_edit_fingerprint: str = ""
    future_preferred_candidate: str = "faithful_factual"
    preference_reason: str = "faithful_factual_fallback"
    current_authority: str = "authoritative_review_draft"
    authority_activated: bool = False
    forwardable_text_present: bool = False
    character_count: int = 0
    telegram_part_count: int = 0
    telegram_split_required: bool = False


@dataclass(frozen=True, slots=True)
class ForwardReadyMediaItem:
    media_id: str
    update_id: str
    event_id: str
    segment_id: str
    kind: str
    update_order: int
    source_media_order: int
    package_order: int
    album_compatible: bool
    exact_duplicate_of: str = ""
    delivery_disposition: str = "eligible"


@dataclass(frozen=True, slots=True)
class ForwardReadyMediaPlan:
    items: tuple[ForwardReadyMediaItem, ...] = ()
    telegram_batches: tuple[tuple[str, ...], ...] = ()
    send_before_text: bool = True
    preparation_status: str = "not_required"
    distinct_media_count: int = 0
    exact_duplicate_count: int = 0
    bytes_persisted: bool = False


@dataclass(frozen=True, slots=True)
class ForwardReadyPresentationPlan:
    order: tuple[str, ...] = ("media", "text", "existing_review_actions")
    concise_indicator: str = "Needs review"
    existing_review_actions_only: bool = True
    auto_forward: bool = False
    public_publish: bool = False
    reply_thread_required: bool = False


@dataclass(frozen=True, slots=True)
class ForwardReadyPackage:
    package_id: str
    context_kind: str
    event_id: str
    segment_id: str
    ordered_update_ids: tuple[str, ...]
    text_plan: ForwardReadyTextPlan
    media_plan: ForwardReadyMediaPlan
    presentation_plan: ForwardReadyPresentationPlan
    readiness_status: str
    warnings: tuple[str, ...] = ()
    internal_review_metadata: Mapping[str, Any] = field(default_factory=dict)
    forwardable_content: Mapping[str, Any] = field(default_factory=dict)
    mode: str = FORWARD_READY_MODE
    version: int = FORWARD_READY_VERSION

    def metadata(self) -> dict[str, Any]:
        """Bounded persistence form: identifiers/fingerprints only, never bodies."""
        return asdict(self)


def _drafts_by_update(state: Any) -> dict[str, Draft]:
    result: dict[str, Draft] = {}
    drafts = state.data.get("drafts", {})
    if not isinstance(drafts, dict):
        return result
    for raw in drafts.values():
        if not isinstance(raw, dict):
            continue
        try:
            draft = Draft.from_dict(raw)
        except (TypeError, ValueError):
            continue
        previous = result.get(draft.update_id)
        if previous is None or draft.created_at >= previous.created_at:
            result[draft.update_id] = draft
    return result


def _active_final_edit(final_edit_store: Any, draft_id: str) -> Any | None:
    if final_edit_store is None or not draft_id:
        return None
    try:
        return final_edit_store.latest_active(draft_id)
    except Exception:
        return None


def _text_plan(
    member_ids: tuple[str, ...],
    fusion: Mapping[str, Any],
    segment_id: str,
    drafts: Mapping[str, Draft],
    final_edit_store: Any,
) -> ForwardReadyTextPlan:
    member_drafts = [drafts[item] for item in member_ids if item in drafts]
    draft = member_drafts[0] if member_drafts else None
    style_rows = fusion.get("style_rewrite_results", {})
    style = style_rows.get(segment_id, {}) if isinstance(style_rows, dict) else {}
    style = style if isinstance(style, dict) else {}
    factual_fp = str(style.get("factual_draft_fingerprint", ""))[:80]
    style_fp = str(style.get("style_candidate_fingerprint", ""))[:80]
    direct_applied = bool(style.get("direct_style_applied", False)) and bool(style.get("accepted", False))
    finals = [
        candidate for candidate in (
            _active_final_edit(final_edit_store, item.id) for item in member_drafts
        ) if candidate is not None
    ]
    final = finals[0] if finals else None
    if final is not None:
        preferred, reason = "confirmed_final_edit", "genuinely_confirmed_private_user_edit"
    elif style_fp and bool(style.get("accepted", False)) and bool(style.get("fidelity_passed", False)):
        preferred = "direct_style_candidate" if direct_applied else "channel_style_candidate"
        reason = "fidelity_safe_shadow_candidate"
    else:
        preferred, reason = "faithful_factual", "faithful_factual_fallback"
    captions = [str(item.caption or "") for item in member_drafts]
    caption = captions[0] if captions else ""
    count = sum(len(item) for item in captions)
    return ForwardReadyTextPlan(
        authoritative_draft_id=draft.id if draft else "",
        authoritative_draft_ids=tuple(item.id for item in member_drafts),
        authoritative_draft_fingerprint=_hash("authoritative-review-draft-v1", caption, length=64) if caption else "",
        faithful_factual_fingerprint=factual_fp,
        channel_style_candidate_fingerprint=style_fp,
        direct_style_rule_id=str(style.get("direct_style_rule_id", ""))[:80],
        direct_style_applied=direct_applied,
        confirmed_final_edit_id=str(getattr(final, "final_edit_id", ""))[:80],
        confirmed_final_edit_fingerprint=str(getattr(final, "final_user_edit_fingerprint", ""))[:80],
        future_preferred_candidate=preferred,
        preference_reason=reason,
        forwardable_text_present=bool(caption or factual_fp or style_fp or final),
        character_count=count,
        telegram_part_count=sum(len(split_telegram_text(item)) for item in captions if item),
        telegram_split_required=any(len(split_telegram_text(item)) > 1 for item in captions),
    )


def _media_plan(
    updates: list[Update], event_id: str, segment_id: str,
    lifecycle: Mapping[str, Any], exact_duplicates: Mapping[str, str],
) -> ForwardReadyMediaPlan:
    items: list[ForwardReadyMediaItem] = []
    first_by_identity: dict[str, str] = {}
    for update_order, update in enumerate(updates):
        for source_order, media in enumerate(update.media):
            media_id = MediaDeliveryLedger.url_identity(media)
            duplicate_of = str(exact_duplicates.get(media_id, "")) or first_by_identity.get(media_id, "")
            items.append(ForwardReadyMediaItem(
                media_id=media_id, update_id=update.id, event_id=event_id,
                segment_id=segment_id, kind=media.kind,
                update_order=update_order, source_media_order=source_order,
                package_order=len(items), album_compatible=media.kind in {"photo", "video"},
                exact_duplicate_of=duplicate_of,
                delivery_disposition="existing_exact_dedupe" if duplicate_of else "eligible",
            ))
            first_by_identity.setdefault(media_id, media_id)
    batches = tuple(
        tuple(item.media_id for item in items[offset:offset + TELEGRAM_ALBUM_LIMIT])
        for offset in range(0, len(items), TELEGRAM_ALBUM_LIMIT)
    )
    statuses = {
        str((lifecycle.get(update.id) or {}).get("media_status", ""))
        for update in updates if isinstance(lifecycle.get(update.id), dict)
    }
    if statuses & {"terminal_failed", "partial_failed"}:
        preparation = "incomplete"
    elif items:
        preparation = "planned"
    else:
        preparation = "not_required"
    duplicate_count = sum(bool(item.exact_duplicate_of) for item in items)
    return ForwardReadyMediaPlan(
        items=tuple(items), telegram_batches=batches, preparation_status=preparation,
        distinct_media_count=len(items) - duplicate_count,
        exact_duplicate_count=duplicate_count,
    )


def _readiness(
    text: ForwardReadyTextPlan, media: ForwardReadyMediaPlan,
    fusion: Mapping[str, Any], segment_id: str, lifecycle_rows: Iterable[Mapping[str, Any]],
) -> tuple[str, tuple[str, ...]]:
    warnings: list[str] = []
    translation_rows = fusion.get("translation_fusion_results", {})
    translation = translation_rows.get(segment_id, {}) if isinstance(translation_rows, dict) else {}
    translation = translation if isinstance(translation, dict) else {}
    conflicts = translation.get("conflict_update_ids") or translation.get("unresolved_conflicts")
    fidelity = str(translation.get("fidelity_status", ""))
    if conflicts:
        return "BLOCKED_CONFLICT", ("CONFLICT_UNRESOLVED",)
    if fidelity and fidelity != "faithful_shadow_candidate":
        return "BLOCKED_FIDELITY", ("TRANSLATION_FIDELITY_NOT_READY",)
    rows = list(lifecycle_rows)
    if any(str(row.get("retrieval_status", "")) == "partial_source_window" for row in rows):
        warnings.append("PARTIAL_COVERAGE")
    if text.telegram_split_required:
        warnings.append("TELEGRAM_TEXT_SPLIT_REQUIRED")
    if media.preparation_status == "incomplete":
        warnings.append("MEDIA_INCOMPLETE")
        return "READY_TEXT_MEDIA_INCOMPLETE" if text.forwardable_text_present else "NEEDS_REVIEW", tuple(warnings)
    if not text.forwardable_text_present and media.items:
        return "MEDIA_ONLY", tuple(warnings)
    if not text.forwardable_text_present:
        return "NEEDS_REVIEW", tuple(warnings or ["FORWARDABLE_TEXT_NOT_READY"])
    if warnings:
        return "READY_WITH_WARNINGS", tuple(warnings)
    return "READY", ()


def _indicator(status: str) -> str:
    return {
        "READY": "Ready to forward", "READY_WITH_WARNINGS": "Ready with warning",
        "MEDIA_ONLY": "Media ready", "READY_TEXT_MEDIA_INCOMPLETE": "Media incomplete",
        "BLOCKED_FIDELITY": "Fidelity warning", "BLOCKED_CONFLICT": "Conflict unresolved",
    }.get(status, "Needs review")


def plan_forward_ready_packages(
    state: Any,
    updates: Iterable[Update],
    *,
    final_edit_store: Any = None,
    exact_duplicates: Mapping[str, str] | None = None,
    persist: bool = True,
) -> list[ForwardReadyPackage]:
    """Plan packages strictly from existing Segment membership or standalone Updates."""
    incoming = sorted(list(updates), key=lambda item: (ensure_utc(item.created_at), str(item.id)))
    fusion = state.data.get("event_fusion")
    fusion = fusion if isinstance(fusion, dict) else {}
    memberships = fusion.get("segment_memberships")
    memberships = memberships if isinstance(memberships, dict) else {}
    lifecycle = state.data.get("update_lifecycle")
    lifecycle = lifecycle if isinstance(lifecycle, dict) else {}
    drafts = _drafts_by_update(state)
    groups: dict[tuple[str, str], list[Update]] = {}
    for update in incoming:
        member = memberships.get(update.id)
        if isinstance(member, dict) and member.get("segment_id") and member.get("event_id"):
            key = (str(member["event_id"]), str(member["segment_id"]))
        else:
            key = ("", f"standalone:{update.id}")
        groups.setdefault(key, []).append(update)

    packages: list[ForwardReadyPackage] = []
    for (event_id, segment_key), members in groups.items():
        members.sort(key=lambda item: (ensure_utc(item.created_at), str(item.id)))
        member_ids = tuple(item.id for item in members)
        segment_id = segment_key if not segment_key.startswith("standalone:") else ""
        context_kind = "segment" if segment_id else "standalone_update"
        context_id = segment_id or member_ids[0]
        text_plan = _text_plan(member_ids, fusion, segment_id, drafts, final_edit_store)
        media_plan = _media_plan(members, event_id, segment_id, lifecycle, exact_duplicates or {})
        status, warnings = _readiness(
            text_plan, media_plan, fusion, segment_id,
            (lifecycle.get(item, {}) for item in member_ids if isinstance(lifecycle.get(item, {}), dict)),
        )
        package = ForwardReadyPackage(
            package_id=make_package_id(context_kind, context_id, member_ids),
            context_kind=context_kind, event_id=event_id, segment_id=segment_id,
            ordered_update_ids=member_ids, text_plan=text_plan, media_plan=media_plan,
            presentation_plan=ForwardReadyPresentationPlan(concise_indicator=_indicator(status)),
            readiness_status=status, warnings=warnings,
            internal_review_metadata={
                "source_update_refs": member_ids,
                "event_ref": event_id,
                "segment_ref": segment_id,
                "readiness_evidence": warnings,
                "debug_visible_in_forwardable_content": False,
            },
            forwardable_content={
                "text_reference": text_plan.authoritative_draft_id or text_plan.future_preferred_candidate,
                "ordered_text_references": text_plan.authoritative_draft_ids,
                "ordered_media_references": tuple(item.media_id for item in media_plan.items),
                "technical_metadata_included": False,
            },
        )
        packages.append(package)

    if persist and isinstance(fusion, dict):
        fusion["forward_ready_version"] = FORWARD_READY_VERSION
        fusion["forward_ready_mode"] = FORWARD_READY_MODE
        incoming_ids = {item.id for item in incoming}
        existing = fusion.get("forward_ready_packages")
        retained: dict[str, Any] = {}
        if isinstance(existing, dict):
            for package_id, metadata in existing.items():
                member_ids = metadata.get("ordered_update_ids", ()) if isinstance(metadata, dict) else ()
                if not incoming_ids.intersection(str(item) for item in member_ids):
                    retained[str(package_id)] = metadata
        retained.update({package.package_id: package.metadata() for package in packages})
        fusion["forward_ready_packages"] = dict(list(retained.items())[-MAX_PACKAGES:])
    return packages


__all__ = [
    "FORWARD_READY_MODE", "FORWARD_READY_VERSION", "READINESS_STATES",
    "ForwardReadyMediaItem", "ForwardReadyMediaPlan", "ForwardReadyPackage",
    "ForwardReadyPresentationPlan", "ForwardReadyTextPlan", "make_package_id",
    "plan_forward_ready_packages",
]
