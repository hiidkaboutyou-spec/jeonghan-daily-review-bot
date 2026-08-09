from __future__ import annotations

"""Deterministic channel entity spelling rules.

These rules are editorial preferences, not historical facts. They are applied only
when the CURRENT source itself names the entity. URLs, hashtags and @mentions are
protected byte-for-byte.
"""

import re

from .ai import GroupCopy
from .models import EventGroup

_PROTECTED_RE = re.compile(r"https?://\S+|[#@][\w\u0600-\u06ff\u3040-\u30ff\uac00-\ud7af]+")

# Source-side forms. Possessives/particles may follow; the entity core is enough to
# authorize the editorial spelling in the Persian output.
_JEONGHAN_SOURCE_RE = re.compile(
    r"(?i)(?<![\w#@])(?:yoon\s+jeonghan|jeonghan|윤정한|정한|ジョンハン)(?![\w])"
)

# Output-side forms include both bad Persian transliterations AND untranslated
# source-script forms. Possessive 's is consumed because Persian does not use it.
_JEONGHAN_OUTPUT_RE = re.compile(
    r"(?i)(?<![#@\w\u200c])(?:"
    r"(?:yoon\s+)?jeonghan(?:['’]s)?|"
    r"윤정한|정한|ジョンハン|"
    r"یون[‌\s-]*جونگهان|یون[‌\s-]*جئونگهان|یون[‌\s-]*جیونگهان|"
    r"جونگهان|جونگ‌هان|جونگان|جئونگهان|جئونگان|جیونگهان|جیونگ‌هان|جنگهان|جنگ‌هان"
    r")(?![\w\u200c])"
)


def source_names_jeonghan(source: str) -> bool:
    text = str(source or "")
    protected = [(m.start(), m.end()) for m in _PROTECTED_RE.finditer(text)]
    for match in _JEONGHAN_SOURCE_RE.finditer(text):
        if not any(start <= match.start() < end for start, end in protected):
            return True
    return False


def canonicalize_jeonghan(source: str, output: str) -> str:
    """Return output with source-authorized Jeonghan mentions spelled «جونگهان».

    This also fixes untranslated `jeonghan`, `정한`, and `ジョンハン` left behind by
    a generic fallback. It never touches URL/hashtag/@mention text.
    """
    result = str(output or "")
    if not source_names_jeonghan(source):
        return result

    pieces: list[str] = []
    cursor = 0
    for match in _PROTECTED_RE.finditer(result):
        pieces.append(_JEONGHAN_OUTPUT_RE.sub("جونگهان", result[cursor:match.start()]))
        pieces.append(match.group(0))
        cursor = match.end()
    pieces.append(_JEONGHAN_OUTPUT_RE.sub("جونگهان", result[cursor:]))
    return "".join(pieces)


def entity_failures(source: str, output: str) -> list[str]:
    if not source_names_jeonghan(source):
        return []
    normalized = canonicalize_jeonghan(source, output)
    if not re.search(r"(?<![#@\w\u200c])جونگهان(?![\w\u200c])", normalized):
        return ["missing canonical source entity: جونگهان"]
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
