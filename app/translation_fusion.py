"""Shadow-only Translation Selection / Fusion + Fidelity foundation.

This layer reasons over configured-source evidence that Timeline has already
associated with one Segment. It never owns retrieval, lifecycle, style, media,
Telegram delivery, or public publishing.

Full source/translation bodies remain canonical in Update/archive evidence.
Durable Translation Fusion state stores bounded IDs, classifications, hashes,
scores and review flags only.
"""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from typing import Any, Iterable, Mapping

from . import event_fusion
from .channel_part4_hardening import semantic_number_tokens
from .channel_style_runtime import detect_language, verify_hard_facts
from .models import Update
from .observability import observe
from .state import StateStore

TRANSLATION_FUSION_VERSION = 1
TRANSLATION_FUSION_MODE = "shadow"
MAX_TRANSLATION_EVIDENCE = 5000
MAX_TRANSLATION_RESULTS = 3000
MAX_TRANSLATION_DECISIONS = 4000

AUTO_FUSIBLE_RELATIONSHIPS = frozenset({"complementary", "continuation"})
NON_ADDITIVE_RELATIONSHIPS = frozenset({"same_moment", "seed"})
BLOCKED_RELATIONSHIPS = frozenset({"ambiguous", "separate"})
CONFLICT_RELATIONSHIPS = frozenset({"conflicting"})
FIDELITY_STATUSES = frozenset({
    "faithful_shadow_candidate", "needs_translation", "needs_review", "insufficient_evidence",
})

_PERSIAN_RE = re.compile(r"[\u0600-\u06ff]")
_LATIN_RE = re.compile(r"[A-Za-z]")
_KOREAN_RE = re.compile(r"[\uac00-\ud7af]")
_JAPANESE_RE = re.compile(r"[\u3040-\u30ff]")
_CJK_RE = re.compile(r"[\u3400-\u9fff]")
_QUESTION_RE = re.compile(r"[?؟]")
_NEGATION_SOURCE_RE = re.compile(
    r"\b(?:not|no|never|didn['’]?t|doesn['’]?t|isn['’]?t|wasn['’]?t|"
    r"won['’]?t|cannot|can['’]?t|without)\b|(?:안|못|아니|않)|(?:ない|ません)|"
    r"(?:نه|نیست|نبود|نکرد|نمی[\u200c -]?)",
    re.I,
)
_NEGATION_PERSIAN_RE = re.compile(
    r"(?:^|[\s،؛,:])(?:نه|نیست|نبود|نشد|نکرد|نرفت(?:م|ی|ه|یم|ین|ند)?|"
    r"نگفت(?:م|ی|ه|یم|ین|ند)?|ندید(?:م|ی|ه|یم|ین|ند)?|"
    r"نخواست(?:م|ی|ه|یم|ین|ند)?|نخورد(?:م|ی|ه|یم|ین|ند)?|"
    r"نیومد(?:م|ی|ه|یم|ین|ند)?|نیامد(?:م|ی|ه|یم|ین|ند)?|"
    r"نمی[\u200c -]?|نخواهد|نمیشه|نمی‌شه)",
    re.I,
)
_MODAL_SOURCE_RE = re.compile(
    r"\b(?:may|might|could|possibly|probably|perhaps|seems?|apparently|maybe)\b|"
    r"(?:것 같|듯|아마|かも|らしい|よう)|(?:شاید|احتمال|ممکن|ظاهراً|انگار)",
    re.I,
)
_MODAL_PERSIAN_RE = re.compile(r"(?:شاید|احتمال|ممکن|ظاهراً|انگار|به نظر)", re.I)
_SPEAKER_RE = re.compile(r"(?m)^\s*([^\s:：]{1,28})\s*[:：]")
_QUOTE_RE = re.compile(r'["“”«»]')
_TRANSLATION_LABEL_RE = re.compile(
    r"(?im)^\s*(?:fa(?:rsi)?\s+trans(?:lation)?|persian\s+trans(?:lation)?|ترجمه(?:\s+فارسی)?)\s*[:：]\s*(.+)$"
)
_FAN_TRANSLATION_LABEL_RE = re.compile(
    r"(?im)^\s*(?:fan\s+trans(?:lation)?|eng(?:lish)?\s+trans(?:lation)?|trans(?:lation)?)\s*[:：]\s*.+$"
)
_SUMMARY_LABEL_RE = re.compile(r"(?im)^\s*(?:summary|rough(?:ly)?|paraphrase|خلاصه)\s*[:：]\s*(.+)$")
_ROMANTIC_INFERENCE_RE = re.compile(
    r"(?:عاشق(?:انه)?|دوست[\u200c -]?پسر|دوست[\u200c -]?دختر|رابطه[\u200c -]?عاشقانه|dating|boyfriend|girlfriend|in love)",
    re.I,
)
_CAUSAL_RE = re.compile(r"(?:\b(?:because|therefore|so that|due to)\b|(?:چون|به خاطر|بنابراین|برای همین))", re.I)
_TOKEN_RE = re.compile(r"[\w\u0600-\u06ff\u3040-\u30ff\uac00-\ud7af]+", re.UNICODE)


def _bounded(value: object, limit: int) -> str:
    return str(value or "").strip()[:limit]


def _hash(namespace: str, value: str, length: int = 24) -> str:
    return hashlib.sha256(f"{namespace}\x1f{value}".encode("utf-8")).hexdigest()[:length]


def _norm(value: str) -> str:
    value = str(value or "").casefold()
    value = re.sub(r"https?://\S+", " ", value)
    value = re.sub(r"\s+", " ", value)
    return value.strip(" \n\t.,!?؟،؛:;\"'“”«»")


def _contains_persian(value: str) -> bool:
    text = str(value or "")
    persian = len(_PERSIAN_RE.findall(text))
    latin = len(_LATIN_RE.findall(text))
    return persian >= 4 and persian >= max(4, latin // 2)


def _language(value: str, hinted: str = "") -> str:
    hint = _bounded(hinted, 24).casefold()
    if hint:
        return hint
    try:
        detected = str(detect_language(str(value or ""))).casefold()
        if detected:
            return detected
    except Exception:
        pass
    text = str(value or "")
    scripts = {
        "ko": bool(_KOREAN_RE.search(text)),
        "ja": bool(_JAPANESE_RE.search(text)),
        "fa": bool(_PERSIAN_RE.search(text)),
        "en": bool(_LATIN_RE.search(text)),
        "zh": bool(_CJK_RE.search(text)),
    }
    active = [key for key, present in scripts.items() if present]
    return active[0] if len(active) == 1 else ("mixed" if active else "unknown")


def _evidence_kind(text: str, language: str, candidate_text: str) -> str:
    lowered = str(text or "").casefold()
    if (candidate_text and _TRANSLATION_LABEL_RE.search(text)) or _FAN_TRANSLATION_LABEL_RE.search(text):
        return "direct_translation"
    if _SUMMARY_LABEL_RE.search(text) or any(marker in lowered for marker in ("rough trans", "roughly:", "summary:")):
        return "summary_or_paraphrase"
    if language == "fa":
        return "persian_source"
    if language in {"ko", "ja", "zh"}:
        return "original_language"
    if language == "mixed":
        return "mixed_language"
    if language == "en":
        return "fan_translation_or_description"
    return "unknown"


def _extract_persian_candidate(text: str, language: str) -> str:
    raw = str(text or "").strip()
    match = _TRANSLATION_LABEL_RE.search(raw)
    if match and _contains_persian(match.group(1)):
        return match.group(1).strip()
    if language == "fa" and _contains_persian(raw):
        return raw
    return ""


def _speaker_labels(value: str) -> tuple[str, ...]:
    return tuple(label.strip() for label in _SPEAKER_RE.findall(str(value or "")) if label.strip())[:24]


def _has_negation(value: str, *, persian_output: bool = False) -> bool:
    pattern = _NEGATION_PERSIAN_RE if persian_output else _NEGATION_SOURCE_RE
    return bool(pattern.search(str(value or "")))


def _has_modality(value: str, *, persian_output: bool = False) -> bool:
    pattern = _MODAL_PERSIAN_RE if persian_output else _MODAL_SOURCE_RE
    return bool(pattern.search(str(value or "")))


def _word_overlap(left: str, right: str) -> float:
    a = {_norm(token) for token in _TOKEN_RE.findall(left) if len(_norm(token)) >= 2}
    b = {_norm(token) for token in _TOKEN_RE.findall(right) if len(_norm(token)) >= 2}
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def _media_reference_ids(update: Update) -> tuple[str, ...]:
    values = []
    for item in list(update.media) + list(update.quoted_media):
        if item.url:
            values.append(f"mediaref:{_hash('translation-media-ref-v1', item.kind.casefold() + ':' + item.url, 20)}")
    return tuple(sorted(set(values))[:40])


@dataclass(frozen=True, slots=True)
class TranslationEvidence:
    update_id: str
    source: str
    source_language: str
    evidence_kind: str
    original_text: str = field(repr=False)
    candidate_text: str = field(default="", repr=False)
    event_id: str = ""
    segment_id: str = ""
    relationship: str = "ambiguous"
    relationship_confidence: float = 0.0
    chronology_index: int = 0
    evidence_strength: float = 0.0
    matching_signals: tuple[str, ...] = ()
    conflicts: tuple[str, ...] = ()
    media_reference_ids: tuple[str, ...] = ()

    def metadata(self) -> dict[str, Any]:
        """Bounded durable metadata; never duplicate source/translation bodies."""
        return {
            "update_id": self.update_id,
            "source": self.source,
            "source_language": self.source_language,
            "evidence_kind": self.evidence_kind,
            "event_id": self.event_id,
            "segment_id": self.segment_id,
            "relationship": self.relationship,
            "relationship_confidence": round(max(0.0, min(1.0, self.relationship_confidence)), 3),
            "chronology_index": max(0, min(int(self.chronology_index), 100000)),
            "evidence_strength": round(max(0.0, min(1.0, self.evidence_strength)), 3),
            "matching_signals": list(self.matching_signals)[:24],
            "conflicts": list(self.conflicts)[:24],
            "media_reference_ids": list(self.media_reference_ids)[:40],
            "source_text_hash": _hash("translation-source-text-v1", self.original_text, 32),
            "candidate_text_hash": _hash("translation-candidate-text-v1", self.candidate_text, 32) if self.candidate_text else "",
            "candidate_text_present": bool(self.candidate_text),
        }


@dataclass(frozen=True, slots=True)
class TranslationFusionResult:
    event_id: str
    segment_id: str
    evidence_update_ids: tuple[str, ...]
    backbone_update_id: str
    complementary_update_ids: tuple[str, ...]
    conflict_update_ids: tuple[str, ...]
    fused_factual_text: str = field(repr=False)
    source_languages: tuple[str, ...] = ()
    fidelity_status: str = "insufficient_evidence"
    confidence: float = 0.0
    unresolved_conflicts: tuple[str, ...] = ()
    review_required: bool = True
    reasoning_signals: tuple[str, ...] = ()
    withheld_update_ids: tuple[str, ...] = ()
    fingerprint: str = ""

    def state_metadata(self) -> dict[str, Any]:
        """Persist only explainable outcome metadata, not text or chain-of-thought."""
        return {
            "event_id": self.event_id,
            "segment_id": self.segment_id,
            "evidence_update_ids": list(self.evidence_update_ids),
            "backbone_update_id": self.backbone_update_id,
            "complementary_update_ids": list(self.complementary_update_ids),
            "conflict_update_ids": list(self.conflict_update_ids),
            "source_languages": list(self.source_languages),
            "fidelity_status": self.fidelity_status,
            "confidence": round(max(0.0, min(1.0, self.confidence)), 3),
            "unresolved_conflicts": list(self.unresolved_conflicts)[:40],
            "review_required": bool(self.review_required),
            "reasoning_signals": list(self.reasoning_signals)[:40],
            "withheld_update_ids": list(self.withheld_update_ids)[:100],
            "fingerprint": self.fingerprint,
            "fused_text_hash": _hash("translation-fused-text-v1", self.fused_factual_text, 32) if self.fused_factual_text else "",
            "fused_text_present": bool(self.fused_factual_text),
        }


def fidelity_failures(source: str, candidate: str) -> list[str]:
    """High-confidence factual fidelity gates for a Persian candidate."""
    source = str(source or "").strip()
    candidate = str(candidate or "").strip()
    if not candidate:
        return ["missing faithful Persian candidate"]
    failures = list(verify_hard_facts(source, candidate))

    src_q = bool(_QUESTION_RE.search(source))
    out_q = bool(_QUESTION_RE.search(candidate))
    if src_q != out_q:
        failures.append("question_statement_polarity_changed")

    src_neg = _has_negation(source)
    out_neg = _has_negation(candidate, persian_output=True)
    if src_neg and not out_neg:
        failures.append("negation_dropped")
    if not src_neg and out_neg and len(_TOKEN_RE.findall(source)) >= 3:
        failures.append("negation_invented")

    if _has_modality(source) and not _has_modality(candidate, persian_output=True):
        failures.append("modality_dropped")

    source_speakers = _speaker_labels(source)
    if source_speakers:
        candidate_speakers = _speaker_labels(candidate)
        if len(candidate_speakers) < len(source_speakers):
            failures.append("speaker_attribution_dropped")

    if _QUOTE_RE.search(source) and not _QUOTE_RE.search(candidate):
        failures.append("quoted_vs_paraphrased_distinction_lost")

    if _ROMANTIC_INFERENCE_RE.search(candidate) and not _ROMANTIC_INFERENCE_RE.search(source):
        failures.append("unsupported_relationship_inference")

    if _CAUSAL_RE.search(candidate) and not _CAUSAL_RE.search(source):
        failures.append("unsupported_causal_link")

    return list(dict.fromkeys(failures))


_KIND_SCORE = {
    "direct_translation": 500,
    "persian_source": 470,
    "original_language": 450,
    "mixed_language": 420,
    "fan_translation_or_description": 390,
    "summary_or_paraphrase": 260,
    "unknown": 150,
}
_REL_SCORE = {
    "seed": 60,
    "same_moment": 80,
    "complementary": 35,
    "continuation": 25,
    "conflicting": -100,
    "ambiguous": -700,
    "separate": -1000,
}


def evidence_score(item: TranslationEvidence) -> tuple[int, str]:
    score = _KIND_SCORE.get(item.evidence_kind, 100)
    score += _REL_SCORE.get(item.relationship, -200)
    score += int(round(item.evidence_strength * 100))
    if _QUOTE_RE.search(item.original_text):
        score += 20
    if item.candidate_text:
        failures = fidelity_failures(item.original_text, item.candidate_text)
        score += 100 if not failures else -220 - 20 * len(failures)
    return score, item.update_id


def _pair_conflicts(left: TranslationEvidence, right: TranslationEvidence) -> tuple[str, ...]:
    conflicts = set(left.conflicts) | set(right.conflicts)
    if left.relationship in CONFLICT_RELATIONSHIPS or right.relationship in CONFLICT_RELATIONSHIPS:
        conflicts.add("timeline_relationship_conflicting")

    left_nums = set(semantic_number_tokens(left.original_text))
    right_nums = set(semantic_number_tokens(right.original_text))
    if (
        left_nums and right_nums and left_nums != right_nums
        and {left.relationship, right.relationship}.intersection({"same_moment", "conflicting", "seed"})
    ):
        conflicts.add("number_or_date_conflict")

    left_speakers = set(_speaker_labels(left.original_text))
    right_speakers = set(_speaker_labels(right.original_text))
    if left_speakers and right_speakers and left_speakers != right_speakers:
        symbolic = lambda labels: any(any(not ch.isalnum() for ch in label) for label in labels)
        if symbolic(left_speakers | right_speakers) or left.source_language == right.source_language:
            conflicts.add("speaker_conflict")

    if (
        _has_negation(left.original_text) != _has_negation(right.original_text)
        and _word_overlap(left.original_text, right.original_text) >= 0.35
    ):
        conflicts.add("negation_conflict")
    return tuple(sorted(conflicts))


def _is_redundant(left: str, right: str) -> bool:
    if not left or not right:
        return False
    if _norm(left) == _norm(right):
        return True
    return _word_overlap(left, right) >= 0.78


def _fusion_fingerprint(segment_id: str, evidence_ids: Iterable[str]) -> str:
    ids = sorted({str(item) for item in evidence_ids if str(item)})
    return f"tfp:{_hash('translation-fusion-v1', segment_id + chr(31) + chr(31).join(ids), 28)}"


def fuse_evidence_items(
    items: Iterable[TranslationEvidence],
    *,
    event_id: str = "",
    segment_id: str = "",
) -> TranslationFusionResult:
    evidence = sorted(list(items), key=lambda item: (item.chronology_index, item.update_id))
    ids = tuple(item.update_id for item in evidence)
    if not evidence:
        return TranslationFusionResult(
            event_id=event_id,
            segment_id=segment_id,
            evidence_update_ids=(),
            backbone_update_id="",
            complementary_update_ids=(),
            conflict_update_ids=(),
            fused_factual_text="",
            fidelity_status="insufficient_evidence",
            review_required=True,
            reasoning_signals=("no_configured_segment_evidence",),
            fingerprint=_fusion_fingerprint(segment_id, ()),
        )

    eligible_backbones = [item for item in evidence if item.relationship not in BLOCKED_RELATIONSHIPS]
    if not eligible_backbones:
        return TranslationFusionResult(
            event_id=event_id or evidence[0].event_id,
            segment_id=segment_id or evidence[0].segment_id,
            evidence_update_ids=ids,
            backbone_update_id="",
            complementary_update_ids=(),
            conflict_update_ids=(),
            fused_factual_text="",
            source_languages=tuple(sorted({item.source_language for item in evidence if item.source_language})),
            fidelity_status="needs_review",
            confidence=0.0,
            review_required=True,
            reasoning_signals=("all_evidence_ambiguous_or_separate",),
            withheld_update_ids=ids,
            fingerprint=_fusion_fingerprint(segment_id or evidence[0].segment_id, ids),
        )

    backbone = max(eligible_backbones, key=evidence_score)
    lines: list[str] = []
    reasoning = [f"backbone:{backbone.evidence_kind}"]
    withheld: set[str] = set()
    conflict_ids: set[str] = set()
    unresolved: set[str] = set()
    complementary_ids: list[str] = []

    backbone_failures = fidelity_failures(backbone.original_text, backbone.candidate_text) if backbone.candidate_text else ["missing faithful Persian candidate"]
    if backbone.candidate_text and not backbone_failures:
        lines.append(backbone.candidate_text.strip())
        reasoning.append("backbone_fidelity_pass")
    else:
        reasoning.append("backbone_needs_translation_or_review")

    for item in evidence:
        if item.update_id == backbone.update_id:
            continue
        if item.relationship in BLOCKED_RELATIONSHIPS:
            withheld.add(item.update_id)
            reasoning.append(f"withheld_{item.relationship}")
            continue

        pair_conflicts = _pair_conflicts(backbone, item)
        if pair_conflicts:
            conflict_ids.add(item.update_id)
            unresolved.update(pair_conflicts)
            reasoning.append("conflict_preserved")
            continue

        if item.relationship in NON_ADDITIVE_RELATIONSHIPS:
            if item.candidate_text and any(_is_redundant(existing, item.candidate_text) for existing in lines):
                reasoning.append("equivalent_translation_deduplicated")
            else:
                reasoning.append("alternate_same_moment_not_additive")
            continue

        if item.relationship not in AUTO_FUSIBLE_RELATIONSHIPS:
            withheld.add(item.update_id)
            reasoning.append("withheld_unproven_relationship")
            continue

        if not item.candidate_text:
            withheld.add(item.update_id)
            reasoning.append("complement_needs_translation")
            continue

        failures = fidelity_failures(item.original_text, item.candidate_text)
        if failures:
            withheld.add(item.update_id)
            unresolved.update(f"fidelity:{failure}" for failure in failures)
            reasoning.append("complement_fidelity_blocked")
            continue

        if any(_is_redundant(existing, item.candidate_text) for existing in lines):
            reasoning.append("equivalent_translation_deduplicated")
            continue

        lines.append(item.candidate_text.strip())
        complementary_ids.append(item.update_id)
        reasoning.append(f"complement_added:{item.relationship}")

    fused = "\n".join(line for line in lines if line).strip()
    review_required = bool(unresolved or conflict_ids or withheld or backbone_failures or not fused)
    if unresolved or conflict_ids:
        fidelity_status = "needs_review"
    elif not fused:
        fidelity_status = "needs_translation"
    elif withheld or backbone_failures:
        fidelity_status = "needs_review"
    else:
        fidelity_status = "faithful_shadow_candidate"

    base_score = max(-1000, evidence_score(backbone)[0])
    confidence = max(0.0, min(1.0, (base_score + 1000) / 1700))
    if review_required:
        confidence *= 0.75
    confidence = round(confidence, 3)

    sid = segment_id or backbone.segment_id
    eid = event_id or backbone.event_id
    return TranslationFusionResult(
        event_id=eid,
        segment_id=sid,
        evidence_update_ids=ids,
        backbone_update_id=backbone.update_id,
        complementary_update_ids=tuple(complementary_ids),
        conflict_update_ids=tuple(sorted(conflict_ids)),
        fused_factual_text=fused,
        source_languages=tuple(sorted({item.source_language for item in evidence if item.source_language})),
        fidelity_status=fidelity_status,
        confidence=confidence,
        unresolved_conflicts=tuple(sorted(unresolved)),
        review_required=review_required,
        reasoning_signals=tuple(dict.fromkeys(reasoning)),
        withheld_update_ids=tuple(sorted(withheld)),
        fingerprint=_fusion_fingerprint(sid, ids),
    )


def _fresh_translation_fields() -> dict[str, Any]:
    return {
        "translation_fusion_version": TRANSLATION_FUSION_VERSION,
        "translation_fusion_mode": TRANSLATION_FUSION_MODE,
        "translation_evidence": {},
        "translation_fusion_results": {},
        "translation_fusion_decisions": [],
    }


def _relationship_for_update(fusion: Mapping[str, Any], update_id: str) -> tuple[str, float, tuple[str, ...], tuple[str, ...]]:
    row = fusion.get("segment_memberships", {}).get(str(update_id), {})
    if not isinstance(row, dict):
        return "ambiguous", 0.0, (), ()
    relationship = _bounded(row.get("relationship"), 40) or "ambiguous"
    try:
        confidence = round(max(0.0, min(1.0, float(row.get("confidence", 0) or 0))), 3)
    except (TypeError, ValueError):
        confidence = 0.0
    signals = tuple(sorted({_bounded(item, 80) for item in row.get("matching_signals", []) if _bounded(item, 80)}))[:24]
    conflicts = tuple(sorted({_bounded(item, 80) for item in row.get("conflicts", []) if _bounded(item, 80)}))[:24]
    return relationship, confidence, signals, conflicts


def build_evidence_for_segment(
    state: StateStore,
    segment_id: str,
    configured_handles: Iterable[str],
    incoming: Mapping[str, Update] | None = None,
) -> list[TranslationEvidence]:
    fusion = state.data.get("event_fusion")
    if not isinstance(fusion, dict):
        return []
    segment = fusion.get("segments", {}).get(str(segment_id))
    if not isinstance(segment, dict):
        return []
    event_id = _bounded(segment.get("event_id"), 80)
    allowed = {str(item).lstrip("@").strip().casefold() for item in configured_handles if str(item).strip()}
    incoming = incoming or {}
    evidence: list[TranslationEvidence] = []
    for update_id in map(str, segment.get("member_update_ids", [])):
        update = incoming.get(update_id) or state.get_update(update_id)
        if update is None:
            continue
        source = str(update.author).lstrip("@").strip().casefold()
        if source not in allowed:
            continue
        source_text = update.translation_source().strip()
        language = _language(source_text, update.lang)
        candidate = _extract_persian_candidate(source_text, language)
        relationship, confidence, signals, conflicts = _relationship_for_update(fusion, update_id)
        try:
            chronology = int(segment.get("order_index", 0) or 0)
        except (TypeError, ValueError):
            chronology = 0
        kind = _evidence_kind(source_text, language, candidate)
        base_strength = {
            "direct_translation": 0.95,
            "persian_source": 0.90,
            "original_language": 0.88,
            "mixed_language": 0.82,
            "fan_translation_or_description": 0.76,
            "summary_or_paraphrase": 0.58,
        }.get(kind, 0.45)
        evidence.append(TranslationEvidence(
            update_id=update_id,
            source=source,
            source_language=language,
            evidence_kind=kind,
            original_text=source_text,
            candidate_text=candidate,
            event_id=event_id,
            segment_id=str(segment_id),
            relationship=relationship,
            relationship_confidence=confidence,
            chronology_index=chronology,
            evidence_strength=base_strength,
            matching_signals=signals,
            conflicts=conflicts,
            media_reference_ids=_media_reference_ids(update),
        ))
    return evidence


def _affected_segment_ids(fusion: Mapping[str, Any], incoming_ids: set[str]) -> list[str]:
    result = set()
    memberships = fusion.get("segment_memberships", {})
    if isinstance(memberships, dict):
        for update_id in incoming_ids:
            row = memberships.get(update_id)
            if isinstance(row, dict) and str(row.get("segment_id", "")).startswith("seg:"):
                result.add(str(row["segment_id"]))
    return sorted(result)


def _prune_translation(fusion: dict[str, Any]) -> None:
    evidence = fusion.get("translation_evidence")
    if isinstance(evidence, dict) and len(evidence) > MAX_TRANSLATION_EVIDENCE:
        fusion["translation_evidence"] = dict(list(evidence.items())[-MAX_TRANSLATION_EVIDENCE:])
    results = fusion.get("translation_fusion_results")
    if isinstance(results, dict) and len(results) > MAX_TRANSLATION_RESULTS:
        fusion["translation_fusion_results"] = dict(list(results.items())[-MAX_TRANSLATION_RESULTS:])
    decisions = fusion.get("translation_fusion_decisions")
    if isinstance(decisions, list):
        fusion["translation_fusion_decisions"] = decisions[-MAX_TRANSLATION_DECISIONS:]


def shadow_fuse_translations(
    state: StateStore,
    updates: Iterable[Update],
    configured_handles: Iterable[str],
) -> list[TranslationFusionResult]:
    """Analyze Translation Fusion after Timeline, without changing existing delivery."""
    incoming_list = list(updates)
    incoming = {str(item.id): item for item in incoming_list}
    fusion = state.data.get("event_fusion")
    if not isinstance(fusion, dict):
        return []
    fresh = _fresh_translation_fields()
    for key, value in fresh.items():
        fusion.setdefault(key, value)

    results: list[TranslationFusionResult] = []
    for segment_id in _affected_segment_ids(fusion, set(incoming)):
        evidence = build_evidence_for_segment(state, segment_id, configured_handles, incoming)
        if not evidence:
            continue
        result = fuse_evidence_items(evidence, segment_id=segment_id)
        for item in evidence:
            fusion["translation_evidence"][item.update_id] = item.metadata()
        fusion["translation_fusion_results"][segment_id] = result.state_metadata()
        fusion["translation_fusion_decisions"].append({
            "event_id": result.event_id,
            "segment_id": result.segment_id,
            "backbone_update_id": result.backbone_update_id,
            "evidence_update_ids": list(result.evidence_update_ids),
            "complementary_update_ids": list(result.complementary_update_ids),
            "conflict_update_ids": list(result.conflict_update_ids),
            "fidelity_status": result.fidelity_status,
            "review_required": result.review_required,
            "confidence": result.confidence,
            "fingerprint": result.fingerprint,
        })
        results.append(result)
        observe(
            "shadow_translation_fusion",
            component="translation_fusion",
            stage="translation_fidelity_shadow",
            status=result.fidelity_status,
            update_id=result.backbone_update_id,
            source="configured_segment_evidence",
        )
    _prune_translation(fusion)
    return results
