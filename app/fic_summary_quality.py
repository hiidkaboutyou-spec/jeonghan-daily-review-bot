from __future__ import annotations

import re

_PERSIAN_RE = re.compile(r"[\u0600-\u06ff]")
_LATIN_WORD_RE = re.compile(r"\b[A-Za-z][A-Za-z'-]{2,}\b")

# Conservative whole-word / phrase detectors for stiff machine-register Persian.
# Keep these boundary-aware: e.g. the pronoun «وی» must never match inside «جلوی».
_BOOKISH_PATTERNS = (
    re.compile(r"(?<!\S)می(?:‌|\s)?باشد(?=\s|[،؛,.!?؟]|$)"),
    re.compile(r"(?<!\S)نمود(?=\s|[،؛,.!?؟]|$)"),
    re.compile(r"(?<!\S)می(?:‌|\s)?نماید(?=\s|[،؛,.!?؟]|$)"),
    re.compile(r"(?<!\S)درصدد(?=\s|[،؛,.!?؟]|$)"),
    re.compile(r"(?<!\S)بدین\s+ترتیب(?=\s|[،؛,.!?؟]|$)"),
    re.compile(r"(?<!\S)وی(?=\s|[،؛,.!?؟]|$)"),
)
_MACHINE_SYNTAX_PATTERNS = (
    re.compile(r"خودش\s+را\s+در\s+حال\s+[^.؟!]{1,60}\s+می[‌ ]?یابد"),
    re.compile(r"این\s+به\s+(?:او|وی)\s+حس(?:ی|ِ)?\s+[^.؟!]{1,45}\s+می[‌ ]?دهد"),
)

# These are deliberately narrow. They catch only explicit source concepts whose
# removal would sanitize the story, while leaving nuanced theme interpretation to
# the grounded model prompt and the real-sample evaluation.
_EXPLICIT_CONCEPTS = (
    (re.compile(r"\b(?:sex|sexual)\b", re.I), re.compile(r"(?:سکس|جنسی|هم[‌ ]?خواب|کشش)")),
    (re.compile(r"\b(?:rape|non-consensual|noncon)\b", re.I), re.compile(r"(?:تجاوز|بدون رضایت)")),
    (re.compile(r"\b(?:violence|violent)\b", re.I), re.compile(r"(?:خشونت|خشن)")),
    (re.compile(r"\b(?:death|dead|dies|killed)\b", re.I), re.compile(r"(?:مرگ|مرد|می[‌ ]?میر|کشت)")),
)


def fic_summary_quality_issues(
    source: str,
    candidate: str,
    *,
    preserve_explicit_content: bool = True,
) -> list[str]:
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
    if persian_chars < 4:
        issues.append("not_persian")

    # A few English fandom terms, titles or proper names are normal in Persian.
    # Reject only when Latin prose clearly dominates the output.
    if len(latin_words) >= 5 and len(latin_words) > max(3, persian_chars // 3):
        issues.append("mostly_untranslated")

    folded_source = re.sub(r"\s+", " ", source_text).casefold()
    folded_candidate = re.sub(r"\s+", " ", text).casefold()
    if folded_source and folded_candidate == folded_source:
        issues.append("source_echo")

    if any(pattern.search(text) for pattern in _BOOKISH_PATTERNS):
        issues.append("bookish_register")
    if any(pattern.search(text) for pattern in _MACHINE_SYNTAX_PATTERNS):
        issues.append("awkward_machine_syntax")

    # Long source summaries should not collapse into a tiny generic sentence;
    # that usually means important setup was lost. Short blurbs remain allowed.
    if len(source_text) >= 220 and len(text) < 55:
        issues.append("overcompressed")

    if preserve_explicit_content and any(
        source_pattern.search(source_text) and not candidate_pattern.search(text)
        for source_pattern, candidate_pattern in _EXPLICIT_CONCEPTS
    ):
        issues.append("sanitized_explicit_content")

    return issues


def fic_summary_is_publishable(source: str, candidate: str) -> bool:
    return not fic_summary_quality_issues(source, candidate)
