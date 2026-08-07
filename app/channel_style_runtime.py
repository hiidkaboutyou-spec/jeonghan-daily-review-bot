from __future__ import annotations

import base64
import bz2
import gzip
import hashlib
import re
from pathlib import Path

from .channel_style import (
    CHANNEL_STYLE_VERSION,
    CORPUS_FORMAT_VERSION,
    PROMPT_TEMPLATE_VERSION,
    PERSIAN_RE,
    RetrievedStyleExample,
    SourceAnalysis,
    ChannelStyleMemory as _BaseChannelStyleMemory,
    analyze_source,
    chronological_style_bonus,
    classify_content_type,
    detect_language,
    historical_base_style_weight,
    is_trivial_source,
    legacy_category_to_content_type,
    normalize_numbers,
    verify_hard_facts as _base_verify_hard_facts,
)

_HARD_NAME_GROUPS = {
    "JEONGHAN": ["Jeonghan", "Yoon Jeonghan", "جونگهان", "هانی", "정한", "윤정한", "ジョンハン"],
    "S.COUPS": ["S.Coups", "SCoups", "Seungcheol", "سونگچول", "چول", "에스쿱스", "승철"],
    "JOSHUA": ["Joshua", "جاشوآ", "شوا", "조슈아"],
    "WONWOO": ["Wonwoo", "ونوو", "원우"],
    "MINGYU": ["Mingyu", "مینگیو", "민규"],
    "HOSHI": ["Hoshi", "هوشی", "호시"],
    "DINO": ["Dino", "دینو", "디노"],
}
_SPEAKER_RE = re.compile(r"^\s*([^\s:：]{1,20})\s*[:：]\s*(.+)$", re.M)
_REACTION_RE = re.compile(r"(?:😭|🥺|💗|🩷|💘|گریه|کیوت|ناز|عسلی|تاینی|میمیر)")


def _materialize_compact_corpus(root: Path) -> None:
    """Decode the committed compact corpus into the legacy gzip path expected by v1.

    The source is a deterministic bz2 payload split into ASCII base64 chunks only
    because the repository integration API writes text files more reliably than
    binary blobs. It is still a derived corpus; raw Telegram exports are never
    parsed at bot runtime.
    """
    part_dir = root / "data" / "channel_style_examples_b64"
    parts = sorted(part_dir.glob("part-*.bz2.b64")) if part_dir.exists() else []
    if not parts:
        return
    target = root / "data" / "channel_style_examples.jsonl.gz"
    digest = hashlib.sha256()
    encoded_parts: list[str] = []
    for part in parts:
        raw = part.read_bytes()
        digest.update(part.name.encode("utf-8"))
        digest.update(raw)
        encoded_parts.append(raw.decode("ascii").strip())
    marker = root / ".state" / "channel-style-corpus-source.sha256"
    source_digest = digest.hexdigest()
    try:
        if target.exists() and marker.read_text(encoding="ascii").strip() == source_digest:
            return
    except FileNotFoundError:
        pass
    decoded = base64.b64decode("".join(encoded_parts).encode("ascii"), validate=True)
    text = bz2.decompress(decoded)
    target.parent.mkdir(parents=True, exist_ok=True)
    marker.parent.mkdir(parents=True, exist_ok=True)
    with target.open("wb") as raw:
        with gzip.GzipFile(filename="", mode="wb", fileobj=raw, compresslevel=9, mtime=0) as gz:
            gz.write(text)
    marker.write_text(source_digest, encoding="ascii")


def _content_family(content_type: str) -> str:
    if content_type in {"LIVE_DIALOGUE", "WEVERSE_LIVE", "FANSIGN", "INTERVIEW", "MEMBER_QUOTE"}:
        return "dialogue"
    if content_type in {"PHOTO_REACTION", "VIDEO_REACTION", "SHORT_REACTION", "MEMBER_INTERACTION"}:
        return "reaction"
    if content_type in {"OFFICIAL_NEWS", "FACTUAL_INFORMATION", "BRAND_AD", "FASHION_EVENT", "AIRPORT", "MAGAZINE"}:
        return "information"
    if content_type in {"KOREAN_LANGUAGE_NUANCE", "JAPANESE_LANGUAGE_NUANCE", "WORDPLAY", "THREAD_OR_LONG_EXPLANATION", "FAN_ACCOUNT_OR_OP_STORY"}:
        return "explanation"
    return "general"


def _register_region(text: str, content_type: str) -> str:
    low = str(text or "").casefold()
    family = _content_family(content_type)
    if family == "information" or any(term in low for term in ("اعلام", "official", "اطلاعیه", "ranking", "رتبه")):
        return "factual"
    if family == "reaction" or _REACTION_RE.search(text or ""):
        return "reaction"
    if family == "dialogue":
        return "dialogue"
    if family == "explanation":
        return "explanatory"
    return "conversational"


class ChannelStyleMemory(_BaseChannelStyleMemory):
    """Runtime style memory with equal chronology and content-aware reranking."""

    def __init__(self, root: Path, db_path: Path | None = None):
        root = Path(root)
        _materialize_compact_corpus(root)
        super().__init__(root, db_path=db_path)

    def retrieve_examples(
        self,
        neutral_persian: str,
        analysis: SourceAnalysis,
        *,
        limit: int = 8,
        exclude_example_ids: set[str] | None = None,
    ) -> list[RetrievedStyleExample]:
        # Ask the base FTS/content retriever for the broadest allowed set, then
        # rerank by script/register. Date is deliberately absent from this code.
        candidates = super().retrieve_examples(
            neutral_persian,
            analysis,
            limit=12,
            exclude_example_ids=exclude_example_ids,
        )
        target_register = _register_region(neutral_persian, analysis.content_type)
        target_language = analysis.source_language
        for item in candidates:
            if item.source_language == target_language:
                item.score += 0.30
                item.reasons.append("matching language/script composition")
            if _register_region(item.text, item.content_type) == target_register:
                item.score += 0.45
                item.reasons.append("matching register/emotional region")
        candidates.sort(key=lambda item: (-item.score, item.example_id))
        return candidates[: max(1, min(int(limit), 12))]


def verify_hard_facts(source: str, output: str, analysis: SourceAnalysis | None = None) -> list[str]:
    """Hard fidelity gate run before style quality is accepted."""
    analysis = analysis or analyze_source(source)
    failures = list(_base_verify_hard_facts(source, output, analysis))
    source_turns = _SPEAKER_RE.findall(source)
    output_turns = _SPEAKER_RE.findall(output)
    if len(source_turns) >= 2 and len(output_turns) < len(source_turns):
        failures.append("speaker turn structure lost")
    for speaker, _ in source_turns:
        speaker = speaker.strip()
        if any(ord(ch) > 0x1F000 for ch in speaker) and speaker not in output:
            failures.append(f"missing speaker label: {speaker}")
    source_cf = source.casefold()
    output_cf = output.casefold()
    for canonical, aliases in _HARD_NAME_GROUPS.items():
        if any(form.casefold() in source_cf for form in aliases):
            if not any(form.casefold() in output_cf for form in aliases):
                failures.append(f"name/identity dropped: {canonical}")
    return list(dict.fromkeys(failures))


__all__ = [
    "CHANNEL_STYLE_VERSION", "CORPUS_FORMAT_VERSION", "PROMPT_TEMPLATE_VERSION",
    "SourceAnalysis", "RetrievedStyleExample", "ChannelStyleMemory", "analyze_source",
    "chronological_style_bonus", "classify_content_type", "detect_language",
    "historical_base_style_weight", "is_trivial_source", "legacy_category_to_content_type",
    "normalize_numbers", "verify_hard_facts",
]
