"""Shadow-only Channel Style Rewrite / User Voice foundation.

The faithful factual Persian draft is the only factual authority. Historical
channel examples are form/style demonstrations only and never factual context.
This module is provider-neutral, text-only, bounded, free, and never owns
Telegram delivery, lifecycle, media, Event/Timeline, or public publishing.
"""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from statistics import median
from typing import Any, Iterable, Mapping, Protocol, Sequence

from .channel_quality import target_register
from .channel_style_runtime import RetrievedStyleExample, analyze_source, classify_content_type
from .direct_style_rules import (
    DEFAULT_AUTHORITY_ORDER,
    DIRECT_STYLE_RULES_MODE,
    DIRECT_STYLE_RULES_VERSION,
    DirectStyleEvidence,
    DirectStylePlanner,
    StyleDirective,
)
from .observability import observe
from .translation_fusion import (
    TranslationFusionResult,
    build_evidence_for_segment,
    fidelity_failures as translation_fidelity_failures,
    fuse_evidence_items,
)

STYLE_REWRITE_VERSION = 1
STYLE_REWRITE_MODE = "shadow"
MAX_STYLE_EXAMPLES = 5
MAX_STYLE_RESULTS = 3000
MAX_DIRECT_STYLE_SYMBOL_HISTORY = 4
MIN_PROFILE_EXAMPLES = 12
STYLE_SCORE_THRESHOLD = 0.34

_TOKEN_RE = re.compile(r"[0-9A-Za-z_\u0600-\u06ff\u3040-\u30ff\u3400-\u9fff\uac00-\ud7af]+", re.UNICODE)
_URL_RE = re.compile(r"https?://\S+")
_NUMBER_RE = re.compile(r"(?<!\w)[+\-]?(?:\d[\d,.:/\-]*\d|\d)(?!\w)")
_SPEAKER_RE = re.compile(r"(?m)^\s*([^\s:：]{1,28})\s*[:：]")
_EMOJI_RE = re.compile(r"[\U0001F000-\U0001FAFF\u2600-\u27BF]")
_REACTION_RE = re.compile(
    r"(?:😭|🥺|💗|🩷|💘|😂|🤣|🥹|ㅠㅠ|ㅜㅜ|دارم\s*می[‌ ]?میرم|میمیرم|عاشقشم|کیوت|ناز|ㅋㅋ|ㅎㅎ)",
    re.I,
)
_FORMAL_AI_RE = re.compile(
    r"(?:لازم به ذکر است|شایان ذکر است|در مجموع|به طور کلی|به‌طور کلی|"
    r"علاوه بر این|از سوی دیگر|به عبارت دیگر|به‌عبارت دیگر|"
    r"این موضوع نشان می[‌ ]?دهد|می[‌ ]?توان گفت|در نتیجه)",
    re.I,
)
_GENERIC_FILLER_RE = re.compile(
    r"(?:واقعاً باید گفت|بدون شک|همان[‌ ]?طور که می[‌ ]?دانید|"
    r"به نوعی|در حقیقت باید گفت|در نهایت می[‌ ]?توان گفت)",
    re.I,
)
_OVER_CUTE_RE = re.compile(
    r"(?:بچه[‌ ]?م|عسلم|عسلکم|کوچولوم|نازنینم|پرنسس|دارم\s*می[‌ ]?میرم|میمیرم|عاشقشم)",
    re.I,
)
# Translation Fusion compares multilingual source -> Persian candidate. Style Rewrite
# compares Persian factual -> Persian candidate, so it needs a Persian boundary-safe
# negation detector. This avoids false positives such as the final "نه" in "خونه"
# while recognizing common colloquial/standard negative verb forms such as "نرفت".
_STYLE_NEGATION_RE = re.compile(
    r"(?<![\w\u200c])(?:"
    r"نه|نیست|نبود|نشد|"
    r"نکرد(?:م|ی|ه|یم|ین|ند)?|"
    r"نرفت(?:م|ی|ه|یم|ین|ند)?|"
    r"نگفت(?:م|ی|ه|یم|ین|ند)?|"
    r"ندید(?:م|ی|ه|یم|ین|ند)?|"
    r"نخواست(?:م|ی|ه|یم|ین|ند)?|"
    r"نخورد(?:م|ی|ه|یم|ین|ند)?|"
    r"نیومد(?:م|ی|ه|یم|ین|ند)?|"
    r"نیامد(?:م|ی|ه|یم|ین|ند)?|"
    r"نمی[\u200c ]?[\u0600-\u06ff]+|"
    r"نخواهد[\u0600-\u06ff]*|نمیشه|نمی[\u200c ]?شه"
    r")(?![\w\u200c])",
    re.I,
)
_TEMPORAL_TERMS = (
    "قبل", "بعد", "اول", "بعدش", "سپس", "امروز", "دیروز", "فردا",
    "صبح", "ظهر", "عصر", "شب", "قبلاً", "بعداً",
)
_SAFE_STYLE_TOKENS = {
    "و", "یا", "که", "رو", "را", "به", "از", "با", "برای", "توی", "در", "یه", "یک",
    "این", "اون", "آن", "هم", "همین", "همون", "ولی", "اما", "اگه", "اگر", "دیگه",
    "خب", "پس", "چون", "وقتی", "مثل", "مثلا", "مثلاً", "فقط", "خیلی", "واقعا", "واقعاً",
    "من", "تو", "ما", "شما", "اونا", "ایشون", "است", "هست", "بود", "شد", "می",
    "ـه", "ـش", "ش", "ی", "ای", "ها", "های", "تر", "ترین",
}
_IDENTITY_ALIASES: dict[str, tuple[str, ...]] = {
    "JEONGHAN": ("jeonghan", "yoon jeonghan", "جونگهان", "هانی", "정한", "윤정한", "ジョンハン"),
    "SCOUPS": ("s.coups", "scoups", "seungcheol", "سونگچول", "چول", "승철", "에스쿱스", "エスクプス"),
    "JOSHUA": ("joshua", "جاشوآ", "شوا", "조슈아", "ジョシュア"),
    "JUN": ("jun", "جون", "준", "ジュン"),
    "HOSHI": ("hoshi", "هوشی", "호시", "ホシ"),
    "WONWOO": ("wonwoo", "ونوو", "원우", "ウォヌ"),
    "WOOZI": ("woozi", "ووزی", "우지", "ウジ"),
    "THE8": ("the8", "the 8", "minghao", "میونگهو", "디에잇", "명호", "ディエイト"),
    "MINGYU": ("mingyu", "مینگیو", "민규", "ミンギュ"),
    "DK": ("dk", "dokyeom", "seokmin", "دوکیوم", "سوکمین", "도겸", "석민", "ドギョム"),
    "SEUNGKWAN": ("seungkwan", "سونگکوان", "승관", "スングァン"),
    "VERNON": ("vernon", "ورنون", "버논", "バーノン"),
    "DINO": ("dino", "دینو", "디노", "ディノ"),
}
_REQUESTED_PROFILE_RULES: dict[str, tuple[set[str], tuple[str, ...]]] = {
    "live_translation": ({"LIVE_DIALOGUE", "WEVERSE_LIVE"}, (" live", "لایو", "라이브", "ライブ")),
    "going_seventeen": (set(), ("going seventeen", "gose", "고잉 세븐틴", "گوئینگ سونتین")),
    "variety_reality": (set(), ("variety", "reality", "ریلیتی", "ورایتی", "예능", "여행", "trip")),
    "interview": ({"INTERVIEW", "MAGAZINE"}, ("interview", "مصاحبه", "인터뷰", "インタビュー")),
    "official_update": ({"OFFICIAL_NEWS", "FACTUAL_INFORMATION"}, ("official", "공지", "اطلاعیه", "اعلام رسمی")),
    "photo_video_update": ({"PHOTO_REACTION", "VIDEO_REACTION", "INSTAGRAM_UPDATE"}, ("photo", "video", "عکس", "ویدیو", "사진", "영상")),
    "fansign_video_call": ({"FANSIGN"}, ("fansign", "fancall", "فن‌ساین", "فن‌کال", "팬싸")),
    "concert_ment": (set(), ("concert", "fancon", "caratland", "کنسرت", "콘서트", "멘트")),
    "social_casual_update": ({"WEVERSE_POST", "INSTAGRAM_UPDATE", "X_FANBASE_UPDATE"}, ("weverse", "instagram", "ویورس", "اینستاگرام")),
    "brand_event": ({"BRAND_AD", "FASHION_EVENT"}, ("brand", "campaign", "بانیلا", "fashion", "کمپین")),
}


@dataclass(frozen=True, slots=True)
class StyleProfile:
    key: str
    content_type: str
    example_count: int
    register: str
    median_chars: float
    multiline_pct: float
    dialogue_pct: float
    emoji_pct: float
    reaction_pct: float
    formal_connector_pct: float
    supported: bool = True

    @property
    def intensity(self) -> str:
        signal = max(self.emoji_pct, self.reaction_pct)
        if signal < 0.22:
            return "restrained"
        if signal < 0.55:
            return "balanced"
        return "expressive"

    def metadata(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "content_type": self.content_type,
            "example_count": self.example_count,
            "register": self.register,
            "median_chars": round(self.median_chars, 2),
            "multiline_pct": round(self.multiline_pct, 4),
            "dialogue_pct": round(self.dialogue_pct, 4),
            "emoji_pct": round(self.emoji_pct, 4),
            "reaction_pct": round(self.reaction_pct, 4),
            "formal_connector_pct": round(self.formal_connector_pct, 4),
            "intensity": self.intensity,
            "supported": self.supported,
        }


@dataclass(frozen=True, slots=True)
class StyleRewriteInput:
    faithful_factual_text: str
    event_id: str
    segment_id: str
    content_type: str
    speaker_metadata: tuple[str, ...]
    hard_factual_invariants: Mapping[str, tuple[str, ...]]
    style_profile: str
    selected_style_example_ids: tuple[str, ...]
    direct_style_rule_id: str = ""
    direct_style_category: str = "generic"
    authority_order: tuple[str, ...] = DEFAULT_AUTHORITY_ORDER


@dataclass(frozen=True, slots=True)
class StyleRewriteResult:
    event_id: str
    segment_id: str
    content_type: str
    style_profile: str
    selected_style_example_ids: tuple[str, ...]
    factual_text: str = field(repr=False)
    candidate_text: str = field(default="", repr=False)
    final_text: str = field(default="", repr=False)
    fidelity_failures: tuple[str, ...] = ()
    style_score: float = 0.0
    accepted: bool = False
    fallback_reason: str = ""
    review_required: bool = True
    provider: str = "local_conservative"
    factual_fingerprint: str = ""
    candidate_fingerprint: str = ""
    direct_style_rule_id: str = ""
    direct_style_category: str = "generic"
    direct_style_applied: bool = False
    direct_style_fallback_reason: str = "no_matching_direct_rule"
    direct_style_symbol: str = ""
    authority_order: tuple[str, ...] = DEFAULT_AUTHORITY_ORDER

    def state_metadata(self) -> dict[str, Any]:
        return {
            "event_id": self.event_id[:80],
            "segment_id": self.segment_id[:80],
            "content_type": self.content_type[:48],
            "style_profile": self.style_profile[:80],
            "selected_style_example_ids": list(self.selected_style_example_ids[:MAX_STYLE_EXAMPLES]),
            "factual_draft_fingerprint": self.factual_fingerprint[:80],
            "style_candidate_fingerprint": self.candidate_fingerprint[:80],
            "fidelity_passed": not self.fidelity_failures,
            "fidelity_failures": list(self.fidelity_failures[:20]),
            "style_score": round(max(0.0, min(1.0, self.style_score)), 4),
            "accepted": bool(self.accepted),
            "fallback_reason": self.fallback_reason[:80],
            "review_required": bool(self.review_required),
            "provider": self.provider[:48],
            "mode": STYLE_REWRITE_MODE,
            "direct_style_rules_version": DIRECT_STYLE_RULES_VERSION,
            "direct_style_rules_mode": DIRECT_STYLE_RULES_MODE,
            "direct_style_rule_id": self.direct_style_rule_id[:80],
            "direct_style_category": self.direct_style_category[:48],
            "direct_style_applied": bool(self.direct_style_applied),
            "direct_style_fallback_reason": self.direct_style_fallback_reason[:80],
            "direct_style_symbol": self.direct_style_symbol[:32],
            "authority_order": list(self.authority_order),
            "text_persisted": False,
        }


@dataclass(frozen=True, slots=True)
class StyleEditFeedback:
    event_id: str
    segment_id: str
    content_type: str
    factual_draft_fingerprint: str
    bot_style_fingerprint: str
    final_edit_fingerprint: str
    feedback_kind: str = "unclassified"
    confirmed: bool = False

    def metadata(self) -> dict[str, Any]:
        allowed = {
            "unclassified", "factual_correction", "style_preference",
            "category_specific_preference", "one_off_wording",
        }
        kind = self.feedback_kind if self.feedback_kind in allowed else "unclassified"
        return {
            "event_id": self.event_id[:80],
            "segment_id": self.segment_id[:80],
            "content_type": self.content_type[:48],
            "factual_draft_fingerprint": self.factual_draft_fingerprint[:80],
            "bot_style_fingerprint": self.bot_style_fingerprint[:80],
            "final_edit_fingerprint": self.final_edit_fingerprint[:80],
            "feedback_kind": kind,
            "confirmed": bool(self.confirmed),
            "auto_learn": False,
        }


class StyleRewriteProvider(Protocol):
    name: str

    def rewrite(
        self,
        rewrite_input: StyleRewriteInput,
        examples: Sequence[RetrievedStyleExample],
        profile: StyleProfile,
    ) -> str:
        ...


class ConservativeLocalStyleProvider:
    """Free deterministic surface-only provider for the shadow foundation."""

    name = "local_conservative"

    def rewrite(
        self,
        rewrite_input: StyleRewriteInput,
        examples: Sequence[RetrievedStyleExample],
        profile: StyleProfile,
    ) -> str:
        del examples, profile
        return normalize_persian_surface(rewrite_input.faithful_factual_text)


def _fingerprint(namespace: str, text: str) -> str:
    digest = hashlib.sha256(f"{namespace}\x1f{text}".encode("utf-8")).hexdigest()[:32]
    return f"csr:{digest}"


def normalize_persian_surface(text: str) -> str:
    value = str(text or "").replace("\r\n", "\n").replace("\r", "\n")
    value = value.translate(str.maketrans({"ي": "ی", "ى": "ی", "ك": "ک"}))
    lines: list[str] = []
    for raw in value.splitlines():
        line = re.sub(r"[ \t]+", " ", raw.strip())
        line = re.sub(r"\s+([،؛؟!?.,:])", r"\1", line)
        line = re.sub(r"([،؛؟!?])([^\s\n])", r"\1 \2", line)
        lines.append(line)
    return "\n".join(lines).strip()


def _tokenize(text: str) -> list[str]:
    return [item.casefold() for item in _TOKEN_RE.findall(str(text or "")) if item.strip()]


def _token_equivalent(candidate: str, factual_tokens: set[str]) -> bool:
    if candidate in factual_tokens:
        return True
    normalized = candidate.replace("ي", "ی").replace("ك", "ک")
    if normalized in factual_tokens:
        return True
    for token in factual_tokens:
        if len(token) >= 4 and len(normalized) >= 4:
            if token.startswith(normalized) or normalized.startswith(token):
                if abs(len(token) - len(normalized)) <= 2:
                    return True
    return False


def identity_sequence(text: str) -> tuple[str, ...]:
    hits: list[tuple[int, str]] = []
    folded = str(text or "").casefold()
    for canonical, aliases in _IDENTITY_ALIASES.items():
        best: int | None = None
        for alias in aliases:
            alias_cf = alias.casefold()
            if re.search(r"[\u3040-\u30ff\u3400-\u9fff\uac00-\ud7af]", alias_cf):
                pos = folded.find(alias_cf)
            else:
                match = re.search(
                    rf"(?<![\w\u200c]){re.escape(alias_cf)}(?![\w\u200c])",
                    folded,
                    re.UNICODE,
                )
                pos = match.start() if match else -1
            if pos >= 0 and (best is None or pos < best):
                best = pos
        if best is not None:
            hits.append((best, canonical))
    return tuple(name for _, name in sorted(hits))


def temporal_sequence(text: str) -> tuple[str, ...]:
    value = str(text or "").casefold()
    hits: list[tuple[int, str]] = []
    for term in _TEMPORAL_TERMS:
        start = 0
        while True:
            pos = value.find(term, start)
            if pos < 0:
                break
            hits.append((pos, term))
            start = pos + len(term)
    return tuple(term for _, term in sorted(hits))


def _has_style_negation(text: str) -> bool:
    return bool(_STYLE_NEGATION_RE.search(str(text or "")))


def hard_factual_invariants(text: str) -> dict[str, tuple[str, ...]]:
    analysis = analyze_source(text)
    return {
        "numbers": tuple(_NUMBER_RE.findall(text)),
        "urls": tuple(_URL_RE.findall(text)),
        "speakers": tuple(_SPEAKER_RE.findall(text)),
        "identities": identity_sequence(text),
        "temporal_sequence": temporal_sequence(text),
        "question": ("true",) if ("?" in text or "؟" in text) else (),
        "negation": ("present",) if _has_style_negation(text) else (),
        "modality": ("present",) if re.search(r"(?:شاید|احتمال|ممکن|ظاهراً|انگار|به نظر)", text) else (),
        "content_type": (analysis.content_type,),
    }


def build_style_rewrite_input(
    factual_text: str,
    *,
    event_id: str,
    segment_id: str,
    content_type: str | None = None,
    style_profile: str = "",
    selected_example_ids: Iterable[str] = (),
    direct_style: StyleDirective | None = None,
) -> StyleRewriteInput:
    text = str(factual_text or "").strip()
    analysis = analyze_source(text, hinted_content_type=content_type)
    chosen_type = content_type if content_type else analysis.content_type
    return StyleRewriteInput(
        faithful_factual_text=text,
        event_id=str(event_id or "")[:80],
        segment_id=str(segment_id or "")[:80],
        content_type=chosen_type,
        speaker_metadata=tuple(_SPEAKER_RE.findall(text))[:24],
        hard_factual_invariants=hard_factual_invariants(text),
        style_profile=(style_profile or chosen_type)[:80],
        selected_style_example_ids=tuple(str(item) for item in selected_example_ids)[:MAX_STYLE_EXAMPLES],
        direct_style_rule_id=(direct_style.rule_id if direct_style else "")[:80],
        direct_style_category=(direct_style.category if direct_style else "generic")[:48],
        authority_order=direct_style.authority_order if direct_style else DEFAULT_AUTHORITY_ORDER,
    )


def _content_family(content_type: str) -> str:
    if content_type in {"LIVE_DIALOGUE", "WEVERSE_LIVE", "FANSIGN", "INTERVIEW", "MEMBER_QUOTE"}:
        return "dialogue"
    if content_type in {"PHOTO_REACTION", "VIDEO_REACTION", "SHORT_REACTION", "MEMBER_INTERACTION"}:
        return "reaction"
    if content_type in {
        "OFFICIAL_NEWS", "FACTUAL_INFORMATION", "BRAND_AD", "FASHION_EVENT",
        "AIRPORT", "MAGAZINE", "X_FANBASE_UPDATE",
    }:
        return "information"
    if content_type in {
        "KOREAN_LANGUAGE_NUANCE", "JAPANESE_LANGUAGE_NUANCE", "WORDPLAY",
        "THREAD_OR_LONG_EXPLANATION", "FAN_ACCOUNT_OR_OP_STORY",
    }:
        return "explanation"
    return "general"


def _profile_from_texts(key: str, content_type: str, texts: Sequence[str]) -> StyleProfile:
    clean = [str(item) for item in texts if str(item).strip()]
    count = len(clean)
    if not clean:
        return StyleProfile(
            key=key,
            content_type=content_type,
            example_count=0,
            register=target_register(content_type),
            median_chars=0.0,
            multiline_pct=0.0,
            dialogue_pct=0.0,
            emoji_pct=0.0,
            reaction_pct=0.0,
            formal_connector_pct=0.0,
            supported=False,
        )
    return StyleProfile(
        key=key,
        content_type=content_type,
        example_count=count,
        register=target_register(content_type),
        median_chars=float(median(len(item) for item in clean)),
        multiline_pct=sum("\n" in item for item in clean) / count,
        dialogue_pct=sum(bool(_SPEAKER_RE.search(item)) for item in clean) / count,
        emoji_pct=sum(bool(_EMOJI_RE.search(item)) for item in clean) / count,
        reaction_pct=sum(bool(_REACTION_RE.search(item)) for item in clean) / count,
        formal_connector_pct=sum(bool(_FORMAL_AI_RE.search(item)) for item in clean) / count,
        supported=count >= MIN_PROFILE_EXAMPLES,
    )


def profile_for_content_type(memory: Any, content_type: str) -> StyleProfile:
    try:
        rows = memory.conn.execute(
            "SELECT text FROM channel_style_examples WHERE content_type=? ORDER BY example_id ASC",
            (str(content_type),),
        ).fetchall()
        texts = [str(row["text"]) for row in rows]
    except Exception:
        texts = []
    return _profile_from_texts(str(content_type), str(content_type), texts)


def audit_requested_profiles(memory: Any) -> dict[str, StyleProfile]:
    try:
        rows = memory.conn.execute(
            "SELECT content_type,text FROM channel_style_examples ORDER BY example_id ASC"
        ).fetchall()
    except Exception:
        rows = []
    result: dict[str, StyleProfile] = {}
    for key, (types, keywords) in _REQUESTED_PROFILE_RULES.items():
        selected: list[str] = []
        dominant_types: dict[str, int] = {}
        for row in rows:
            row_type = str(row["content_type"])
            text = str(row["text"])
            low = text.casefold()
            if row_type in types or any(keyword in low for keyword in keywords):
                selected.append(text)
                dominant_types[row_type] = dominant_types.get(row_type, 0) + 1
        dominant = max(dominant_types, key=dominant_types.get) if dominant_types else "OTHER"
        result[key] = _profile_from_texts(key, dominant, selected)
    return result


def retrieve_structural_examples(
    memory: Any,
    rewrite_input: StyleRewriteInput,
    *,
    limit: int = MAX_STYLE_EXAMPLES,
) -> list[RetrievedStyleExample]:
    """Retrieve form-similar examples without topical/current-text FTS matching."""
    limit = max(1, min(int(limit), MAX_STYLE_EXAMPLES))
    target_type = rewrite_input.content_type
    family = _content_family(target_type)
    target_len = max(1, len(rewrite_input.faithful_factual_text))
    target_dialogue = bool(rewrite_input.speaker_metadata)
    target_register_name = target_register(target_type, rewrite_input.faithful_factual_text)
    try:
        rows = memory.conn.execute(
            "SELECT example_id,text,content_type,source_language,date,char_count,has_dialogue "
            "FROM channel_style_examples ORDER BY example_id ASC"
        ).fetchall()
    except Exception:
        return []

    candidates: list[RetrievedStyleExample] = []
    for row in rows:
        row_type = str(row["content_type"])
        row_family = _content_family(row_type)
        if row_type != target_type and row_family != family:
            continue
        text = str(row["text"])
        row_len = max(1, int(row["char_count"] or len(text) or 1))
        ratio = min(row_len, target_len) / max(row_len, target_len)
        dialogue_match = bool(row["has_dialogue"]) == target_dialogue
        row_register = target_register(row_type, text)
        score = 1.0
        reasons = ["equal historical base weight=1.0", "topic/lexical similarity excluded"]
        if row_type == target_type:
            score += 3.0
            reasons.append("same content type")
        elif row_family == family:
            score += 1.0
            reasons.append("same structural family")
        score += 0.85 * ratio
        if ratio >= 0.65:
            reasons.append("similar length")
        if dialogue_match:
            score += 0.75
            reasons.append("matching dialogue structure")
        if row_register == target_register_name:
            score += 0.75
            reasons.append("matching register")
        candidates.append(
            RetrievedStyleExample(
                example_id=str(row["example_id"]),
                text=text[:1200],
                content_type=row_type,
                source_language=str(row["source_language"]),
                date=str(row["date"]),
                score=score,
                reasons=reasons,
            )
        )

    candidates.sort(key=lambda item: (-item.score, item.example_id))
    chosen: list[RetrievedStyleExample] = []
    format_fingerprints: set[str] = set()
    for item in candidates:
        fingerprint = re.sub(
            r"[\w\u0600-\u06ff\u3040-\u30ff\uac00-\ud7af]+",
            "W",
            item.text[:120],
        )
        fingerprint = re.sub(r"W(?:\s+W)+", "W", fingerprint)
        if fingerprint in format_fingerprints and len(chosen) >= 2:
            continue
        format_fingerprints.add(fingerprint)
        chosen.append(item)
        if len(chosen) >= limit:
            break
    return chosen


def _historical_exclusive_additions(
    factual_text: str,
    candidate_text: str,
    examples: Sequence[RetrievedStyleExample],
) -> list[str]:
    factual_tokens = set(_tokenize(factual_text))
    candidate_tokens = _tokenize(candidate_text)
    historical_tokens = {
        token
        for example in examples
        for token in _tokenize(example.text)
        if len(token) >= 2
    }
    failures: list[str] = []
    for token in candidate_tokens:
        if token in _SAFE_STYLE_TOKENS or _token_equivalent(token, factual_tokens):
            continue
        if token in historical_tokens:
            failures.append(f"historical_example_exclusive_token:{token}")
    return list(dict.fromkeys(failures))


def _unsupported_content_additions(factual_text: str, candidate_text: str) -> list[str]:
    factual_tokens = set(_tokenize(factual_text))
    failures: list[str] = []
    for token in _tokenize(candidate_text):
        if token in _SAFE_STYLE_TOKENS or _token_equivalent(token, factual_tokens):
            continue
        if len(token) >= 3 and not token.isdigit():
            failures.append(f"unsupported_content_token:{token}")
    return list(dict.fromkeys(failures))


def style_fidelity_failures(
    factual_text: str,
    candidate_text: str,
    examples: Sequence[RetrievedStyleExample] = (),
) -> list[str]:
    factual = str(factual_text or "").strip()
    candidate = str(candidate_text or "").strip()
    if not candidate:
        return ["missing_style_candidate"]

    # Reuse proven Translation Fusion gates, but replace its multilingual-source
    # negation comparison with a symmetric Persian->Persian check for this layer.
    failures = [
        failure
        for failure in translation_fidelity_failures(factual, candidate)
        if failure not in {"negation_dropped", "negation_invented"}
    ]
    factual_neg = _has_style_negation(factual)
    candidate_neg = _has_style_negation(candidate)
    if factual_neg and not candidate_neg:
        failures.append("negation_dropped")
    if not factual_neg and candidate_neg:
        failures.append("negation_invented")

    factual_ids = identity_sequence(factual)
    candidate_ids = identity_sequence(candidate)
    if factual_ids and candidate_ids and factual_ids != candidate_ids:
        failures.append("actor_identity_order_changed")

    factual_time = temporal_sequence(factual)
    candidate_time = temporal_sequence(candidate)
    if factual_time and candidate_time and factual_time != candidate_time:
        failures.append("chronology_changed")
    for term in factual_time:
        if term not in candidate_time:
            failures.append(f"temporal_marker_dropped:{term}")

    failures.extend(_historical_exclusive_additions(factual, candidate, examples))
    failures.extend(_unsupported_content_additions(factual, candidate))
    return list(dict.fromkeys(failures))


def ai_like_findings(text: str, profile: StyleProfile | None = None) -> list[str]:
    value = str(text or "")
    findings: list[str] = []
    if _FORMAL_AI_RE.search(value):
        findings.append("formal_connector_overuse")
    if _GENERIC_FILLER_RE.search(value):
        findings.append("generic_emotional_or_explanatory_filler")
    emoji_count = len(_EMOJI_RE.findall(value))
    if emoji_count >= 6 and emoji_count / max(1, len(value)) > 0.06:
        findings.append("excessive_emoji_density")
    if profile is not None and profile.register == "factual" and profile.reaction_pct < 0.35:
        if _OVER_CUTE_RE.search(value):
            findings.append("over_cute_for_profile")
    sentences = [
        item.strip()
        for item in re.split(r"[.!؟?]\s*", value)
        if len(item.strip()) >= 8
    ]
    normalized = [re.sub(r"\s+", " ", item.casefold()) for item in sentences]
    if len(normalized) != len(set(normalized)):
        findings.append("redundant_restatement")
    return list(dict.fromkeys(findings))


def score_style_match(text: str, profile: StyleProfile) -> float:
    value = str(text or "")
    if not value or not profile.supported:
        return 0.0
    length_ratio = min(max(1, len(value)), max(1.0, profile.median_chars)) / max(
        max(1, len(value)), max(1.0, profile.median_chars)
    )
    multiline = 1.0 if "\n" in value else 0.0
    dialogue = 1.0 if _SPEAKER_RE.search(value) else 0.0
    emoji = 1.0 if _EMOJI_RE.search(value) else 0.0
    reaction = 1.0 if _REACTION_RE.search(value) else 0.0

    def closeness(observed: float, expected: float) -> float:
        return max(0.0, 1.0 - abs(observed - expected))

    score = (
        0.32 * length_ratio
        + 0.18 * closeness(multiline, profile.multiline_pct)
        + 0.18 * closeness(dialogue, profile.dialogue_pct)
        + 0.16 * closeness(emoji, profile.emoji_pct)
        + 0.16 * closeness(reaction, profile.reaction_pct)
    )
    penalty = 0.10 * len(ai_like_findings(value, profile))
    return round(max(0.0, min(1.0, score - penalty)), 4)


def evaluate_style_candidate(
    rewrite_input: StyleRewriteInput,
    candidate_text: str,
    examples: Sequence[RetrievedStyleExample],
    profile: StyleProfile,
    *,
    provider: str = "fixture",
    direct_style: StyleDirective | None = None,
    direct_style_evidence: DirectStyleEvidence | None = None,
) -> StyleRewriteResult:
    factual = rewrite_input.faithful_factual_text
    directive = direct_style or StyleDirective(authority_order=rewrite_input.authority_order)
    evidence = direct_style_evidence or DirectStyleEvidence(content_type=rewrite_input.content_type)
    body_candidate = str(candidate_text or "").strip()
    candidate = directive.render(body_candidate, is_dialogue=evidence.is_dialogue)
    projected, directive_failures = directive.factual_projection(candidate, is_dialogue=evidence.is_dialogue)
    failures = list(directive_failures)
    failures.extend(style_fidelity_failures(factual, projected, examples))
    failures = list(dict.fromkeys(failures))
    score = score_style_match(projected, profile)
    findings = ai_like_findings(projected, profile)
    fallback = ""
    accepted = not failures
    if failures:
        fallback = "fidelity_lock_rejected"
    elif findings and (score < STYLE_SCORE_THRESHOLD or directive.applied):
        accepted = False
        fallback = "unnatural_or_overstyled"
    elif score < STYLE_SCORE_THRESHOLD and not directive.applied:
        accepted = False
        fallback = "low_style_confidence"
    final_text = candidate if accepted else factual
    return StyleRewriteResult(
        event_id=rewrite_input.event_id,
        segment_id=rewrite_input.segment_id,
        content_type=rewrite_input.content_type,
        style_profile=rewrite_input.style_profile,
        selected_style_example_ids=rewrite_input.selected_style_example_ids,
        factual_text=factual,
        candidate_text=candidate,
        final_text=final_text,
        fidelity_failures=tuple(failures),
        style_score=score,
        accepted=accepted,
        fallback_reason=fallback,
        review_required=not accepted,
        provider=provider,
        factual_fingerprint=_fingerprint("factual-v1", factual),
        candidate_fingerprint=_fingerprint("candidate-v1", candidate) if candidate else "",
        direct_style_rule_id=directive.rule_id,
        direct_style_category=directive.category,
        direct_style_applied=directive.applied,
        direct_style_fallback_reason=directive.fallback_reason,
        direct_style_symbol=directive.symbol,
        authority_order=directive.authority_order,
    )


def rewrite_shadow_candidate(
    memory: Any,
    factual_text: str,
    *,
    event_id: str,
    segment_id: str,
    content_type: str | None = None,
    provider: StyleRewriteProvider | None = None,
    direct_evidence: DirectStyleEvidence | Mapping[str, Any] | None = None,
    recent_symbols: Sequence[object] = (),
) -> StyleRewriteResult:
    provider = provider or ConservativeLocalStyleProvider()
    content_type = content_type or classify_content_type(factual_text)
    evidence = direct_evidence if isinstance(direct_evidence, DirectStyleEvidence) else DirectStyleEvidence.from_mapping(direct_evidence)
    if evidence.content_type == "OTHER" and content_type != "OTHER":
        evidence = DirectStyleEvidence(
            content_type=content_type,
            category=evidence.category,
            platform=evidence.platform,
            account=evidence.account,
            brand=evidence.brand,
            date=evidence.date,
            title=evidence.title,
            is_story=evidence.is_story,
            is_dialogue=evidence.is_dialogue,
            has_jeonghan=evidence.has_jeonghan,
            ambiguous=evidence.ambiguous,
        )
    directive = DirectStylePlanner().plan(
        evidence,
        context_key=f"{event_id}:{segment_id}",
        recent_symbols=recent_symbols,
    )
    profile = profile_for_content_type(memory, content_type)
    preliminary = build_style_rewrite_input(
        factual_text,
        event_id=event_id,
        segment_id=segment_id,
        content_type=content_type,
        style_profile=profile.key,
        direct_style=directive,
    )
    examples = retrieve_structural_examples(memory, preliminary, limit=MAX_STYLE_EXAMPLES)
    final_input = build_style_rewrite_input(
        factual_text,
        event_id=event_id,
        segment_id=segment_id,
        content_type=content_type,
        style_profile=profile.key,
        selected_example_ids=[item.example_id for item in examples],
        direct_style=directive,
    )
    provider_name = getattr(provider, "name", type(provider).__name__)
    if not profile.supported and not directive.applied:
        return StyleRewriteResult(
            event_id=event_id,
            segment_id=segment_id,
            content_type=content_type,
            style_profile=profile.key,
            selected_style_example_ids=final_input.selected_style_example_ids,
            factual_text=factual_text,
            final_text=factual_text,
            accepted=False,
            fallback_reason="unsupported_style_profile",
            review_required=True,
            provider=provider_name,
            factual_fingerprint=_fingerprint("factual-v1", factual_text),
            direct_style_rule_id=directive.rule_id,
            direct_style_category=directive.category,
            direct_style_applied=directive.applied,
            direct_style_fallback_reason=directive.fallback_reason,
            direct_style_symbol=directive.symbol,
            authority_order=directive.authority_order,
        )
    if not examples and not directive.applied:
        return StyleRewriteResult(
            event_id=event_id,
            segment_id=segment_id,
            content_type=content_type,
            style_profile=profile.key,
            selected_style_example_ids=(),
            factual_text=factual_text,
            final_text=factual_text,
            accepted=False,
            fallback_reason="style_example_retrieval_failed",
            review_required=True,
            provider=provider_name,
            factual_fingerprint=_fingerprint("factual-v1", factual_text),
            direct_style_rule_id=directive.rule_id,
            direct_style_category=directive.category,
            direct_style_applied=directive.applied,
            direct_style_fallback_reason=directive.fallback_reason,
            direct_style_symbol=directive.symbol,
            authority_order=directive.authority_order,
        )
    try:
        candidate = provider.rewrite(final_input, examples, profile)
    except Exception:
        return StyleRewriteResult(
            event_id=event_id,
            segment_id=segment_id,
            content_type=content_type,
            style_profile=profile.key,
            selected_style_example_ids=final_input.selected_style_example_ids,
            factual_text=factual_text,
            final_text=factual_text,
            accepted=False,
            fallback_reason="style_provider_failed",
            review_required=True,
            provider=provider_name,
            factual_fingerprint=_fingerprint("factual-v1", factual_text),
            direct_style_rule_id=directive.rule_id,
            direct_style_category=directive.category,
            direct_style_applied=directive.applied,
            direct_style_fallback_reason=directive.fallback_reason,
            direct_style_symbol=directive.symbol,
            authority_order=directive.authority_order,
        )
    return evaluate_style_candidate(
        final_input,
        candidate,
        examples,
        profile,
        provider=provider_name,
        direct_style=directive,
        direct_style_evidence=evidence,
    )


def _affected_segment_ids(state: Any, incoming_ids: set[str]) -> list[str]:
    fusion = state.data.get("event_fusion")
    if not isinstance(fusion, dict):
        return []
    memberships = fusion.get("segment_memberships")
    if not isinstance(memberships, dict):
        return []
    result: set[str] = set()
    for update_id in incoming_ids:
        row = memberships.get(str(update_id))
        if isinstance(row, dict):
            segment_id = str(row.get("segment_id", ""))
            if segment_id.startswith("seg:"):
                result.add(segment_id)
    return sorted(result)


def preview_factual_results(
    state: Any,
    updates: Iterable[Any],
    configured_handles: Iterable[str],
) -> list[TranslationFusionResult]:
    incoming_list = list(updates)
    incoming = {str(item.id): item for item in incoming_list}
    results: list[TranslationFusionResult] = []
    for segment_id in _affected_segment_ids(state, set(incoming)):
        evidence = build_evidence_for_segment(state, segment_id, configured_handles, incoming)
        if evidence:
            results.append(fuse_evidence_items(evidence, segment_id=segment_id))
    return results


def _infer_content_type(state: Any, result: TranslationFusionResult, incoming: Mapping[str, Any]) -> str:
    pieces: list[str] = []
    for update_id in result.evidence_update_ids:
        update = incoming.get(str(update_id)) or state.get_update(str(update_id))
        if update is not None:
            pieces.append(str(update.translation_source()))
    source_text = "\n".join(pieces).strip()
    if source_text:
        detected = classify_content_type(source_text)
        if detected != "OTHER":
            return detected
    return classify_content_type(result.fused_factual_text)


def _direct_style_evidence(
    state: Any,
    result: TranslationFusionResult,
    incoming: Mapping[str, Any],
    content_type: str,
) -> DirectStyleEvidence:
    """Derive rule inputs only from current Segment Updates and their metadata."""
    current_updates: list[Any] = []
    for update_id in result.evidence_update_ids:
        item = incoming.get(str(update_id)) or state.get_update(str(update_id))
        if item is not None:
            current_updates.append(item)
    backbone = next(
        (item for item in current_updates if str(item.id) == str(result.backbone_update_id)),
        current_updates[0] if current_updates else None,
    )
    if backbone is None:
        return DirectStyleEvidence(content_type=content_type, ambiguous=True)

    source_text = "\n".join(str(item.translation_source()) for item in current_updates).strip()
    folded = source_text.casefold()
    categories = {
        str(getattr(item, "category", "") or "general").casefold().replace("_", "-")
        for item in current_updates
    }
    platforms: set[str] = set()
    detected_platform = analyze_source(source_text, hinted_content_type=content_type).platform
    if detected_platform:
        platforms.add(detected_platform)
    if any(marker in folded for marker in ("weverse", "ویورس", "위버스")):
        platforms.add("weverse")
    if any(marker in folded for marker in ("instagram", "اینستاگرام", "인스타", "ig update", "ig story")):
        platforms.add("instagram")

    category = str(getattr(backbone, "category", "") or "general").casefold().replace("_", "-")
    if category in {"jeonghan-instagram", "member-instagram", "instagram", "instagram-story"}:
        platforms.add("instagram")
    if category in {"weverse", "weverse-post", "weverse-live"}:
        platforms.add("weverse")
    platform = next(iter(platforms)) if len(platforms) == 1 else ""

    explicit_story = category == "instagram-story" or bool(
        re.search(r"(?:instagram|ig|اینستاگرام|인스타)\s+story|استوری\s+اینستاگرام", folded, re.I)
    )
    account = "jeonghaniyoo_n" if (
        str(getattr(backbone, "author", "")).casefold().lstrip("@") == "jeonghaniyoo_n"
        or "jeonghaniyoo_n" in folded
    ) else ""
    brand = "banila co" if any(marker in folded for marker in ("banila co", "banilaco", "banila", "بانیلا")) else ""
    has_jeonghan = bool(account) or "JEONGHAN" in identity_sequence(source_text + "\n" + result.fused_factual_text)
    title = str(getattr(backbone, "event_title", "") or "").strip()[:160]
    try:
        current_date = backbone.created_at.strftime("%y%m%d")
    except Exception:
        current_date = ""
    dialogue = bool(_SPEAKER_RE.search(result.fused_factual_text)) or content_type in {
        "LIVE_DIALOGUE", "WEVERSE_LIVE", "FANSIGN", "INTERVIEW", "MEMBER_QUOTE",
    }
    meaningful_categories = {item for item in categories if item not in {"", "general", "other"}}
    return DirectStyleEvidence(
        content_type=content_type,
        category=category,
        platform=platform,
        account=account,
        brand=brand,
        date=current_date,
        title=title,
        is_story=explicit_story,
        is_dialogue=dialogue,
        has_jeonghan=has_jeonghan,
        ambiguous=len(platforms) > 1 or len(meaningful_categories) > 1,
    )


def _fresh_style_fields() -> dict[str, Any]:
    return {
        "channel_style_rewrite_version": STYLE_REWRITE_VERSION,
        "channel_style_rewrite_mode": STYLE_REWRITE_MODE,
        "direct_style_rules_version": DIRECT_STYLE_RULES_VERSION,
        "direct_style_rules_mode": DIRECT_STYLE_RULES_MODE,
        "direct_style_authority_order": list(DEFAULT_AUTHORITY_ORDER),
        "direct_style_recent_symbols": [],
        "style_rewrite_results": {},
    }


def _prune_style(event_state: dict[str, Any]) -> None:
    results = event_state.get("style_rewrite_results")
    if isinstance(results, dict) and len(results) > MAX_STYLE_RESULTS:
        event_state["style_rewrite_results"] = dict(list(results.items())[-MAX_STYLE_RESULTS:])
    history = event_state.get("direct_style_recent_symbols")
    if isinstance(history, list):
        event_state["direct_style_recent_symbols"] = [
            str(item)[:32] for item in history[-MAX_DIRECT_STYLE_SYMBOL_HISTORY:] if str(item).strip()
        ]
    else:
        event_state["direct_style_recent_symbols"] = []


def shadow_style_rewrite(
    state: Any,
    memory: Any,
    updates: Iterable[Any],
    configured_handles: Iterable[str],
    *,
    provider: StyleRewriteProvider | None = None,
) -> list[StyleRewriteResult]:
    """Evaluate style after Translation Fusion without changing delivery/lifecycle."""
    incoming_list = list(updates)
    incoming = {str(item.id): item for item in incoming_list}
    fusion = state.data.get("event_fusion")
    if not isinstance(fusion, dict):
        return []
    for key, value in _fresh_style_fields().items():
        fusion.setdefault(key, value)

    results: list[StyleRewriteResult] = []
    for factual in preview_factual_results(state, incoming_list, configured_handles):
        if not factual.fused_factual_text or factual.fidelity_status != "faithful_shadow_candidate":
            fusion["style_rewrite_results"][factual.segment_id] = {
                "event_id": factual.event_id[:80],
                "segment_id": factual.segment_id[:80],
                "content_type": "",
                "style_profile": "",
                "selected_style_example_ids": [],
                "factual_draft_fingerprint": _fingerprint("factual-v1", factual.fused_factual_text) if factual.fused_factual_text else "",
                "style_candidate_fingerprint": "",
                "fidelity_passed": factual.fidelity_status == "faithful_shadow_candidate",
                "fidelity_failures": [f"translation_fusion:{factual.fidelity_status}"],
                "style_score": 0.0,
                "accepted": False,
                "fallback_reason": "translation_fidelity_not_ready",
                "review_required": True,
                "provider": getattr(provider, "name", "local_conservative") if provider else "local_conservative",
                "mode": STYLE_REWRITE_MODE,
                "direct_style_rules_version": DIRECT_STYLE_RULES_VERSION,
                "direct_style_rules_mode": DIRECT_STYLE_RULES_MODE,
                "direct_style_rule_id": "",
                "direct_style_category": "generic",
                "direct_style_applied": False,
                "direct_style_fallback_reason": "translation_fidelity_not_ready",
                "direct_style_symbol": "",
                "authority_order": list(DEFAULT_AUTHORITY_ORDER),
                "text_persisted": False,
            }
            continue
        content_type = _infer_content_type(state, factual, incoming)
        direct_evidence = _direct_style_evidence(state, factual, incoming, content_type)
        result = rewrite_shadow_candidate(
            memory,
            factual.fused_factual_text,
            event_id=factual.event_id,
            segment_id=factual.segment_id,
            content_type=content_type,
            provider=provider,
            direct_evidence=direct_evidence,
            recent_symbols=fusion.get("direct_style_recent_symbols", ()),
        )
        fusion["style_rewrite_results"][factual.segment_id] = result.state_metadata()
        if result.accepted and result.direct_style_symbol:
            fusion["direct_style_recent_symbols"].append(result.direct_style_symbol)
        results.append(result)
        observe(
            "shadow_channel_style_rewrite",
            component="channel_style_rewrite",
            stage="channel_style_shadow",
            status="accepted" if result.accepted else "fallback",
            update_id=factual.backbone_update_id,
            source="faithful_factual_persian",
        )
    _prune_style(fusion)
    return results
