from __future__ import annotations

import re

_PERSIAN_RE = re.compile(r"[\u0600-\u06ff]")
_LATIN_WORD_RE = re.compile(r"\b[A-Za-z][A-Za-z'-]{2,}\b")

# Common literal / bookish constructions that repeatedly make short AO3 blurbs
# sound machine-translated in Persian. This is deliberately conservative: it is
# a quality gate, not an automatic rewriter.
_BOOKISH_MARKERS = (
    "می‌باشد",
    "می باشد",
    "نمود",
    "می‌نماید",
    "می نماید",
    "درصدد",
    "بدین ترتیب",
    "وی ",
)


def fic_summary_quality_issues(source: str, candidate: str) -> list[str]:
    """Return deterministic reasons a generated Persian fic summary is unsafe to ship.

    The gate catches obvious non-Persian, untranslated and machine-register output
    without pretending it can judge all literary quality. Rejected outputs fall
    back to the original AO3 summary instead of silently publishing bad Persian.
    """
    text = str(candidate or "").strip()
    source_text = str(source or "").strip()
    issues: list[str] = []
    if not text:
        return ["empty"]

    persian_chars = len(_PERSIAN_RE.findall(text))
    latin_words = _LATIN_WORD_RE.findall(text)
    visible_letters = sum(ch.isalpha() for ch in text)
    if persian_chars < 4:
        issues.append("not_persian")
    if visible_letters and persian_chars / max(visible_letters, 1) < 0.45 and len(latin_words) >= 3:
        issues.append("mostly_untranslated")

    # Exact or near-exact English source echoes are never acceptable as a
    # successful translation result.
    folded_source = re.sub(r"\s+", " ", source_text).casefold()
    folded_candidate = re.sub(r"\s+", " ", text).casefold()
    if folded_source and folded_candidate == folded_source:
        issues.append("source_echo")

    for marker in _BOOKISH_MARKERS:
        if marker in text:
            issues.append("bookish_register")
            break

    # Long source summaries should not collapse into a tiny generic sentence;
    # that usually means important setup was lost. Short blurbs remain allowed.
    if len(source_text) >= 220 and len(text) < 55:
        issues.append("overcompressed")

    return issues


def fic_summary_is_publishable(source: str, candidate: str) -> bool:
    return not fic_summary_quality_issues(source, candidate)
