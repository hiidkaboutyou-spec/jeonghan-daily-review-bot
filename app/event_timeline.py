"""Shadow-only long-form Event Timeline / Segment foundation.

Segments are reversible organizational metadata layered under Event Fusion. They
never become retrieval, lifecycle, translation, media-dedupe, or Telegram-delivery
identities.
"""
from __future__ import annotations

import hashlib
import math
import re
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Iterable
from urllib.parse import urlsplit

from . import event_fusion
from . import observability as _observability
from .models import Update, ensure_utc
from .observability import observe
from .organizer import extract_part_number
from .state import StateStore

TIMELINE_STATE_VERSION = 1
TIMELINE_MODE = "shadow"
MAX_TIMELINE_FINGERPRINTS = 5000
MAX_SEGMENTS = 4000
MAX_SEGMENT_RELATIONSHIPS = 6000
MAX_TIMELINE_DECISIONS = 4000

RELATIONSHIPS = frozenset({
    "same_moment", "complementary", "continuation", "conflicting", "ambiguous", "separate",
})
_SEGMENTABLE_EVENT_TYPES = frozenset({
    "live", "interview", "going_seventeen", "variety", "reality",
    "fansign_or_video_call", "concert", "award_show", "brand_event",
    "official_content", "other", "unknown",
})
_LONG_FORM_TYPES = frozenset({
    "live", "interview", "going_seventeen", "variety", "reality",
    "award_show", "brand_event", "official_content",
})
_STRONG_SAME_MOMENT = frozenset({
    "shared_quoted_reference", "shared_media_reference", "same_content_timestamp",
    "shared_clip_reference", "same_question_anchor",
})
_ATTACHING_RELATIONSHIPS = frozenset({"same_moment", "continuation", "conflicting"})

_TIMESTAMP_HMS_RE = re.compile(r"(?<!\d)(\d{1,2}):([0-5]\d):([0-5]\d)(?!\d)")
_TIMESTAMP_BRACKET_RE = re.compile(r"[\[(]\s*(\d{1,3}):([0-5]\d)\s*[\])]")
_TIMESTAMP_CLOCK_RE = re.compile(
    r"(?:(?:\bat\b|\btime\b|\btimestamp\b|시각|타임|زمان)\s*)([0-2]?\d):([0-5]\d)(?!\d)", re.I,
)
_QUESTION_RE = re.compile(
    r"(?:^|\n)\s*(?:q(?:uestion)?\s*(\d{0,3})|질문\s*(\d{0,3})|質問\s*(\d{0,3})|س[ؤو]ال\s*(\d{0,3}))\s*[:：\-]\s*([^\n]{3,220})",
    re.I,
)
_QUOTED_TEXT_RE = re.compile(r"[\"“‘']([^\"”’'\n]{8,240})[\"”’']")
_TOKEN_RE = re.compile(
    r"[a-z0-9][a-z0-9_'-]{1,40}|[\u0600-\u06ff]{2,30}|[\u3040-\u30ff]{2,30}|[\uac00-\ud7af]{2,30}", re.I,
)
_NUMBER_RE = re.compile(r"(?<![\w:])\d+(?:\.\d+)?(?![\w:])")
_NEGATION_RE = re.compile(
    r"\b(?:not|no|never|didn['’]?t|doesn['’]?t|isn['’]?t|wasn['’]?t|cannot|can['’]?t)\b|(?:안|못|아니|않)|(?:ない|ません)|(?:نه|نیست|نکرد|نمیکن|نمی‌کن)",
    re.I,
)
_URL_RE = re.compile(r"https?://[^\s<>()\[\]{}\"']+", re.I)
_GENERIC_TEXT_TOKENS = {
    "jeonghan", "yoon", "yoonjeonghan", "정한", "윤정한", "ジョンハン", "جونگهان", "هانی",
    "seventeen", "svt", "세븐틴", "live", "weverse", "interview", "update", "updates",
    "clip", "video", "photo", "fancam", "part", "episode", "going", "the", "and", "with",
    "from", "this", "that", "for", "his", "her",
}


@dataclass(frozen=True, slots=True)
class SegmentFingerprint:
    update_id: str
    event_id: str
    source: str
    created_at: str
    event_type: str
    language: str = ""
    conversation_id: str = ""
    reply_to_id: str = ""
    quoted_id: str = ""
    reference_hashes: tuple[str, ...] = ()
    topic_hashes: tuple[str, ...] = ()
    participants: tuple[str, ...] = ()
    content_timestamp_seconds: int | None = None
    timestamp_kind: str = ""
    part_number: int | None = None
    question_hashes: tuple[str, ...] = ()
    media_hashes: tuple[str, ...] = ()
    text_anchor_hashes: tuple[str, ...] = ()
    fact_numbers: tuple[str, ...] = ()
    has_negation: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "update_id": self.update_id, "event_id": self.event_id, "source": self.source,
            "created_at": self.created_at, "event_type": self.event_type, "language": self.language,
            "conversation_id": self.conversation_id, "reply_to_id": self.reply_to_id,
            "quoted_id": self.quoted_id, "reference_hashes": list(self.reference_hashes),
            "topic_hashes": list(self.topic_hashes), "participants": list(self.participants),
            "content_timestamp_seconds": self.content_timestamp_seconds,
            "timestamp_kind": self.timestamp_kind, "part_number": self.part_number,
            "question_hashes": list(self.question_hashes), "media_hashes": list(self.media_hashes),
            "text_anchor_hashes": list(self.text_anchor_hashes), "fact_numbers": list(self.fact_numbers),
            "has_negation": bool(self.has_negation),
        }

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "SegmentFingerprint":
        try:
            timestamp = int(value["content_timestamp_seconds"]) if value.get("content_timestamp_seconds") is not None else None
        except (TypeError, ValueError):
            timestamp = None
        if timestamp is not None and not 0 <= timestamp <= 172800:
            timestamp = None
        try:
            part = int(value["part_number"]) if value.get("part_number") is not None else None
        except (TypeError, ValueError):
            part = None
        if part is not None and not 0 <= part <= 10000:
            part = None
        return cls(
            update_id=_bounded(value.get("update_id"), 160),
            event_id=_bounded(value.get("event_id"), 80),
            source=_bounded(value.get("source"), 80).lstrip("@").casefold(),
            created_at=_bounded(value.get("created_at"), 80),
            event_type=_bounded(value.get("event_type"), 48).casefold(),
            language=_bounded(value.get("language"), 24).casefold(),
            conversation_id=_bounded(value.get("conversation_id"), 160),
            reply_to_id=_bounded(value.get("reply_to_id"), 160),
            quoted_id=_bounded(value.get("quoted_id"), 160),
            reference_hashes=_safe_tuple(value.get("reference_hashes"), 32, 40),
            topic_hashes=_safe_tuple(value.get("topic_hashes"), 48, 40),
            participants=_safe_tuple(value.get("participants"), 16, 40),
            content_timestamp_seconds=timestamp,
            timestamp_kind=_bounded(value.get("timestamp_kind"), 40),
            part_number=part,
            question_hashes=_safe_tuple(value.get("question_hashes"), 16, 40),
            media_hashes=_safe_tuple(value.get("media_hashes"), 40, 40),
            text_anchor_hashes=_safe_tuple(value.get("text_anchor_hashes"), 40, 40),
            fact_numbers=_safe_tuple(value.get("fact_numbers"), 20, 32),
            has_negation=bool(value.get("has_negation", False)),
        )


@dataclass(frozen=True, slots=True)
class SegmentCandidate:
    left_update_id: str
    right_update_id: str
    confidence: float
    matching_signals: tuple[str, ...]
    conflicts: tuple[str, ...]
    relationship: str
    same_segment: bool


def _bounded(value: object, limit: int) -> str:
    return str(value or "").strip()[:limit]


def _safe_tuple(value: object, max_items: int, item_limit: int) -> tuple[str, ...]:
    if not isinstance(value, (list, tuple, set)):
        return ()
    return tuple(sorted({_bounded(item, item_limit) for item in value if _bounded(item, item_limit)}))[:max_items]


def _hash(namespace: str, value: str, length: int = 24) -> str:
    return hashlib.sha256(f"{namespace}\x1f{value}".encode("utf-8")).hexdigest()[:length]


def make_segment_id(event_id: str, seed_update_ids: Iterable[str]) -> str:
    event_id = _bounded(event_id, 80)
    seeds = sorted({_bounded(item, 160) for item in seed_update_ids if _bounded(item, 160)})
    if not event_id.startswith("evt:"):
        raise ValueError("Segment identity requires a semantic Event.id")
    if not seeds:
        raise ValueError("Segment identity requires at least one Update.id seed")
    return f"seg:{_hash('segment-v1', event_id + chr(31) + chr(31).join(seeds))}"


def _normalize_url(raw: str) -> str:
    try:
        parsed = urlsplit(str(raw).strip().rstrip(".,!?;:)]}"))
    except ValueError:
        return ""
    if parsed.scheme.casefold() not in {"http", "https"} or not parsed.netloc:
        return ""
    host = parsed.netloc.casefold().removeprefix("www.")
    path = re.sub(r"/+", "/", parsed.path or "/").rstrip("/") or "/"
    suffix = f"?{parsed.query}" if parsed.query else ""
    if parsed.fragment:
        suffix += f"#{parsed.fragment}"
    return f"{host}{path}{suffix}"[:500]


def _extract_content_timestamp(text: str) -> tuple[int | None, str]:
    match = _TIMESTAMP_HMS_RE.search(text)
    if match:
        h, m, s = map(int, match.groups())
        if h <= 47:
            return h * 3600 + m * 60 + s, "explicit_hms"
    match = _TIMESTAMP_BRACKET_RE.search(text)
    if match:
        m, s = map(int, match.groups())
        if m <= 999:
            return m * 60 + s, "explicit_bracket_mmss"
    match = _TIMESTAMP_CLOCK_RE.search(text)
    if match:
        h, m = map(int, match.groups())
        if h <= 23:
            return h * 3600 + m * 60, "explicit_clock"
    return None, ""


def _question_hashes(text: str) -> tuple[str, ...]:
    result: set[str] = set()
    for match in _QUESTION_RE.finditer(text):
        number = next((item for item in match.groups()[:4] if item), "")
        tokens = [
            token.casefold().strip("_'-") for token in _TOKEN_RE.findall(match.group(5))
            if token.casefold().strip("_'-") not in _GENERIC_TEXT_TOKENS
        ]
        if number:
            result.add(_hash("question-number-v1", number))
        if len(tokens) >= 2:
            result.add(_hash("question-text-v1", " ".join(tokens[:12])))
    return tuple(sorted(result)[:16])


def _text_anchor_hashes(text: str) -> tuple[str, ...]:
    result: set[str] = set()
    for quoted in _QUOTED_TEXT_RE.findall(text):
        tokens = [
            token.casefold().strip("_'-") for token in _TOKEN_RE.findall(quoted)
            if token.casefold().strip("_'-") not in _GENERIC_TEXT_TOKENS
        ]
        if len(tokens) >= 3:
            result.add(_hash("spoken-anchor-v1", " ".join(tokens[:24])))
    return tuple(sorted(result)[:40])


def _media_hashes(update: Update) -> tuple[str, ...]:
    values: set[str] = set()
    for item in list(update.media) + list(update.quoted_media):
        normalized = _normalize_url(item.url)
        if normalized:
            values.add(_hash("segment-media-v1", f"{item.kind.casefold()}:{normalized}"))
    return tuple(sorted(values)[:40])


def _fact_numbers(text: str) -> tuple[str, ...]:
    stripped = _TIMESTAMP_HMS_RE.sub(" ", text)
    stripped = _TIMESTAMP_BRACKET_RE.sub(" ", stripped)
    stripped = _TIMESTAMP_CLOCK_RE.sub(" ", stripped)
    return tuple(sorted(set(_NUMBER_RE.findall(stripped)))[:20])


def build_segment_fingerprint(update: Update, event_id: str) -> SegmentFingerprint:
    base = event_fusion.build_fingerprint(update)
    text = f"{update.text}\n{update.quoted_text}"
    timestamp, timestamp_kind = _extract_content_timestamp(text)
    return SegmentFingerprint(
        update_id=str(update.id), event_id=str(event_id), source=base.source,
        created_at=base.created_at, event_type=base.event_type,
        language=str(update.lang or "").casefold(), conversation_id=base.conversation_id,
        reply_to_id=base.reply_to_id, quoted_id=base.quoted_id,
        reference_hashes=base.reference_hashes, topic_hashes=base.topic_hashes,
        participants=base.participants, content_timestamp_seconds=timestamp,
        timestamp_kind=timestamp_kind, part_number=extract_part_number(text),
        question_hashes=_question_hashes(text), media_hashes=_media_hashes(update),
        text_anchor_hashes=_text_anchor_hashes(text), fact_numbers=_fact_numbers(text),
        has_negation=bool(_NEGATION_RE.search(text)),
    )


def _created(fp: SegmentFingerprint) -> datetime:
    try:
        return ensure_utc(fp.created_at)
    except Exception:
        return datetime.min.replace(tzinfo=timezone.utc)


def _overlap(left: tuple[str, ...], right: tuple[str, ...]) -> set[str]:
    return set(left).intersection(right)


def _topic_overlap(left: SegmentFingerprint, right: SegmentFingerprint) -> tuple[int, float]:
    shared = _overlap(left.topic_hashes, right.topic_hashes)
    union = set(left.topic_hashes).union(right.topic_hashes)
    return len(shared), len(shared) / max(1, len(union))


def match_segment_fingerprints(
    left: SegmentFingerprint,
    right: SegmentFingerprint,
    *,
    container_reference_hashes: Iterable[str] = (),
) -> SegmentCandidate:
    if left.event_id != right.event_id:
        return SegmentCandidate(left.update_id, right.update_id, 0.0, (), ("different_event",), "separate", False)
    if left.update_id == right.update_id:
        return SegmentCandidate(left.update_id, right.update_id, 1.0, ("same_update_identity",), (), "same_moment", True)

    container_refs = set(container_reference_hashes)
    signals: list[str] = []
    conflicts: list[str] = []
    score = 0.0
    direct_reply = (
        (left.reply_to_id and left.reply_to_id == right.update_id)
        or (right.reply_to_id and right.reply_to_id == left.update_id)
    )
    if direct_reply:
        signals.append("direct_reply_relation"); score += 0.94
    if (
        (left.quoted_id and left.quoted_id == right.quoted_id)
        or (left.quoted_id and left.quoted_id == right.update_id)
        or (right.quoted_id and right.quoted_id == left.update_id)
    ):
        signals.append("shared_quoted_reference"); score += 0.98
    if _overlap(left.media_hashes, right.media_hashes):
        signals.append("shared_media_reference"); score += 0.97

    if left.content_timestamp_seconds is not None and right.content_timestamp_seconds is not None:
        delta = abs(left.content_timestamp_seconds - right.content_timestamp_seconds)
        if delta <= 15:
            signals.append("same_content_timestamp"); score += 0.95
        elif delta > 120:
            conflicts.append("content_timestamp_mismatch"); score -= 0.35
        else:
            signals.append("near_content_timestamp"); score += 0.22

    shared_refs = _overlap(left.reference_hashes, right.reference_hashes)
    moment_refs = shared_refs.difference(container_refs)
    if moment_refs:
        signals.append("shared_clip_reference"); score += 0.90
    elif shared_refs:
        signals.append("shared_container_reference"); score += 0.04

    shared_questions = _overlap(left.question_hashes, right.question_hashes)
    if shared_questions:
        signals.append("same_question_anchor"); score += 0.90
    elif left.question_hashes and right.question_hashes:
        conflicts.append("different_question"); score -= 0.30

    if left.part_number is not None and right.part_number is not None:
        if left.part_number == right.part_number:
            signals.append("same_part_number"); score += 0.62
        elif abs(left.part_number - right.part_number) == 1:
            signals.append("adjacent_part_sequence"); score += 0.48
        elif abs(left.part_number - right.part_number) > 2:
            conflicts.append("distant_part_sequence"); score -= 0.18

    left_threaded = bool(left.conversation_id) and left.conversation_id != left.update_id
    right_threaded = bool(right.conversation_id) and right.conversation_id != right.update_id
    if left_threaded and right_threaded and left.conversation_id == right.conversation_id:
        signals.append("same_conversation"); score += 0.50

    shared_topics, topic_ratio = _topic_overlap(left, right)
    if shared_topics >= 3 and topic_ratio >= 0.28:
        signals.append("topic_overlap"); score += 0.28
    elif shared_topics >= 2 and topic_ratio >= 0.20:
        signals.append("topic_overlap"); score += 0.18
    if _overlap(left.text_anchor_hashes, right.text_anchor_hashes):
        signals.append("shared_spoken_text_anchor"); score += 0.30
    if _overlap(left.participants, right.participants):
        signals.append("shared_participants"); score += 0.10

    source_delta = abs((_created(left) - _created(right)).total_seconds())
    if source_delta <= 15 * 60:
        signals.append("temporal_proximity"); score += 0.08
    elif source_delta <= 2 * 3600:
        signals.append("temporal_proximity"); score += 0.04
    elif source_delta > 24 * 3600:
        conflicts.append("large_source_time_gap"); score -= 0.20
    if left.event_type == right.event_type:
        signals.append("same_event_type"); score += 0.02

    same_moment_anchor = any(signal in _STRONG_SAME_MOMENT for signal in signals)
    continuation_anchor = direct_reply or "adjacent_part_sequence" in signals or "same_conversation" in signals
    supporting = sum(signal in {
        "topic_overlap", "shared_participants", "temporal_proximity",
        "shared_spoken_text_anchor", "same_part_number",
    } for signal in signals)

    if same_moment_anchor and left.fact_numbers and right.fact_numbers and set(left.fact_numbers).isdisjoint(right.fact_numbers):
        conflicts.append("fact_value_conflict")
    if same_moment_anchor and left.has_negation != right.has_negation and (
        "topic_overlap" in signals or "shared_spoken_text_anchor" in signals
    ):
        conflicts.append("polarity_conflict")

    score = round(max(0.0, min(1.0, score)), 3)
    unresolved = any(item in {"fact_value_conflict", "polarity_conflict"} for item in conflicts)
    if same_moment_anchor and unresolved and "content_timestamp_mismatch" not in conflicts:
        relationship, same_segment = "conflicting", True
    elif same_moment_anchor and score >= 0.78 and "content_timestamp_mismatch" not in conflicts and "different_question" not in conflicts:
        relationship, same_segment = "same_moment", True
    elif direct_reply and score >= 0.80 and "content_timestamp_mismatch" not in conflicts:
        relationship, same_segment = "continuation", True
    elif continuation_anchor and supporting >= 1 and score >= 0.82 and "different_question" not in conflicts and "content_timestamp_mismatch" not in conflicts:
        relationship, same_segment = "continuation", True
    elif continuation_anchor and supporting >= 1 and score >= 0.55:
        relationship, same_segment = "complementary", False
    elif score >= 0.45 or same_moment_anchor:
        relationship, same_segment = "ambiguous", False
    else:
        relationship, same_segment = "separate", False
    return SegmentCandidate(
        left.update_id, right.update_id, score, tuple(sorted(set(signals))),
        tuple(sorted(set(conflicts))), relationship, same_segment,
    )


def _fresh_timeline_fields() -> dict[str, Any]:
    return {
        "timeline_version": TIMELINE_STATE_VERSION, "timeline_mode": TIMELINE_MODE,
        "segments": {}, "segment_memberships": {}, "timeline_fingerprints": {},
        "segment_relationships": {}, "timeline_decisions": [],
    }


def _timeline_state(state: StateStore) -> dict[str, Any]:
    fusion = event_fusion._event_state(state)
    for key, value in _fresh_timeline_fields().items():
        fusion.setdefault(key, value)
    return fusion


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _relationship_id(event_id: str, left_id: str, right_id: str) -> str:
    pair = chr(31).join(sorted((str(left_id), str(right_id))))
    return f"rel:{_hash('segment-relation-v1', event_id + chr(31) + pair, 22)}"


def _record_decision(
    fusion: dict[str, Any], *, decision: str, event_id: str = "", segment_id: str = "",
    update_id: str = "", candidate_update_id: str = "", candidate: SegmentCandidate | None = None,
) -> None:
    fusion["timeline_decisions"].append({
        "decision": _bounded(decision, 48), "event_id": _bounded(event_id, 80),
        "segment_id": _bounded(segment_id, 80), "update_id": _bounded(update_id, 160),
        "candidate_update_id": _bounded(candidate_update_id, 160),
        "relationship": candidate.relationship if candidate else "",
        "confidence": candidate.confidence if candidate else 0.0,
        "matching_signals": list(candidate.matching_signals if candidate else ()),
        "conflicts": list(candidate.conflicts if candidate else ()), "created_at": _now(),
    })
    fusion["timeline_decisions"] = fusion["timeline_decisions"][-MAX_TIMELINE_DECISIONS:]


def _observe(
    fp: SegmentFingerprint, candidate: SegmentCandidate | None, status: str, *,
    segment_id: str = "", segment_count: int = 0, order_evidence: str = "",
) -> None:
    observe(
        "shadow_event_timeline", component="event_timeline", stage="shadow_timeline", status=status,
        update_id=fp.update_id, source=fp.source, event_id=fp.event_id, event_type=fp.event_type,
        segment_id=segment_id, segment_confidence=candidate.confidence if candidate else 0.0,
        segment_signals=",".join(candidate.matching_signals if candidate else ()),
        segment_conflicts=",".join(candidate.conflicts if candidate else ()),
        segment_relationship=candidate.relationship if candidate else "", segment_count=segment_count,
        timeline_mode=TIMELINE_MODE, order_evidence=order_evidence,
    )


def _event_membership_map(fusion: dict[str, Any]) -> dict[str, str]:
    return {
        str(uid): str(row.get("event_id", "")) for uid, row in fusion.get("memberships", {}).items()
        if isinstance(row, dict) and str(row.get("event_id", ""))
    }


def _reconcile(fusion: dict[str, Any]) -> None:
    event_memberships = _event_membership_map(fusion)
    segments = fusion.get("segments", {})
    memberships = fusion.get("segment_memberships", {})
    for update_id, row in list(memberships.items()):
        if not isinstance(row, dict):
            memberships.pop(update_id, None); continue
        event_id = str(row.get("event_id", "")); segment_id = str(row.get("segment_id", ""))
        if event_memberships.get(str(update_id)) != event_id or segment_id not in segments:
            memberships.pop(update_id, None)
            segment = segments.get(segment_id)
            if isinstance(segment, dict):
                segment["member_update_ids"] = [str(item) for item in segment.get("member_update_ids", []) if str(item) != str(update_id)]
                segment["updated_at"] = _now()
            _record_decision(fusion, decision="event_membership_reconciled", segment_id=segment_id, update_id=str(update_id))
    for segment_id, row in list(segments.items()):
        if not isinstance(row, dict) or not row.get("member_update_ids"):
            segments.pop(segment_id, None)
    valid_segments = set(segments); valid_updates = set(memberships)
    fusion["segment_relationships"] = {
        key: row for key, row in fusion.get("segment_relationships", {}).items()
        if isinstance(row, dict)
        and str(row.get("left_update_id", "")) in valid_updates
        and str(row.get("right_update_id", "")) in valid_updates
        and (not row.get("left_segment_id") or str(row.get("left_segment_id")) in valid_segments)
        and (not row.get("right_segment_id") or str(row.get("right_segment_id")) in valid_segments)
    }


def _create_segment(fusion: dict[str, Any], event_id: str, fp: SegmentFingerprint) -> str:
    segment_id = make_segment_id(event_id, [fp.update_id]); now = _now()
    fusion["segments"][segment_id] = {
        "segment_id": segment_id, "event_id": event_id, "created_at": now, "updated_at": now,
        "member_update_ids": [fp.update_id], "confidence": 1.0, "status": "shadow_candidate",
        "order_index": 0, "order_evidence": {},
    }
    fusion["segment_memberships"][fp.update_id] = {
        "event_id": event_id, "segment_id": segment_id, "confidence": 1.0,
        "relationship": "seed", "matching_signals": [], "conflicts": [], "updated_at": now,
    }
    return segment_id


def _attach_to_segment(fusion: dict[str, Any], segment_id: str, fp: SegmentFingerprint, candidate: SegmentCandidate) -> None:
    segment = fusion["segments"][segment_id]
    segment["member_update_ids"] = sorted(set(map(str, segment.get("member_update_ids", []))) | {fp.update_id})
    segment["updated_at"] = _now()
    segment["confidence"] = round(min(float(segment.get("confidence", 1.0) or 1.0), candidate.confidence), 3)
    fusion["segment_memberships"][fp.update_id] = {
        "event_id": fp.event_id, "segment_id": segment_id, "confidence": candidate.confidence,
        "relationship": candidate.relationship, "matching_signals": list(candidate.matching_signals),
        "conflicts": list(candidate.conflicts), "updated_at": _now(),
    }


def _store_relationship(fusion: dict[str, Any], left: SegmentFingerprint, right: SegmentFingerprint, candidate: SegmentCandidate) -> None:
    rel_id = _relationship_id(left.event_id, left.update_id, right.update_id)
    left_row = fusion["segment_memberships"].get(left.update_id, {})
    right_row = fusion["segment_memberships"].get(right.update_id, {})
    fusion["segment_relationships"][rel_id] = {
        "relationship_id": rel_id, "event_id": left.event_id,
        "left_update_id": left.update_id, "right_update_id": right.update_id,
        "left_segment_id": str(left_row.get("segment_id", "")),
        "right_segment_id": str(right_row.get("segment_id", "")),
        "relationship": candidate.relationship, "confidence": candidate.confidence,
        "matching_signals": list(candidate.matching_signals), "conflicts": list(candidate.conflicts),
        "status": "unresolved" if candidate.relationship in {"conflicting", "ambiguous"} else "observed",
        "updated_at": _now(),
    }


def _container_reference_hashes(fps: list[SegmentFingerprint], event_type: str) -> set[str]:
    if len(fps) < 2:
        return set()
    counts: Counter[str] = Counter()
    for fp in fps:
        counts.update(set(fp.reference_hashes))
    threshold = max(2, math.ceil(len(fps) * 0.6))
    common = {value for value, count in counts.items() if count >= threshold}
    if event_type in _LONG_FORM_TYPES:
        return common
    return {value for value in common if counts[value] >= 3}


def _fingerprint_sort_key(fp: SegmentFingerprint) -> tuple:
    if fp.content_timestamp_seconds is not None:
        return (0, fp.content_timestamp_seconds, fp.created_at, fp.update_id)
    if fp.part_number is not None:
        return (1, fp.part_number, fp.created_at, fp.update_id)
    return (2, fp.created_at, fp.update_id)


def _segment_order_evidence(segment: dict[str, Any], fps: dict[str, SegmentFingerprint]) -> tuple[tuple, dict[str, Any]]:
    members = [fps[uid] for uid in map(str, segment.get("member_update_ids", [])) if uid in fps]
    timestamps = [fp.content_timestamp_seconds for fp in members if fp.content_timestamp_seconds is not None]
    if timestamps:
        value = min(timestamps); return (0, value, str(segment.get("segment_id", ""))), {"kind": "content_timestamp", "value": value, "confidence": "explicit"}
    parts = [fp.part_number for fp in members if fp.part_number is not None]
    if parts:
        value = min(parts); return (1, value, str(segment.get("segment_id", ""))), {"kind": "part_or_thread", "value": value, "confidence": "explicit_part"}
    created = sorted(fp.created_at for fp in members if fp.created_at)
    if created:
        value = created[0]; return (2, value, str(segment.get("segment_id", ""))), {"kind": "source_created_at", "value": value, "confidence": "source_time"}
    segment_id = str(segment.get("segment_id", ""))
    return (3, segment_id), {"kind": "stable_fallback", "value": segment_id, "confidence": "deterministic"}


def _reorder_event_segments(fusion: dict[str, Any], event_id: str, fps: dict[str, SegmentFingerprint]) -> list[str]:
    rows = [row for row in fusion.get("segments", {}).values() if isinstance(row, dict) and str(row.get("event_id", "")) == event_id]
    ordered = []
    for row in rows:
        key, evidence = _segment_order_evidence(row, fps); ordered.append((key, evidence, row))
    ordered.sort(key=lambda item: item[0]); result = []
    for index, (_, evidence, row) in enumerate(ordered, start=1):
        row["order_index"] = index; row["order_evidence"] = evidence; result.append(str(row.get("segment_id", "")))
    return result


def _best_candidate(fp: SegmentFingerprint, segment: dict[str, Any], fps: dict[str, SegmentFingerprint], container_refs: set[str]) -> tuple[SegmentCandidate | None, SegmentFingerprint | None]:
    rank = {"conflicting": 6, "same_moment": 5, "continuation": 4, "complementary": 3, "ambiguous": 2, "separate": 1}
    pairs = []
    for member_id in map(str, segment.get("member_update_ids", [])):
        other = fps.get(member_id)
        if other is None or other.update_id == fp.update_id:
            continue
        pairs.append((match_segment_fingerprints(fp, other, container_reference_hashes=container_refs), other))
    if not pairs:
        return None, None
    pairs.sort(key=lambda item: (1 if item[0].same_segment else 0, rank.get(item[0].relationship, 0), item[0].confidence, item[1].update_id), reverse=True)
    return pairs[0]


def shadow_segment_events(state: StateStore, updates: Iterable[Update], configured_handles: Iterable[str]) -> list[SegmentCandidate]:
    """Organize existing semantic Event members into durable shadow Segments only."""
    allowed = {str(item).lstrip("@").strip().casefold() for item in configured_handles if str(item).strip()}
    incoming = sorted(list(updates), key=lambda item: (ensure_utc(item.created_at), str(item.id)))
    fusion = _timeline_state(state); _reconcile(fusion)
    event_memberships = _event_membership_map(fusion); affected_events: set[str] = set()
    for update in incoming:
        source = str(update.author).lstrip("@").strip().casefold()
        event_id = event_memberships.get(str(update.id), "")
        if source not in allowed or not event_id:
            continue
        affected_events.add(event_id)
        fp = build_segment_fingerprint(update, event_id)
        fusion["timeline_fingerprints"][fp.update_id] = fp.to_dict()

    results: list[SegmentCandidate] = []
    for event_id in sorted(affected_events):
        event = fusion.get("events", {}).get(event_id)
        if not isinstance(event, dict) or str(event.get("event_type", "unknown")) not in _SEGMENTABLE_EVENT_TYPES:
            continue
        member_ids = sorted({str(item) for item in event.get("member_update_ids", [])})
        if len(member_ids) < 2:
            continue
        fps: dict[str, SegmentFingerprint] = {}
        for update_id in member_ids:
            raw = fusion.get("timeline_fingerprints", {}).get(update_id)
            if isinstance(raw, dict):
                fp = SegmentFingerprint.from_dict(raw)
                if fp.event_id == event_id:
                    fps[update_id] = fp; continue
            archived = state.get_update(update_id)
            if archived is not None and str(archived.author).lstrip("@").strip().casefold() in allowed:
                fp = build_segment_fingerprint(archived, event_id)
                fusion["timeline_fingerprints"][update_id] = fp.to_dict(); fps[update_id] = fp
        if len(fps) < 2:
            continue
        container_refs = _container_reference_hashes(list(fps.values()), str(event.get("event_type", "unknown")))
        for fp in sorted(fps.values(), key=_fingerprint_sort_key):
            if isinstance(fusion["segment_memberships"].get(fp.update_id), dict):
                continue
            segments = [row for row in fusion["segments"].values() if isinstance(row, dict) and str(row.get("event_id", "")) == event_id]
            if not segments:
                segment_id = _create_segment(fusion, event_id, fp)
                _record_decision(fusion, decision="segment_seeded", event_id=event_id, segment_id=segment_id, update_id=fp.update_id)
                _observe(fp, None, "segment_seeded", segment_id=segment_id, segment_count=1); continue
            choices = []
            for segment in segments:
                candidate, other = _best_candidate(fp, segment, fps, container_refs)
                if candidate is not None and other is not None:
                    choices.append((candidate, other, segment))
            if not choices:
                _create_segment(fusion, event_id, fp); continue
            rank = {"conflicting": 6, "same_moment": 5, "continuation": 4, "complementary": 3, "ambiguous": 2, "separate": 1}
            choices.sort(key=lambda item: (1 if item[0].same_segment else 0, rank.get(item[0].relationship, 0), item[0].confidence, str(item[2].get("segment_id", "")), item[1].update_id), reverse=True)
            candidate, other, segment = choices[0]; results.append(candidate)
            if candidate.same_segment and candidate.relationship in _ATTACHING_RELATIONSHIPS:
                segment_id = str(segment["segment_id"]); _attach_to_segment(fusion, segment_id, fp, candidate); decision = "segment_attached"
            else:
                segment_id = _create_segment(fusion, event_id, fp); decision = "segment_separated"
            _store_relationship(fusion, fp, other, candidate)
            _record_decision(fusion, decision=decision, event_id=event_id, segment_id=segment_id, update_id=fp.update_id, candidate_update_id=other.update_id, candidate=candidate)
            _observe(fp, candidate, decision, segment_id=segment_id, segment_count=sum(1 for row in fusion["segments"].values() if isinstance(row, dict) and row.get("event_id") == event_id))
        ordered_ids = _reorder_event_segments(fusion, event_id, fps)
        for segment_id in ordered_ids:
            segment = fusion["segments"].get(segment_id, {})
            if isinstance(segment, dict):
                for member_id in segment.get("member_update_ids", []):
                    fp = fps.get(str(member_id))
                    if fp is not None:
                        _observe(fp, None, "ordered", segment_id=segment_id, segment_count=len(ordered_ids), order_evidence=str(segment.get("order_evidence", {}).get("kind", "")))
    _prune_timeline(fusion)
    return results


def reclassify_segment_membership(state: StateStore, update_id: str, target_segment_id: str, *, confidence: float = 1.0) -> None:
    fusion = _timeline_state(state); _reconcile(fusion); update_id = str(update_id)
    target = fusion["segments"].get(str(target_segment_id))
    if not isinstance(target, dict):
        raise KeyError(target_segment_id)
    event_id = str(target.get("event_id", ""))
    if _event_membership_map(fusion).get(update_id) != event_id:
        raise ValueError("Segment reclassification cannot cross Event membership")
    current = fusion["segment_memberships"].get(update_id)
    if isinstance(current, dict):
        old_id = str(current.get("segment_id", "")); old = fusion["segments"].get(old_id)
        if isinstance(old, dict):
            old["member_update_ids"] = [str(item) for item in old.get("member_update_ids", []) if str(item) != update_id]
            old["updated_at"] = _now()
            if not old["member_update_ids"]:
                fusion["segments"].pop(old_id, None)
    target["member_update_ids"] = sorted(set(map(str, target.get("member_update_ids", []))) | {update_id}); target["updated_at"] = _now()
    fusion["segment_memberships"][update_id] = {
        "event_id": event_id, "segment_id": str(target_segment_id),
        "confidence": round(max(0.0, min(1.0, float(confidence))), 3),
        "relationship": "reclassified", "matching_signals": ["manual_reclassification"],
        "conflicts": [], "updated_at": _now(),
    }
    _record_decision(fusion, decision="segment_membership_reclassified", event_id=event_id, segment_id=str(target_segment_id), update_id=update_id)


def split_segment(state: StateStore, segment_id: str, moved_update_ids: Iterable[str]) -> str:
    fusion = _timeline_state(state); _reconcile(fusion)
    source = fusion["segments"].get(str(segment_id))
    if not isinstance(source, dict):
        raise KeyError(segment_id)
    members = {str(item) for item in source.get("member_update_ids", [])}; moved = {str(item) for item in moved_update_ids if str(item)}
    if not moved or not moved.issubset(members) or moved == members:
        raise ValueError("Segment split requires a non-empty proper member subset")
    event_id = str(source.get("event_id", "")); new_id = make_segment_id(event_id, sorted(moved))
    if new_id == str(segment_id):
        raise ValueError("Segment split must create a distinct Segment.id")
    now = _now(); source["member_update_ids"] = sorted(members - moved); source["updated_at"] = now
    fusion["segments"][new_id] = {
        "segment_id": new_id, "event_id": event_id, "created_at": now, "updated_at": now,
        "member_update_ids": sorted(moved), "confidence": float(source.get("confidence", 1.0) or 1.0),
        "status": "shadow_candidate", "order_index": 0, "order_evidence": {},
    }
    for uid in moved:
        row = fusion["segment_memberships"].get(uid)
        if isinstance(row, dict):
            row["segment_id"] = new_id; row["relationship"] = "split_reclassified"; row["updated_at"] = now
    _record_decision(fusion, decision="segment_split", event_id=event_id, segment_id=new_id)
    return new_id


def merge_segments(state: StateStore, left_segment_id: str, right_segment_id: str) -> str:
    fusion = _timeline_state(state); _reconcile(fusion)
    left = fusion["segments"].get(str(left_segment_id)); right = fusion["segments"].get(str(right_segment_id))
    if not isinstance(left, dict) or not isinstance(right, dict):
        raise KeyError(left_segment_id if not isinstance(left, dict) else right_segment_id)
    event_id = str(left.get("event_id", ""))
    if event_id != str(right.get("event_id", "")):
        raise ValueError("Segments from different Events cannot be merged")
    members = sorted({str(item) for item in left.get("member_update_ids", [])} | {str(item) for item in right.get("member_update_ids", [])})
    merged_id = make_segment_id(event_id, members); now = _now()
    fusion["segments"].pop(str(left_segment_id), None); fusion["segments"].pop(str(right_segment_id), None)
    fusion["segments"][merged_id] = {
        "segment_id": merged_id, "event_id": event_id, "created_at": now, "updated_at": now,
        "member_update_ids": members, "confidence": round(min(float(left.get("confidence", 1.0) or 1.0), float(right.get("confidence", 1.0) or 1.0)), 3),
        "status": "shadow_candidate", "order_index": 0, "order_evidence": {},
    }
    for uid in members:
        row = fusion["segment_memberships"].get(uid)
        if isinstance(row, dict):
            row["segment_id"] = merged_id; row["relationship"] = "merge_reclassified"; row["updated_at"] = now
    _record_decision(fusion, decision="segments_merged", event_id=event_id, segment_id=merged_id)
    return merged_id


def _prune_timeline(fusion: dict[str, Any]) -> None:
    fps = fusion.get("timeline_fingerprints", {})
    if len(fps) > MAX_TIMELINE_FINGERPRINTS:
        fusion["timeline_fingerprints"] = dict(sorted(fps.items(), key=lambda pair: str(pair[1].get("created_at", "")) if isinstance(pair[1], dict) else "", reverse=True)[:MAX_TIMELINE_FINGERPRINTS])
    segments = fusion.get("segments", {})
    if len(segments) > MAX_SEGMENTS:
        fusion["segments"] = dict(sorted(segments.items(), key=lambda pair: str(pair[1].get("updated_at", "")) if isinstance(pair[1], dict) else "", reverse=True)[:MAX_SEGMENTS])
        kept = set(fusion["segments"])
        fusion["segment_memberships"] = {uid: row for uid, row in fusion.get("segment_memberships", {}).items() if isinstance(row, dict) and str(row.get("segment_id", "")) in kept}
    relationships = fusion.get("segment_relationships", {})
    if len(relationships) > MAX_SEGMENT_RELATIONSHIPS:
        fusion["segment_relationships"] = dict(sorted(relationships.items(), key=lambda pair: str(pair[1].get("updated_at", "")) if isinstance(pair[1], dict) else "", reverse=True)[:MAX_SEGMENT_RELATIONSHIPS])
    fusion["timeline_decisions"] = list(fusion.get("timeline_decisions", []))[-MAX_TIMELINE_DECISIONS:]


def install_observability() -> None:
    _observability._ALLOWED_TAGS.update({
        "segment_id", "segment_confidence", "segment_signals", "segment_conflicts",
        "segment_relationship", "segment_count", "timeline_mode", "order_evidence",
    })


install_observability()
