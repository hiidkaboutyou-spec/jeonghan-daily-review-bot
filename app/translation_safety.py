from __future__ import annotations

"""Deterministic publishability checks for generated Persian channel copy.

These checks intentionally cover only high-confidence failures. They do not claim
to measure voice quality; uncertain or low-information material is sent to manual
review instead of being presented as publishable copy.
"""

import re

from .models import Update
from .channel_quality import classify_content_type

_PROTECTED_RE = re.compile(r"https?://\S+|[#@][\w\u0600-\u06ff\u3040-\u30ff\uac00-\ud7af]+")
_FOREIGN_RE = re.compile(r"[A-Za-z\u3040-\u30ff\u3400-\u9fff\uac00-\ud7af]")
_PERSIAN_RE = re.compile(r"[\u0600-\u06ff]")
_FAILURE_RE = re.compile(r"ترجمه[ٔ‌ ]+(?:خودکار[ ]+)?این پیام ناموفق", re.I)
_UNAVAILABLE_RE = re.compile(r"ترجمه[ٔ‌ ]+خودکار در دسترس نبود", re.I)
_MANUAL_TRANSLATION_FALLBACK_RE = re.compile(
    r"^⚠️ نیاز به بازبینی دستی \(سرویس ترجمه موقتاً در دسترس نیست\)", re.I
)
_LITERAL_RE = re.compile(
    r"(?:گوشی را راه[‌ -]?اندازی کنید|مثل یک شراب خوب پیر شده(?: است)?|"
    r"نسخه بد خود را نشان می[‌ ]?دهد|اول تو خانواده من هستی)",
    re.I,
)
_SOCIAL_LITERAL_RE = re.compile(
    r"(?:این چه کراس[‌ -]?اوری است|این چه همکاری متقاطعی است|"
    r"(?:این|آن) حس(?:ِ|ی)?[^.؟!]{0,45}را می[‌ ]?دهد|"
    r"نه، زیرا چرا|چرا او فقط در آنجا ایستاده است)",
    re.I,
)
_NONSENSE_NAME_RE = re.compile(
    r"(?:نازی[‌ ]های سیوخان|جئونگان|جیونگان|جونگهانی|جئونگهان|"
    r"سئونگ[‌ ]?چئول|جئونگان تو سئونگ)",
    re.I,
)
_HASHTAG_ONLY_RE = re.compile(r"^(?:\s*#[\w\u0600-\u06ff\u3040-\u30ff\uac00-\ud7af]+[،,؛;.!؟?]*)+\s*$")
_INFORMAL_TYPES = {
    "LIVE_DIALOGUE", "WEVERSE_POST", "WEVERSE_LIVE", "FANSIGN",
    "PHOTO_REACTION", "VIDEO_REACTION", "MEMBER_QUOTE", "MEMBER_INTERACTION",
    "SHORT_REACTION", "FAN_ACCOUNT_OR_OP_STORY",
}
_BOOKISH_RE = re.compile(
    r"(?:\bاو\b|\bایشان\b|می کند|می دهد|می شود|نمی کند|نمی کنم|"
    r"می[‌ ]?(?:توانم|تواند|شوم|شود|خواهم)|اطرافیانم را|"
    r"به وضوح|متعلق به|با استفاده از|خود را|"
    r"به هیچ چیز[^.؟!]{0,40}نیاز ندارم|من الان \d+ ساله هستم|"
    r"به دوربین لبخند می زند|دارد چه کار می کند|\sـ(?:ه|ست)(?:\s|$))",
    re.I,
)
# Voice-aware: formal verb conjugations that break the colloquial voice
_FORMAL_VERB_RE = re.compile(
    r"(?:می[‌ ]?شود|می[‌ ]?کند|می[‌ ]?خواهد|می[‌ ]?باشد|می[‌ ]?نماید|"
    r"درصدد|استفاده از|متعلق به|به وضوح|اطرافیان)",
    re.I,
)
# Voice-aware: excessive emoji (>4 in a short text)
_EXCESSIVE_EMOJI_RE = re.compile(
    r"[\U0001F600-\U0001F64F\U0001F300-\U0001F5FF\U0001F680-\U0001F6FF"
    r"\U0001F1E0-\U0001F1FF\U00002702-\U000027B0\U0001f926-\U0001f937"
    r"\U00010000-\U0010ffff\u2600-\u2B55]{5,}",
)
# Voice-aware: generic praise without specific observation.
# Only matches standalone empty praise or repeated empty words,
# not natural Persian sentences that contain these words.
_GENERIC_PRAISE_RE = re.compile(
    r"(?:^\s*خیلی خوبه\s*$|^\s*عالیه\s*$|^\s*فوق\u200c?العاده\u200c?ست\s*$|^\s*خیلی قشنگه\s*$"
    r"|^\s*بهترینه\s*$|^\s*عالیه خیلی خوبه\s*$"
    r"|عالیه\s+عالیه(?:\s+عالیه)*|خیلی خوبه\s+خیلی خوبه)",
    re.I,
)


def metadata_only(update: Update) -> bool:
    if update.quoted_text.strip():
        return False
    text = update.text.strip()
    return not text or bool(_HASHTAG_ONLY_RE.fullmatch(text))


def translation_unavailable(value: str) -> bool:
    """Return true for an outage placeholder that must never be delivered as a draft."""
    return bool(_UNAVAILABLE_RE.search(str(value or "")))


def manual_translation_fallback(value: str) -> bool:
    """Return true for a source-preserving outage draft safe for private review."""
    return bool(_MANUAL_TRANSLATION_FALLBACK_RE.search(str(value or "").strip()))


def safe_metadata_body(update: Update) -> str:
    kinds = {item.kind.casefold() for item in update.media}
    if "video" in kinds or "animated_gif" in kinds:
        label = "🎥 پست ویدیویی بدون متن"
    elif kinds:
        label = "📸 پست تصویری بدون متن"
    else:
        label = "پست بدون متن قابل ترجمه"
    return f"{label}\n{update.text.strip()}".strip()


def natural_persian_failures(update: Update, output: str) -> list[str]:
    """High-confidence register failures, not a claim of full voice evaluation."""
    content_type = classify_content_type(update.translation_source())
    if content_type not in _INFORMAL_TYPES:
        return []
    failures: list[str] = []
    text = str(output or "")
    if _BOOKISH_RE.search(text):
        failures.append("bookish or machine-like register for informal source")
    # Voice-aware checks: detect patterns that break the channel's natural voice.
    # Only flag formal verbs that weren't already caught by _BOOKISH_RE above.
    if not _BOOKISH_RE.search(text) and _FORMAL_VERB_RE.search(text):
        failures.append("formal verb conjugation in informal context")
    if _EXCESSIVE_EMOJI_RE.search(text):
        failures.append("excessive emoji usage")
    if _GENERIC_PRAISE_RE.search(text):
        failures.append("generic praise without specific observation")
    return failures


def semantic_quality_failures(update: Update, output: str) -> list[str]:
    source = update.translation_source()
    candidate = str(output or "").strip()
    failures: list[str] = []
    if metadata_only(update):
        return failures
    if not candidate or _FAILURE_RE.search(candidate):
        failures.append("automatic translation failure")
    if _LITERAL_RE.search(candidate):
        failures.append("high-confidence literal Persian")
    if _SOCIAL_LITERAL_RE.search(candidate):
        failures.append("literal social-media slang")
    if _NONSENSE_NAME_RE.search(candidate):
        failures.append("malformed entity or nonsensical phrase")
    failures.extend(natural_persian_failures(update, candidate))

    unprotected = _PROTECTED_RE.sub("", candidate)
    # Uppercase official titles such as BAD/SUPER are intentionally preserved.
    for token in re.findall(r"(?<![A-Za-z])[A-Z][A-Z0-9_-]{1,}(?![A-Za-z])", source):
        unprotected = re.sub(rf"(?<![A-Za-z]){re.escape(token)}(?![A-Za-z])", "", unprotected)
    if re.search(r"(?:[A-Za-z][\u0600-\u06ff]|[\u0600-\u06ff][A-Za-z])", unprotected):
        failures.append("malformed mixed-script token")
    foreign = len(_FOREIGN_RE.findall(unprotected))
    persian = len(_PERSIAN_RE.findall(unprotected))
    if foreign >= 4 and foreign > persian / 2:
        failures.append("substantial untranslated source language")

    source_letters = len(re.findall(r"[\w\u0600-\u06ff\u3040-\u30ff\uac00-\ud7af]", source))
    output_letters = len(re.findall(r"[\w\u0600-\u06ff]", unprotected))
    if source_letters >= 100 and output_letters < 24:
        failures.append("implausibly incomplete translation")
    output_words = re.findall(r"[\w\u0600-\u06ff]+", unprotected.casefold())
    if len(output_words) >= 8 and len(set(output_words)) <= 3:
        failures.append("degenerate repetitive output")

    # A short, contextless fan reaction cannot honestly be certified publishable.
    natural_source = _PROTECTED_RE.sub("", source)
    words = re.findall(r"[\w\u0600-\u06ff\u3040-\u30ff\uac00-\ud7af]+", natural_source)
    if not update.media and len(words) < 3 and len(natural_source.strip()) < 28:
        failures.append("low-information source needs editorial judgment")
    return list(dict.fromkeys(failures))


def manual_review_body(body: str, reasons: list[str]) -> str:
    labels = {
        "automatic translation failure": "ترجمهٔ خودکار ناموفق",
        "high-confidence literal Persian": "فارسی تحت‌اللفظی",
        "malformed entity or nonsensical phrase": "نام یا عبارت نامفهوم",
        "substantial untranslated source language": "بخش ترجمه‌نشده",
        "implausibly incomplete translation": "ترجمهٔ احتمالاً ناقص",
        "degenerate repetitive output": "خروجی تکراری و نامعتبر",
        "low-information source needs editorial judgment": "منبع کم‌اطلاعات",
        "bookish or machine-like register for informal source": "لحن کتابی یا ماشینی",
        "malformed mixed-script token": "واژهٔ مخلوط و نامعتبر",
        "literal social-media slang": "اسلنگ تحت‌اللفظی و غیرطبیعی",
        "formal verb conjugation in informal context": " فعل رسمی در متن عامیانه",
        "excessive emoji usage": "ایموجی بیش از حد",
        "generic praise without specific observation": "تعریف کلی بدون جزئیات",
    }
    reason = "، ".join(labels.get(item, item) for item in reasons)
    return f"⚠️ نیاز به بازبینی دستی ({reason})\n\n{body.strip()}".strip()
