"""Bound and sanitize Channel Style Rewrite shadow metadata in Event durable state."""
from __future__ import annotations

from typing import Any


def _bounded(value: object, limit: int) -> str:
    return str(value or "").strip()[:limit]


def _strings(value: object, max_items: int, item_limit: int) -> list[str]:
    if not isinstance(value, (list, tuple, set)):
        return []
    return [_bounded(item, item_limit) for item in list(value)[:max_items] if _bounded(item, item_limit)]


def _score(value: object) -> float:
    try:
        return round(max(0.0, min(1.0, float(value or 0))), 4)
    except (TypeError, ValueError):
        return 0.0


def _sanitize_style(raw: object, rewrite: Any) -> dict[str, Any]:
    clean = rewrite._fresh_style_fields()
    if not isinstance(raw, dict):
        return clean
    events = raw.get("events") if isinstance(raw.get("events"), dict) else {}
    segments = raw.get("segments") if isinstance(raw.get("segments"), dict) else {}
    source = raw.get("style_rewrite_results")
    results: dict[str, dict[str, Any]] = {}
    if isinstance(source, dict):
        for segment_id, value in list(source.items())[-rewrite.MAX_STYLE_RESULTS:]:
            if not isinstance(value, dict):
                continue
            sid = _bounded(value.get("segment_id"), 80)
            eid = _bounded(value.get("event_id"), 80)
            if sid != str(segment_id) or sid not in segments or eid not in events:
                continue
            results[sid] = {
                "event_id": eid,
                "segment_id": sid,
                "content_type": _bounded(value.get("content_type"), 48),
                "style_profile": _bounded(value.get("style_profile"), 80),
                "selected_style_example_ids": _strings(
                    value.get("selected_style_example_ids"),
                    rewrite.MAX_STYLE_EXAMPLES,
                    160,
                ),
                "factual_draft_fingerprint": _bounded(value.get("factual_draft_fingerprint"), 80),
                "style_candidate_fingerprint": _bounded(value.get("style_candidate_fingerprint"), 80),
                "fidelity_passed": bool(value.get("fidelity_passed", False)),
                "fidelity_failures": _strings(value.get("fidelity_failures"), 20, 120),
                "style_score": _score(value.get("style_score")),
                "accepted": bool(value.get("accepted", False)),
                "fallback_reason": _bounded(value.get("fallback_reason"), 80),
                "review_required": bool(value.get("review_required", True)),
                "provider": _bounded(value.get("provider"), 48),
                "mode": rewrite.STYLE_REWRITE_MODE,
                "text_persisted": False,
            }
    clean["style_rewrite_results"] = results
    return clean


def install(event_fusion: Any, rewrite: Any) -> None:
    """Extend Event/Timeline/Translation sanitizer without a DB or schema migration."""
    current_sanitize = event_fusion._sanitize_event_state
    if not getattr(current_sanitize, "_channel_style_rewrite_state_compat_installed", False):
        def sanitize(raw):
            clean = current_sanitize(raw)
            clean.update(_sanitize_style(raw, rewrite))
            return clean
        sanitize._channel_style_rewrite_state_compat_installed = True
        event_fusion._sanitize_event_state = sanitize

    current_prune = event_fusion._prune
    if not getattr(current_prune, "_channel_style_rewrite_state_compat_installed", False):
        def prune(event_state):
            current_prune(event_state)
            rewrite._prune_style(event_state)
        prune._channel_style_rewrite_state_compat_installed = True
        event_fusion._prune = prune
