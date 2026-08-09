from __future__ import annotations

"""Narrow human-gate fixes discovered from fresh SOURCE→OLD→NEW evidence.

This remains source-authorized: it tightens laughter fidelity and normalizes one
well-defined Japanese welcome-back phrase without adding any new fact.
"""

import re
from collections import Counter

from . import channel_part4_hardening as hardening
from . import channel_part4_humanfix as humanfix
from . import channel_style_runtime as runtime
from . import channel_translation as translation
from .ai import GroupCopy

_LAUGHTER_RE = re.compile(r"(?:ㅋ{2,}|ㅎ{2,})")
_BASE_VERIFY = humanfix.verify_hard_facts


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


_BaseWriter = translation.ChannelStyleCaptionWriter


class ChannelStyleCaptionWriter(_BaseWriter):
    def write_group(self, group, *, mode: str = "default") -> GroupCopy:
        result = super().write_group(group, mode=mode)
        repaired: dict[str, str] = {}
        for item in group.updates:
            body = result.bodies.get(item.id, item.text)
            repaired[item.id] = _normalize_source_authorized_japanese(item.text, body)
        return GroupCopy(result.title, result.category, repaired)


# New benchmark evidence must be regenerated for this production behavior.
humanfix.HUMAN_GATE_VERSION = 2
humanfix.HUMAN_GATE_FINGERPRINT = "channel-human-gate-v2"
humanfix.verify_hard_facts = verify_hard_facts
hardening.verify_hard_facts = verify_hard_facts
runtime.verify_hard_facts = verify_hard_facts
translation.verify_hard_facts = verify_hard_facts
translation.ChannelStyleCaptionWriter = ChannelStyleCaptionWriter
