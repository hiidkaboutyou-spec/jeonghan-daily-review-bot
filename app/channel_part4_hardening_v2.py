from __future__ import annotations

import re

from . import channel_part4_hardening as hardening


# English ordinal words are semantic equivalents of the Persian ordinal forms
# already supported by PART 4 hard-fact normalization. Adding them closes a
# verifier false positive such as "first" -> "اول" without allowing a changed
# ordinal value to pass.
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

_original_canonicalize = hardening._canonicalize_source_authorized_terms
_HASHTAG_RE = re.compile(r"#[\w\u0600-\u06ff\u3040-\u30ff\uac00-\ud7af]+", re.UNICODE)


def _canonicalize_without_touching_hashtags(source: str, output: str) -> str:
    """Canonicalize channel terms while preserving hashtag bytes exactly.

    Hashtags are hard factual/source tokens. A member-name spelling may be
    canonicalized in prose, but `#JEONGHAN` must never become `#جونگهان`.
    Existing candidate hashtags are protected before the normal source-authorized
    prose canonicalizer runs, then restored unchanged. If Gemini itself changed or
    invented a hashtag, the hard verifier still rejects it.
    """
    protected: list[str] = []

    def stash(match: re.Match[str]) -> str:
        index = len(protected)
        protected.append(match.group(0))
        return f"__P4TAG_{index}__"

    masked = _HASHTAG_RE.sub(stash, str(output or ""))
    fixed = _original_canonicalize(source, masked)
    for index, tag in enumerate(protected):
        fixed = fixed.replace(f"__P4TAG_{index}__", tag)
    return fixed


# The existing production hardening writer resolves this helper from its module
# globals at call time, so this patch applies to normal private-review runtime and
# the real benchmark without changing the quality gate itself.
hardening._canonicalize_source_authorized_terms = _canonicalize_without_touching_hashtags
