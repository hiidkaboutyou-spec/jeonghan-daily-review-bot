from __future__ import annotations

import hashlib
import json
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


def _load_manifest(root: Path) -> tuple[dict, Path]:
    manifest_path = Path(root) / "data" / "channel_style" / "manifest.json"
    try:
        value = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}, manifest_path
    return (value if isinstance(value, dict) else {}), manifest_path


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
    """Channel style memory backed by deterministic UTF-8 JSONL shards.

    Runtime reads only data/channel_style/manifest.json and the ordered shard files
    listed there. Raw Telegram exports and compressed/base64 materialization are not
    part of normal startup.
    """

    def __init__(self, root: Path, db_path: Path | None = None):
        self._shard_manifest, self._shard_manifest_path = _load_manifest(Path(root))
        super().__init__(Path(root), db_path=db_path)

    def _corpus_files(self) -> list[Path]:
        manifest = self._shard_manifest
        if not manifest:
            manifest, path = _load_manifest(self.root)
            self._shard_manifest, self._shard_manifest_path = manifest, path
        base = self._shard_manifest_path.parent
        result: list[Path] = []
        for entry in manifest.get("shards", []) if isinstance(manifest, dict) else []:
            if not isinstance(entry, dict):
                continue
            name = str(entry.get("filename", "")).strip()
            if name:
                result.append(base / name)
        return result

    def _corpus_digest(self) -> str:
        manifest = self._shard_manifest
        if not manifest or not self._shard_manifest_path.exists():
            return "missing"
        digest = hashlib.sha256()
        manifest_raw = self._shard_manifest_path.read_bytes()
        digest.update(self._shard_manifest_path.name.encode("utf-8"))
        digest.update(manifest_raw)
        for path in self._corpus_files():
            if not path.exists():
                return "missing"
            digest.update(path.name.encode("utf-8"))
            digest.update(path.read_bytes())
        return digest.hexdigest()

    def rebuild_from_derived_corpus(self) -> int:
        try:
            self._init_schema()
        except Exception:
            pass
        files = self._corpus_files()
        if not files:
            return 0
        rows: list[dict] = []
        try:
            for path in files:
                with path.open("r", encoding="utf-8") as fh:
                    for line in fh:
                        if not line.strip():
                            continue
                        item = json.loads(line)
                        if isinstance(item, dict) and str(item.get("text", "")).strip() and str(item.get("example_id", "")).strip():
                            rows.append(item)
        except (OSError, json.JSONDecodeError):
            return 0
        try:
            with self.conn:
                self.conn.execute("DROP TABLE IF EXISTS channel_style_fts")
                self.conn.execute("DELETE FROM channel_style_examples")
                self.conn.execute("CREATE VIRTUAL TABLE channel_style_fts USING fts5(example_id UNINDEXED,text,content_type,source_language,platform,tokenize='unicode61 remove_diacritics 2')")
                for item in rows:
                    text = str(item.get("text", ""))
                    content_type = str(item.get("content_type", "OTHER"))
                    source_language = str(item.get("source_language", "other"))
                    platform = self._platform_for_runtime(text, content_type)
                    values = (
                        str(item.get("example_id", "")), str(item.get("channel_id", "")), str(item.get("message_id", "")),
                        text, content_type, source_language, str(item.get("date", "")), platform,
                        int(item.get("line_count", 1) or 1), int(item.get("char_count", len(text)) or len(text)),
                        int(bool(item.get("has_dialogue"))), int(bool(item.get("has_laughter"))), int(bool(item.get("has_media"))),
                        str(item.get("format_prefix", "")), float(item.get("base_style_weight", 0) or 0),
                        json.dumps(item, ensure_ascii=False, separators=(",", ":")),
                    )
                    self.conn.execute(
                        "INSERT INTO channel_style_examples(example_id,channel_id,message_id,text,content_type,source_language,date,platform,line_count,char_count,has_dialogue,has_laughter,has_media,format_prefix,base_style_weight,raw_json) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                        values,
                    )
                    self.conn.execute(
                        "INSERT INTO channel_style_fts(example_id,text,content_type,source_language,platform) VALUES(?,?,?,?,?)",
                        (values[0], values[3], values[4], values[5], values[7]),
                    )
                self._set_meta("style_version", CHANNEL_STYLE_VERSION)
                self._set_meta("corpus_format_version", CORPUS_FORMAT_VERSION)
                self._set_meta("prompt_template_version", PROMPT_TEMPLATE_VERSION)
                self._set_meta("corpus_sha256", self._corpus_digest())
                self._set_meta("example_count", len(rows))
            return len(rows)
        except Exception:
            return 0

    @staticmethod
    def _platform_for_runtime(text: str, content_type: str) -> str:
        lower = text.casefold()
        if "weverse" in lower or "ویورس" in lower or "위버스" in lower or content_type in {"WEVERSE_POST", "WEVERSE_LIVE"}:
            return "weverse"
        if "instagram" in lower or "اینستاگرام" in lower or content_type == "INSTAGRAM_UPDATE":
            return "instagram"
        if "youtube" in lower or "youtu.be" in lower:
            return "youtube"
        if content_type == "X_FANBASE_UPDATE":
            return "x"
        return ""

    def retrieve_examples(self, neutral_persian: str, analysis: SourceAnalysis, *, limit: int = 8, exclude_example_ids: set[str] | None = None) -> list[RetrievedStyleExample]:
        candidates = super().retrieve_examples(neutral_persian, analysis, limit=12, exclude_example_ids=exclude_example_ids)
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
        if any(form.casefold() in source_cf for form in aliases) and not any(form.casefold() in output_cf for form in aliases):
            failures.append(f"name/identity dropped: {canonical}")
    return list(dict.fromkeys(failures))


__all__ = [
    "CHANNEL_STYLE_VERSION", "CORPUS_FORMAT_VERSION", "PROMPT_TEMPLATE_VERSION",
    "SourceAnalysis", "RetrievedStyleExample", "ChannelStyleMemory", "analyze_source",
    "chronological_style_bonus", "classify_content_type", "detect_language",
    "historical_base_style_weight", "is_trivial_source", "legacy_category_to_content_type",
    "normalize_numbers", "verify_hard_facts",
]
