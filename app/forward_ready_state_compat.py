"""Bound Forward-ready metadata inside the existing Event Fusion namespace."""
from __future__ import annotations

from typing import Any


def _text(value: object, limit: int) -> str:
    return str(value or "")[:limit]


def _strings(value: object, count: int, limit: int) -> list[str]:
    if not isinstance(value, (list, tuple)):
        return []
    return [_text(item, limit) for item in list(value)[:count] if _text(item, limit)]


def _integer(value: object, maximum: int = 100000) -> int:
    try:
        return max(0, min(int(value or 0), maximum))
    except (TypeError, ValueError):
        return 0


def _sanitize(raw: object, planner: Any) -> dict[str, Any]:
    clean = planner._fresh_forward_ready_fields()
    if not isinstance(raw, dict):
        return clean
    events = raw.get("events") if isinstance(raw.get("events"), dict) else {}
    segments = raw.get("segments") if isinstance(raw.get("segments"), dict) else {}
    memberships = raw.get("segment_memberships") if isinstance(raw.get("segment_memberships"), dict) else {}
    source = raw.get("forward_ready_packages")
    packages: dict[str, dict[str, Any]] = {}
    if not isinstance(source, dict):
        return clean
    for package_id, value in list(source.items())[-planner.MAX_PACKAGES:]:
        if not isinstance(value, dict) or not str(package_id).startswith("frp:"):
            continue
        status = _text(value.get("readiness_status"), 48)
        context_kind = _text(value.get("context_kind"), 40)
        event_id = _text(value.get("event_id"), 80)
        segment_id = _text(value.get("segment_id"), 80)
        update_ids = _strings(value.get("ordered_update_ids"), 500, 160)
        if not update_ids or status not in planner.READINESS_STATES:
            continue
        if context_kind not in {"segment", "standalone_update"}:
            continue
        if context_kind == "segment":
            segment = segments.get(segment_id)
            if not isinstance(segment, dict) or event_id not in events or _text(segment.get("event_id"), 80) != event_id:
                continue
            canonical_members = set(_strings(segment.get("member_update_ids"), 500, 160))
            if not set(update_ids).issubset(canonical_members):
                continue
            if any(
                not isinstance(memberships.get(update_id), dict)
                or _text(memberships[update_id].get("event_id"), 80) != event_id
                or _text(memberships[update_id].get("segment_id"), 80) != segment_id
                for update_id in update_ids
            ):
                continue
            context_id = segment_id
        else:
            if len(update_ids) != 1 or event_id or segment_id:
                continue
            context_id = update_ids[0]
        if str(package_id) != planner.make_package_id(context_kind, context_id, update_ids):
            continue
        text_plan = value.get("text_plan") if isinstance(value.get("text_plan"), dict) else {}
        media_plan = value.get("media_plan") if isinstance(value.get("media_plan"), dict) else {}
        presentation = value.get("presentation_plan") if isinstance(value.get("presentation_plan"), dict) else {}
        media_items = []
        for item in list(media_plan.get("items", []))[:1000] if isinstance(media_plan.get("items"), (list, tuple)) else []:
            if not isinstance(item, dict):
                continue
            media_id = _text(item.get("media_id"), 80)
            update_id = _text(item.get("update_id"), 160)
            if not media_id.startswith("url:") or update_id not in update_ids:
                continue
            media_items.append({
                "media_id": media_id, "update_id": update_id,
                "event_id": event_id, "segment_id": segment_id,
                "kind": _text(item.get("kind"), 24),
                "update_order": _integer(item.get("update_order")),
                "source_media_order": _integer(item.get("source_media_order")),
                "package_order": _integer(item.get("package_order")),
                "album_compatible": bool(item.get("album_compatible", False)),
                "exact_duplicate_of": _text(item.get("exact_duplicate_of"), 80),
                "delivery_disposition": _text(item.get("delivery_disposition"), 40),
            })
        packages[str(package_id)] = {
            "package_id": str(package_id), "context_kind": context_kind,
            "event_id": event_id, "segment_id": segment_id,
            "ordered_update_ids": update_ids,
            "text_plan": {
                "authoritative_draft_id": _text(text_plan.get("authoritative_draft_id"), 80),
                "authoritative_draft_ids": _strings(text_plan.get("authoritative_draft_ids"), 500, 80),
                "authoritative_draft_fingerprint": _text(text_plan.get("authoritative_draft_fingerprint"), 80),
                "faithful_factual_fingerprint": _text(text_plan.get("faithful_factual_fingerprint"), 80),
                "channel_style_candidate_fingerprint": _text(text_plan.get("channel_style_candidate_fingerprint"), 80),
                "direct_style_rule_id": _text(text_plan.get("direct_style_rule_id"), 80),
                "direct_style_applied": bool(text_plan.get("direct_style_applied", False)),
                "confirmed_final_edit_id": _text(text_plan.get("confirmed_final_edit_id"), 80),
                "confirmed_final_edit_fingerprint": _text(text_plan.get("confirmed_final_edit_fingerprint"), 80),
                "future_preferred_candidate": _text(text_plan.get("future_preferred_candidate"), 48),
                "preference_reason": _text(text_plan.get("preference_reason"), 80),
                "current_authority": "authoritative_review_draft",
                "authority_activated": False,
                "forwardable_text_present": bool(text_plan.get("forwardable_text_present", False)),
                "character_count": _integer(text_plan.get("character_count"), 10000000),
                "telegram_part_count": _integer(text_plan.get("telegram_part_count"), 10000),
                "telegram_split_required": bool(text_plan.get("telegram_split_required", False)),
            },
            "media_plan": {
                "items": media_items,
                "telegram_batches": [
                    _strings(batch, planner.TELEGRAM_ALBUM_LIMIT, 80)
                    for batch in list(media_plan.get("telegram_batches", []))[:100]
                    if isinstance(batch, (list, tuple))
                ],
                "send_before_text": True,
                "preparation_status": _text(media_plan.get("preparation_status"), 32),
                "distinct_media_count": _integer(media_plan.get("distinct_media_count"), 1000),
                "exact_duplicate_count": _integer(media_plan.get("exact_duplicate_count"), 1000),
                "bytes_persisted": False,
            },
            "presentation_plan": {
                "order": ["media", "text", "existing_review_actions"],
                "concise_indicator": _text(presentation.get("concise_indicator"), 40),
                "existing_review_actions_only": True,
                "auto_forward": False, "public_publish": False,
                "reply_thread_required": bool(presentation.get("reply_thread_required", False)),
            },
            "readiness_status": status,
            "warnings": _strings(value.get("warnings"), 20, 80),
            "internal_review_metadata": {
                "source_update_refs": update_ids, "event_ref": event_id, "segment_ref": segment_id,
                "readiness_evidence": _strings(value.get("warnings"), 20, 80),
                "debug_visible_in_forwardable_content": False,
            },
            "forwardable_content": {
                "text_reference": _text((value.get("forwardable_content") or {}).get("text_reference") if isinstance(value.get("forwardable_content"), dict) else "", 80),
                "ordered_text_references": _strings((value.get("forwardable_content") or {}).get("ordered_text_references") if isinstance(value.get("forwardable_content"), dict) else [], 500, 80),
                "ordered_media_references": [item["media_id"] for item in media_items],
                "technical_metadata_included": False,
            },
            "mode": planner.FORWARD_READY_MODE, "version": planner.FORWARD_READY_VERSION,
        }
    clean["forward_ready_packages"] = packages
    return clean


def install(event_fusion: Any, planner: Any) -> None:
    current_sanitize = event_fusion._sanitize_event_state
    if not getattr(current_sanitize, "_forward_ready_state_compat_installed", False):
        def sanitize(raw):
            clean = current_sanitize(raw)
            clean.update(_sanitize(raw, planner))
            return clean
        sanitize._forward_ready_state_compat_installed = True
        event_fusion._sanitize_event_state = sanitize

    current_prune = event_fusion._prune
    if not getattr(current_prune, "_forward_ready_state_compat_installed", False):
        def prune(event_state):
            current_prune(event_state)
            planner._prune_forward_ready(event_state)
        prune._forward_ready_state_compat_installed = True
        event_fusion._prune = prune


__all__ = ["install"]
