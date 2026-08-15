"""Preserve bounded Translation Fusion metadata inside the existing Event namespace."""
from __future__ import annotations

from typing import Any


def _bounded(value: object, limit: int) -> str:
    return str(value or "").strip()[:limit]


def _confidence(value: object) -> float:
    try:
        return round(max(0.0, min(1.0, float(value or 0))), 3)
    except (TypeError, ValueError):
        return 0.0


def _strings(value: object, max_items: int, item_limit: int) -> list[str]:
    if not isinstance(value, (list, tuple, set)):
        return []
    return sorted({_bounded(item, item_limit) for item in value if _bounded(item, item_limit)})[:max_items]


def _sanitize_translation(raw: object, translation: Any) -> dict[str, Any]:
    clean = translation._fresh_translation_fields()
    if not isinstance(raw, dict):
        return clean

    events = raw.get("events") if isinstance(raw.get("events"), dict) else {}
    segments = raw.get("segments") if isinstance(raw.get("segments"), dict) else {}
    memberships = raw.get("segment_memberships") if isinstance(raw.get("segment_memberships"), dict) else {}

    evidence: dict[str, dict[str, Any]] = {}
    source = raw.get("translation_evidence")
    if isinstance(source, dict):
        for update_id, value in list(source.items())[-translation.MAX_TRANSLATION_EVIDENCE:]:
            if not isinstance(value, dict):
                continue
            uid = _bounded(value.get("update_id"), 160)
            event_id = _bounded(value.get("event_id"), 80)
            segment_id = _bounded(value.get("segment_id"), 80)
            member = memberships.get(uid)
            if (
                uid != str(update_id)
                or event_id not in events
                or segment_id not in segments
                or not isinstance(member, dict)
                or _bounded(member.get("segment_id"), 80) != segment_id
            ):
                continue
            relationship = _bounded(value.get("relationship"), 40)
            try:
                chronology = max(0, min(int(value.get("chronology_index", 0) or 0), 100000))
            except (TypeError, ValueError):
                chronology = 0
            evidence[uid] = {
                "update_id": uid,
                "source": _bounded(value.get("source"), 80).lstrip("@").casefold(),
                "source_language": _bounded(value.get("source_language"), 24).casefold(),
                "evidence_kind": _bounded(value.get("evidence_kind"), 48),
                "event_id": event_id,
                "segment_id": segment_id,
                "relationship": relationship if relationship in (
                    translation.AUTO_FUSIBLE_RELATIONSHIPS
                    | translation.NON_ADDITIVE_RELATIONSHIPS
                    | translation.BLOCKED_RELATIONSHIPS
                    | translation.CONFLICT_RELATIONSHIPS
                    | {"reclassified", "split_reclassified", "merge_reclassified"}
                ) else "ambiguous",
                "relationship_confidence": _confidence(value.get("relationship_confidence")),
                "chronology_index": chronology,
                "evidence_strength": _confidence(value.get("evidence_strength")),
                "matching_signals": _strings(value.get("matching_signals"), 24, 80),
                "conflicts": _strings(value.get("conflicts"), 24, 80),
                "media_reference_ids": _strings(value.get("media_reference_ids"), 40, 64),
                "source_text_hash": _bounded(value.get("source_text_hash"), 64),
                "candidate_text_hash": _bounded(value.get("candidate_text_hash"), 64),
                "candidate_text_present": bool(value.get("candidate_text_present", False)),
            }

    results: dict[str, dict[str, Any]] = {}
    source = raw.get("translation_fusion_results")
    if isinstance(source, dict):
        for segment_id, value in list(source.items())[-translation.MAX_TRANSLATION_RESULTS:]:
            if not isinstance(value, dict):
                continue
            sid = _bounded(value.get("segment_id"), 80)
            event_id = _bounded(value.get("event_id"), 80)
            if sid != str(segment_id) or sid not in segments or event_id not in events:
                continue
            status = _bounded(value.get("fidelity_status"), 48)
            results[sid] = {
                "event_id": event_id,
                "segment_id": sid,
                "evidence_update_ids": _strings(value.get("evidence_update_ids"), 500, 160),
                "backbone_update_id": _bounded(value.get("backbone_update_id"), 160),
                "complementary_update_ids": _strings(value.get("complementary_update_ids"), 500, 160),
                "conflict_update_ids": _strings(value.get("conflict_update_ids"), 500, 160),
                "source_languages": _strings(value.get("source_languages"), 16, 24),
                "fidelity_status": status if status in translation.FIDELITY_STATUSES else "needs_review",
                "confidence": _confidence(value.get("confidence")),
                "unresolved_conflicts": _strings(value.get("unresolved_conflicts"), 40, 100),
                "review_required": bool(value.get("review_required", True)),
                "reasoning_signals": _strings(value.get("reasoning_signals"), 40, 100),
                "withheld_update_ids": _strings(value.get("withheld_update_ids"), 100, 160),
                "fingerprint": _bounded(value.get("fingerprint"), 80),
                "fused_text_hash": _bounded(value.get("fused_text_hash"), 64),
                "fused_text_present": bool(value.get("fused_text_present", False)),
            }

    decisions = []
    source = raw.get("translation_fusion_decisions")
    if isinstance(source, list):
        for value in source[-translation.MAX_TRANSLATION_DECISIONS:]:
            if not isinstance(value, dict):
                continue
            sid = _bounded(value.get("segment_id"), 80)
            event_id = _bounded(value.get("event_id"), 80)
            if sid not in segments or event_id not in events:
                continue
            status = _bounded(value.get("fidelity_status"), 48)
            decisions.append({
                "event_id": event_id,
                "segment_id": sid,
                "backbone_update_id": _bounded(value.get("backbone_update_id"), 160),
                "evidence_update_ids": _strings(value.get("evidence_update_ids"), 500, 160),
                "complementary_update_ids": _strings(value.get("complementary_update_ids"), 500, 160),
                "conflict_update_ids": _strings(value.get("conflict_update_ids"), 500, 160),
                "fidelity_status": status if status in translation.FIDELITY_STATUSES else "needs_review",
                "review_required": bool(value.get("review_required", True)),
                "confidence": _confidence(value.get("confidence")),
                "fingerprint": _bounded(value.get("fingerprint"), 80),
            })

    clean.update({
        "translation_evidence": evidence,
        "translation_fusion_results": results,
        "translation_fusion_decisions": decisions,
    })
    return clean


def install(event_fusion: Any, translation: Any) -> None:
    """Extend existing Event/Timeline sanitizer and pruner without a schema migration."""
    current_sanitize = event_fusion._sanitize_event_state
    if not getattr(current_sanitize, "_translation_fusion_state_compat_installed", False):
        def sanitize(raw):
            clean = current_sanitize(raw)
            clean.update(_sanitize_translation(raw, translation))
            return clean
        sanitize._translation_fusion_state_compat_installed = True
        event_fusion._sanitize_event_state = sanitize

    current_prune = event_fusion._prune
    if not getattr(current_prune, "_translation_fusion_state_compat_installed", False):
        def prune(event_state):
            current_prune(event_state)
            translation._prune_translation(event_state)
        prune._translation_fusion_state_compat_installed = True
        event_fusion._prune = prune
