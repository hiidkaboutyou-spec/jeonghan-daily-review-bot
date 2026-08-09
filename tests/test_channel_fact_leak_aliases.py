from __future__ import annotations

import unittest
from datetime import datetime, timezone

from app.ai import GroupCopy
from app.channel_translation import ChannelStyleCaptionWriter
from app.models import EventGroup, Update


JEONGHAN = {
    "category": "member_names",
    "canonical_form": "جونگهان",
    "alternatives": ["یون جونگهان"],
    "aliases": ["Jeonghan", "Yoon Jeonghan", "정한", "윤정한", "ジョンハン"],
}
JOSHUA = {
    "category": "member_names",
    "canonical_form": "جاشوآ",
    "alternatives": ["شوا"],
    "aliases": ["Joshua", "조슈아", "ジョシュア"],
}


class _Memory:
    def __init__(self):
        self.glossary = {"categories": {"member_names": [JEONGHAN, JOSHUA]}}
        self.profile = {}

    def relevant_glossary(self, source: str, neutral: str):
        haystack = f"{source}\n{neutral}".casefold()
        out = []
        if "jeonghan" in haystack or "جونگهان" in haystack:
            out.append(dict(JEONGHAN))
        if "joshua" in haystack or "جاشوآ" in haystack or "شوا" in haystack:
            out.append(dict(JOSHUA))
        return out


def _group(source: str) -> EventGroup:
    update = Update(
        id="1",
        url="https://x.com/source/status/1",
        author="source",
        author_name="source",
        text=source,
        created_at=datetime(2026, 8, 9, tzinfo=timezone.utc),
        lang="en",
        category="general",
    )
    return EventGroup(key="event", category="general", title="title", updates=[update])


class SourceAuthorizedGlossaryLeakTests(unittest.TestCase):
    def setUp(self):
        self.writer = ChannelStyleCaptionWriter("", "gemini-2.5-flash-lite", _Memory())

    def test_source_jeonghan_allows_canonical_persian_even_if_neutral_transliteration_is_bad(self):
        group = _group("no because why does Jeonghan look this pretty just standing there 😭😭😭")
        neutral = GroupCopy("title", "general", {"1": "نه چون چرا جئونگان اینقدر زیبا به نظر میرسه 😭😭😭"})
        styled = GroupCopy("title", "general", {"1": "نه آخه چرا جونگهان همینطوری وایساده انقدر خوشگله 😭😭😭"})
        self.assertFalse(self.writer._contains_historical_fact_leak(group, neutral, styled))

    def test_unrelated_member_is_still_blocked(self):
        group = _group("he looks so pretty just standing there 😭")
        neutral = GroupCopy("title", "general", {"1": "فقط وایساده ولی خیلی خوشگله 😭"})
        styled = GroupCopy("title", "general", {"1": "جاشوآ فقط وایساده ولی خیلی خوشگله 😭"})
        self.assertTrue(self.writer._contains_historical_fact_leak(group, neutral, styled))

    def test_source_authorization_does_not_come_from_bad_neutral(self):
        group = _group("he looks so pretty just standing there 😭")
        neutral = GroupCopy("title", "general", {"1": "جونگهان خیلی خوشگله 😭"})
        styled = GroupCopy("title", "general", {"1": "جونگهان خیلی خوشگله 😭"})
        self.assertTrue(self.writer._contains_historical_fact_leak(group, neutral, styled))


if __name__ == "__main__":
    unittest.main()
