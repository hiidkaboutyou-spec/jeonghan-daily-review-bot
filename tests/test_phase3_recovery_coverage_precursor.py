from __future__ import annotations

import asyncio
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from app import phase3_recovery as phase3
from app import phase3_recovery_hardening as integrity
from app.models import Update
from app.state import StateStore
from app.x_client import XCollectionError, XCollector


def _update(identifier: str, when: datetime, *, author: str = "source") -> Update:
    return Update(
        id=identifier,
        url=f"https://x.com/{author}/status/{identifier}",
        author=author,
        author_name=author,
        text=identifier,
        created_at=when,
    )


class _RawAPI:
    def __init__(self, responses=(), *, cursor=None):
        self.responses = list(responses)
        self.cursor = cursor
        self.calls: list[tuple[int, dict]] = []

    async def user_tweets_and_replies_raw(self, user_id, **kwargs):
        self.calls.append((user_id, kwargs))
        for response in self.responses:
            yield response

    async def user_tweets_raw(self, user_id, **kwargs):
        self.calls.append((user_id, kwargs))
        for response in self.responses:
            yield response

    def _get_cursor(self, _payload, _kind):
        return self.cursor


class _Response:
    def __init__(self, payload):
        self.payload = payload

    def json(self):
        return self.payload


class _MinimalCollector(XCollector):
    def __init__(self, api=None):
        super().__init__(
            {},
            [{"handle": "source", "enabled": True, "include_replies": True}],
            [],
        )
        self.api = api

    async def _get_api(self):
        return self.api


class RecoveryCoveragePrecursorTests(unittest.TestCase):
    def setUp(self):
        self.end = datetime(2026, 9, 18, 10, 0, tzinfo=timezone.utc)
        self.start = self.end - timedelta(hours=4)

    def _checkpoint(
        self,
        *,
        source: str = "source",
        start: datetime | None = None,
        end: datetime | None = None,
        include_replies: bool = True,
    ):
        return phase3._new_checkpoint(
            source,
            start or self.start,
            end or self.end,
            include_replies=include_replies,
        )

    def test_hardened_checkpoint_sanitizer_rejects_corruption_and_clamps_counters(self):
        self.assertIsNone(phase3._sanitize_checkpoint(None))
        self.assertIsNone(phase3._sanitize_checkpoint({"version": 1}))

        bad_number = self._checkpoint()
        bad_number["pages_completed"] = "not-an-int"
        self.assertIsNone(phase3._sanitize_checkpoint(bad_number))

        wrong_version = self._checkpoint()
        wrong_version["version"] = 999
        self.assertIsNone(phase3._sanitize_checkpoint(wrong_version))

        wrong_id = self._checkpoint()
        wrong_id["checkpoint_id"] = "wrong"
        self.assertIsNone(phase3._sanitize_checkpoint(wrong_id))

        bad_range = self._checkpoint()
        bad_range["segment_start"] = bad_range["segment_end"]
        self.assertIsNone(phase3._sanitize_checkpoint(bad_range))

        bad_cursor = self._checkpoint()
        bad_cursor["next_cursor"] = {"cursor": "bad"}
        self.assertIsNone(phase3._sanitize_checkpoint(bad_cursor))

        huge_cursor = self._checkpoint()
        huge_cursor["next_cursor"] = "x" * 4097
        self.assertIsNone(phase3._sanitize_checkpoint(huge_cursor))

        bad_updates = self._checkpoint()
        bad_updates["updates"] = "not-a-list"
        self.assertIsNone(phase3._sanitize_checkpoint(bad_updates))

        non_dict_update = self._checkpoint()
        non_dict_update["updates"] = ["bad"]
        self.assertIsNone(phase3._sanitize_checkpoint(non_dict_update))

        wrong_author = self._checkpoint()
        wrong_author["updates"] = [
            _update("1", self.end - timedelta(hours=1), author="external").to_dict()
        ]
        self.assertIsNone(phase3._sanitize_checkpoint(wrong_author))

        clamped = self._checkpoint()
        clamped["pages_completed"] = -4
        clamped["raw_seen"] = -3
        clamped["retry_count"] = 5000
        clamped["next_cursor"] = ""
        clean = phase3._sanitize_checkpoint(clamped)
        self.assertIsNotNone(clean)
        self.assertEqual(clean["pages_completed"], 0)
        self.assertEqual(clean["raw_seen"], 0)
        self.assertEqual(clean["retry_count"], 1000)
        self.assertIsNone(clean["next_cursor"])

    def test_state_normalization_prune_save_clear_and_older_checkpoint_selection(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "state.json"
            path.write_text(
                '{"schema": 6, "x_retrieval_checkpoints": "corrupt"}',
                encoding="utf-8",
            )
            state = StateStore(path)
            self.assertEqual(state.data["x_retrieval_checkpoints"], {})

            state.data["x_retrieval_checkpoints"] = "corrupt"
            state.prune()
            self.assertEqual(state.data["x_retrieval_checkpoints"], {})

            with self.assertRaises(ValueError):
                state.save_x_retrieval_checkpoint({"bad": True})

            older_start = self.start - timedelta(hours=2)
            older = self._checkpoint(start=older_start)
            older["segment_end"] = self.end.isoformat()
            exact = self._checkpoint()
            wrong_source = self._checkpoint(source="other")
            wrong_replies = self._checkpoint(include_replies=False)

            for checkpoint in (older, exact, wrong_source, wrong_replies):
                state.save_x_retrieval_checkpoint(checkpoint)

            selected = state.get_x_retrieval_checkpoint(
                source="source",
                start=self.start,
                end=self.end,
                include_replies=True,
                allow_older=True,
            )
            self.assertIsNotNone(selected)
            self.assertEqual(selected["window_start"], older_start.isoformat())

            selected_exact = state.get_x_retrieval_checkpoint(
                source="source",
                start=self.start,
                end=self.end,
                include_replies=True,
                allow_older=False,
            )
            self.assertIsNotNone(selected_exact)
            self.assertEqual(selected_exact["checkpoint_id"], exact["checkpoint_id"])

            state.data["x_retrieval_checkpoints"] = []
            self.assertIsNone(
                state.get_x_retrieval_checkpoint(
                    source="source",
                    start=self.start,
                    end=self.end,
                    include_replies=True,
                    allow_older=True,
                )
            )

            state.data["x_retrieval_checkpoints"] = {}
            state.save_x_retrieval_checkpoint(exact)
            state.clear_x_retrieval_checkpoint(exact["checkpoint_id"])
            self.assertEqual(state.data["x_retrieval_checkpoints"], {})

    def test_provider_page_rejects_missing_contract_and_marks_payload_validity(self):
        with self.assertRaises(AttributeError):
            asyncio.run(
                phase3._provider_page(
                    object(),
                    1,
                    include_replies=True,
                    cursor=None,
                )
            )

        empty_api = _RawAPI()
        empty = asyncio.run(
            phase3._provider_page(
                empty_api,
                1,
                include_replies=False,
                cursor=None,
            )
        )
        self.assertTrue(empty.exhausted)
        self.assertEqual(empty.tweets, [])
        self.assertFalse(empty.valid_response)

        payload = {
            "data": {
                "timeline": {
                    "type": "TimelineAddEntries",
                    "entries": [],
                }
            }
        }
        api = _RawAPI([_Response(payload)], cursor="next")
        with patch("twscrape.models.parse_tweets", return_value=[]):
            page = asyncio.run(
                phase3._provider_page(
                    api,
                    7,
                    include_replies=True,
                    cursor="previous",
                )
            )
        self.assertEqual(api.calls, [(7, {"limit": 1, "kv": {"cursor": "previous"}})])
        self.assertEqual(page.next_cursor, "next")
        self.assertFalse(page.exhausted)
        self.assertTrue(page.valid_response)

        no_cursor_api = _RawAPI([_Response(payload)])
        no_cursor_api._get_cursor = None
        with patch("twscrape.models.parse_tweets", return_value=[]):
            with self.assertRaises(RuntimeError):
                asyncio.run(
                    phase3._provider_page(
                        no_cursor_api,
                        1,
                        include_replies=True,
                        cursor=None,
                    )
                )

        error_api = _RawAPI([_Response({"errors": [{"message": "bad"}], "data": {}})])
        with patch("twscrape.models.parse_tweets", return_value=[]):
            error_page = asyncio.run(
                phase3._provider_page(
                    error_api,
                    1,
                    include_replies=True,
                    cursor=None,
                )
            )
        self.assertFalse(error_page.valid_response)

    def test_checkpoint_helpers_fail_closed_without_state(self):
        with self.assertRaises(ValueError):
            phase3._checkpoint_updates({"updates": ["bad"]})

        checkpoint = self._checkpoint()
        update = _update("1", self.end - timedelta(hours=1))
        phase3._persist_checkpoint(None, checkpoint, [update])
        phase3._clear_checkpoint(None, checkpoint["checkpoint_id"])

        collector = _MinimalCollector()
        phase3._remember_partial(collector, "@Source", [update, update])
        self.assertEqual(list(collector._phase3_partial_updates), ["source"])
        self.assertEqual([item.id for item in collector._phase3_partial_updates["source"]], ["1"])

    def test_resumable_timeline_rejects_invalid_source_and_uses_legacy_without_raw_api(self):
        collector = _MinimalCollector(api=object())
        with self.assertRaises(XCollectionError):
            asyncio.run(
                phase3._resumable_source_timeline(
                    collector,
                    "",
                    self.start,
                    self.end,
                    limit=10,
                    include_replies=True,
                )
            )
        with self.assertRaises(XCollectionError):
            asyncio.run(
                phase3._resumable_source_timeline(
                    collector,
                    "external",
                    self.start,
                    self.end,
                    limit=10,
                    include_replies=True,
                )
            )

        legacy = AsyncMock(return_value=[_update("legacy", self.end - timedelta(hours=1))])
        with patch.object(phase3, "_LEGACY_TIMELINE", new=legacy):
            result = asyncio.run(
                phase3._resumable_source_timeline(
                    collector,
                    "source",
                    self.start,
                    self.end,
                    limit=10,
                    include_replies=True,
                )
            )
        self.assertEqual([item.id for item in result], ["legacy"])
        legacy.assert_awaited_once()

    def test_malformed_resumed_checkpoint_is_replaced_conservatively(self):
        api = SimpleNamespace(
            user_by_login=AsyncMock(return_value=SimpleNamespace(id=1)),
            user_tweets_and_replies_raw=Mock(),
        )
        collector = _MinimalCollector(api=api)
        state = Mock(spec=StateStore)
        state.get_x_retrieval_checkpoint.return_value = {
            "checkpoint_id": "broken",
            "updates": ["not-a-dict"],
        }
        collector._phase3_state = state

        page = phase3._ProviderPage([], None, True, valid_response=True)
        with patch.object(phase3, "_fetch_page_with_retry", new=AsyncMock(return_value=page)), patch.object(
            phase3, "_persist_checkpoint"
        ), patch.object(phase3, "_clear_checkpoint") as clear:
            result = asyncio.run(
                phase3._resumable_source_timeline(
                    collector,
                    "source",
                    self.start,
                    self.end,
                    limit=20,
                    include_replies=True,
                )
            )

        self.assertEqual(result, [])
        clear.assert_called_once()

    def test_unexpected_conversion_failure_is_checkpointed_and_wrapped(self):
        api = SimpleNamespace(
            user_by_login=AsyncMock(return_value=SimpleNamespace(id=1)),
            user_tweets_and_replies_raw=Mock(),
        )
        collector = _MinimalCollector(api=api)
        collector._convert_tweet = Mock(side_effect=ValueError("broken conversion"))
        page = phase3._ProviderPage(
            [SimpleNamespace(retweetedTweet=None)],
            "next",
            False,
            valid_response=True,
        )
        persisted = Mock()
        with patch.object(phase3, "_fetch_page_with_retry", new=AsyncMock(return_value=page)), patch.object(
            phase3, "_persist_checkpoint", new=persisted
        ), patch.object(phase3, "_lookup_user", new=AsyncMock(return_value=SimpleNamespace(id=1))):
            with self.assertRaises(XCollectionError):
                asyncio.run(
                    phase3._resumable_source_timeline(
                        collector,
                        "source",
                        self.start,
                        self.end,
                        limit=20,
                        include_replies=True,
                    )
                )
        self.assertTrue(persisted.called)
        self.assertEqual(collector._phase3_partial_updates["source"], [])

    def test_resumable_collect_window_recovers_partial_and_restores_flags(self):
        collector = _MinimalCollector()
        collector._phase3_allow_older_checkpoint = False
        collector._phase3_partial_updates = {"previous": []}
        collector._phase3_syndication_fallback_count = 3
        recovered = _update("safe", self.end - timedelta(hours=1))

        async def failing(original_self, *_args, **_kwargs):
            original_self._phase3_partial_updates = {"source": [recovered]}
            raise XCollectionError("core failed")

        with patch.object(phase3, "_ORIGINAL_COLLECT_WINDOW", new=failing):
            result = asyncio.run(phase3._resumable_collect_window(collector, self.start, self.end))

        self.assertEqual([item.id for item in result], ["safe"])
        self.assertFalse(collector._phase3_allow_older_checkpoint)
        self.assertEqual(collector._phase3_partial_updates, {"previous": []})
        self.assertEqual(collector._phase3_syndication_fallback_count, 3)

        async def empty_failure(original_self, *_args, **_kwargs):
            original_self._phase3_partial_updates = {}
            raise XCollectionError("still failed")

        with patch.object(phase3, "_ORIGINAL_COLLECT_WINDOW", new=empty_failure):
            with self.assertRaises(XCollectionError):
                asyncio.run(phase3._resumable_collect_window(collector, self.start, self.end))

    def test_integrity_lookup_retry_recovery_and_scoped_search_failure(self):
        retry_api = SimpleNamespace()
        retry_api.user_by_login = AsyncMock(
            side_effect=[RuntimeError("temporary"), SimpleNamespace(id=7, username="source")]
        )
        with patch.object(phase3, "_sleep_for_retry", new=AsyncMock()), patch.object(
            integrity, "observe"
        ) as observe:
            user = asyncio.run(
                integrity._lookup_user_with_scoped_id_recovery(
                    retry_api,
                    "source",
                    "attempt",
                )
            )
        self.assertEqual(user.id, 7)
        self.assertTrue(
            any(call.kwargs.get("retry_outcome") == "profile_recovered" for call in observe.call_args_list)
        )

        class SearchFailureAPI:
            async def user_by_login(self, _handle):
                return None

            def search(self, *_args, **_kwargs):
                raise RuntimeError("search failed")

        with patch.object(phase3, "_sleep_for_retry", new=AsyncMock()), patch.object(
            integrity, "observe"
        ) as observe:
            with self.assertRaises(XCollectionError):
                asyncio.run(
                    integrity._lookup_user_with_scoped_id_recovery(
                        SearchFailureAPI(),
                        "source",
                        "attempt",
                    )
                )
        self.assertTrue(
            any(call.kwargs.get("retry_outcome") == "profile_exhausted" for call in observe.call_args_list)
        )


if __name__ == "__main__":
    unittest.main()
