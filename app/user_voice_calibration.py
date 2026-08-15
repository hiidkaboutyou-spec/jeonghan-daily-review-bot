"""Deterministic, shadow-only user-voice calibration foundation.

Facts remain owned by source evidence and Translation Fusion/Fidelity.  This module
only measures observable style differences between a faithful factual draft, a
shadow style candidate, and a confirmed user final edit.  Full message bodies are
analysis-time inputs and are never included in durable calibration metadata.

Nothing here owns Telegram delivery, Event/Timeline membership, media, receipts,
seen/delivered state, or Fanfic/AO3 behavior.  Calibration is explicit and
reversible; AUTO_LEARN intentionally remains false.
"""
from __future__ import annotations

import hashlib
import math
import re
from dataclasses import dataclass, field
from statistics import median
from typing import Any, Iterable, Mapping, Sequence

from .channel_style_rewrite import (
    MAX_STYLE_EXAMPLES,
    StyleProfile,
    ai_like_findings,
    score_style_match,
    style_fidelity_failures,
)
from .channel_style_runtime import RetrievedStyleExample

VOICE_CALIBRATION_VERSION = 1
VOICE_CALIBRATION_MODE = "shadow"
AUTO_LEARN = False
MAX_CALIBRATION_RECORDS = 1000
MAX_EVIDENCE_IDS = 80
MIN_GLOBAL_EVIDENCE = 3
MIN_GLOBAL_CATEGORIES = 2
MIN_CATEGORY_EVIDENCE = 3
MIN_AI_PATTERN_EVIDENCE = 3
MAX_RANKING_DELTA = 0.35
HOLDOUT_MODULUS = 5  # deterministic 80/20 split

EDIT_LABELS = frozenset(
    {
        "factual_correction",
        "style_preference",
        "category_specific_preference",
        "one_off_wording",
        "formatting_preference",
        "tone_preference",
        "shortening",
        "expansion",
        "emoji_symbol_preference",
        "dialogue_format_preference",
        "rejected_bot_artifact",
        "ambiguous",
        "unclassified",
    }
)

_KNOWN_CONTENT_TYPES = frozenset(
    {
        "LIVE_DIALOGUE", "WEVERSE_POST", "WEVERSE_LIVE", "FANSIGN", "INTERVIEW",
        "MAGAZINE", "OFFICIAL_NEWS", "BRAND_AD", "FASHION_EVENT", "AIRPORT",
        "INSTAGRAM_UPDATE", "X_FANBASE_UPDATE", "FAN_ACCOUNT_OR_OP_STORY",
        "PHOTO_REACTION", "VIDEO_REACTION", "MEMBER_QUOTE", "MEMBER_INTERACTION",
        "KOREAN_LANGUAGE_NUANCE", "JAPANESE_LANGUAGE_NUANCE", "WORDPLAY",
        "THREAD_OR_LONG_EXPLANATION", "SHORT_REACTION", "FACTUAL_INFORMATION",
        "OTHER",
    }
)

_EMOJI_RE = re.compile(r"[\U0001F000-\U0001FAFF\u2600-\u27BF]")
_PUNCT_RE = re.compile(r"[،؛؟!?.,:…ـ~؛]")
_FORMAL_RE = re.compile(
    r"(?:لازم به ذکر است|شایان ذکر است|در مجموع|به طور کلی|به‌طور کلی|"
    r"علاوه بر این|از سوی دیگر|به عبارت دیگر|به‌عبارت دیگر|می[‌ ]?توان گفت)",
    re.I,
)
_REACTION_RE = re.compile(
    r"(?:😭|🥺|💗|🩷|💘|😂|🤣|🥹|ㅠㅠ|ㅜㅜ|ㅋㅋ|ㅎㅎ|میمیرم|می[‌ ]?میرم|عاشقشم|کیوت|ناز)",
    re.I,
)
_PERSIAN_RE = re.compile(r"[\u0600-\u06ff]")
_LATIN_RE = re.compile(r"[A-Za-z]")
_SPEAKER_RE = re.compile(r"(?m)^\s*[^\s:：]{1,28}\s*[:：]")
_WORD_RE = re.compile(r"[0-9A-Za-z_\u0600-\u06ff\u3040-\u30ff\u3400-\u9fff\uac00-\ud7af]+", re.UNICODE)

_STYLE_FEATURES = (
    "length",
    "line_breaks",
    "emoji",
    "punctuation",
    "formality",
    "reaction",
    "code_switching",
    "dialogue_markers",
)


def _bounded(value: object, limit: int) -> str:
    return str(value or "").strip()[:limit]


def _fingerprint(namespace: str, text: str) -> str:
    value = str(text or "")
    digest = hashlib.sha256(f"uvc-v1\x1f{namespace}\x1f{value}".encode("utf-8")).hexdigest()[:32]
    return f"uvc:{digest}"


def _record_id(update_id: str, factual: str, candidate: str, final: str) -> str:
    digest = hashlib.sha256(
        f"record-v1\x1f{update_id}\x1f{_fingerprint('factual', factual)}\x1f"
        f"{_fingerprint('candidate', candidate)}\x1f{_fingerprint('final', final)}".encode("utf-8")
    ).hexdigest()[:24]
    return f"uvcr:{digest}"


def _ratio_delta(before: float, after: float) -> float:
    scale = max(1.0, abs(before))
    return round(max(-2.0, min(2.0, (after - before) / scale)), 4)


def _feature_vector(text: str) -> dict[str, float]:
    value = str(text or "")
    return {
        "length": round(min(len(value), 800) / 800.0, 4),
        "line_breaks": round(min(value.count("\n"), 8) / 8.0, 4),
        "emoji": round(min(len(_EMOJI_RE.findall(value)), 8) / 8.0, 4),
        "punctuation": round(min(len(_PUNCT_RE.findall(value)), 16) / 16.0, 4),
        "formality": 1.0 if _FORMAL_RE.search(value) else 0.0,
        "reaction": 1.0 if _REACTION_RE.search(value) else 0.0,
        "code_switching": 1.0 if _PERSIAN_RE.search(value) and _LATIN_RE.search(value) else 0.0,
        "dialogue_markers": 1.0 if _SPEAKER_RE.search(value) else 0.0,
    }


def _lexical_change_ratio(before: str, after: str) -> float:
    left = [token.casefold() for token in _WORD_RE.findall(str(before or ""))]
    right = [token.casefold() for token in _WORD_RE.findall(str(after or ""))]
    if not left and not right:
        return 0.0
    common = 0
    remaining: dict[str, int] = {}
    for token in left:
        remaining[token] = remaining.get(token, 0) + 1
    for token in right:
        count = remaining.get(token, 0)
        if count:
            common += 1
            remaining[token] = count - 1
    denominator = max(1, max(len(left), len(right)))
    return round(max(0.0, min(1.0, 1.0 - common / denominator)), 4)


@dataclass(frozen=True, slots=True)
class StyleDeltaFeatures:
    length_delta_ratio: float = 0.0
    line_break_delta: int = 0
    emoji_delta: int = 0
    punctuation_delta: int = 0
    formality_delta: int = 0
    reaction_delta: int = 0
    code_switch_delta: int = 0
    dialogue_marker_delta: int = 0
    lexical_change_ratio: float = 0.0
    removed_ai_like_patterns: tuple[str, ...] = ()
    added_ai_like_patterns: tuple[str, ...] = ()

    def metadata(self) -> dict[str, Any]:
        return {
            "length_delta_ratio": round(max(-2.0, min(2.0, self.length_delta_ratio)), 4),
            "line_break_delta": max(-20, min(20, int(self.line_break_delta))),
            "emoji_delta": max(-20, min(20, int(self.emoji_delta))),
            "punctuation_delta": max(-40, min(40, int(self.punctuation_delta))),
            "formality_delta": max(-1, min(1, int(self.formality_delta))),
            "reaction_delta": max(-1, min(1, int(self.reaction_delta))),
            "code_switch_delta": max(-1, min(1, int(self.code_switch_delta))),
            "dialogue_marker_delta": max(-1, min(1, int(self.dialogue_marker_delta))),
            "lexical_change_ratio": round(max(0.0, min(1.0, self.lexical_change_ratio)), 4),
            "removed_ai_like_patterns": list(self.removed_ai_like_patterns[:12]),
            "added_ai_like_patterns": list(self.added_ai_like_patterns[:12]),
        }


@dataclass(frozen=True, slots=True)
class VoiceCalibrationRecord:
    record_id: str
    update_id: str
    event_id: str
    segment_id: str
    content_type: str
    factual_draft_fingerprint: str
    shadow_candidate_fingerprint: str
    final_user_edit_fingerprint: str
    labels: tuple[str, ...]
    style_delta: StyleDeltaFeatures
    fidelity_passed: bool
    fidelity_failures: tuple[str, ...]
    confidence: float
    eligible_for_learning: bool
    traceable: bool
    translation_conflict: bool
    review_action: str = ""
    created_at: str = ""

    def metadata(self) -> dict[str, Any]:
        return {
            "record_id": _bounded(self.record_id, 64),
            "update_id": _bounded(self.update_id, 96),
            "event_id": _bounded(self.event_id, 80),
            "segment_id": _bounded(self.segment_id, 80),
            "content_type": _bounded(self.content_type, 48),
            "factual_draft_fingerprint": _bounded(self.factual_draft_fingerprint, 80),
            "shadow_candidate_fingerprint": _bounded(self.shadow_candidate_fingerprint, 80),
            "final_user_edit_fingerprint": _bounded(self.final_user_edit_fingerprint, 80),
            "labels": [item for item in self.labels if item in EDIT_LABELS][:12],
            "style_delta": self.style_delta.metadata(),
            "fidelity_passed": bool(self.fidelity_passed),
            "fidelity_failures": [_bounded(item, 120) for item in self.fidelity_failures[:20]],
            "confidence": round(max(0.0, min(1.0, self.confidence)), 4),
            "eligible_for_learning": bool(self.eligible_for_learning),
            "traceable": bool(self.traceable),
            "translation_conflict": bool(self.translation_conflict),
            "review_action": _bounded(self.review_action, 48),
            "created_at": _bounded(self.created_at, 80),
            "auto_learn": False,
            "mode": VOICE_CALIBRATION_MODE,
            "text_persisted": False,
        }


@dataclass(frozen=True, slots=True)
class PreferenceSignal:
    feature: str
    scope: str
    category: str
    evidence_count: int
    category_count: int
    direction: int
    strength: float
    confidence: float
    evidence_record_ids: tuple[str, ...] = ()

    def metadata(self) -> dict[str, Any]:
        return {
            "feature": _bounded(self.feature, 64),
            "scope": self.scope if self.scope in {"global", "category", "ai_pattern"} else "category",
            "category": _bounded(self.category, 48),
            "evidence_count": max(0, min(int(self.evidence_count), MAX_CALIBRATION_RECORDS)),
            "category_count": max(0, min(int(self.category_count), 100)),
            "direction": -1 if self.direction < 0 else (1 if self.direction > 0 else 0),
            "strength": round(max(0.0, min(1.0, self.strength)), 4),
            "confidence": round(max(0.0, min(1.0, self.confidence)), 4),
            "evidence_record_ids": [_bounded(item, 64) for item in self.evidence_record_ids[:MAX_EVIDENCE_IDS]],
        }


@dataclass(frozen=True, slots=True)
class CalibrationSnapshot:
    calibration_version: int
    snapshot_id: str
    category: str
    evidence_record_ids: tuple[str, ...]
    previous_weights: Mapping[str, float]
    new_weights: Mapping[str, float]
    reason: str
    confidence: float
    signals: tuple[PreferenceSignal, ...] = ()
    previous_snapshot_id: str = ""

    def metadata(self) -> dict[str, Any]:
        keys = sorted(set(self.previous_weights) | set(self.new_weights))[:40]
        return {
            "calibration_version": int(self.calibration_version),
            "snapshot_id": _bounded(self.snapshot_id, 80),
            "previous_snapshot_id": _bounded(self.previous_snapshot_id, 80),
            "category": _bounded(self.category, 48),
            "evidence_record_ids": [_bounded(item, 64) for item in self.evidence_record_ids[:MAX_EVIDENCE_IDS]],
            "previous_weights": {key: round(float(self.previous_weights.get(key, 0.0)), 4) for key in keys},
            "new_weights": {key: round(float(self.new_weights.get(key, 0.0)), 4) for key in keys},
            "reason": _bounded(self.reason, 160),
            "confidence": round(max(0.0, min(1.0, self.confidence)), 4),
            "signals": [item.metadata() for item in self.signals[:40]],
            "auto_learn": False,
            "mode": VOICE_CALIBRATION_MODE,
            "text_persisted": False,
        }


def analyze_style_delta(shadow_candidate: str, final_user_text: str, profile: StyleProfile | None = None) -> StyleDeltaFeatures:
    before = str(shadow_candidate or "")
    after = str(final_user_text or "")
    before_findings = set(ai_like_findings(before, profile))
    after_findings = set(ai_like_findings(after, profile))
    before_vec = _feature_vector(before)
    after_vec = _feature_vector(after)
    return StyleDeltaFeatures(
        length_delta_ratio=_ratio_delta(len(before), len(after)),
        line_break_delta=after.count("\n") - before.count("\n"),
        emoji_delta=len(_EMOJI_RE.findall(after)) - len(_EMOJI_RE.findall(before)),
        punctuation_delta=len(_PUNCT_RE.findall(after)) - len(_PUNCT_RE.findall(before)),
        formality_delta=int(after_vec["formality"] - before_vec["formality"]),
        reaction_delta=int(after_vec["reaction"] - before_vec["reaction"]),
        code_switch_delta=int(after_vec["code_switching"] - before_vec["code_switching"]),
        dialogue_marker_delta=int(after_vec["dialogue_markers"] - before_vec["dialogue_markers"]),
        lexical_change_ratio=_lexical_change_ratio(before, after),
        removed_ai_like_patterns=tuple(sorted(before_findings - after_findings)),
        added_ai_like_patterns=tuple(sorted(after_findings - before_findings)),
    )


def _format_only_change(before: str, after: str) -> bool:
    def semantic(value: str) -> str:
        return " ".join(_WORD_RE.findall(str(value or "").casefold()))
    return semantic(before) == semantic(after) and str(before or "") != str(after or "")


def _looks_typo_only(delta: StyleDeltaFeatures, before: str, after: str) -> bool:
    if delta.lexical_change_ratio > 0.08:
        return False
    if abs(len(str(before or "")) - len(str(after or ""))) > 2:
        return False
    return not any(
        (
            delta.line_break_delta,
            delta.emoji_delta,
            delta.punctuation_delta,
            delta.formality_delta,
            delta.reaction_delta,
            delta.code_switch_delta,
            delta.dialogue_marker_delta,
        )
    )


def classify_edit(
    factual_text: str,
    shadow_candidate: str,
    final_user_text: str,
    *,
    content_type: str,
    traceable: bool,
    translation_conflict: bool,
    review_action: str = "",
    profile: StyleProfile | None = None,
) -> tuple[tuple[str, ...], StyleDeltaFeatures, tuple[str, ...], float]:
    factual = str(factual_text or "").strip()
    candidate = str(shadow_candidate or "").strip()
    final = str(final_user_text or "").strip()
    delta = analyze_style_delta(candidate, final, profile)
    failures = tuple(style_fidelity_failures(factual, final)) if final else ("missing_final_user_edit",)
    labels: list[str] = []

    known_category = str(content_type or "") in _KNOWN_CONTENT_TYPES
    if not traceable or not known_category or translation_conflict or not factual or not candidate or not final:
        labels.append("ambiguous")
    if failures:
        labels.append("factual_correction")
    if review_action == "reject":
        labels.append("rejected_bot_artifact")

    if not failures and final:
        if final == candidate:
            labels.append("style_preference")
        else:
            labels.append("style_preference")
            if _format_only_change(candidate, final):
                labels.append("formatting_preference")
            if delta.length_delta_ratio <= -0.10:
                labels.append("shortening")
            elif delta.length_delta_ratio >= 0.10:
                labels.append("expansion")
            if delta.emoji_delta or delta.punctuation_delta:
                labels.append("emoji_symbol_preference" if delta.emoji_delta else "formatting_preference")
            if delta.dialogue_marker_delta:
                labels.append("dialogue_format_preference")
            if delta.formality_delta or delta.reaction_delta or delta.code_switch_delta:
                labels.append("tone_preference")
            if delta.removed_ai_like_patterns:
                labels.append("rejected_bot_artifact")
            if delta.lexical_change_ratio > 0.05:
                labels.append("one_off_wording")

    if _looks_typo_only(delta, candidate, final) and final != candidate:
        labels = [item for item in labels if item not in {"style_preference", "one_off_wording"}]
        labels.append("unclassified")

    labels = list(dict.fromkeys(item for item in labels if item in EDIT_LABELS))
    if not labels:
        labels = ["unclassified"]

    confidence = 1.0
    if "ambiguous" in labels:
        confidence = min(confidence, 0.2)
    if "unclassified" in labels:
        confidence = min(confidence, 0.35)
    if failures:
        confidence = min(confidence, 0.95)
    elif final == candidate:
        confidence = min(confidence, 0.9)
    elif delta.lexical_change_ratio < 0.05 and not _format_only_change(candidate, final):
        confidence = min(confidence, 0.55)
    return tuple(labels), delta, failures, round(confidence, 4)


def build_calibration_record(
    *,
    update_id: str,
    factual_text: str,
    shadow_candidate: str,
    final_user_text: str,
    content_type: str,
    event_id: str = "",
    segment_id: str = "",
    traceable: bool = True,
    translation_conflict: bool = False,
    review_action: str = "",
    created_at: str = "",
    profile: StyleProfile | None = None,
) -> VoiceCalibrationRecord:
    labels, delta, failures, confidence = classify_edit(
        factual_text,
        shadow_candidate,
        final_user_text,
        content_type=content_type,
        traceable=traceable,
        translation_conflict=translation_conflict,
        review_action=review_action,
        profile=profile,
    )
    disallowed = {"factual_correction", "ambiguous", "unclassified"}
    eligible = (
        not failures
        and traceable
        and not translation_conflict
        and content_type in _KNOWN_CONTENT_TYPES
        and not disallowed.intersection(labels)
        and confidence >= 0.6
    )
    return VoiceCalibrationRecord(
        record_id=_record_id(update_id, factual_text, shadow_candidate, final_user_text),
        update_id=_bounded(update_id, 96),
        event_id=_bounded(event_id, 80),
        segment_id=_bounded(segment_id, 80),
        content_type=_bounded(content_type, 48),
        factual_draft_fingerprint=_fingerprint("factual", factual_text),
        shadow_candidate_fingerprint=_fingerprint("candidate", shadow_candidate),
        final_user_edit_fingerprint=_fingerprint("final", final_user_text),
        labels=labels,
        style_delta=delta,
        fidelity_passed=not failures,
        fidelity_failures=failures,
        confidence=confidence,
        eligible_for_learning=eligible,
        traceable=traceable,
        translation_conflict=translation_conflict,
        review_action=_bounded(review_action, 48),
        created_at=_bounded(created_at, 80),
    )


def deterministic_record_split(records: Sequence[VoiceCalibrationRecord]) -> tuple[list[VoiceCalibrationRecord], list[VoiceCalibrationRecord]]:
    calibration: list[VoiceCalibrationRecord] = []
    holdout: list[VoiceCalibrationRecord] = []
    for record in sorted(records, key=lambda item: item.record_id):
        digest = hashlib.sha256(record.record_id.encode("utf-8")).digest()
        bucket = int.from_bytes(digest[:4], "big") % HOLDOUT_MODULUS
        (holdout if bucket == 0 else calibration).append(record)
    return calibration, holdout


def _delta_value(record: VoiceCalibrationRecord, feature: str) -> float:
    delta = record.style_delta
    mapping = {
        "length": delta.length_delta_ratio,
        "line_breaks": float(delta.line_break_delta),
        "emoji": float(delta.emoji_delta),
        "punctuation": float(delta.punctuation_delta),
        "formality": float(delta.formality_delta),
        "reaction": float(delta.reaction_delta),
        "code_switching": float(delta.code_switch_delta),
        "dialogue_markers": float(delta.dialogue_marker_delta),
    }
    return float(mapping.get(feature, 0.0))


def derive_preference_signals(records: Sequence[VoiceCalibrationRecord]) -> tuple[PreferenceSignal, ...]:
    eligible = [item for item in records if item.eligible_for_learning]
    signals: list[PreferenceSignal] = []

    for feature in _STYLE_FEATURES:
        evidence = [item for item in eligible if abs(_delta_value(item, feature)) > 1e-9]
        categories = {item.content_type for item in evidence}
        if len(evidence) >= MIN_GLOBAL_EVIDENCE and len(categories) >= MIN_GLOBAL_CATEGORIES:
            values = [_delta_value(item, feature) for item in evidence]
            med = median(values)
            direction = 1 if med > 0 else (-1 if med < 0 else 0)
            if direction:
                consistency = sum((value > 0) == (direction > 0) for value in values) / len(values)
                if consistency >= 0.67:
                    signals.append(
                        PreferenceSignal(
                            feature=feature,
                            scope="global",
                            category="",
                            evidence_count=len(evidence),
                            category_count=len(categories),
                            direction=direction,
                            strength=min(1.0, abs(float(med))),
                            confidence=round(consistency, 4),
                            evidence_record_ids=tuple(item.record_id for item in evidence[:MAX_EVIDENCE_IDS]),
                        )
                    )

    categories = sorted({item.content_type for item in eligible})
    for category in categories:
        scoped = [item for item in eligible if item.content_type == category]
        for feature in _STYLE_FEATURES:
            evidence = [item for item in scoped if abs(_delta_value(item, feature)) > 1e-9]
            if len(evidence) < MIN_CATEGORY_EVIDENCE:
                continue
            values = [_delta_value(item, feature) for item in evidence]
            med = median(values)
            direction = 1 if med > 0 else (-1 if med < 0 else 0)
            if not direction:
                continue
            consistency = sum((value > 0) == (direction > 0) for value in values) / len(values)
            if consistency < 0.67:
                continue
            signals.append(
                PreferenceSignal(
                    feature=feature,
                    scope="category",
                    category=category,
                    evidence_count=len(evidence),
                    category_count=1,
                    direction=direction,
                    strength=min(1.0, abs(float(med))),
                    confidence=round(consistency, 4),
                    evidence_record_ids=tuple(item.record_id for item in evidence[:MAX_EVIDENCE_IDS]),
                )
            )

    ai_evidence: dict[str, list[VoiceCalibrationRecord]] = {}
    for item in eligible:
        for pattern in item.style_delta.removed_ai_like_patterns:
            ai_evidence.setdefault(pattern, []).append(item)
    for pattern, evidence in sorted(ai_evidence.items()):
        if len(evidence) < MIN_AI_PATTERN_EVIDENCE:
            continue
        categories = {item.content_type for item in evidence}
        signals.append(
            PreferenceSignal(
                feature=f"remove_ai:{pattern}",
                scope="ai_pattern",
                category="",
                evidence_count=len(evidence),
                category_count=len(categories),
                direction=-1,
                strength=1.0,
                confidence=min(1.0, len(evidence) / (MIN_AI_PATTERN_EVIDENCE + 1)),
                evidence_record_ids=tuple(item.record_id for item in evidence[:MAX_EVIDENCE_IDS]),
            )
        )
    return tuple(signals)


def make_calibration_snapshot(
    records: Sequence[VoiceCalibrationRecord],
    *,
    category: str = "",
    previous_weights: Mapping[str, float] | None = None,
    previous_snapshot_id: str = "",
) -> CalibrationSnapshot:
    selected = [item for item in records if item.eligible_for_learning and (not category or item.content_type == category)]
    signals = derive_preference_signals(selected)
    previous = {str(key): float(value) for key, value in (previous_weights or {}).items()}
    new = dict(previous)
    for signal in signals:
        key = f"{signal.scope}:{signal.category or '*'}:{signal.feature}"
        raw = signal.direction * min(MAX_RANKING_DELTA, 0.08 + 0.18 * signal.strength) * signal.confidence
        new[key] = round(max(-MAX_RANKING_DELTA, min(MAX_RANKING_DELTA, raw)), 4)
    evidence_ids = tuple(item.record_id for item in selected[:MAX_EVIDENCE_IDS])
    seed = "|".join(evidence_ids) + "|" + category + "|" + repr(sorted(new.items()))
    snapshot_id = "uvcs:" + hashlib.sha256(seed.encode("utf-8")).hexdigest()[:24]
    confidence = sum(signal.confidence for signal in signals) / len(signals) if signals else 0.0
    return CalibrationSnapshot(
        calibration_version=VOICE_CALIBRATION_VERSION,
        snapshot_id=snapshot_id,
        category=_bounded(category, 48),
        evidence_record_ids=evidence_ids,
        previous_weights=previous,
        new_weights=new,
        reason="repeated eligible style evidence only" if signals else "insufficient repeated evidence; weights unchanged",
        confidence=round(confidence, 4),
        signals=signals,
        previous_snapshot_id=_bounded(previous_snapshot_id, 80),
    )


def rollback_weights(snapshot: CalibrationSnapshot) -> dict[str, float]:
    """Return the exact pre-calibration weights without mutating canonical state."""
    return {str(key): float(value) for key, value in snapshot.previous_weights.items()}


def _signal_applies(signal: PreferenceSignal, content_type: str) -> bool:
    if signal.scope in {"global", "ai_pattern"}:
        return True
    return signal.scope == "category" and signal.category == content_type


def calibrate_example_ranking(
    examples: Sequence[RetrievedStyleExample],
    *,
    content_type: str,
    signals: Sequence[PreferenceSignal],
    limit: int = MAX_STYLE_EXAMPLES,
) -> list[RetrievedStyleExample]:
    """Boundedly re-rank style examples.  Example text never becomes factual context."""
    limit = max(1, min(int(limit), MAX_STYLE_EXAMPLES))
    output: list[RetrievedStyleExample] = []
    for example in examples:
        vector = _feature_vector(example.text)
        total = 0.0
        reasons = list(example.reasons)
        for signal in signals:
            if not _signal_applies(signal, content_type):
                continue
            if signal.scope == "ai_pattern":
                pattern = signal.feature.split(":", 1)[-1]
                has_pattern = pattern in ai_like_findings(example.text)
                contribution = -0.08 * signal.confidence if has_pattern else 0.02 * signal.confidence
            elif signal.feature in vector:
                scalar = vector[signal.feature]
                centered = (2.0 * scalar) - 1.0
                contribution = 0.10 * signal.direction * centered * signal.confidence * max(0.25, signal.strength)
            else:
                contribution = 0.0
            total += contribution
        delta = max(-MAX_RANKING_DELTA, min(MAX_RANKING_DELTA, total))
        if abs(delta) > 1e-9:
            reasons.append(f"bounded user-voice calibration delta={delta:+.4f}")
        output.append(
            RetrievedStyleExample(
                example_id=example.example_id,
                text=example.text,
                content_type=example.content_type,
                source_language=example.source_language,
                date=example.date,
                score=round(float(example.score) + delta, 4),
                reasons=reasons,
            )
        )
    output.sort(key=lambda item: (-item.score, item.example_id))
    return output[:limit]


def compare_shadow_style(
    factual_text: str,
    old_candidate: str,
    calibrated_candidate: str,
    *,
    profile: StyleProfile,
    examples: Sequence[RetrievedStyleExample] = (),
) -> dict[str, Any]:
    """Compare old/calibrated shadow output while keeping the same factual input."""
    old_failures = style_fidelity_failures(factual_text, old_candidate, examples)
    new_failures = style_fidelity_failures(factual_text, calibrated_candidate, examples)
    return {
        "old_fidelity_passed": not old_failures,
        "calibrated_fidelity_passed": not new_failures,
        "old_style_score": score_style_match(old_candidate, profile),
        "calibrated_style_score": score_style_match(calibrated_candidate, profile),
        "old_ai_like_findings": ai_like_findings(old_candidate, profile),
        "calibrated_ai_like_findings": ai_like_findings(calibrated_candidate, profile),
        "unsupported_additions": len(new_failures),
        "historical_leakage": any(item.startswith("historical_example_exclusive_token:") for item in new_failures),
        "text_persisted": False,
    }


def bounded_state_payload(
    records: Sequence[VoiceCalibrationRecord],
    snapshot: CalibrationSnapshot | None = None,
) -> dict[str, Any]:
    """Privacy-safe durable metadata only.  No full factual/candidate/final text."""
    clean_records = list(records)[-MAX_CALIBRATION_RECORDS:]
    return {
        "voice_calibration_version": VOICE_CALIBRATION_VERSION,
        "voice_calibration_mode": VOICE_CALIBRATION_MODE,
        "auto_learn": False,
        "records": {item.record_id: item.metadata() for item in clean_records},
        "active_snapshot": snapshot.metadata() if snapshot is not None else {},
        "text_persisted": False,
    }


def record_bodies_are_absent(payload: Mapping[str, Any]) -> bool:
    """Defensive helper used by tests/state sanitizer."""
    forbidden = {"factual_text", "candidate_text", "final_user_text", "source_text", "generated_text", "text"}

    def walk(value: Any) -> bool:
        if isinstance(value, Mapping):
            for key, nested in value.items():
                if str(key) in forbidden:
                    return False
                if not walk(nested):
                    return False
        elif isinstance(value, (list, tuple)):
            return all(walk(item) for item in value)
        return True

    return walk(payload)
