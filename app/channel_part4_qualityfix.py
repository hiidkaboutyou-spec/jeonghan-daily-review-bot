from __future__ import annotations

"""Narrow human-gate fixes discovered from fresh SOURCE→OLD→NEW evidence.

All repairs remain source-authorized: they tighten laughter/emoji fidelity and
normalize well-defined mistranslations/transliterations without adding any new fact.
"""

import re
from collections import Counter

from . import channel_part4_hardening as hardening
from . import channel_part4_humanfix as humanfix
from . import channel_style_runtime as runtime
from . import channel_translation as translation
from .ai import GroupCopy

_LAUGHTER_RE = re.compile(r"(?:ㅋ{2,}|ㅎ{2,})")
_EMOJI_RE = re.compile(r"[\U0001F300-\U0001FAFF\u2600-\u27BF]")
_BASE_VERIFY = humanfix.verify_hard_facts
_BASE_TRANSLATE_LINE = translation._translate_line
_JEONGHAN_SOURCE_ALIASES = ("jeonghan", "yoon jeonghan", "정한", "윤정한", "ジョンハン")
_JEONGHAN_BAD_PERSIAN = (
    "جئونگان",
    "جیونگهان",
    "جونگان",
    "جئونگهان",
    "جونگ‌هان",
)


def _laughter_count_failures(source: str, output: str) -> list[str]:
    src = Counter(_LAUGHTER_RE.findall(str(source or "")))
    out = Counter(_LAUGHTER_RE.findall(str(output or "")))
    if src == out:
        return []
    failures: list[str] = []
    for token, count in src.items():
        if out.get(token, 0) < count:
            failures.append(f"missing source laughter count: {token} x{count - out.get(token, 0)}")
    for token, count in out.items():
        if src.get(token, 0) < count:
            failures.append(f"invented source laughter count: {token} x{count - src.get(token, 0)}")
    return failures


def verify_hard_facts(source: str, output: str, analysis=None) -> list[str]:
    failures = list(_BASE_VERIFY(source, output, analysis))
    failures.extend(_laughter_count_failures(source, output))
    return list(dict.fromkeys(failures))


def _normalize_source_authorized_japanese(source: str, output: str) -> str:
    result = str(output or "")
    if "おかえり" in str(source or ""):
        # `おかえり` is a natural "welcome back". The literal Persian phrase
        # "به بازگشت خوش اومدی" is not idiomatic; this replacement removes only
        # that mistranslation and preserves the surrounding sentence.
        result = re.sub(r"به\s+بازگشت\s+خوش\s+(?:اومدی|آمدی)", "خوش اومدی", result)
    return result


def _normalize_source_authorized_identity(source: str, output: str) -> str:
    """Repair known Persian transliteration drift only when SOURCE names Jeonghan."""
    source_cf = str(source or "").casefold()
    result = str(output or "")
    if not any(alias.casefold() in source_cf for alias in _JEONGHAN_SOURCE_ALIASES):
        return result
    for variant in _JEONGHAN_BAD_PERSIAN:
        # Never rewrite inside hashtags/@mentions; those are exact source facts.
        result = re.sub(
            rf"(?<![#@\w\u200c]){re.escape(variant)}(?![\w\u200c])",
            "جونگهان",
            result,
        )
    return result


def _restore_missing_source_tokens(source: str, output: str) -> str:
    """Preserve source emoji/laughter counts in deterministic neutral fallback.

    This only restores tokens that SOURCE already contains. It never removes or
    invents semantic content and therefore cannot authorize a fact from output.
    """
    result = str(output or "").strip()
    for regex in (_EMOJI_RE, _LAUGHTER_RE):
        source_counts = Counter(regex.findall(str(source or "")))
        output_counts = Counter(regex.findall(result))
        missing: list[str] = []
        for token, count in source_counts.items():
            missing.extend([token] * max(0, count - output_counts.get(token, 0)))
        if missing:
            suffix = " ".join(missing)
            result = f"{result} {suffix}".strip()
    return result


def _safe_fallback_translate_line(text: str) -> str:
    """Harden the actual deterministic fallback used when Gemini is unavailable."""
    translated = _BASE_TRANSLATE_LINE(text)
    translated = _normalize_source_authorized_japanese(text, translated)
    translated = _normalize_source_authorized_identity(text, translated)
    translated = _restore_missing_source_tokens(text, translated)
    return translated


_BaseWriter = translation.ChannelStyleCaptionWriter


class ChannelStyleCaptionWriter(_BaseWriter):
    def write_group(self, group, *, mode: str = "default") -> GroupCopy:
        result = super().write_group(group, mode=mode)
        repaired: dict[str, str] = {}
        for item in group.updates:
            body = result.bodies.get(item.id, item.text)
            body = _normalize_source_authorized_japanese(item.text, body)
            body = _normalize_source_authorized_identity(item.text, body)
            body = _restore_missing_source_tokens(item.text, body)
            repaired[item.id] = body
        return GroupCopy(result.title, result.category, repaired)


# Harden the base neutral fallback itself so every caller, including benchmark
# paths that hold an earlier writer-class reference, receives the same safe line.
translation._translate_line = _safe_fallback_translate_line

# New benchmark evidence must be regenerated for this production behavior.
humanfix.HUMAN_GATE_VERSION = 4
humanfix.HUMAN_GATE_FINGERPRINT = "channel-human-gate-v4"
humanfix.verify_hard_facts = verify_hard_facts
hardening.verify_hard_facts = verify_hard_facts
runtime.verify_hard_facts = verify_hard_facts
translation.verify_hard_facts = verify_hard_facts
translation.ChannelStyleCaptionWriter = ChannelStyleCaptionWriter
