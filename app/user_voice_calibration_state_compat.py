"""Bound/sanitize shadow user-voice calibration metadata in existing Event state."""
from __future__ import annotations

from typing import Any, Mapping


def _bounded(value: object, limit: int) -> str:
    return str(value or "").strip()[:limit]


def _bool(value: object) -> bool:
    return bool(value)


def _score(value: object) -> float:
    try:
        return round(max(0.0, min(1.0, float(value or 0.0))), 4)
    except (TypeError, ValueError):
        return 0.0


def _signed(value: object, limit: float = 2.0) -> float:
    try:
        return round(max(-limit, min(limit, float(value or 0.0))), 4)
    except (TypeError, ValueError):
        return 0.0


def _strings(value: object, max_items: int, item_limit: int) -> list[str]:
    if not isinstance(value, (list, tuple, set)):
        return []
    return [_bounded(item, item_limit) for item in list(value)[:max_items] if _bounded(item, item_limit)]


def _style_delta(value: object) -> dict[str, Any]:
    raw = value if isinstance(value, Mapping) else {}
    return {
        "length_delta_ratio": _signed(raw.get("length_delta_ratio"), 2.0),
        "line_break_delta": max(-20, min(20, int(raw.get("line_break_delta", 0) or 0))),
        "emoji_delta": max(-20, min(20, int(raw.get("emoji_delta", 0) or 0))),
        "punctuation_delta": max(-40, min(40, int(raw.get("punctuation_delta", 0) or 0))),
        "formality_delta": max(-1, min(1, int(raw.get("formality_delta", 0) or 0))),
        "reaction_delta": max(-1, min(1, int(raw.get("reaction_delta", 0) or 0))),
        "code_switch_delta": max(-1, min(1, int(raw.get("code_switch_delta", 0) or 0))),
        "dialogue_marker_delta": max(-1, min(1, int(raw.get("dialogue_marker_delta", 0) or 0))),
        "lexical_change_ratio": _score(raw.get("lexical_change_ratio")),
        "removed_ai_like_patterns": _strings(raw.get("removed_ai_like_patterns"), 12, 80),
        "added_ai_like_patterns": _strings(raw.get("added_ai_like_patterns"), 12, 80),
    }


def _record(value: object, calibration: Any) -> dict[str, Any] | None:
    if not isinstance(value, Mapping):
        return None
    record_id = _bounded(value.get("record_id"), 64)
    update_id = _bounded(value.get("update_id"), 96)
    if not record_id or not update_id:
        return None
    allowed_labels = getattr(calibration, "EDIT_LABELS", frozenset())
    labels = [item for item in _strings(value.get("labels"), 12, 48) if item in allowed_labels]
    return {
        "record_id": record_id,
        "update_id": update_id,
        "event_id": _bounded(value.get("event_id"), 80),
        "segment_id": _bounded(value.get("segment_id"), 80),
        "content_type": _bounded(value.get("content_type"), 48),
        "factual_draft_fingerprint": _bounded(value.get("factual_draft_fingerprint"), 80),
        "shadow_candidate_fingerprint": _bounded(value.get("shadow_candidate_fingerprint"), 80),
        "final_user_edit_fingerprint": _bounded(value.get("final_user_edit_fingerprint"), 80),
        "labels": labels,
        "style_delta": _style_delta(value.get("style_delta")),
        "fidelity_passed": _bool(value.get("fidelity_passed")),
        "fidelity_failures": _strings(value.get("fidelity_failures"), 20, 120),
        "confidence": _score(value.get("confidence")),
        "eligible_for_learning": _bool(value.get("eligible_for_learning")),
        "traceable": _bool(value.get("traceable")),
        "translation_conflict": _bool(value.get("translation_conflict")),
        "review_action": _bounded(value.get("review_action"), 48),
        "created_at": _bounded(value.get("created_at"), 80),
        "auto_learn": False,
        "mode": calibration.VOICE_CALIBRATION_MODE,
        "text_persisted": False,
    }


def _weights(value: object, calibration: Any) -> dict[str, float]:
    if not isinstance(value, Mapping):
        return {}
    clean: dict[str, float] = {}
    for key, raw in list(value.items())[:40]:
        name = _bounded(key, 96)
        if not name:
            continue
        try:
            score = float(raw)
        except (TypeError, ValueError):
            continue
        clean[name] = round(
            max(-calibration.MAX_RANKING_DELTA, min(calibration.MAX_RANKING_DELTA, score)), 4
        )
    return clean


def _signal(value: object, calibration: Any) -> dict[str, Any] | None:
    if not isinstance(value, Mapping):
        return None
    feature = _bounded(value.get("feature"), 64)
    if not feature:
        return None
    scope = _bounded(value.get("scope"), 16)
    if scope not in {"global", "category", "ai_pattern"}:
        scope = "category"
    direction_raw = value.get("direction", 0)
    try:
        direction_num = int(direction_raw or 0)
    except (TypeError, ValueError):
        direction_num = 0
    return {
        "feature": feature,
        "scope": scope,
        "category": _bounded(value.get("category"), 48),
        "evidence_count": max(0, min(int(value.get("evidence_count", 0) or 0), calibration.MAX_CALIBRATION_RECORDS)),
        "category_count": max(0, min(int(value.get("category_count", 0) or 0), 100)),
        "direction": -1 if direction_num < 0 else (1 if direction_num > 0 else 0),
        "strength": _score(value.get("strength")),
        "confidence": _score(value.get("confidence")),
        "evidence_record_ids": _strings(value.get("evidence_record_ids"), calibration.MAX_EVIDENCE_IDS, 64),
    }


def _snapshot(value: object, calibration: Any) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        return {}
    signals: list[dict[str, Any]] = []
    raw_signals = value.get("signals")
    if isinstance(raw_signals, (list, tuple)):
        for raw in list(raw_signals)[:40]:
            clean = _signal(raw, calibration)
            if clean is not None:
                signals.append(clean)
    return {
        "calibration_version": calibration.VOICE_CALIBRATION_VERSION,
        "snapshot_id": _bounded(value.get("snapshot_id"), 80),
        "previous_snapshot_id": _bounded(value.get("previous_snapshot_id"), 80),
        "category": _bounded(value.get("category"), 48),
        "evidence_record_ids": _strings(value.get("evidence_record_ids"), calibration.MAX_EVIDENCE_IDS, 64),
        "previous_weights": _weights(value.get("previous_weights"), calibration),
        "new_weights": _weights(value.get("new_weights"), calibration),
        "reason": _bounded(value.get("reason"), 160),
        "confidence": _score(value.get("confidence")),
        "signals": signals,
        "auto_learn": False,
        "mode": calibration.VOICE_CALIBRATION_MODE,
        "text_persisted": False,
    }


def fresh_voice_state(calibration: Any) -> dict[str, Any]:
    return {
        "voice_calibration_version": calibration.VOICE_CALIBRATION_VERSION,
        "voice_calibration_mode": calibration.VOICE_CALIBRATION_MODE,
        "auto_learn": False,
        "records": {},
        "active_snapshot": {},
        "text_persisted": False,
    }


def sanitize_voice_state(raw: object, calibration: Any) -> dict[str, Any]:
    clean = fresh_voice_state(calibration)
    if not isinstance(raw, Mapping):
        return clean
    source = raw.get("voice_calibration")
    if not isinstance(source, Mapping):
        return clean
    records: dict[str, dict[str, Any]] = {}
    raw_records = source.get("records")
    if isinstance(raw_records, Mapping):
        for key, value in list(raw_records.items())[-calibration.MAX_CALIBRATION_RECORDS:]:
            item = _record(value, calibration)
            if item is None or item["record_id"] != str(key):
                continue
            records[item["record_id"]] = item
    clean["records"] = records
    clean["active_snapshot"] = _snapshot(source.get("active_snapshot"), calibration)
    return clean


def prune_voice_state(event_state: dict[str, Any], calibration: Any) -> None:
    source = event_state.get("voice_calibration")
    if not isinstance(source, dict):
        event_state["voice_calibration"] = fresh_voice_state(calibration)
        return
    records = source.get("records")
    if isinstance(records, dict) and len(records) > calibration.MAX_CALIBRATION_RECORDS:
        source["records"] = dict(list(records.items())[-calibration.MAX_CALIBRATION_RECORDS:])
    source["auto_learn"] = False
    source["voice_calibration_mode"] = calibration.VOICE_CALIBRATION_MODE
    source["text_persisted"] = False


def install(event_fusion: Any, calibration: Any) -> None:
    """Compose with existing Event/Timeline/Translation/Style sanitizer; no new DB."""
    current_sanitize = event_fusion._sanitize_event_state
    if not getattr(current_sanitize, "_user_voice_calibration_state_compat_installed", False):
        def sanitize(raw):
            clean = current_sanitize(raw)
            clean["voice_calibration"] = sanitize_voice_state(raw, calibration)
            return clean
        sanitize._user_voice_calibration_state_compat_installed = True
        event_fusion._sanitize_event_state = sanitize

    current_prune = event_fusion._prune
    if not getattr(current_prune, "_user_voice_calibration_state_compat_installed", False):
        def prune(event_state):
            current_prune(event_state)
            prune_voice_state(event_state, calibration)
        prune._user_voice_calibration_state_compat_installed = True
        event_fusion._prune = prune
