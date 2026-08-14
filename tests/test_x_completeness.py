from __future__ import annotations

import asyncio
import unittest
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from app.models import Update
from app.x_completeness import CompleteWindowXCollector, XCompletenessError
from app.x_client import XCollectionError


class _FakeAPI:
    def __init__(self, tweets, *, fail_profile=False):
        self.tweets = list(tweets)
        self.fail_profile = fail_profile
        self.timeline_calls = []

    async def user_by_login(self, handle):
        if self.fail_profile:
            raise RuntimeError("profile unavailable")
        return SimpleNamespace(id=123)

    def user_tweets_and_replies(self, user_id, limit):
        self.timeline_calls.append(("replies", user_id, limit))
        return self._generator(limit)

    def user_tweets(self, user_id, limit):
        self.timeline_calls.append(("tweets", user_id, limit))
        return self._generator(limit)

    async def _generator(self, limit):
        for tweet in self.tweets[:limit]:
            yield tweet


class _Collector(CompleteWindowXCollector):
    def __init__(self, api):
        super().__init__(
            {},
            [{"handle": "source", "enabled": True, "include_replies": True}],
            [],
        )
        self.fake_api = api

    async def _get_api(self):
        return self.fake_api

    def _convert_tweet(self, tweet, *, raw_query):
        return tweet.update


def _tweet(id_: int, created_at: datetime, *, retweet=False):
    update = Update(
        id=str(id_),
        url=f"https://x.com/source/status/{id_}",
        author="source",
        author_name="Source",
        text="JEONGHAN update",
        created_at=created_at,
    )
    return SimpleNamespace(update=update, retweetedTweet=object() if retweet else None)


class XCompletenessTests(unittest.TestCase):
    def setUp(self):
        self.end = datetime(2026, 8, 9, 12, 0, tzinfo=timezone.utc)
        self.start = self.end - timedelta(hours=24)

    def test_natural_generator_exhaustion_proves_complete_window(self):
        api = _FakeAPI([
            _tweet(3, self.end - timedelta(hours=1)),
            _tweet(2, self.end - timedelta(hours=2)),
        ])
        collector = _Collector(api)
        result = asyncio.run(
            collector._collect_source_timeline(
                "source", self.start, self.end, limit=10, include_replies=True
            )
        )
        self.assertEqual([item.id for item in result], ["3", "2"])

    def test_crossing_lower_time_boundary_proves_complete_window(self):
        api = _FakeAPI([
            _tweet(3, self.end - timedelta(hours=1)),
            _tweet(2, self.end - timedelta(hours=23)),
            _tweet(1, self.end - timedelta(hours=25)),
        ])
        collector = _Collector(api)
        result = asyncio.run(
            collector._collect_source_timeline(
                "source", self.start, self.end, limit=3, include_replies=True
            )
        )
        self.assertEqual([item.id for item in result], ["3", "2"])

    def test_hitting_limit_before_boundary_fails_instead_of_claiming_complete(self):
        api = _FakeAPI([
            _tweet(3, self.end - timedelta(hours=1)),
            _tweet(2, self.end - timedelta(hours=2)),
            _tweet(1, self.end - timedelta(hours=3)),
        ])
        collector = _Collector(api)
        with self.assertRaises(XCompletenessError):
            asyncio.run(
                collector._collect_source_timeline(
                    "source", self.start, self.end, limit=3, include_replies=True
                )
            )

    def test_retweets_count_toward_cap_even_when_excluded(self):
        api = _FakeAPI([
            _tweet(3, self.end - timedelta(hours=1), retweet=True),
            _tweet(2, self.end - timedelta(hours=2), retweet=True),
            _tweet(1, self.end - timedelta(hours=3)),
        ])
        collector = _Collector(api)
        with self.assertRaises(XCompletenessError):
            asyncio.run(
                collector._collect_source_timeline(
                    "source", self.start, self.end, limit=3, include_replies=True
                )
            )

    def test_complete_source_mode_does_not_silently_fallback_to_search(self):
        api = _FakeAPI([], fail_profile=True)
        collector = _Collector(api)
        with self.assertRaises(XCompletenessError) as raised:
            asyncio.run(collector.collect_source("source", self.start, self.end))
        self.assertIn("search-only fallback was not used", str(raised.exception))

    def test_invalid_handle_still_fails_as_collection_error(self):
        collector = _Collector(_FakeAPI([]))
        with self.assertRaises(XCollectionError):
            asyncio.run(collector.collect_source("bad handle!", self.start, self.end))


if __name__ == "__main__":
    unittest.main()
