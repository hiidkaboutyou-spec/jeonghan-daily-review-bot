from __future__ import annotations

import asyncio
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from app import phase3_recovery as phase3
from app.models import Update
from app.state import StateStore
from app.x_client import XCollectionError, XCollector


class _ScopedIdentityAPI:
    def __init__(self, *, scoped_username: str = "source"):
        self.profile_calls = 0
        self.scoped_username = scoped_username
        self.search_queries: list[str] = []

    async def user_by_login(self, handle: str):
        self.profile_calls += 1
        return None

    async def search(self, query: str, *, limit: int):
        self.search_queries.append(query)
        yield SimpleNamespace(user=SimpleNamespace(id=4242, username=self.scoped_username))

    async def user_tweets_and_replies_raw(self, user_id, **kwargs):
        if False:
            yield None

    async def user_tweets_raw(self, user_id, **kwargs):
        if False:
            yield None


class _Collector(XCollector):
    def __init__(self, api):
        super().__init__(
            {},
            [{"handle": "source", "enabled": True, "include_replies": True}],
            [],
        )
        self.api = api

    async def _get_api(self):
        return self.api

    def _convert_tweet(self, tweet, *, raw_query):
        return tweet.update


def _update(identifier: str, when: datetime) -> Update:
    return Update(
        id=identifier,
        url=f"https://x.com/source/status/{identifier}",
        author="source",
        author_name="source",
        text=identifier,
        created_at=when,
    )


class Phase3RecoveryHardeningTests(unittest.TestCase):
    def setUp(self):
        self.end = datetime(2026, 8, 14, 18, 0, tzinfo=timezone.utc)
        self.start = self.end - timedelta(hours=4)

    def test_unresolved_checkpoint_does_not_expire_by_age(self):
        checkpoint = phase3._new_checkpoint(
            "source", self.start, self.end, include_replies=True
        )
        checkpoint["next_cursor"] = "opaque-provider-cursor"
        checkpoint["pages_completed"] = 2
        checkpoint["updated_at"] = (datetime.now(timezone.utc) - timedelta(days=30)).isoformat()
        checkpoint["updates"] = [_update("1", self.end - timedelta(hours=1)).to_dict()]

        clean = phase3._sanitize_checkpoint(checkpoint)
        self.assertIsNotNone(clean)
        self.assertEqual(clean["next_cursor"], "opaque-provider-cursor")
        self.assertEqual(clean["pages_completed"], 2)

        with tempfile.TemporaryDirectory() as temp:
            state = StateStore(Path(temp) / "state.json")
            state.save_x_retrieval_checkpoint(checkpoint)
            restarted = StateStore(Path(temp) / "state.json")
            restored = restarted.get_x_retrieval_checkpoint(
                source="source",
                start=self.start,
                end=self.end,
                include_replies=True,
                allow_older=True,
            )
        self.assertIsNotNone(restored)
        self.assertEqual(restored["next_cursor"], "opaque-provider-cursor")

    def test_profile_parse_failure_recovers_user_id_only_from_exact_scoped_author(self):
        api = _ScopedIdentityAPI(scoped_username="source")
        collector = _Collector(api)
        provider = AsyncMock(
            return_value=phase3._ProviderPage(
                tweets=[
                    SimpleNamespace(
                        update=_update("keep", self.end - timedelta(hours=1)),
                        retweetedTweet=None,
                    ),
                    SimpleNamespace(
                        update=_update("old", self.end - timedelta(hours=5)),
                        retweetedTweet=None,
                    ),
                ],
                next_cursor=None,
                exhausted=True,
            )
        )
        with patch("app.phase3_recovery._provider_page", new=provider), patch(
            "app.phase3_recovery._sleep_for_retry", new=AsyncMock()
        ):
            result = asyncio.run(
                phase3._resumable_source_timeline(
                    collector,
                    "source",
                    self.start,
                    self.end,
                    limit=200,
                    include_replies=True,
                )
            )

        self.assertEqual(api.profile_calls, phase3.MAX_SOURCE_RETRIES + 1)
        self.assertEqual(api.search_queries, ["from:source -filter:retweets"])
        self.assertEqual(provider.await_args.args[1], 4242)
        self.assertEqual([item.id for item in result], ["keep"])

    def test_scoped_identity_recovery_rejects_external_author_identity(self):
        api = _ScopedIdentityAPI(scoped_username="external")
        collector = _Collector(api)
        provider = AsyncMock()
        with patch("app.phase3_recovery._provider_page", new=provider), patch(
            "app.phase3_recovery._sleep_for_retry", new=AsyncMock()
        ):
            with self.assertRaises(XCollectionError):
                asyncio.run(
                    phase3._resumable_source_timeline(
                        collector,
                        "source",
                        self.start,
                        self.end,
                        limit=200,
                        include_replies=True,
                    )
                )
        provider.assert_not_awaited()
        self.assertEqual(api.search_queries, ["from:source -filter:retweets"])


if __name__ == "__main__":
    unittest.main()
