from __future__ import annotations

import asyncio
import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from app.config import ROOT
from app.models import Update
from app.phase3_recovery import (
    MAX_SOURCE_RETRIES,
    _ProviderPage,
    _checkpoint_id,
    _resumable_source_timeline,
)
from app.state import StateStore
from app.webhook_aware_assistant import WebhookAwarePersonalAssistant
from app.x_client import XCollectionError, XCollector
from app.x_completeness import XCompletenessError
from app.x_syndication import SyndicationError


class _FakeAPI:
    def __init__(self, profile_results=None):
        self.profile_results = list(profile_results or [SimpleNamespace(id=1)])
        self.profile_calls = 0

    async def user_by_login(self, handle):
        self.profile_calls += 1
        if self.profile_results:
            value = self.profile_results.pop(0)
        else:
            value = SimpleNamespace(id=1)
        if isinstance(value, BaseException):
            raise value
        return value

    async def user_tweets_and_replies_raw(self, user_id, **kwargs):
        if False:
            yield None

    async def user_tweets_raw(self, user_id, **kwargs):
        if False:
            yield None


class _Collector(XCollector):
    def __init__(self, api, sources=None, recovery=None):
        super().__init__(
            {},
            sources or [{"handle": "source", "enabled": True, "include_replies": True}],
            [],
        )
        self.fake_api = api
        self.recovery = list(recovery or [])

    async def _get_api(self):
        return self.fake_api

    def _convert_tweet(self, tweet, *, raw_query):
        return tweet.update

    async def _run_queries(self, *args, **kwargs):
        return list(self.recovery)


def _update(identifier: str, when: datetime, *, author: str = "source") -> Update:
    return Update(
        id=str(identifier),
        url=f"https://x.com/{author}/status/{identifier}",
        author=author,
        author_name=author,
        text=f"update {identifier}",
        created_at=when,
    )


def _tweet(identifier: str, when: datetime, *, author: str = "source", retweet: bool = False):
    return SimpleNamespace(
        update=_update(identifier, when, author=author),
        retweetedTweet=object() if retweet else None,
    )


class Phase3RecoveryTests(unittest.TestCase):
    def setUp(self):
        self.end = datetime(2026, 8, 14, 18, 0, tzinfo=timezone.utc)
        self.start = self.end - timedelta(hours=4)
        self.syndication = patch(
            "app.phase3_recovery.collect_syndication_timeline",
            side_effect=SyndicationError("offline in unit test"),
        )
        self.syndication.start()
        self.addCleanup(self.syndication.stop)

    def test_all_provider_paths_failed_returns_authorized_public_fallback(self):
        recovered = _update("fallback", self.end - timedelta(hours=1))
        self.syndication.stop()
        with tempfile.TemporaryDirectory() as temp, patch(
            "app.phase3_recovery.collect_syndication_timeline",
            return_value=SimpleNamespace(updates=[recovered], raw_seen=1),
        ), patch(
            "app.phase3_recovery._provider_page",
            new=AsyncMock(side_effect=[TimeoutError("down")] * (MAX_SOURCE_RETRIES + 1)),
        ), patch("app.phase3_recovery._sleep_for_retry", new=AsyncMock()), patch(
            "app.source_authority_hardening.asyncio.sleep", new=AsyncMock()
        ):
            collector = self._collector_with_state(temp)
            result = asyncio.run(collector.collect_window(self.start, self.end, max_per_query=20))

        self.assertEqual([item.id for item in result], ["fallback"])
        self.assertTrue(collector.last_errors)
        self.assertEqual(collector._phase3_state.data["last_auto_run"], "")

    def _collector_with_state(self, temp: str, *, api=None, sources=None, recovery=None):
        collector = _Collector(api or _FakeAPI(), sources=sources, recovery=recovery)
        collector._phase3_state = StateStore(Path(temp) / "state.json")
        return collector

    def test_high_volume_multi_page_success(self):
        pages = [
            _ProviderPage([_tweet("3", self.end - timedelta(hours=1))], "c1", False),
            _ProviderPage([_tweet("2", self.end - timedelta(hours=2))], "c2", False),
            _ProviderPage(
                [
                    _tweet("1", self.end - timedelta(hours=3)),
                    _tweet("0", self.end - timedelta(hours=5)),
                ],
                "c3",
                False,
            ),
        ]
        with tempfile.TemporaryDirectory() as temp, patch(
            "app.phase3_recovery._provider_page", new=AsyncMock(side_effect=pages)
        ):
            collector = self._collector_with_state(temp)
            result = asyncio.run(
                _resumable_source_timeline(
                    collector,
                    "source",
                    self.start,
                    self.end,
                    limit=200,
                    include_replies=True,
                )
            )
        self.assertEqual([item.id for item in result], ["1", "2", "3"])

    def test_failure_on_middle_page_keeps_partial_checkpoint(self):
        page1 = _ProviderPage([_tweet("3", self.end - timedelta(hours=1))], "c1", False)
        failures = [TimeoutError("timeout")] * (MAX_SOURCE_RETRIES + 1)
        with tempfile.TemporaryDirectory() as temp, patch(
            "app.phase3_recovery._provider_page",
            new=AsyncMock(side_effect=[page1, *failures]),
        ), patch("app.phase3_recovery._sleep_for_retry", new=AsyncMock()):
            collector = self._collector_with_state(temp)
            with self.assertRaises(XCollectionError):
                asyncio.run(
                    _resumable_source_timeline(
                        collector,
                        "source",
                        self.start,
                        self.end,
                        limit=200,
                        include_replies=True,
                    )
                )
            checkpoints = collector._phase3_state.data["x_retrieval_checkpoints"]
            self.assertEqual(len(checkpoints), 1)
            checkpoint = next(iter(checkpoints.values()))
            self.assertEqual(checkpoint["next_cursor"], "c1")
            self.assertEqual(checkpoint["pages_completed"], 1)
            self.assertEqual([row["id"] for row in checkpoint["updates"]], ["3"])

    def test_successful_pages_are_returned_from_partial_collect_window(self):
        page1 = _ProviderPage([_tweet("3", self.end - timedelta(hours=1))], "c1", False)
        failures = [TimeoutError("timeout")] * (MAX_SOURCE_RETRIES + 1)
        with tempfile.TemporaryDirectory() as temp, patch(
            "app.phase3_recovery._provider_page",
            new=AsyncMock(side_effect=[page1, *failures]),
        ), patch("app.phase3_recovery._sleep_for_retry", new=AsyncMock()), patch(
            "app.source_authority_hardening.asyncio.sleep", new=AsyncMock()
        ):
            collector = self._collector_with_state(temp)
            result = asyncio.run(
                collector.collect_window(self.start, self.end, max_per_query=20)
            )
            self.assertEqual([item.id for item in result], ["3"])
            self.assertTrue(collector.last_errors)

    def test_retry_resumes_from_safe_provider_cursor(self):
        page1 = _ProviderPage([_tweet("3", self.end - timedelta(hours=1))], "c1", False)
        failures = [TimeoutError("timeout")] * (MAX_SOURCE_RETRIES + 1)
        with tempfile.TemporaryDirectory() as temp, patch(
            "app.phase3_recovery._provider_page",
            new=AsyncMock(side_effect=[page1, *failures]),
        ), patch("app.phase3_recovery._sleep_for_retry", new=AsyncMock()):
            collector = self._collector_with_state(temp)
            with self.assertRaises(XCollectionError):
                asyncio.run(
                    _resumable_source_timeline(
                        collector, "source", self.start, self.end, limit=200, include_replies=True
                    )
                )

            resumed_page = _ProviderPage(
                [
                    _tweet("2", self.end - timedelta(hours=2)),
                    _tweet("0", self.end - timedelta(hours=5)),
                ],
                "c2",
                False,
            )
            provider = AsyncMock(return_value=resumed_page)
            with patch("app.phase3_recovery._provider_page", new=provider):
                result = asyncio.run(
                    _resumable_source_timeline(
                        collector, "source", self.start, self.end, limit=200, include_replies=True
                    )
                )
            self.assertEqual(provider.await_args.kwargs["cursor"], "c1")
            self.assertEqual([item.id for item in result], ["2", "3"])

    def test_retry_dedupes_overlap_by_stable_post_id(self):
        page1 = _ProviderPage([_tweet("3", self.end - timedelta(hours=1))], "c1", False)
        failures = [RuntimeError("temporary")] * (MAX_SOURCE_RETRIES + 1)
        with tempfile.TemporaryDirectory() as temp, patch(
            "app.phase3_recovery._provider_page",
            new=AsyncMock(side_effect=[page1, *failures]),
        ), patch("app.phase3_recovery._sleep_for_retry", new=AsyncMock()):
            collector = self._collector_with_state(temp)
            with self.assertRaises(XCollectionError):
                asyncio.run(
                    _resumable_source_timeline(
                        collector, "source", self.start, self.end, limit=200, include_replies=True
                    )
                )
            overlap = _ProviderPage(
                [
                    _tweet("3", self.end - timedelta(hours=1)),
                    _tweet("2", self.end - timedelta(hours=2)),
                    _tweet("0", self.end - timedelta(hours=5)),
                ],
                "c2",
                False,
            )
            with patch("app.phase3_recovery._provider_page", new=AsyncMock(return_value=overlap)):
                result = asyncio.run(
                    _resumable_source_timeline(
                        collector, "source", self.start, self.end, limit=200, include_replies=True
                    )
                )
        self.assertEqual([item.id for item in result], ["2", "3"])

    def test_repeated_failure_remains_partial_and_bounded(self):
        provider = AsyncMock(side_effect=[TimeoutError("down")] * (MAX_SOURCE_RETRIES + 1))
        sleeper = AsyncMock()
        with tempfile.TemporaryDirectory() as temp, patch(
            "app.phase3_recovery._provider_page", new=provider
        ), patch("app.phase3_recovery._sleep_for_retry", new=sleeper):
            collector = self._collector_with_state(temp)
            with self.assertRaises(XCollectionError):
                asyncio.run(
                    _resumable_source_timeline(
                        collector, "source", self.start, self.end, limit=200, include_replies=True
                    )
                )
            self.assertEqual(provider.await_count, MAX_SOURCE_RETRIES + 1)
            self.assertEqual(sleeper.await_count, MAX_SOURCE_RETRIES)
            self.assertTrue(collector._phase3_partial_updates.get("source") == [])

    def test_partial_scheduled_retrieval_retains_success_cursor(self):
        now = datetime.now(timezone.utc)
        old = now - timedelta(hours=2)
        page1 = _ProviderPage([_tweet("p1", now - timedelta(hours=1))], "c1", False)
        failures = [TimeoutError("timeout")] * (MAX_SOURCE_RETRIES + 1)
        with tempfile.TemporaryDirectory() as temp, patch(
            "app.phase3_recovery._provider_page",
            new=AsyncMock(side_effect=[page1, *failures]),
        ), patch("app.phase3_recovery._sleep_for_retry", new=AsyncMock()):
            app = WebhookAwarePersonalAssistant.__new__(WebhookAwarePersonalAssistant)
            app.state = StateStore(Path(temp) / "state.json")
            app.state.data["last_auto_run"] = old.isoformat()
            app.state.data["last_auto_attempt"] = ""
            app.settings = SimpleNamespace(
                runtime={"scheduled_lookback_hours": 24, "scheduled_min_interval_minutes": 1}
            )
            app.collector = _Collector(_FakeAPI())
            app.collector._phase3_state = app.state
            app._record_x_scan_failure = Mock()
            asyncio.run(app.run_scheduled_scan())
            self.assertEqual(app.state.data["last_auto_run"], old.isoformat())
            self.assertTrue(app.collector.last_errors)

    def test_successful_recovery_eventually_allows_cursor_advance(self):
        now = datetime.now(timezone.utc)
        old = now - timedelta(hours=2)
        page1 = _ProviderPage([_tweet("p1", now - timedelta(hours=1))], "c1", False)
        failures = [TimeoutError("timeout")] * (MAX_SOURCE_RETRIES + 1)
        with tempfile.TemporaryDirectory() as temp, patch(
            "app.phase3_recovery._provider_page",
            new=AsyncMock(side_effect=[page1, *failures]),
        ), patch("app.phase3_recovery._sleep_for_retry", new=AsyncMock()):
            app = WebhookAwarePersonalAssistant.__new__(WebhookAwarePersonalAssistant)
            app.state = StateStore(Path(temp) / "state.json")
            app.state.data["last_auto_run"] = old.isoformat()
            app.state.data["last_auto_attempt"] = ""
            app.settings = SimpleNamespace(
                runtime={"scheduled_lookback_hours": 24, "scheduled_min_interval_minutes": 1}
            )
            app.collector = _Collector(_FakeAPI())
            app.collector._phase3_state = app.state
            app._record_x_scan_failure = Mock()
            asyncio.run(app.run_scheduled_scan())
            self.assertEqual(app.state.data["last_auto_run"], old.isoformat())

            app.state.data["last_auto_attempt"] = ""
            resumed = _ProviderPage(
                [_tweet("p0", now - timedelta(hours=3))],
                "c2",
                False,
            )
            head = _ProviderPage([], None, True)
            with patch(
                "app.phase3_recovery._provider_page",
                new=AsyncMock(side_effect=[resumed, head]),
            ):
                asyncio.run(app.run_scheduled_scan())
            self.assertNotEqual(app.state.data["last_auto_run"], old.isoformat())
            self.assertFalse(app.collector.last_errors)

    def test_fallback_and_retry_remain_configured_author_scoped(self):
        recovered = [
            _update("safe", self.end - timedelta(hours=1), author="source"),
            _update("leak", self.end - timedelta(hours=1), author="external"),
        ]
        failures = [TimeoutError("down")] * (MAX_SOURCE_RETRIES + 1)
        with tempfile.TemporaryDirectory() as temp, patch(
            "app.phase3_recovery._provider_page", new=AsyncMock(side_effect=failures)
        ), patch("app.phase3_recovery._sleep_for_retry", new=AsyncMock()):
            collector = self._collector_with_state(temp, recovery=recovered)
            result = asyncio.run(collector.collect_window(self.start, self.end, max_per_query=20))
            self.assertEqual([item.id for item in result], ["safe"])
            self.assertTrue(collector.last_errors)

    def test_external_author_inside_provider_page_is_blocked(self):
        page = _ProviderPage(
            [
                _tweet("leak", self.end - timedelta(hours=1), author="external"),
                _tweet("old", self.end - timedelta(hours=5), author="source"),
            ],
            "c1",
            False,
        )
        with tempfile.TemporaryDirectory() as temp, patch(
            "app.phase3_recovery._provider_page", new=AsyncMock(return_value=page)
        ):
            collector = self._collector_with_state(temp)
            result = asyncio.run(
                _resumable_source_timeline(
                    collector, "source", self.start, self.end, limit=200, include_replies=True
                )
            )
        self.assertEqual(result, [])

    def test_one_failing_source_does_not_block_healthy_source(self):
        sources = [
            {"handle": "bad", "enabled": True, "include_replies": True},
            {"handle": "good", "enabled": True, "include_replies": True},
        ]
        api = _FakeAPI(
            profile_results=[SimpleNamespace(id=1), SimpleNamespace(id=2)]
        )
        good_update = _update("good1", self.end - timedelta(hours=1), author="good")

        async def page_for_user(api_obj, user_id, **kwargs):
            if user_id == 1:
                raise TimeoutError("bad source")
            return _ProviderPage(
                [
                    SimpleNamespace(update=good_update, retweetedTweet=None),
                    SimpleNamespace(
                        update=_update("old", self.end - timedelta(hours=5), author="good"),
                        retweetedTweet=None,
                    ),
                ],
                None,
                True,
            )

        with tempfile.TemporaryDirectory() as temp, patch(
            "app.phase3_recovery._provider_page", new=AsyncMock(side_effect=page_for_user)
        ), patch("app.phase3_recovery._sleep_for_retry", new=AsyncMock()), patch(
            "app.source_authority_hardening.asyncio.sleep", new=AsyncMock()
        ):
            collector = self._collector_with_state(temp, api=api, sources=sources)
            result = asyncio.run(collector.collect_window(self.start, self.end, max_per_query=20))
        self.assertEqual([item.id for item in result], ["good1"])
        self.assertTrue(any("@bad" in err for err in collector.last_errors))

    def test_provider_timeout_retries_then_recovers(self):
        page = _ProviderPage(
            [
                _tweet("1", self.end - timedelta(hours=1)),
                _tweet("0", self.end - timedelta(hours=5)),
            ],
            None,
            True,
        )
        provider = AsyncMock(side_effect=[TimeoutError("one"), TimeoutError("two"), page])
        sleeper = AsyncMock()
        with tempfile.TemporaryDirectory() as temp, patch(
            "app.phase3_recovery._provider_page", new=provider
        ), patch("app.phase3_recovery._sleep_for_retry", new=sleeper):
            collector = self._collector_with_state(temp)
            result = asyncio.run(
                _resumable_source_timeline(
                    collector, "source", self.start, self.end, limit=200, include_replies=True
                )
            )
        self.assertEqual([item.id for item in result], ["1"])
        self.assertEqual(provider.await_count, 3)
        self.assertEqual(sleeper.await_count, 2)

    def test_rate_limit_like_failure_has_bounded_backoff(self):
        provider = AsyncMock(side_effect=[RuntimeError("rate")] * (MAX_SOURCE_RETRIES + 1))
        sleeper = AsyncMock()
        with tempfile.TemporaryDirectory() as temp, patch(
            "app.phase3_recovery._provider_page", new=provider
        ), patch("app.phase3_recovery._sleep_for_retry", new=sleeper):
            collector = self._collector_with_state(temp)
            with self.assertRaises(XCollectionError):
                asyncio.run(
                    _resumable_source_timeline(
                        collector, "source", self.start, self.end, limit=200, include_replies=True
                    )
                )
        self.assertEqual(provider.await_count, MAX_SOURCE_RETRIES + 1)
        self.assertEqual(sleeper.await_count, MAX_SOURCE_RETRIES)

    def test_profile_none_failure_is_retried_before_page_one(self):
        api = _FakeAPI(profile_results=[None, None, None])
        sleeper = AsyncMock()
        with tempfile.TemporaryDirectory() as temp, patch(
            "app.phase3_recovery._sleep_for_retry", new=sleeper
        ):
            collector = self._collector_with_state(temp, api=api)
            with self.assertRaises(XCollectionError):
                asyncio.run(
                    _resumable_source_timeline(
                        collector, "source", self.start, self.end, limit=200, include_replies=True
                    )
                )
        self.assertEqual(api.profile_calls, MAX_SOURCE_RETRIES + 1)
        self.assertEqual(sleeper.await_count, MAX_SOURCE_RETRIES)

    def test_malformed_checkpoint_is_discarded_conservatively(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "state.json"
            path.write_text(
                json.dumps(
                    {
                        "schema": 6,
                        "x_retrieval_checkpoints": {
                            "bad": {
                                "version": 1,
                                "checkpoint_id": "wrong",
                                "source": "source",
                                "window_start": self.start.isoformat(),
                                "segment_start": self.start.isoformat(),
                                "segment_end": self.end.isoformat(),
                                "updated_at": self.end.isoformat(),
                                "next_cursor": {"not": "a string"},
                                "updates": [],
                            }
                        },
                    }
                ),
                encoding="utf-8",
            )
            state = StateStore(path)
            self.assertEqual(state.data["x_retrieval_checkpoints"], {})

    def test_restart_between_partial_attempt_and_retry_restores_checkpoint(self):
        page1 = _ProviderPage([_tweet("3", self.end - timedelta(hours=1))], "c1", False)
        failures = [TimeoutError("down")] * (MAX_SOURCE_RETRIES + 1)
        with tempfile.TemporaryDirectory() as temp, patch(
            "app.phase3_recovery._provider_page",
            new=AsyncMock(side_effect=[page1, *failures]),
        ), patch("app.phase3_recovery._sleep_for_retry", new=AsyncMock()):
            first = self._collector_with_state(temp)
            with self.assertRaises(XCollectionError):
                asyncio.run(
                    _resumable_source_timeline(
                        first, "source", self.start, self.end, limit=200, include_replies=True
                    )
                )

            second = self._collector_with_state(temp)
            resumed_page = _ProviderPage(
                [
                    _tweet("2", self.end - timedelta(hours=2)),
                    _tweet("0", self.end - timedelta(hours=5)),
                ],
                None,
                True,
            )
            provider = AsyncMock(return_value=resumed_page)
            with patch("app.phase3_recovery._provider_page", new=provider):
                result = asyncio.run(
                    _resumable_source_timeline(
                        second, "source", self.start, self.end, limit=200, include_replies=True
                    )
                )
        self.assertEqual(provider.await_args.kwargs["cursor"], "c1")
        self.assertEqual([item.id for item in result], ["2", "3"])

    def test_oldest_to_newest_ordering_survives_resume(self):
        pages = [
            _ProviderPage(
                [
                    _tweet("9", self.end - timedelta(minutes=30)),
                    _tweet("7", self.end - timedelta(hours=1)),
                ],
                "c1",
                False,
            ),
            _ProviderPage(
                [
                    _tweet("8", self.end - timedelta(hours=2)),
                    _tweet("0", self.end - timedelta(hours=5)),
                ],
                None,
                True,
            ),
        ]
        with tempfile.TemporaryDirectory() as temp, patch(
            "app.phase3_recovery._provider_page", new=AsyncMock(side_effect=pages)
        ):
            collector = self._collector_with_state(temp)
            result = asyncio.run(
                _resumable_source_timeline(
                    collector, "source", self.start, self.end, limit=200, include_replies=True
                )
            )
        self.assertEqual([item.id for item in result], ["8", "7", "9"])

    def test_duplicate_provider_cursor_fails_partial_not_loop(self):
        page = _ProviderPage([_tweet("3", self.end - timedelta(hours=1))], "same", False)
        with tempfile.TemporaryDirectory() as temp, patch(
            "app.phase3_recovery._provider_page",
            new=AsyncMock(side_effect=[page, page]),
        ):
            collector = self._collector_with_state(temp)
            with self.assertRaises(XCompletenessError):
                asyncio.run(
                    _resumable_source_timeline(
                        collector, "source", self.start, self.end, limit=200, include_replies=True
                    )
                )
            checkpoint = next(iter(collector._phase3_state.data["x_retrieval_checkpoints"].values()))
            self.assertEqual(checkpoint["next_cursor"], "same")

    def test_per_run_volume_budget_checkpoints_instead_of_claiming_complete(self):
        page = _ProviderPage(
            [
                _tweet("3", self.end - timedelta(hours=1)),
                _tweet("2", self.end - timedelta(hours=2)),
            ],
            "c1",
            False,
        )
        with tempfile.TemporaryDirectory() as temp, patch(
            "app.phase3_recovery._provider_page", new=AsyncMock(return_value=page)
        ):
            collector = self._collector_with_state(temp)
            with self.assertRaises(XCompletenessError):
                asyncio.run(
                    _resumable_source_timeline(
                        collector, "source", self.start, self.end, limit=2, include_replies=True
                    )
                )
            checkpoint = next(iter(collector._phase3_state.data["x_retrieval_checkpoints"].values()))
            self.assertEqual(checkpoint["next_cursor"], "c1")
            self.assertEqual([row["id"] for row in checkpoint["updates"]], ["2", "3"])

    def test_source_expansion_config_still_has_24_unique_sources(self):
        data = json.loads((ROOT / "config" / "sources.json").read_text(encoding="utf-8"))
        sources = data["sources"] if isinstance(data, dict) else data
        handles = [str(item["handle"]).casefold() for item in sources]
        self.assertEqual(len(handles), 24)
        self.assertEqual(len(handles), len(set(handles)))
        self.assertEqual(sum(bool(item.get("enabled", True)) for item in sources), 23)
        self.assertIn("honeyya_hanihae", handles)
        self.assertIn("yoon_1004_hani", handles)

    def test_retweets_remain_excluded_during_resumable_pages(self):
        page = _ProviderPage(
            [
                _tweet("rt", self.end - timedelta(hours=1), retweet=True),
                _tweet("keep", self.end - timedelta(hours=2)),
                _tweet("old", self.end - timedelta(hours=5)),
            ],
            None,
            True,
        )
        with tempfile.TemporaryDirectory() as temp, patch(
            "app.phase3_recovery._provider_page", new=AsyncMock(return_value=page)
        ):
            collector = self._collector_with_state(temp)
            result = asyncio.run(
                _resumable_source_timeline(
                    collector, "source", self.start, self.end, limit=200, include_replies=True
                )
            )
        self.assertEqual([item.id for item in result], ["keep"])

    def test_checkpoint_identity_does_not_include_provider_cursor_or_private_content(self):
        checkpoint_id = _checkpoint_id("source", self.start, True)
        self.assertEqual(len(checkpoint_id), 20)
        self.assertNotIn("source", checkpoint_id)
        self.assertNotIn("cursor", checkpoint_id)


if __name__ == "__main__":
    unittest.main()
