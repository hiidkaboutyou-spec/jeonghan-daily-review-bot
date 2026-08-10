from __future__ import annotations

import re
from typing import Iterable, Sequence, TypeVar

KOREAN_RE = re.compile(r"[\uac00-\ud7af]")
JAPANESE_RE = re.compile(r"[\u3040-\u30ff]")
PERSIAN_RE = re.compile(r"[\u0600-\u06ff]")
LATIN_RE = re.compile(r"[A-Za-z]")
SPEAKER_RE = re.compile(r"^\s*([^\s:：]{1,24})\s*[:：]\s*(.+)$", re.M)
REACTION_RE = re.compile(
    r"(?:😭|🥺|💗|🩷|💘|😂|🤣|🥹|ㅠㅠ|ㅜㅜ|گریه|کیوت|ناز|عسلی|تاینی|"
    r"دارم\s*می[‌ ]?میرم|میمیرم|sobbing|crying|cute|adorable|help\b|lmao|ㅋㅋ|ㅎㅎ)",
    re.I,
)
PHOTO_RE = re.compile(
    r"(?:\bphoto\b|\bphotos\b|\bpicture\b|\bpic\b|\bselca\b|\bselfie\b|mirror\s*selca|"
    r"عکس|سلکا|سلفی|사진|셀카|フォト|写真|セルカ)",
    re.I,
)
VIDEO_RE = re.compile(
    r"(?:\bvideo\b|\bclip\b|\breel\b|\btiktok\b|\b fancam\b|ویدیو|کلیپ|ریل|تیک[‌ -]?تاک|"
    r"영상|동영상|직캠|ビデオ|動画)",
    re.I,
)
QUOTE_MARK_RE = re.compile(r'(?:["“«][^"”»\n]{2,}["”»])')
FACT_SIGNAL_RE = re.compile(
    r"(?:\b20\d{2}\b|\b\d{1,2}[:/.-]\d{1,2}(?:[:/.-]\d{1,4})?\b|"
    r"\b\d+(?:[.,]\d+)?%?\b|schedule|release|ranking|sold|announced|confirmed|"
    r"تاریخ|ساعت|رتبه|اعلام|تأیید|منتشر|فروش)",
    re.I,
)

CONTENT_TYPES = (
    "LIVE_DIALOGUE", "WEVERSE_POST", "WEVERSE_LIVE", "FANSIGN", "INTERVIEW",
    "MAGAZINE", "OFFICIAL_NEWS", "BRAND_AD", "FASHION_EVENT", "AIRPORT",
    "INSTAGRAM_UPDATE", "X_FANBASE_UPDATE", "FAN_ACCOUNT_OR_OP_STORY",
    "PHOTO_REACTION", "VIDEO_REACTION", "MEMBER_QUOTE", "MEMBER_INTERACTION",
    "KOREAN_LANGUAGE_NUANCE", "JAPANESE_LANGUAGE_NUANCE", "WORDPLAY",
    "THREAD_OR_LONG_EXPLANATION", "SHORT_REACTION", "FACTUAL_INFORMATION",
    "FANFIC_UPDATE", "OTHER",
)

_INFORMATION_TYPES = {
    "OFFICIAL_NEWS", "FACTUAL_INFORMATION", "BRAND_AD", "FASHION_EVENT",
    "AIRPORT", "MAGAZINE", "X_FANBASE_UPDATE", "FANFIC_UPDATE",
}
_REACTION_TYPES = {"PHOTO_REACTION", "VIDEO_REACTION", "SHORT_REACTION", "MEMBER_INTERACTION"}
_EXPLANATION_TYPES = {
    "KOREAN_LANGUAGE_NUANCE", "JAPANESE_LANGUAGE_NUANCE", "WORDPLAY",
    "THREAD_OR_LONG_EXPLANATION", "FAN_ACCOUNT_OR_OP_STORY",
}
_DIALOGUE_TYPES = {"LIVE_DIALOGUE", "WEVERSE_LIVE", "FANSIGN", "INTERVIEW", "MEMBER_QUOTE"}

T = TypeVar("T")


def detect_language(text: str) -> str:
    value = str(text or "")
    present = {
        "ko": bool(KOREAN_RE.search(value)),
        "ja": bool(JAPANESE_RE.search(value)),
        "fa": bool(PERSIAN_RE.search(value)),
        "en": bool(LATIN_RE.search(value)),
    }
    scripts = [name for name, enabled in present.items() if enabled]
    if len(scripts) >= 2:
        natural = re.sub(r"https?://\S+|[@#][\w.-]+", "", value)
        natural_present = {
            "ko": bool(KOREAN_RE.search(natural)),
            "ja": bool(JAPANESE_RE.search(natural)),
            "fa": bool(PERSIAN_RE.search(natural)),
            "en": bool(LATIN_RE.search(natural)),
        }
        if sum(natural_present.values()) >= 2:
            return "mixed"
    if present["ko"]:
        return "ko"
    if present["ja"]:
        return "ja"
    if present["fa"] and present["en"]:
        return "fa_mixed"
    if present["fa"]:
        return "fa"
    if present["en"]:
        return "en"
    return "other"


def classify_content_type(text: str) -> str:
    value = str(text or "")
    low = value.casefold()
    lines = [line for line in value.splitlines() if line.strip()]
    speakers = SPEAKER_RE.findall(value)

    if any(k in low for k in ("fanfic", "ao3", "فیک", "فن‌فیک", "فن فیک")):
        return "FANFIC_UPDATE"

    is_weverse = any(k in low for k in ("weverse", "ویورس", "위버스"))
    live_signal = any(k in low for k in (" live", "live ", "라이브", "ライブ", "لایو"))
    if is_weverse and live_signal:
        return "WEVERSE_LIVE"
    if is_weverse:
        return "WEVERSE_POST"

    if any(k in low for k in ("fansign", "fan sign", "فن ساین", "فن‌ساین", "팬싸", "fancall", "fan call", "فن‌کال")):
        return "FANSIGN"
    if any(k in low for k in ("interview", "مصاحبه", "インタビュー", "인터뷰", "q&a", "q & a")):
        return "INTERVIEW"
    if any(k in low for k in ("magazine", "مجله", "vogue", "allure", "elle", "gq ", "화보", "雑誌")):
        return "MAGAZINE"

    if any(k in low for k in ("wordplay", "pun", "بازی با کلمه", "بازی با کلمات", "말장난", "言葉遊び", "ダジャレ")):
        return "WORDPLAY"
    nuance_terms = ("معنی", "یعنی", "گرامر", "پسوند", "لحن", "nuance", "means", "ending", "honorific")
    if KOREAN_RE.search(value) and any(k in low for k in nuance_terms):
        return "KOREAN_LANGUAGE_NUANCE"
    if JAPANESE_RE.search(value) and any(k in low for k in nuance_terms):
        return "JAPANESE_LANGUAGE_NUANCE"

    if len(speakers) >= 2:
        return "LIVE_DIALOGUE"

    if any(k in low for k in ("op:", "op ", "اوپ", "fan account", "fanaccount", "فنی که", "تعریف کرد", "후기", "目撃")):
        return "FAN_ACCOUNT_OR_OP_STORY"

    if len(value) > 520 or len(lines) >= 7 or any(k in low for k in ("thread:", "🧵", "رشته:", "توضیح طولانی")):
        return "THREAD_OR_LONG_EXPLANATION"

    if any(k in low for k in ("fashion week", "fashion event", "runway", "showroom", "فشن ویک", "فشن‌شو", "فشن شو", "패션위크")):
        return "FASHION_EVENT"
    if any(k in low for k in ("airport", "فرودگاه", "공항", "空港")):
        return "AIRPORT"
    if any(k in low for k in ("instagram", " ig ", "ig update", "اینستاگرام", "insta", "인스타", "インスタ")):
        return "INSTAGRAM_UPDATE"

    reaction = bool(REACTION_RE.search(value))
    if VIDEO_RE.search(value) and reaction:
        return "VIDEO_REACTION"
    if PHOTO_RE.search(value) and reaction:
        return "PHOTO_REACTION"

    if any(k in low for k in ("interaction", "member interaction", "جونگچول", "جیهان", "couphan", "gyuhan", "باهم", "همدیگه", "together", "둘이", "ふたり")):
        return "MEMBER_INTERACTION"

    if QUOTE_MARK_RE.search(value) and len(value) < 420:
        return "MEMBER_QUOTE"

    if any(k in low for k in ("official", "공지", "notice", "اطلاعیه", "اعلام رسمی", "pledis", "hybe", "공식", "公式")):
        return "OFFICIAL_NEWS"

    if any(k in low for k in (
        "ambassador", "campaign", "کمپین", "سفیر", "brand", "banila", "بانیلا",
        "dior", "gucci", "ysl", "calvin klein", "ad campaign", "광고", "アンバサダー",
    )):
        return "BRAND_AD"

    if len(value) <= 120 and reaction:
        return "SHORT_REACTION"

    if any(k in low for k in (
        "update:", "updates:", "according to", "via @", "posted by", "source:",
        "schedule:", "translation:", "trans:", "📢", "🔗",
    )):
        return "X_FANBASE_UPDATE"

    if FACT_SIGNAL_RE.search(value) and not reaction:
        return "FACTUAL_INFORMATION"

    if len(speakers) == 1 and len(value) < 420:
        return "MEMBER_QUOTE"

    return "OTHER"


def target_register(content_type: str, text: str = "") -> str:
    if content_type in _INFORMATION_TYPES:
        return "factual"
    if content_type in _REACTION_TYPES or REACTION_RE.search(text or ""):
        return "reaction"
    if content_type in _DIALOGUE_TYPES:
        return "dialogue"
    if content_type in _EXPLANATION_TYPES:
        return "explanatory"
    return "conversational"


def language_guidance(source_language: str, content_type: str) -> str:
    common = "فارسی باید طبیعی و channel-native باشد، اما SOURCE همیشه مرجع معنا و fact است."
    if source_language == "en":
        return common + " ساختار نحوی انگلیسی را کپی نکن؛ شوخی، attitude، attribution و fandom vocabulary را بدون خلاصه‌سازی طبیعی کن."
    if source_language == "ko":
        return common + " نرمی/صمیمیت/طعنه/مکث/ناتمام‌ماندن جمله، honorific و sentence-ending nuance کره‌ای را حفظ کن؛ ㅋㅋㅋ/ㅎㅎㅎ را خودکار فارسی نکن."
    if source_language == "ja":
        return common + " نرمی، ادب، cuteness عمدی، honorific و sentence-ending nuance ژاپنی را صاف و خنثی نکن؛ wordplay را فقط در صورت نیاز کوتاه توضیح بده."
    if source_language in {"mixed", "fa_mixed"}:
        return common + " code-switching معنادار را حفظ کن و بخش‌های چندزبانه را یک‌دست و بی‌دلیل به فارسی خالص تبدیل نکن."
    return common


def commentary_policy(content_type: str) -> str:
    if content_type in _INFORMATION_TYPES:
        return "COMMENTARY: factual translation اولویت دارد؛ واکنش کانالی حداقلی یا صفر باشد."
    if content_type in _REACTION_TYPES:
        return "COMMENTARY: شخصیت کانال می‌تواند پررنگ‌تر باشد، اما باید از نمونه‌های مرتبط بیاید و fact تازه نسازد."
    return "COMMENTARY: فقط اگر با نوع محتوا و نمونه‌های مرتبط طبیعی است؛ از cute کردن مکانیکی متن خودداری کن."


def rerank_for_mode(examples: Sequence[T], mode: str) -> list[T]:
    if mode not in {"funnier", "softer", "precise"}:
        return list(examples)

    def bonus(item: T) -> tuple[float, str]:
        text = str(getattr(item, "text", ""))
        low = text.casefold()
        if mode == "funnier":
            score = 1.0 if re.search(r"(?:ㅋㅋ|ㅎㅎ|😂|🤣|خخ|lmao|lol|دارم میمیرم|میمیرم)", low, re.I) else 0.0
            return score, str(getattr(item, "example_id", ""))
        if mode == "softer":
            score = 1.0 if re.search(r"(?:🥺|💗|🩷|💘|عزیز|ناز|قربون|دوستش|soft|sweet|귀여|かわい)", low, re.I) else 0.0
            return score, str(getattr(item, "example_id", ""))
        factual = 1.0 if target_register(str(getattr(item, "content_type", "")), text) in {"factual", "explanatory"} else 0.0
        return factual, str(getattr(item, "example_id", ""))

    return sorted(
        examples,
        key=lambda item: (-bonus(item)[0], -float(getattr(item, "score", 0.0)), bonus(item)[1]),
    )


def diverse_after_rerank(items: Sequence[T], limit: int) -> list[T]:
    limit = max(1, min(int(limit), 12))
    chosen: list[T] = []
    fingerprints: set[str] = set()
    content_counts: dict[str, int] = {}
    for item in items:
        text = str(getattr(item, "text", ""))
        fp = re.sub(r"[0-9A-Za-z\u0600-\u06ff\u3040-\u30ff\uac00-\ud7af]+", "W", text[:140])
        fp = re.sub(r"W(?:\s+W)+", "W", fp)
        ctype = str(getattr(item, "content_type", "OTHER"))
        duplicate_shape = fp in fingerprints
        if duplicate_shape and len(chosen) >= 3:
            continue
        if content_counts.get(ctype, 0) >= max(4, limit - 2) and len(chosen) >= 4:
            continue
        fingerprints.add(fp)
        content_counts[ctype] = content_counts.get(ctype, 0) + 1
        chosen.append(item)
        if len(chosen) >= limit:
            return chosen
    selected = {str(getattr(x, "example_id", id(x))) for x in chosen}
    for item in items:
        key = str(getattr(item, "example_id", id(item)))
        if key not in selected:
            chosen.append(item)
            selected.add(key)
        if len(chosen) >= limit:
            break
    return chosen
