from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

from .channel_quality import (
    classify_content_type as _quality_classify_content_type,
    detect_language as _quality_detect_language,
    diverse_after_rerank,
    target_register,
)
from .channel_style import (
    CHANNEL_STYLE_VERSION,
    CORPUS_FORMAT_VERSION,
    PROMPT_TEMPLATE_VERSION,
    PERSIAN_RE,
    RetrievedStyleExample,
    SourceAnalysis,
    ChannelStyleMemory as _BaseChannelStyleMemory,
    analyze_source as _base_analyze_source,
    chronological_style_bonus,
    historical_base_style_weight,
    is_trivial_source,
    legacy_category_to_content_type as _base_legacy_category_to_content_type,
    normalize_numbers,
    verify_hard_facts as _base_verify_hard_facts,
)

_HARD_NAME_GROUPS = {
    "JEONGHAN": ["Jeonghan", "Yoon Jeonghan", "جونگهان", "هانی", "정한", "윤정한", "ジョンハン"],
    "S.COUPS": ["S.Coups", "SCoups", "Seungcheol", "سونگچول", "چول", "에스쿱스", "승철", "エスクプス"],
    "JOSHUA": ["Joshua", "جاشوآ", "شوا", "조슈아", "ジョシュア"],
    "JUN": ["Jun", "جون", "준", "ジュン"],
    "HOSHI": ["Hoshi", "هوشی", "호시", "ホシ"],
    "WONWOO": ["Wonwoo", "ونوو", "원우", "ウォヌ"],
    "WOOZI": ["Woozi", "ووزی", "우지", "ウジ"],
    "THE8": ["The8", "THE 8", "Minghao", "میونگهو", "디에잇", "명호", "ディエイト"],
    "MINGYU": ["Mingyu", "مینگیو", "민규", "ミンギュ"],
    "DK": ["DK", "Dokyeom", "Seokmin", "دوکیوم", "سوکمین", "도겸", "석민", "ドギョム"],
    "SEUNGKWAN": ["Seungkwan", "سونگکوان", "승관", "スングァン"],
    "VERNON": ["Vernon", "ورنون", "버논", "バーノン"],
    "DINO": ["Dino", "دینو", "디노", "ディノ"],
}
_SPEAKER_RE = re.compile(r"^\s*([^\s:：]{1,24})\s*[:：]\s*(.+)$", re.M)
_QUOTE_MARK_RE = re.compile(r'(?:["“«][^"”»\n]{2,}["”»])')

_GLOSSARY_SOURCE_ALIASES = {
    "جونگهان": ("jeonghan", "yoon jeonghan", "정한", "윤정한", "ジョンハン"),
    "سونگچول": ("seungcheol", "s.coups", "scoups", "승철", "에스쿱스"),
    "جاشوآ": ("joshua", "조슈아", "ジョシュア"),
    "مینگیو": ("mingyu", "민규", "ミンギュ"),
    "ونوو": ("wonwoo", "원우", "ウォヌ"),
    "هوشی": ("hoshi", "호시", "ホシ"),
    "دینو": ("dino", "디노", "ディノ"),
    "سونگکوان": ("seungkwan", "승관", "スングァン"),
    "هانی": ("hannie", "hani", "한이", "ハニ"),
    "کارات": ("carat", "캐럿", "カラット"),
    "سونتین": ("seventeen", "세븐틴", "セブチ"),
    "ویورس": ("weverse", "위버스"),
    "توییتر": ("twitter", "x.com"),
    "اینستاگرام": ("instagram", "insta", "인스타"),
    "فن‌ساین": ("fansign", "fan sign", "팬싸"),
    "فن‌کال": ("fancall", "fan call"),
    "بانیلاکو": ("banila co", "banilaco", "banila"),
}


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
    if content_type in {
        "OFFICIAL_NEWS", "FACTUAL_INFORMATION", "BRAND_AD", "FASHION_EVENT",
        "AIRPORT", "MAGAZINE", "X_FANBASE_UPDATE", "FANFIC_UPDATE",
    }:
        return "information"
    if content_type in {
        "KOREAN_LANGUAGE_NUANCE", "JAPANESE_LANGUAGE_NUANCE", "WORDPLAY",
        "THREAD_OR_LONG_EXPLANATION", "FAN_ACCOUNT_OR_OP_STORY",
    }:
        return "explanation"
    return "general"


def detect_language(text: str) -> str:
    return _quality_detect_language(text)


def classify_content_type(text: str) -> str:
    return _quality_classify_content_type(text)


def analyze_source(text: str, *, hinted_content_type: str | None = None) -> SourceAnalysis:
    base = _base_analyze_source(text)
    content_type = hinted_content_type if hinted_content_type in {
        "LIVE_DIALOGUE", "WEVERSE_POST", "WEVERSE_LIVE", "FANSIGN", "INTERVIEW",
        "MAGAZINE", "OFFICIAL_NEWS", "BRAND_AD", "FASHION_EVENT", "AIRPORT",
        "INSTAGRAM_UPDATE", "X_FANBASE_UPDATE", "FAN_ACCOUNT_OR_OP_STORY",
        "PHOTO_REACTION", "VIDEO_REACTION", "MEMBER_QUOTE", "MEMBER_INTERACTION",
        "KOREAN_LANGUAGE_NUANCE", "JAPANESE_LANGUAGE_NUANCE", "WORDPLAY",
        "THREAD_OR_LONG_EXPLANATION", "SHORT_REACTION", "FACTUAL_INFORMATION",
        "FANFIC_UPDATE", "OTHER",
    } else classify_content_type(text)
    if hinted_content_type == "OTHER":
        content_type = classify_content_type(text)
    base.source_language = detect_language(text)
    base.content_type = content_type
    base.has_dialogue = len(_SPEAKER_RE.findall(str(text or ""))) >= 2 or content_type in {
        "LIVE_DIALOGUE", "WEVERSE_LIVE", "FANSIGN", "INTERVIEW",
    }
    base.platform = _platform_from_text(str(text or ""), content_type)
    return base


def legacy_category_to_content_type(category: str, text: str = "") -> str:
    category = str(category or "")
    fixed = {
        "live": "LIVE_DIALOGUE",
        "jeonghan_instagram": "INSTAGRAM_UPDATE",
        "member_instagram": "INSTAGRAM_UPDATE",
        "brand": "BRAND_AD",
        "fansign": "FANSIGN",
        "airport": "AIRPORT",
    }
    return fixed.get(category, classify_content_type(text))


def _platform_from_text(text: str, content_type: str) -> str:
    lower = text.casefold()
    if "weverse" in lower or "ویورس" in lower or "위버스" in lower or content_type in {"WEVERSE_POST", "WEVERSE_LIVE"}:
        return "weverse"
    if "instagram" in lower or "اینستاگرام" in lower or "인스타" in lower or content_type == "INSTAGRAM_UPDATE":
        return "instagram"
    if "youtube" in lower or "youtu.be" in lower:
        return "youtube"
    if content_type == "X_FANBASE_UPDATE":
        return "x"
    return ""


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
                self.conn.execute(
                    "CREATE VIRTUAL TABLE channel_style_fts USING fts5("
                    "example_id UNINDEXED,text,content_type,source_language,platform,"
                    "tokenize='unicode61 remove_diacritics 2')"
                )
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
                        "INSERT INTO channel_style_examples("
                        "example_id,channel_id,message_id,text,content_type,source_language,date,platform,"
                        "line_count,char_count,has_dialogue,has_laughter,has_media,format_prefix,base_style_weight,raw_json"
                        ") VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
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
        return _platform_from_text(text, content_type)

    def retrieve_examples(
        self,
        neutral_persian: str,
        analysis: SourceAnalysis,
        *,
        limit: int = 8,
        exclude_example_ids: set[str] | None = None,
    ) -> list[RetrievedStyleExample]:
        candidates = super().retrieve_examples(
            neutral_persian,
            analysis,
            limit=12,
            exclude_example_ids=exclude_example_ids,
        )
        target_reg = target_register(analysis.content_type, neutral_persian)
        target_language = analysis.source_language
        target_lines = max(1, analysis.line_count)
        for item in candidates:
            if item.source_language == target_language:
                item.score += 0.30
                item.reasons.append("matching language/script composition")
            item_reg = target_register(item.content_type, item.text)
            if item_reg == target_reg:
                item.score += 0.45
                item.reasons.append("matching register/emotional region")
            item_lines = max(1, item.text.count("\n") + 1)
            line_ratio = min(item_lines, target_lines) / max(item_lines, target_lines)
            item.score += 0.20 * line_ratio
            if line_ratio >= 0.7:
                item.reasons.append("matching line/format structure")
        candidates.sort(key=lambda item: (-item.score, item.example_id))
        return diverse_after_rerank(candidates, max(6, min(int(limit), 12)))

    def relevant_glossary(self, source: str, neutral_persian: str, *, max_entries: int = 18) -> list[dict]:
        result = super().relevant_glossary(source, neutral_persian, max_entries=max_entries)
        seen = {(str(item.get("category", "")), str(item.get("canonical_form", ""))) for item in result}
        haystack = f"{source}\n{neutral_persian}".casefold()
        compact = re.sub(r"[\s\u200c_\-]+", "", haystack)
        categories = self.glossary.get("categories", {}) if isinstance(self.glossary, dict) else {}
        for category, entries in categories.items() if isinstance(categories, dict) else []:
            if not isinstance(entries, list):
                continue
            for entry in entries:
                if not isinstance(entry, dict):
                    continue
                canonical = str(entry.get("canonical_form", "")).strip()
                key = (str(category), canonical)
                if not canonical or key in seen:
                    continue
                forms = [canonical, *[str(x) for x in entry.get("alternatives", []) if str(x).strip()]]
                aliases = list(_GLOSSARY_SOURCE_ALIASES.get(canonical, ()))
                matched = any(
                    form.casefold() in haystack
                    or re.sub(r"[\s\u200c_\-]+", "", form.casefold()) in compact
                    for form in forms + aliases
                    if form
                )
                if matched:
                    clean = dict(entry)
                    clean["category"] = category
                    result.append(clean)
                    seen.add(key)
                    if len(result) >= max_entries:
                        return result
        return result


def _canonical_identity(value: str) -> str:
    low = str(value or "").casefold()
    for canonical, aliases in _HARD_NAME_GROUPS.items():
        if any(alias.casefold() in low for alias in aliases):
            return canonical
    return ""


def verify_hard_facts(source: str, output: str, analysis: SourceAnalysis | None = None) -> list[str]:
    analysis = analysis or analyze_source(source)
    failures = list(_base_verify_hard_facts(source, output, analysis))

    source_turns = _SPEAKER_RE.findall(source)
    output_turns = _SPEAKER_RE.findall(output)
    if len(source_turns) >= 2 and len(output_turns) < len(source_turns):
        failures.append("speaker turn structure lost")
    if source_turns and output_turns:
        source_ids = [_canonical_identity(speaker) for speaker, _ in source_turns]
        output_ids = [_canonical_identity(speaker) for speaker, _ in output_turns]
        known_source = [item for item in source_ids if item]
        known_output = [item for item in output_ids if item]
        if known_source and known_output and known_source != known_output[:len(known_source)]:
            failures.append("speaker identity/order changed")
    for speaker, _ in source_turns:
        speaker = speaker.strip()
        if any(ord(ch) > 0x1F000 for ch in speaker) and speaker not in output:
            failures.append(f"missing speaker label: {speaker}")

    source_cf = source.casefold()
    output_cf = output.casefold()
    for canonical, aliases in _HARD_NAME_GROUPS.items():
        in_source = any(form.casefold() in source_cf for form in aliases)
        in_output = any(form.casefold() in output_cf for form in aliases)
        if in_source:
            if not in_output:
                failures.append(f"name/identity dropped: {canonical}")
        elif in_output:
            failures.append(f"invented name/identity: {canonical}")

    if _QUOTE_MARK_RE.search(source) and not _QUOTE_MARK_RE.search(output):
        failures.append("quoted material/attribution structure lost")

    return list(dict.fromkeys(failures))


__all__ = [
    "CHANNEL_STYLE_VERSION", "CORPUS_FORMAT_VERSION", "PROMPT_TEMPLATE_VERSION",
    "SourceAnalysis", "RetrievedStyleExample", "ChannelStyleMemory", "analyze_source",
    "chronological_style_bonus", "classify_content_type", "detect_language",
    "historical_base_style_weight", "is_trivial_source", "legacy_category_to_content_type",
    "normalize_numbers", "verify_hard_facts",
]
