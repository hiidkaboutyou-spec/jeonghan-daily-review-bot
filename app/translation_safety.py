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
_LITERAL_RE = re.compile(
    r"(?:گوشی را راه[‌ -]?اندازی کنید|مثل یک شراب خوب پیر شده(?: است)?|"
    r"نسخه بد خود را نشان می[‌ ]?دهد|اول تو خانواده من هستی)",
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
    r"به هیچ چیز[^.؟!]{0,40}نیاز ندارم|من الان \d+ ساله هستم|"
    r"به دوربین لبخند می زند|دارد چه کار می کند|\sـ(?:ه|ست)(?:\s|$))",
    re.I,
)


def metadata_only(update: Update) -> bool:
    if update.quoted_text.strip():
        return False
    text = update.text.strip()
    return not text or bool(_HASHTAG_ONLY_RE.fullmatch(text))


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
    if _BOOKISH_RE.search(str(output or "")):
        return ["bookish or machine-like register for informal source"]
    return []


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
    if _NONSENSE_NAME_RE.search(candidate):
        failures.append("malformed entity or nonsensical phrase")
    failures.extend(natural_persian_failures(update, candidate))

    unprotected = _PROTECTED_RE.sub("", candidate)
    # Uppercase official titles such as BAD/SUPER are intentionally preserved.
    for token in re.findall(r"(?<![A-Za-z])[A-Z][A-Z0-9_-]{1,}(?![A-Za-z])", source):
        unprotected = re.sub(rf"(?<![A-Za-z]){re.escape(token)}(?![A-Za-z])", "", unprotected)
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
    }
    reason = "، ".join(labels.get(item, item) for item in reasons)
    return f"⚠️ نیاز به بازبینی دستی ({reason})\n\n{body.strip()}".strip()
