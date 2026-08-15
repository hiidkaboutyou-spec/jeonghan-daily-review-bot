"""Preserve bounded Timeline metadata inside the existing Event Fusion namespace.

This extension never changes the top-level StateStore schema version. It only teaches
Event Fusion's sanitizer/pruner how to retain shadow Segment metadata.
"""
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


def _sanitize_timeline(raw: object, timeline: Any) -> dict[str, Any]:
    clean = timeline._fresh_timeline_fields()
    if not isinstance(raw, dict):
        return clean

    events = raw.get("events")
    event_ids = {
        str(key) for key, value in events.items()
        if isinstance(events, dict) and isinstance(value, dict) and str(key).startswith("evt:")
    } if isinstance(events, dict) else set()

    fingerprints: dict[str, dict[str, Any]] = {}
    source = raw.get("timeline_fingerprints")
    if isinstance(source, dict):
        for update_id, value in list(source.items())[-timeline.MAX_TIMELINE_FINGERPRINTS:]:
            if not isinstance(value, dict):
                continue
            fp = timeline.SegmentFingerprint.from_dict(value)
            if fp.update_id == str(update_id) and fp.event_id in event_ids:
                fingerprints[fp.update_id] = fp.to_dict()

    segments: dict[str, dict[str, Any]] = {}
    source = raw.get("segments")
    if isinstance(source, dict):
        for segment_id, value in list(source.items())[-timeline.MAX_SEGMENTS:]:
            if not isinstance(value, dict):
                continue
            sid = _bounded(value.get("segment_id"), 80)
            event_id = _bounded(value.get("event_id"), 80)
            members = _strings(value.get("member_update_ids"), 500, 160)
            if sid != str(segment_id) or not sid.startswith("seg:") or event_id not in event_ids or not members:
                continue
            evidence = value.get("order_evidence") if isinstance(value.get("order_evidence"), dict) else {}
            try:
                order_index = max(0, min(int(value.get("order_index", 0) or 0), 100000))
            except (TypeError, ValueError):
                order_index = 0
            raw_order_value = evidence.get("value")
            order_value = raw_order_value if isinstance(raw_order_value, (int, float)) else _bounded(raw_order_value, 120)
            segments[sid] = {
                "segment_id": sid, "event_id": event_id,
                "created_at": _bounded(value.get("created_at"), 80),
                "updated_at": _bounded(value.get("updated_at"), 80),
                "member_update_ids": members, "confidence": _confidence(value.get("confidence")),
                "status": "shadow_candidate", "order_index": order_index,
                "order_evidence": {
                    "kind": _bounded(evidence.get("kind"), 48), "value": order_value,
                    "confidence": _bounded(evidence.get("confidence"), 40),
                },
            }

    event_memberships = raw.get("memberships") if isinstance(raw.get("memberships"), dict) else {}
    memberships: dict[str, dict[str, Any]] = {}
    source = raw.get("segment_memberships")
    if isinstance(source, dict):
        for update_id, value in source.items():
            if not isinstance(value, dict):
                continue
            sid = _bounded(value.get("segment_id"), 80)
            event_id = _bounded(value.get("event_id"), 80)
            segment = segments.get(sid)
            event_row = event_memberships.get(str(update_id), {})
            canonical_event_id = _bounded(event_row.get("event_id"), 80) if isinstance(event_row, dict) else ""
            if (
                not segment or event_id != str(segment.get("event_id", ""))
                or canonical_event_id != event_id
                or str(update_id) not in segment.get("member_update_ids", [])
            ):
                continue
            memberships[str(update_id)] = {
                "event_id": event_id, "segment_id": sid,
                "confidence": _confidence(value.get("confidence")),
                "relationship": _bounded(value.get("relationship"), 40) or "ambiguous",
                "matching_signals": _strings(value.get("matching_signals"), 24, 80),
                "conflicts": _strings(value.get("conflicts"), 24, 80),
                "updated_at": _bounded(value.get("updated_at"), 80),
            }

    relationships: dict[str, dict[str, Any]] = {}
    source = raw.get("segment_relationships")
    if isinstance(source, dict):
        for relationship_id, value in list(source.items())[-timeline.MAX_SEGMENT_RELATIONSHIPS:]:
            if not isinstance(value, dict):
                continue
            rid = _bounded(value.get("relationship_id"), 80)
            event_id = _bounded(value.get("event_id"), 80)
            left = _bounded(value.get("left_update_id"), 160)
            right = _bounded(value.get("right_update_id"), 160)
            relationship = _bounded(value.get("relationship"), 40)
            if (
                rid != str(relationship_id) or not rid.startswith("rel:")
                or event_id not in event_ids or not left or not right
                or relationship not in timeline.RELATIONSHIPS
            ):
                continue
            left_segment = _bounded(value.get("left_segment_id"), 80)
            right_segment = _bounded(value.get("right_segment_id"), 80)
            relationships[rid] = {
                "relationship_id": rid, "event_id": event_id,
                "left_update_id": left, "right_update_id": right,
                "left_segment_id": left_segment if left_segment in segments else "",
                "right_segment_id": right_segment if right_segment in segments else "",
                "relationship": relationship, "confidence": _confidence(value.get("confidence")),
                "matching_signals": _strings(value.get("matching_signals"), 24, 80),
                "conflicts": _strings(value.get("conflicts"), 24, 80),
                "status": "unresolved" if relationship in {"conflicting", "ambiguous"} else "observed",
                "updated_at": _bounded(value.get("updated_at"), 80),
            }

    decisions: list[dict[str, Any]] = []
    source = raw.get("timeline_decisions")
    if isinstance(source, list):
        for value in source[-timeline.MAX_TIMELINE_DECISIONS:]:
            if not isinstance(value, dict):
                continue
            decisions.append({
                "decision": _bounded(value.get("decision"), 48),
                "event_id": _bounded(value.get("event_id"), 80),
                "segment_id": _bounded(value.get("segment_id"), 80),
                "update_id": _bounded(value.get("update_id"), 160),
                "candidate_update_id": _bounded(value.get("candidate_update_id"), 160),
                "relationship": _bounded(value.get("relationship"), 40),
                "confidence": _confidence(value.get("confidence")),
                "matching_signals": _strings(value.get("matching_signals"), 24, 80),
                "conflicts": _strings(value.get("conflicts"), 24, 80),
                "created_at": _bounded(value.get("created_at"), 80),
            })

    clean.update({
        "segments": segments, "segment_memberships": memberships,
        "timeline_fingerprints": fingerprints, "segment_relationships": relationships,
        "timeline_decisions": decisions,
    })
    return clean


def install(event_fusion: Any, timeline: Any) -> None:
    """Extend Event Fusion sanitizer/pruner without changing StateStore schema."""
    current_sanitize = event_fusion._sanitize_event_state
    if not getattr(current_sanitize, "_event_timeline_state_compat_installed", False):
        def sanitize(raw):
            clean = current_sanitize(raw)
            clean.update(_sanitize_timeline(raw, timeline))
            return clean
        sanitize._event_timeline_state_compat_installed = True
        event_fusion._sanitize_event_state = sanitize

    current_prune = event_fusion._prune
    if not getattr(current_prune, "_event_timeline_state_compat_installed", False):
        def prune(event_state):
            current_prune(event_state)
            timeline._prune_timeline(event_state)
        prune._event_timeline_state_compat_installed = True
        event_fusion._prune = prune
