from __future__ import annotations

import re

from . import channel_part4_hardening as hardening


# English ordinal words are semantic numeric facts just like their Persian forms.
# This is deliberately small/bounded rather than a general word-to-number parser.
hardening._ORDINAL_WORDS.update(
    {
        "first": 1,
        "second": 2,
        "third": 3,
        "fourth": 4,
        "fifth": 5,
        "sixth": 6,
        "seventh": 7,
        "eighth": 8,
        "ninth": 9,
        "tenth": 10,
    }
)


def _safe_canonicalize_source_authorized_terms(source: str, output: str) -> str:
    result = str(output or "")
    source_cf = str(source or "").casefold()
    replacements: list[tuple[tuple[str, ...], str, tuple[str, ...]]] = [
        (("jeonghan", "yoon jeonghan", "정한", "윤정한", "ジョンハン"), "جونگهان", ("Jeonghan", "Yoon Jeonghan")),
        (("joshua", "조슈아", "ジョシュア"), "جاشوآ", ("جاشوا",)),
        (("seungcheol", "s.coups", "scoups", "승철", "에스쿱스", "エスクプス"), "سونگچول", ("سئونگ‌چول", "سئونگچول", "سونگ‌چول")),
        (("fancall", "fan call"), "فن‌کال", ("فنس‌کال", "فن کال")),
    ]
    for source_aliases, canonical, output_variants in replacements:
        if not any(alias.casefold() in source_cf for alias in source_aliases):
            continue
        for variant in output_variants:
            # Hashtags and @mentions are exact source facts. Never transliterate or
            # canonicalize inside them; only ordinary prose tokens are normalized.
            result = re.sub(
                rf"(?<![#@\w\u200c]){re.escape(variant)}(?![\w\u200c])",
                canonical,
                result,
                flags=re.I,
            )
    return result


# ChannelStyleCaptionWriter.write_group resolves this module global at call time,
# so replacing it updates the production hardening without a second writer class.
hardening._canonicalize_source_authorized_terms = _safe_canonicalize_source_authorized_terms
