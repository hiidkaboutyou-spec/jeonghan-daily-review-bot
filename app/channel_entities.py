from __future__ import annotations

"""Deterministic, source-authorized channel entity spelling rules.

Jeonghan has three valid Persian renderings in this channel, selected by context:
- «یون جونگهان» for an explicit full-name reference.
- «هانی» for an explicit Hani/하니 nickname reference.
- «جونگهان» for the ordinary stage/name reference.

When the source is mixed or the model deliberately chooses another one of those
three valid forms for a plain Jeonghan reference, that valid choice is preserved.
Bad transliterations and untranslated EN/KR/JP forms are normalized into the same
name family. URLs, hashtags and @mentions are protected byte-for-byte.
"""

import re

from .ai import GroupCopy
from .models import EventGroup

_PROTECTED_RE = re.compile(r"https?://\S+|[#@][\w\u0600-\u06ff\u3040-\u30ff\uac00-\ud7af]+")

# Match explicit source forms. Non-Latin names can have grammatical particles or
# address suffixes attached directly (정한이/정한아/ジョンハンが), so they must not
# depend on a Python \w boundary after the name core.
_FULL_SOURCE_RE = re.compile(
    r"(?i)(?<![\w#@])(?:yoon\s+jeonghan|윤정한|ユン[・･\s]?ジョンハン)"
)
_HANI_SOURCE_RE = re.compile(
    r"(?i)(?<![\w#@])(?:hani(?:e)?|hannie|하니|정하니|윤정하니|ハニ)(?![A-Za-z])"
)
_PLAIN_SOURCE_RE = re.compile(
    r"(?i)(?<![\w#@])(?:jeonghan|정한|ジョンハン)"
)

# Match every family we may need to repair. More specific/full forms come first.
# Possessive English 's is consumed because Persian does not use it.
_JEONGHAN_OUTPUT_RE = re.compile(
    r"(?i)(?<![#@\w\u200c])(?:"
    r"(?:yoon\s+)?jeonghan(?:['’]s)?|"
    r"윤정한|ユン[・･\s]?ジョンハン|"
    r"hannie|hanie|hani|윤정하니|정하니|하니|ハニ|"
    r"یون[‌\s-]*(?:جونگهان|جونگ‌هان|جونگان|جئونگهان|جئونگان|جیونگهان|جیونگ‌هان|جنگهان|جنگ‌هان)|"
    r"یون\s+جونگهان|"
    r"هانی|"
    r"جونگهان|جونگ‌هان|جونگان|جئونگهان|جئونگان|جیونگهان|جیونگ‌هان|جنگهان|جنگ‌هان|"
    r"정한|ジョンハン"
    r")(?![A-Za-z0-9_\u0600-\u06ff\u3040-\u30ff\uac00-\ud7af\u200c])"
)

_ACCEPTED_OUTPUT_RE = re.compile(
    r"(?<![#@\w\u200c])(?:یون\s+جونگهان|جونگهان|هانی)(?![\w\u200c])"
)


def _is_protected(position: int, protected: list[tuple[int, int]]) -> bool:
    return any(start <= position < end for start, end in protected)


def _source_kinds(source: str) -> set[str]:
    """Return source-authorized Jeonghan naming families: full/plain/hani."""
    text = str(source or "")
    protected = [(m.start(), m.end()) for m in _PROTECTED_RE.finditer(text)]
    kinds: set[str] = set()
    occupied: list[tuple[int, int]] = []

    # Specific forms first so 윤정하니 is not reduced to a plain 정한-like hit and
    # Yoon Jeonghan is not double-counted as both full and plain.
    for kind, pattern in (("hani", _HANI_SOURCE_RE), ("full", _FULL_SOURCE_RE)):
        for match in pattern.finditer(text):
            if _is_protected(match.start(), protected):
                continue
            kinds.add(kind)
            occupied.append((match.start(), match.end()))

    for match in _PLAIN_SOURCE_RE.finditer(text):
        if _is_protected(match.start(), protected):
            continue
        if any(start <= match.start() < end for start, end in occupied):
            continue
        kinds.add("plain")

    return kinds


def source_names_jeonghan(source: str) -> bool:
    return bool(_source_kinds(source))


def preferred_jeonghan_form(source: str) -> str | None:
    """Return a forced Persian form only when source naming intent is unambiguous."""
    kinds = _source_kinds(source)
    if kinds == {"full"}:
        return "یون جونگهان"
    if kinds == {"hani"}:
        return "هانی"
    if kinds == {"plain"}:
        return "جونگهان"
    # Mixed source styles: preserve each valid family chosen by the translation and
    # only repair malformed/untranslated forms.
    return None


def _family_for_output(token: str) -> str:
    lowered = token.casefold().replace("’", "'")
    if (
        lowered.startswith("yoon ")
        or token.startswith("윤정한")
        or token.startswith("ユン")
        or re.match(r"یون[‌\s-]*", token)
    ):
        return "full"
    if (
        lowered.startswith("hani")
        or token.startswith(("하니", "정하니", "윤정하니", "ハニ"))
        or token == "هانی"
    ):
        return "hani"
    return "plain"


def _canonical_for_family(family: str) -> str:
    if family == "full":
        return "یون جونگهان"
    if family == "hani":
        return "هانی"
    return "جونگهان"


def canonicalize_jeonghan(source: str, output: str) -> str:
    """Normalize Jeonghan references without flattening valid contextual naming.

    Explicit full-name and nickname sources force their matching Persian form.
    Plain references force «جونگهان». Mixed-source text preserves the family of
    each translated mention while fixing spelling/untranslated-script errors.
    """
    result = str(output or "")
    if not source_names_jeonghan(source):
        return result

    forced = preferred_jeonghan_form(source)

    def replace_entity(match: re.Match[str]) -> str:
        if forced is not None:
            return forced
        return _canonical_for_family(_family_for_output(match.group(0)))

    pieces: list[str] = []
    cursor = 0
    for match in _PROTECTED_RE.finditer(result):
        pieces.append(_JEONGHAN_OUTPUT_RE.sub(replace_entity, result[cursor:match.start()]))
        pieces.append(match.group(0))
        cursor = match.end()
    pieces.append(_JEONGHAN_OUTPUT_RE.sub(replace_entity, result[cursor:]))
    return "".join(pieces)


def entity_failures(source: str, output: str) -> list[str]:
    if not source_names_jeonghan(source):
        return []

    normalized = canonicalize_jeonghan(source, output)
    required = preferred_jeonghan_form(source)
    if required is not None:
        required_re = re.compile(
            rf"(?<![#@\w\u200c]){re.escape(required)}(?![\w\u200c])"
        )
        if not required_re.search(normalized):
            return [f"missing contextual source entity: {required}"]
        return []

    if not _ACCEPTED_OUTPUT_RE.search(normalized):
        return ["missing contextual source entity: یون جونگهان|جونگهان|هانی"]
    return []


def canonicalize_group(group: EventGroup, copy: GroupCopy) -> GroupCopy:
    return GroupCopy(
        title=canonicalize_jeonghan("\n".join(item.text for item in group.updates), copy.title),
        category=copy.category,
        bodies={
            item.id: canonicalize_jeonghan(item.text, copy.bodies.get(item.id, item.text))
            for item in group.updates
        },
    )
