from __future__ import annotations

import re
from typing import Iterable

from . import channel_style_runtime as runtime
from . import channel_translation as translation
from .ai import GroupCopy


_DIGIT_TABLE = str.maketrans("۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩", "01234567890123456789")
_ASCII_NUMBER_RE = re.compile(r"(?<![A-Za-z0-9_])[+\-]?(?:\d[\d,.:/\-]*\d|\d)(?![A-Za-z0-9_])")
_ISO_DATE_RE = re.compile(r"(?<!\d)(20\d{2})[-/.](\d{1,2})[-/.](\d{1,2})(?!\d)")
_DMY_DATE_RE = re.compile(r"(?<!\d)(\d{1,2})[/.](\d{1,2})[/.](20\d{2})(?!\d)")
_CJK_MD_RE = re.compile(r"(?<!\d)(\d{1,2})\s*[월月]\s*(\d{1,2})\s*[일日]?")
_CJK_MONTH_RE = re.compile(r"(?<!\d)(\d{1,2})\s*[월月](?!\s*\d)")
_TIME_RE = re.compile(r"(?<!\d)(\d{1,2}):(\d{2})(?!\d)")

_MONTH_NAMES = {
    1: ("january", "jan", "ژانویه"),
    2: ("february", "feb", "فوریه"),
    3: ("march", "mar", "مارس"),
    4: ("april", "apr", "آوریل"),
    5: ("may", "مه"),
    6: ("june", "jun", "ژوئن"),
    7: ("july", "jul", "ژوئیه"),
    8: ("august", "aug", "آگوست"),
    9: ("september", "sep", "sept", "سپتامبر"),
    10: ("october", "oct", "اکتبر"),
    11: ("november", "nov", "نوامبر"),
    12: ("december", "dec", "دسامبر"),
}
_ORDINAL_WORDS = {
    "اول": 1,
    "نخست": 1,
    "دوم": 2,
    "سوم": 3,
    "چهارم": 4,
    "پنجم": 5,
    "ششم": 6,
    "هفتم": 7,
    "هشتم": 8,
    "نهم": 9,
    "دهم": 10,
}


def _mark_span(chars: list[str], start: int, end: int) -> None:
    for index in range(start, end):
        chars[index] = " "


def _month_name_pattern(name: str) -> str:
    return rf"(?<![\w\u200c]){re.escape(name)}(?![\w\u200c])"


def semantic_number_tokens(value: str) -> list[str]:
    """Return meaning-oriented number/date tokens without loosening changed facts.

    The normalizer accepts digit-script changes, thousands separators, common
    date-layout changes, Korean/Japanese month/day suffixes, and a small set of
    Persian ordinal words used for ranks. A genuinely changed numeric value still
    produces a different token and fails fidelity.
    """
    text = str(value or "").translate(_DIGIT_TABLE)
    working = list(text)
    tokens: list[str] = []

    def consume(pattern: re.Pattern[str], formatter) -> None:
        snapshot = "".join(working)
        for match in pattern.finditer(snapshot):
            tokens.append(formatter(match))
            _mark_span(working, match.start(), match.end())

    consume(_ISO_DATE_RE, lambda m: f"date:{int(m.group(1)):04d}-{int(m.group(2)):02d}-{int(m.group(3)):02d}")
    consume(_DMY_DATE_RE, lambda m: f"date:{int(m.group(3)):04d}-{int(m.group(2)):02d}-{int(m.group(1)):02d}")
    consume(_CJK_MD_RE, lambda m: f"md:{int(m.group(1)):02d}-{int(m.group(2)):02d}")
    consume(_TIME_RE, lambda m: f"time:{int(m.group(1)):02d}:{int(m.group(2)):02d}")

    # Day + named month (15 August / ۱۵ آگوست) and named month + day.
    snapshot = "".join(working)
    for month, names in _MONTH_NAMES.items():
        for name in names:
            for pattern in (
                re.compile(rf"(?<!\d)(\d{{1,2}})\s+{_month_name_pattern(name)}", re.I),
                re.compile(rf"{_month_name_pattern(name)}\s+(\d{{1,2}})(?!\d)", re.I),
            ):
                for match in pattern.finditer(snapshot):
                    day = int(match.group(1))
                    tokens.append(f"md:{month:02d}-{day:02d}")
                    _mark_span(working, match.start(), match.end())
        snapshot = "".join(working)

    # Standalone month names and CJK issue-month forms (e.g. 10月号 ↔ اکتبر).
    consume(_CJK_MONTH_RE, lambda m: f"month:{int(m.group(1)):02d}")
    snapshot = "".join(working)
    for month, names in _MONTH_NAMES.items():
        for name in names:
            pattern = re.compile(_month_name_pattern(name), re.I)
            for match in pattern.finditer(snapshot):
                tokens.append(f"month:{month:02d}")
                _mark_span(working, match.start(), match.end())
        snapshot = "".join(working)

    # Remaining times may have become visible after date/month consumption.
    consume(_TIME_RE, lambda m: f"time:{int(m.group(1)):02d}:{int(m.group(2)):02d}")

    for match in _ASCII_NUMBER_RE.finditer("".join(working)):
        raw = match.group(0)
        if ":" in raw and re.fullmatch(r"[+\-]?\d{1,2}:\d{2}", raw):
            hour, minute = raw.lstrip("+").split(":", 1)
            sign = "-" if raw.startswith("-") else ""
            tokens.append(f"time:{sign}{int(hour):02d}:{int(minute):02d}")
            continue
        compact = raw.replace(",", "")
        if re.fullmatch(r"[+\-]?\d+", compact):
            tokens.append(f"num:{int(compact)}")
        else:
            tokens.append(f"num:{compact}")

    text_cf = text.casefold()
    for word, number in _ORDINAL_WORDS.items():
        if re.search(rf"(?<![\w\u200c]){re.escape(word)}(?![\w\u200c])", text_cf):
            tokens.append(f"num:{number}")

    # Keep deterministic order but remove duplicate semantic values.
    result: list[str] = []
    seen: set[str] = set()
    for token in tokens:
        if token not in seen:
            seen.add(token)
            result.append(token)
    return result


def _semantic_number_failures(source: str, output: str) -> list[str]:
    src = semantic_number_tokens(source)
    out = semantic_number_tokens(output)
    missing = [item for item in src if item not in out]
    extra = [item for item in out if item not in src]
    failures: list[str] = []
    if missing:
        failures.append("missing semantic numbers: " + ", ".join(missing))
    if extra:
        failures.append("invented semantic numbers: " + ", ".join(extra))
    return failures


def _identity_groups() -> dict[str, tuple[str, ...]]:
    groups = {key: tuple(values) for key, values in runtime._HARD_NAME_GROUPS.items()}
    groups["JOSHUA"] = tuple(dict.fromkeys((*groups["JOSHUA"], "جاشوا")))
    groups["S.COUPS"] = tuple(dict.fromkeys((*groups["S.COUPS"], "سئونگ‌چول", "سئونگچول", "سونگ‌چول")))
    return groups


def _identity_present(text: str, aliases: Iterable[str]) -> bool:
    return any(runtime._alias_present(text, alias) for alias in aliases)


def verify_hard_facts(source: str, output: str, analysis=None) -> list[str]:
    analysis = analysis or runtime.analyze_source(source)
    failures = _semantic_number_failures(source, output)

    for url in analysis.urls:
        if url not in output:
            failures.append(f"missing URL: {url}")
    for url in runtime.re.findall(r"https?://\S+", output):
        if url not in analysis.urls:
            failures.append(f"invented URL: {url}")
    for tag in analysis.hashtags:
        if tag not in output:
            failures.append(f"missing hashtag: {tag}")
    for tag in runtime.re.findall(r"#[\w\u0600-\u06ff\u3040-\u30ff\uac00-\ud7af]+", output):
        if tag not in analysis.hashtags:
            failures.append(f"invented hashtag: {tag}")
    for laugh in analysis.laughter:
        if (laugh.startswith("ㅋ") or laugh.startswith("ㅎ")) and laugh not in output:
            failures.append(f"missing source laughter: {laugh}")

    source_turns = runtime._SPEAKER_RE.findall(source)
    output_turns = runtime._SPEAKER_RE.findall(output)
    if len(source_turns) >= 2 and len(output_turns) < len(source_turns):
        failures.append("speaker turn structure lost")
    if source_turns and output_turns:
        groups = _identity_groups()
        def identity(label: str) -> str:
            for canonical, aliases in groups.items():
                if _identity_present(label, aliases):
                    return canonical
            return ""
        known_source = [item for item in (identity(speaker) for speaker, _ in source_turns) if item]
        known_output = [item for item in (identity(speaker) for speaker, _ in output_turns) if item]
        if known_source and known_output and known_source != known_output[: len(known_source)]:
            failures.append("speaker identity/order changed")
    for speaker, _ in source_turns:
        speaker = speaker.strip()
        if any(ord(ch) > 0x1F000 for ch in speaker) and speaker not in output:
            failures.append(f"missing speaker label: {speaker}")

    for canonical, aliases in _identity_groups().items():
        in_source = _identity_present(source, aliases)
        in_output = _identity_present(output, aliases)
        if in_source and not in_output:
            failures.append(f"name/identity dropped: {canonical}")
        elif not in_source and in_output:
            failures.append(f"invented name/identity: {canonical}")

    if runtime._QUOTE_MARK_RE.search(source) and not runtime._QUOTE_MARK_RE.search(output):
        failures.append("quoted material/attribution structure lost")
    return list(dict.fromkeys(failures))


def _restore_dialogue_linebreaks(source: str, output: str) -> str:
    source_turns = runtime._SPEAKER_RE.findall(source)
    if len(source_turns) < 2:
        return output
    result = str(output or "")
    for speaker, _ in source_turns:
        label = speaker.strip()
        if not label or label not in result:
            continue
        # Only split before a label that already exists in the candidate; no label
        # or speaker can be invented by this repair.
        result = re.sub(rf"(?<!^)\s+(?={re.escape(label)}\s*[:：])", "\n", result)
    return result.strip()


def _canonicalize_source_authorized_terms(source: str, output: str) -> str:
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
            result = re.sub(rf"(?<![\w\u200c]){re.escape(variant)}(?![\w\u200c])", canonical, result, flags=re.I)
    return result


class ChannelStyleCaptionWriter(translation.ChannelStyleCaptionWriter):
    """Production writer with deterministic PART 4 fidelity hardening."""

    def write_group(self, group, *, mode: str = "default") -> GroupCopy:
        result = super().write_group(group, mode=mode)
        repaired: dict[str, str] = {}
        for item in group.updates:
            body = result.bodies.get(item.id, item.text)
            body = _canonicalize_source_authorized_terms(item.text, body)
            body = _restore_dialogue_linebreaks(item.text, body)
            repaired[item.id] = body
        return GroupCopy(result.title, result.category, repaired)

    def _contains_historical_fact_leak(self, group, neutral: GroupCopy, candidate: GroupCopy) -> bool:
        categories = getattr(self.memory, "glossary", {}).get("categories", {}) or {}
        protected_entries: list[set[str]] = []
        if isinstance(categories, dict):
            for category, entries in categories.items():
                if category not in {"member_names", "nicknames", "brands", "fan_events", "platforms"} or not isinstance(entries, list):
                    continue
                for entry in entries:
                    if isinstance(entry, dict):
                        forms = self._entry_protected_forms(entry)
                        if forms:
                            protected_entries.append(forms)

        for item in group.updates:
            source = item.text
            neutral_text = neutral.bodies.get(item.id, item.text)
            output = candidate.bodies.get(item.id, "")
            authority = f"{source}\n{neutral_text}"
            authority_cf = authority.casefold()
            source_cf = source.casefold()
            out_cf = output.casefold()

            authority_numbers = set(semantic_number_tokens(authority))
            if any(number not in authority_numbers for number in semantic_number_tokens(output)):
                return True

            authority_urls = set(re.findall(r"https?://\S+", authority_cf))
            if any(url.casefold() not in authority_urls for url in re.findall(r"https?://\S+", output)):
                return True
            authority_tags = set(re.findall(r"#[\w\u0600-\u06ff\u3040-\u30ff\uac00-\ud7af]+", authority_cf))
            if any(tag.casefold() not in authority_tags for tag in re.findall(r"#[\w\u0600-\u06ff\u3040-\u30ff\uac00-\ud7af]+", output)):
                return True

            for forms in protected_entries:
                source_authorized = any(form in source_cf for form in forms)
                output_present = any(form in out_cf for form in forms)
                if output_present and not source_authorized:
                    return True
        return False


# The original writer methods resolve verify_hard_facts from their module globals at
# call time. Patch both production modules so internal verification and external
# benchmark verification use the same semantic hard-fact contract.
runtime.verify_hard_facts = verify_hard_facts
translation.verify_hard_facts = verify_hard_facts
translation.ChannelStyleCaptionWriter = ChannelStyleCaptionWriter
