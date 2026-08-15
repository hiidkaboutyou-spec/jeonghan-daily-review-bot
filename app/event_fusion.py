"""Shadow-only semantic Event identity and membership foundation.

Events are derived metadata over canonical Update.id evidence.  This module never
changes organizer groups, translation/media behavior, delivery, seen state, or
Phase 3 completeness.
"""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Iterable
from urllib.parse import urlsplit

from . import observability as _observability
from . import state as _state_module
from .main import Application
from .models import Update, ensure_utc
from .observability import observe
from .state import StateStore

EVENT_STATE_VERSION = 1
EVENT_SCHEMA_VERSION = 6
EVENT_MODE = "shadow"
MAX_FINGERPRINTS = 5000
MAX_EVENTS = 2000
MAX_DECISIONS = 2000

EVENT_TYPES = frozenset({
    "unknown", "live", "interview", "variety", "reality", "going_seventeen",
    "fansign_or_video_call", "concert", "award_show", "brand_event",
    "airport_or_public_appearance", "official_content", "social_update", "other",
})
DECISIONS = frozenset({
    "confident_same_event", "probable_same_event", "ambiguous", "separate_event",
})
_GENERIC_TYPES = {"unknown", "official_content", "social_update", "other"}
_STRONG_SIGNALS = {
    "direct_reply_relation", "same_conversation", "shared_quoted_reference",
    "shared_external_reference",
}
_SEMANTIC_SIGNALS = {"shared_context_anchor", "topic_overlap", "shared_participants"}
_OFFICIAL_HANDLES = {"pledis_17", "pledis_17jp"}

_URL_RE = re.compile(r"https?://[^\s<>()\[\]{}\"']+", re.I)
_HASHTAG_RE = re.compile(r"(?<!\w)#([\w\u0600-\u06ff\u3040-\u30ff\uac00-\ud7af]{2,80})", re.UNICODE)
_TOKEN_RE = re.compile(
    r"[a-z0-9][a-z0-9_'-]{1,40}|[\u0600-\u06ff]{2,30}|"
    r"[\u3040-\u30ff]{2,30}|[\uac00-\ud7af]{2,30}", re.I,
)
_EVENT_PATTERNS = (
    ("going_seventeen", ("going seventeen", "going_seventeen", "go se", "고잉 세븐틴", "고잉세븐틴")),
    ("concert", ("concert", "caratland", "carat land", "캐럿랜드", "tour stop", "soundcheck", "encore")),
    ("award_show", ("award show", "mama awards", "golden disc", "aaa awards", "시상식")),
    ("fansign_or_video_call", ("fansign", "fan sign", "fancall", "fan call", "video call", "영통", "팬싸")),
    ("interview", ("interview", "q&a", "q & a", "인터뷰", "インタビュー", "مصاحبه")),
    ("reality", ("reality show", "nana tour", "in the soop", "리얼리티")),
    ("variety", ("variety", "variety show", "예능", "バラエティ")),
    ("brand_event", ("brand event", "ambassador", "campaign", "pop-up", "popup", "브랜드", "광고")),
    ("airport_or_public_appearance", ("airport", "incheon", "gimpo", "공항", "فرودگاه")),
    ("live", ("weverse live", "instagram live", "youtube live", "라이브", "위버스 라이브", "لایو")),
    ("social_update", ("instagram", "insta", "ig update", "story", "reel", "인스타", "اینستا")),
)
_KNOWN_ANCHORS = (
    "caratland", "carat land", "going seventeen", "going_seventeen", "nana tour",
    "in the soop", "mama awards", "golden disc", "aaa awards", "follow again",
    "seventeen right here",
)
_GENERIC_HASHTAGS = {
    "jeonghan", "yoonjeonghan", "seventeen", "svt", "carat", "정한", "윤정한",
    "세븐틴", "ジョンハン", "جونگهان",
}
_TOKEN_STOP = {
    "jeonghan", "yoon", "yoonjeonghan", "정한", "윤정한", "ジョンハン", "جونگهان", "هانی",
    "seventeen", "svt", "세븐틴", "update", "updates", "اپدیت", "آپدیت", "video",
    "photo", "photos", "fancam", "clip", "today", "the", "and", "with", "from",
    "this", "that", "was", "were", "for", "his", "her",
}
_PARTICIPANTS = {
    "scoups": ("s.coups", "scoups", "seungcheol", "승철", "에스쿱스"),
    "joshua": ("joshua", "jisoo", "조슈아", "지수"),
    "jun": ("junhui", "jun", "준휘"),
    "hoshi": ("hoshi", "soonyoung", "호시", "순영"),
    "wonwoo": ("wonwoo", "원우"), "woozi": ("woozi", "jihoon", "우지", "지훈"),
    "the8": ("the8", "minghao", "디에잇", "명호"), "mingyu": ("mingyu", "민규"),
    "dk": ("dokyeom", "seokmin", "도겸", "석민"), "seungkwan": ("seungkwan", "승관"),
    "vernon": ("vernon", "hansol", "버논", "한솔"), "dino": ("dino", "디노"),
}


@dataclass(frozen=True, slots=True)
class EventFingerprint:
    update_id: str
    source: str
    created_at: str
    event_type: str
    conversation_id: str = ""
    reply_to_id: str = ""
    quoted_id: str = ""
    reference_hashes: tuple[str, ...] = ()
    topic_hashes: tuple[str, ...] = ()
    anchor_hashes: tuple[str, ...] = ()
    participants: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "update_id": self.update_id, "source": self.source, "created_at": self.created_at,
            "event_type": self.event_type, "conversation_id": self.conversation_id,
            "reply_to_id": self.reply_to_id, "quoted_id": self.quoted_id,
            "reference_hashes": list(self.reference_hashes), "topic_hashes": list(self.topic_hashes),
            "anchor_hashes": list(self.anchor_hashes), "participants": list(self.participants),
        }

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "EventFingerprint":
        return cls(
            update_id=_bounded(value.get("update_id"), 160),
            source=_bounded(value.get("source"), 80).lstrip("@").casefold(),
            created_at=_bounded(value.get("created_at"), 80),
            event_type=_safe_type(value.get("event_type")),
            conversation_id=_bounded(value.get("conversation_id"), 160),
            reply_to_id=_bounded(value.get("reply_to_id"), 160),
            quoted_id=_bounded(value.get("quoted_id"), 160),
            reference_hashes=_safe_tuple(value.get("reference_hashes"), 32, 40),
            topic_hashes=_safe_tuple(value.get("topic_hashes"), 48, 40),
            anchor_hashes=_safe_tuple(value.get("anchor_hashes"), 24, 40),
            participants=_safe_tuple(value.get("participants"), 16, 40),
        )


@dataclass(frozen=True, slots=True)
class EventCandidate:
    left_update_id: str
    right_update_id: str
    confidence: float
    matching_signals: tuple[str, ...]
    conflicts: tuple[str, ...]
    decision: str


def _bounded(value: object, limit: int) -> str:
    return str(value or "").strip()[:limit]


def _safe_tuple(value: object, max_items: int, item_limit: int) -> tuple[str, ...]:
    if not isinstance(value, (list, tuple, set)):
        return ()
    return tuple(sorted({_bounded(item, item_limit) for item in value if _bounded(item, item_limit)})[:max_items])


def _safe_type(value: object) -> str:
    value = _bounded(value, 48).casefold()
    return value if value in EVENT_TYPES else "unknown"


def _hash(namespace: str, value: str, length: int = 24) -> str:
    return hashlib.sha256(f"{namespace}\x1f{value}".encode("utf-8")).hexdigest()[:length]


def make_event_id(seed_update_ids: Iterable[str]) -> str:
    seeds = sorted({_bounded(item, 160) for item in seed_update_ids if _bounded(item, 160)})
    if len(seeds) < 2:
        raise ValueError("Event identity requires two distinct Update IDs")
    return f"evt:{_hash('event-v1', chr(31).join(seeds))}"


def classify_event_type(update: Update) -> str:
    text = f"{update.text}\n{update.quoted_text}".casefold()
    for event_type, phrases in _EVENT_PATTERNS:
        if any(phrase in text for phrase in phrases):
            return event_type
    mapped = {
        "live": "live", "fansign": "fansign_or_video_call", "brand": "brand_event",
        "airport": "airport_or_public_appearance", "jeonghan_instagram": "social_update",
        "member_instagram": "social_update",
    }.get(str(update.category or "").casefold())
    if mapped:
        return mapped
    return "official_content" if update.author.casefold() in _OFFICIAL_HANDLES else "unknown"


def _reference_hashes(update: Update) -> tuple[str, ...]:
    values = set()
    for raw in _URL_RE.findall(f"{update.text}\n{update.quoted_text}"):
        try:
            parsed = urlsplit(raw.rstrip(".,!?;:)]}"))
        except ValueError:
            continue
        if parsed.scheme.casefold() not in {"http", "https"} or not parsed.netloc:
            continue
        host = parsed.netloc.casefold().removeprefix("www.")
        path = re.sub(r"/+", "/", parsed.path or "/").rstrip("/") or "/"
        values.add(_hash("ref-v1", f"{host}{path}"))
    return tuple(sorted(values)[:24])


def _anchor_hashes(update: Update) -> tuple[str, ...]:
    text = f"{update.text}\n{update.quoted_text}".casefold()
    anchors = {
        tag.strip("_").casefold() for tag in _HASHTAG_RE.findall(text)
        if tag.strip("_").casefold() not in _GENERIC_HASHTAGS
    }
    anchors.update(phrase for phrase in _KNOWN_ANCHORS if phrase in text)
    return tuple(sorted(_hash("anchor-v1", item) for item in anchors)[:24])


def _topic_hashes(update: Update) -> tuple[str, ...]:
    text = _URL_RE.sub(" ", f"{update.text}\n{update.quoted_text}".casefold())
    tokens = set()
    for token in _TOKEN_RE.findall(text):
        token = token.strip("_'-").casefold()
        if len(token) >= 2 and token not in _TOKEN_STOP and token not in _GENERIC_HASHTAGS:
            tokens.add(token)
    return tuple(sorted(_hash("topic-v1", token) for token in sorted(tokens)[:80])[:48])


def _participants(update: Update) -> tuple[str, ...]:
    text = f"{update.text}\n{update.quoted_text}".casefold()
    found = []
    for canonical, aliases in _PARTICIPANTS.items():
        if any(re.search(rf"(?<!\w){re.escape(alias.casefold())}(?!\w)", text) for alias in aliases):
            found.append(canonical)
    return tuple(sorted(set(found)))


def build_fingerprint(update: Update) -> EventFingerprint:
    return EventFingerprint(
        update_id=str(update.id), source=str(update.author).lstrip("@").casefold(),
        created_at=ensure_utc(update.created_at).isoformat(), event_type=classify_event_type(update),
        conversation_id=str(update.conversation_id or ""), reply_to_id=str(update.reply_to_id or ""),
        quoted_id=str(update.quoted_id or ""), reference_hashes=_reference_hashes(update),
        topic_hashes=_topic_hashes(update), anchor_hashes=_anchor_hashes(update),
        participants=_participants(update),
    )


def _created(fp: EventFingerprint) -> datetime:
    try:
        return ensure_utc(fp.created_at)
    except Exception:
        return datetime.min.replace(tzinfo=timezone.utc)


def _overlap(left: tuple[str, ...], right: tuple[str, ...]) -> set[str]:
    return set(left).intersection(right)


def match_fingerprints(left: EventFingerprint, right: EventFingerprint) -> EventCandidate:
    if left.update_id == right.update_id:
        return EventCandidate(left.update_id, right.update_id, 1.0, ("same_update_identity",), (), "confident_same_event")
    signals: list[str] = []
    conflicts: list[str] = []
    score = 0.0
    if (left.reply_to_id and left.reply_to_id == right.update_id) or (right.reply_to_id and right.reply_to_id == left.update_id):
        signals.append("direct_reply_relation"); score += 0.96
    left_threaded = bool(left.conversation_id) and left.conversation_id != left.update_id
    right_threaded = bool(right.conversation_id) and right.conversation_id != right.update_id
    if left_threaded and right_threaded and left.conversation_id == right.conversation_id:
        signals.append("same_conversation"); score += 0.94
    if (left.quoted_id and left.quoted_id == right.quoted_id) or (left.quoted_id and left.quoted_id == right.update_id) or (right.quoted_id and right.quoted_id == left.update_id):
        signals.append("shared_quoted_reference"); score += 0.92
    if _overlap(left.reference_hashes, right.reference_hashes):
        signals.append("shared_external_reference"); score += 0.88
    if _overlap(left.anchor_hashes, right.anchor_hashes):
        signals.append("shared_context_anchor"); score += 0.35
    shared_topics = _overlap(left.topic_hashes, right.topic_hashes)
    union_topics = set(left.topic_hashes).union(right.topic_hashes)
    ratio = len(shared_topics) / max(1, len(union_topics))
    if len(shared_topics) >= 3 and ratio >= 0.28:
        signals.append("topic_overlap"); score += 0.30
    elif len(shared_topics) >= 2 and ratio >= 0.22:
        signals.append("topic_overlap"); score += 0.22
    if _overlap(left.participants, right.participants):
        signals.append("shared_participants"); score += 0.12
    if left.event_type == right.event_type and left.event_type not in _GENERIC_TYPES:
        signals.append("compatible_event_type"); score += 0.06
    delta = abs((_created(left) - _created(right)).total_seconds())
    if delta <= 2 * 3600:
        signals.append("temporal_proximity"); score += 0.10
    elif delta <= 8 * 3600:
        signals.append("temporal_proximity"); score += 0.06
    elif delta <= 24 * 3600:
        signals.append("temporal_proximity"); score += 0.03
    elif delta > 7 * 24 * 3600:
        conflicts.append("large_temporal_gap"); score -= 0.30
    elif delta > 72 * 3600:
        conflicts.append("temporal_gap"); score -= 0.18
    if left.event_type not in _GENERIC_TYPES and right.event_type not in _GENERIC_TYPES and left.event_type != right.event_type:
        conflicts.append("event_type_conflict"); score -= 0.20
    score = round(max(0.0, min(1.0, score)), 3)
    strong = any(signal in _STRONG_SIGNALS for signal in signals)
    semantic_count = sum(signal in _SEMANTIC_SIGNALS for signal in signals)
    if strong and score >= 0.85 and not conflicts:
        decision = "confident_same_event"
    elif strong and score >= 0.65:
        decision = "probable_same_event"
    elif score >= 0.72 and semantic_count >= 2 and "event_type_conflict" not in conflicts:
        decision = "probable_same_event"
    elif score >= 0.45:
        decision = "ambiguous"
    else:
        decision = "separate_event"
    return EventCandidate(left.update_id, right.update_id, score, tuple(signals), tuple(conflicts), decision)


def _fresh_event_state() -> dict[str, Any]:
    return {"version": EVENT_STATE_VERSION, "mode": EVENT_MODE, "events": {}, "memberships": {}, "fingerprints": {}, "decisions": []}


def _sanitize_event_state(raw: object) -> dict[str, Any]:
    clean = _fresh_event_state()
    if not isinstance(raw, dict):
        return clean
    fingerprints: dict[str, dict[str, Any]] = {}
    source = raw.get("fingerprints")
    if isinstance(source, dict):
        for key, value in list(source.items())[-MAX_FINGERPRINTS:]:
            if not isinstance(value, dict):
                continue
            fp = EventFingerprint.from_dict(value)
            if fp.update_id and fp.update_id == str(key):
                fingerprints[fp.update_id] = fp.to_dict()
    events: dict[str, dict[str, Any]] = {}
    source = raw.get("events")
    if isinstance(source, dict):
        for key, value in list(source.items())[-MAX_EVENTS:]:
            if not isinstance(value, dict):
                continue
            event_id = _bounded(value.get("event_id"), 80)
            members = list(_safe_tuple(value.get("member_update_ids"), 500, 160))
            if event_id != str(key) or not event_id.startswith("evt:") or not members:
                continue
            try:
                confidence = round(max(0.0, min(1.0, float(value.get("confidence", 0) or 0))), 3)
            except (TypeError, ValueError):
                confidence = 0.0
            events[event_id] = {
                "event_id": event_id, "event_type": _safe_type(value.get("event_type")),
                "created_at": _bounded(value.get("created_at"), 80), "updated_at": _bounded(value.get("updated_at"), 80),
                "member_update_ids": members, "confidence": confidence, "status": "shadow_candidate",
                "subject_key": _bounded(value.get("subject_key"), 80),
            }
    memberships: dict[str, dict[str, Any]] = {}
    source = raw.get("memberships")
    if isinstance(source, dict):
        for update_id, value in source.items():
            if not isinstance(value, dict):
                continue
            event_id = _bounded(value.get("event_id"), 80)
            event = events.get(event_id)
            if not event or str(update_id) not in event["member_update_ids"]:
                continue
            try:
                confidence = round(max(0.0, min(1.0, float(value.get("confidence", 0) or 0))), 3)
            except (TypeError, ValueError):
                confidence = 0.0
            decision = _bounded(value.get("decision"), 40)
            memberships[str(update_id)] = {
                "event_id": event_id, "confidence": confidence,
                "matching_signals": list(_safe_tuple(value.get("matching_signals"), 20, 80)),
                "conflicts": list(_safe_tuple(value.get("conflicts"), 20, 80)),
                "decision": decision if decision in DECISIONS else "probable_same_event",
                "updated_at": _bounded(value.get("updated_at"), 80),
            }
    decisions = []
    source = raw.get("decisions")
    allowed_decisions = DECISIONS | {"source_blocked", "membership_removed", "membership_reclassified"}
    if isinstance(source, list):
        for value in source[-MAX_DECISIONS:]:
            if not isinstance(value, dict) or _bounded(value.get("decision"), 40) not in allowed_decisions:
                continue
            decisions.append({
                "update_id": _bounded(value.get("update_id"), 160), "candidate_update_id": _bounded(value.get("candidate_update_id"), 160),
                "event_id": _bounded(value.get("event_id"), 80), "decision": _bounded(value.get("decision"), 40),
                "confidence": value.get("confidence", 0), "matching_signals": list(_safe_tuple(value.get("matching_signals"), 20, 80)),
                "conflicts": list(_safe_tuple(value.get("conflicts"), 20, 80)), "created_at": _bounded(value.get("created_at"), 80),
            })
    clean.update({"events": events, "memberships": memberships, "fingerprints": fingerprints, "decisions": decisions})
    return clean


def _event_state(state: StateStore) -> dict[str, Any]:
    state.data["event_fusion"] = _sanitize_event_state(state.data.get("event_fusion"))
    return state.data["event_fusion"]


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _record(event_state: dict[str, Any], update_id: str, decision: str, *, candidate_update_id: str = "", event_id: str = "", match: EventCandidate | None = None) -> None:
    event_state["decisions"].append({
        "update_id": str(update_id), "candidate_update_id": str(candidate_update_id), "event_id": str(event_id),
        "decision": decision, "confidence": match.confidence if match else 0.0,
        "matching_signals": list(match.matching_signals if match else ()), "conflicts": list(match.conflicts if match else ()),
        "created_at": _now(),
    })
    del event_state["decisions"][:-MAX_DECISIONS]


def _subject_key(left: EventFingerprint, right: EventFingerprint) -> str:
    shared = sorted(_overlap(left.anchor_hashes, right.anchor_hashes) or _overlap(left.reference_hashes, right.reference_hashes) or _overlap(left.topic_hashes, right.topic_hashes))
    raw = chr(31).join(shared[:8]) or chr(31).join(sorted((left.update_id, right.update_id)))
    return f"subject:{_hash('event-subject-v1', raw, 20)}"


def _event_type(left: EventFingerprint, right: EventFingerprint) -> str:
    if left.event_type == right.event_type:
        return left.event_type
    if left.event_type in _GENERIC_TYPES and right.event_type not in _GENERIC_TYPES:
        return right.event_type
    if right.event_type in _GENERIC_TYPES and left.event_type not in _GENERIC_TYPES:
        return left.event_type
    return "unknown"


def _membership(event_state: dict[str, Any], event_id: str, update_id: str, match: EventCandidate) -> None:
    event_state["memberships"][str(update_id)] = {
        "event_id": event_id, "confidence": match.confidence, "matching_signals": list(match.matching_signals),
        "conflicts": list(match.conflicts), "decision": match.decision, "updated_at": _now(),
    }


def _create_event(event_state: dict[str, Any], left: EventFingerprint, right: EventFingerprint, match: EventCandidate) -> str:
    event_id = make_event_id((left.update_id, right.update_id)); now = _now()
    event_state["events"][event_id] = {
        "event_id": event_id, "event_type": _event_type(left, right), "created_at": now, "updated_at": now,
        "member_update_ids": sorted({left.update_id, right.update_id}), "confidence": match.confidence,
        "status": "shadow_candidate", "subject_key": _subject_key(left, right),
    }
    _membership(event_state, event_id, left.update_id, match); _membership(event_state, event_id, right.update_id, match)
    return event_id


def _attach(event_state: dict[str, Any], event_id: str, fp: EventFingerprint, match: EventCandidate) -> str:
    event = event_state["events"].get(event_id)
    if not isinstance(event, dict):
        raise KeyError(event_id)
    event["member_update_ids"] = sorted(set(map(str, event.get("member_update_ids", []))) | {fp.update_id})
    event["updated_at"] = _now(); event["confidence"] = round(min(float(event.get("confidence", 1.0)), match.confidence), 3)
    if event.get("event_type") in _GENERIC_TYPES and fp.event_type not in _GENERIC_TYPES:
        event["event_type"] = fp.event_type
    _membership(event_state, event_id, fp.update_id, match)
    return event_id


def _observe(fp: EventFingerprint, match: EventCandidate | None, status: str, event_id: str = "", member_count: int = 0) -> None:
    observe(
        "shadow_event_grouping", component="event_fusion", stage="shadow_grouping", status=status,
        update_id=fp.update_id, source=fp.source, event_id=event_id, event_type=fp.event_type,
        event_confidence=match.confidence if match else 0.0,
        event_signals=",".join(match.matching_signals if match else ()),
        event_conflicts=",".join(match.conflicts if match else ()), member_count=member_count,
        grouping_mode=EVENT_MODE,
    )


def shadow_group_updates(state: StateStore, updates: Iterable[Update], configured_handles: Iterable[str]) -> list[EventCandidate]:
    """Build durable candidate Events while leaving normal Telegram output unchanged."""
    allowed = {str(item).lstrip("@").strip().casefold() for item in configured_handles if str(item).strip()}
    event_state = _event_state(state)
    incoming = sorted(list(updates), key=lambda item: (ensure_utc(item.created_at), str(item.id)))
    configured: list[Update] = []
    for update in incoming:
        fp = build_fingerprint(update)
        if fp.source not in allowed:
            _record(event_state, update.id, "source_blocked"); _observe(fp, None, "source_blocked"); continue
        configured.append(update); event_state["fingerprints"][fp.update_id] = fp.to_dict()
    all_fps = {
        str(key): EventFingerprint.from_dict(raw) for key, raw in event_state["fingerprints"].items()
        if isinstance(raw, dict)
    }
    results: list[EventCandidate] = []
    rank = {"confident_same_event": 3, "probable_same_event": 2, "ambiguous": 1, "separate_event": 0}
    for update in configured:
        fp = all_fps[update.id]
        if update.id in event_state["memberships"]:
            event_id = str(event_state["memberships"][update.id].get("event_id", "")); event = event_state["events"].get(event_id, {})
            _observe(fp, None, "existing_membership", event_id, len(event.get("member_update_ids", [])) if isinstance(event, dict) else 0); continue
        matches = [(match_fingerprints(fp, other), other) for oid, other in all_fps.items() if oid != update.id]
        if not matches:
            best = EventCandidate(update.id, "", 0.0, (), (), "separate_event"); results.append(best)
            _record(event_state, update.id, best.decision, match=best); _observe(fp, best, best.decision); continue
        matches.sort(key=lambda pair: (rank[pair[0].decision], pair[0].confidence, pair[1].update_id), reverse=True)
        best, other = matches[0]
        plausible_events = {
            str(event_state["memberships"].get(candidate.update_id, {}).get("event_id", ""))
            for match, candidate in matches if match.decision in {"confident_same_event", "probable_same_event"}
            and str(event_state["memberships"].get(candidate.update_id, {}).get("event_id", ""))
        }
        if len(plausible_events) > 1:
            best = EventCandidate(best.left_update_id, best.right_update_id, best.confidence, best.matching_signals,
                                  tuple(sorted(set(best.conflicts) | {"competing_event_candidates"})), "ambiguous")
        results.append(best); event_id = ""
        if best.decision in {"confident_same_event", "probable_same_event"}:
            existing = str(event_state["memberships"].get(other.update_id, {}).get("event_id", ""))
            event_id = _attach(event_state, existing, fp, best) if existing in event_state["events"] else _create_event(event_state, fp, other, best)
        _record(event_state, update.id, best.decision, candidate_update_id=other.update_id, event_id=event_id, match=best)
        event = event_state["events"].get(event_id, {}) if event_id else {}
        _observe(fp, best, best.decision, event_id, len(event.get("member_update_ids", [])) if isinstance(event, dict) else 0)
    _prune(event_state)
    return results


def remove_event_membership(state: StateStore, update_id: str, *, reason: str = "manual_reclassification") -> bool:
    event_state = _event_state(state); membership = event_state["memberships"].pop(str(update_id), None)
    if not isinstance(membership, dict):
        return False
    event_id = str(membership.get("event_id", "")); event = event_state["events"].get(event_id)
    if isinstance(event, dict):
        event["member_update_ids"] = sorted({str(item) for item in event.get("member_update_ids", []) if str(item) != str(update_id)})
        event["updated_at"] = _now()
        if not event["member_update_ids"]:
            event_state["events"].pop(event_id, None)
    _record(event_state, str(update_id), "membership_removed", event_id=event_id)
    event_state["decisions"][-1]["conflicts"] = [str(reason)[:80]]
    return True


def reclassify_event_membership(state: StateStore, update_id: str, target_event_id: str, *, confidence: float = 1.0) -> None:
    event_state = _event_state(state)
    if target_event_id not in event_state["events"]:
        raise KeyError(target_event_id)
    remove_event_membership(state, update_id)
    event_state = _event_state(state); target = event_state["events"].get(target_event_id)
    if not isinstance(target, dict):
        raise KeyError(target_event_id)
    target["member_update_ids"] = sorted(set(map(str, target.get("member_update_ids", []))) | {str(update_id)}); target["updated_at"] = _now()
    synthetic = EventCandidate(str(update_id), "", round(max(0.0, min(1.0, confidence)), 3), ("manual_reclassification",), (), "probable_same_event")
    _membership(event_state, target_event_id, str(update_id), synthetic)
    _record(event_state, str(update_id), "membership_reclassified", event_id=target_event_id, match=synthetic)


def _prune(event_state: dict[str, Any]) -> None:
    if len(event_state.get("fingerprints", {})) > MAX_FINGERPRINTS:
        ordered = sorted(event_state["fingerprints"].items(), key=lambda pair: str(pair[1].get("created_at", "")), reverse=True)[:MAX_FINGERPRINTS]
        event_state["fingerprints"] = dict(ordered)
    if len(event_state.get("events", {})) > MAX_EVENTS:
        ordered = sorted(event_state["events"].items(), key=lambda pair: str(pair[1].get("updated_at", "")), reverse=True)[:MAX_EVENTS]
        event_state["events"] = dict(ordered); kept = set(event_state["events"])
        event_state["memberships"] = {uid: row for uid, row in event_state["memberships"].items() if str(row.get("event_id", "")) in kept}
    event_state["decisions"] = list(event_state.get("decisions", []))[-MAX_DECISIONS:]


def _install_state_extension() -> None:
    if StateStore.__dict__.get("_event_fusion_installed", False):
        return
    _state_module.SCHEMA_VERSION = max(EVENT_SCHEMA_VERSION, int(_state_module.SCHEMA_VERSION))
    original_fresh, original_normalize, original_prune = StateStore._fresh, StateStore._normalize_loaded, StateStore.prune
    def fresh(self):
        data = original_fresh(self); data.setdefault("event_fusion", _fresh_event_state()); data["schema"] = _state_module.SCHEMA_VERSION; return data
    def normalize(self, value):
        data = original_normalize(self, value); data["event_fusion"] = _sanitize_event_state(value.get("event_fusion") if isinstance(value, dict) else None); data["schema"] = _state_module.SCHEMA_VERSION; return data
    def prune(self):
        original_prune(self)
        if isinstance(self.data.get("event_fusion"), dict): _prune(self.data["event_fusion"])
    StateStore._fresh, StateStore._normalize_loaded, StateStore.prune = fresh, normalize, prune
    StateStore._event_fusion_installed = True


def _configured(application: Application) -> set[str]:
    return {str(item.get("handle", "")).lstrip("@").strip().casefold() for item in application.settings.sources if item.get("enabled", True) and str(item.get("handle", "")).strip()}


def _install_shadow_runtime() -> None:
    current = Application.deliver_updates
    if getattr(current, "_event_fusion_shadow_installed", False):
        return
    async def deliver_updates(self, updates: list[Update], *, force: bool) -> None:
        try:
            shadow_group_updates(self.state, updates, _configured(self))
        except Exception as exc:
            observe("shadow_event_grouping", level="warning", component="event_fusion", stage="shadow_grouping", status="failed", error_class=type(exc).__name__, grouping_mode=EVENT_MODE)
        return await current(self, updates, force=force)
    deliver_updates._event_fusion_shadow_installed = True
    Application.deliver_updates = deliver_updates


def install() -> None:
    _observability._ALLOWED_TAGS.update({"event_type", "event_confidence", "event_signals", "event_conflicts", "member_count", "grouping_mode"})
    _install_state_extension(); _install_shadow_runtime()


install()
