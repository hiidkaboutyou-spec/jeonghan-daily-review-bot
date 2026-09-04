from __future__ import annotations

import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

from app.models import MediaItem, Update
from app.raw_observation import RawObservation, RawObservationError, RawObservationStore
from app.raw_observation_runtime import _build_observation
from app.source_modes import SourceModeGate
from app.x_client import XCollector


def _update(
    *,
    post_id: str = "101",
    author: str = "source",
    text: str = "hello",
    media: list[MediaItem] | None = None,
    quoted_text: str = "",
) -> Update:
    return Update(
        id=post_id,
        url=f"https://x.com/{author}/status/{post_id}",
        author=author,
        author_name=author,
        text=text,
        created_at=datetime(2026, 9, 4, 12, 0, tzinfo=timezone.utc),
        media=list(media or []),
        quoted_text=quoted_text,
    )


def _tweet(
    *,
    post_id: str = "101",
    author: str = "source",
    text: str = "hello",
    retweeted: object | None = None,
    reply_to: str = "",
    quoted: object | None = None,
):
    return SimpleNamespace(
        id_str=post_id,
        id=int(post_id) if post_id.isdigit() else 0,
        user=SimpleNamespace(username=author),
        rawContent=text,
        date=datetime(2026, 9, 4, 12, 0, tzinfo=timezone.utc),
        lang="en",
        conversationId=post_id,
        inReplyToTweetId=reply_to,
        quotedTweet=quoted,
        retweetedTweet=retweeted,
    )


class RawObservationStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.store = RawObservationStore(Path(self.tmp.name) / "raw.sqlite3")

    def tearDown(self) -> None:
        self.store.close()
        self.tmp.cleanup()

    def test_records_and_reads_original_fields(self) -> None:
        observation = RawObservation(
            provider="x",
            external_post_id="101",
            source_handle="@Source",
            source_mode="full_feed",
            created_at="2026-09-04T12:00:00+00:00",
            text="original text",
            quoted_text="quoted",
            media_json='[{"kind":"photo"}]',
            provenance="timeline:@source",
        )
        self.store.record(observation, observed_at="2026-09-04T12:01:00+00:00")
        row = self.store.get(provider="x", external_post_id="101", source_handle="source")
        self.assertIsNotNone(row)
        assert row is not None
        self.assertEqual(row["text"], "original text")
        self.assertEqual(row["quoted_text"], "quoted")
        self.assertEqual(row["source_handle"], "source")
        self.assertEqual(row["observation_count"], 1)
        self.assertEqual(self.store.version_count(observation.observation_key), 1)

    def test_repeated_identical_observation_counts_without_duplicate_version(self) -> None:
        observation = RawObservation(
            provider="x",
            external_post_id="101",
            source_handle="source",
            source_mode="full_feed",
            created_at="2026-09-04T12:00:00+00:00",
            text="same",
        )
        self.store.record(observation)
        self.store.record(observation)
        row = self.store.get(provider="x", external_post_id="101", source_handle="source")
        assert row is not None
        self.assertEqual(row["observation_count"], 2)
        self.assertEqual(self.store.version_count(observation.observation_key), 1)

    def test_changed_snapshot_preserves_second_version(self) -> None:
        first = RawObservation(
            provider="x",
            external_post_id="101",
            source_handle="source",
            source_mode="full_feed",
            created_at="2026-09-04T12:00:00+00:00",
            text="before",
        )
        second = RawObservation(
            provider="x",
            external_post_id="101",
            source_handle="source",
            source_mode="full_feed",
            created_at="2026-09-04T12:00:00+00:00",
            text="after edit",
        )
        self.store.record(first)
        self.store.record(second)
        row = self.store.get(provider="x", external_post_id="101", source_handle="source")
        assert row is not None
        self.assertEqual(row["text"], "after edit")
        self.assertEqual(row["observation_count"], 2)
        self.assertEqual(self.store.version_count(first.observation_key), 2)

    def test_incomplete_identity_is_rejected(self) -> None:
        with self.assertRaises(RawObservationError):
            RawObservation(
                provider="x",
                external_post_id="",
                source_handle="source",
                source_mode="full_feed",
                created_at="",
            )


class RawObservationRuntimeTests(unittest.TestCase):
    def _collector(self, *, mode: str = "keyword_filter") -> XCollector:
        collector = object.__new__(XCollector)
        collector.sources = [
            {
                "handle": "source",
                "label": "source",
                "enabled": True,
                "priority": 10,
                "include_replies": True,
                "mode": mode,
            }
        ]
        collector.source_mode_gate = SourceModeGate(collector.sources)
        collector.db_path = Path("x_accounts.db")
        return collector

    def test_generic_keyword_filter_post_is_persistable_before_gate_rejects_it(self) -> None:
        collector = self._collector()
        update = _update(text="look at this")
        observation = _build_observation(
            collector,
            _tweet(text="look at this"),
            raw_query="timeline:@source",
            update=update,
            status="converted",
        )
        self.assertIsNotNone(observation)
        assert observation is not None
        with tempfile.TemporaryDirectory() as tmp:
            store = RawObservationStore(Path(tmp) / "raw.sqlite3")
            try:
                store.record(observation)
                self.assertEqual(store.count(source_handle="source"), 1)
            finally:
                store.close()
        self.assertEqual(collector.source_mode_gate.filter_posts([update]), [])

    def test_media_only_keyword_filter_post_is_preserved_before_gate(self) -> None:
        collector = self._collector()
        media = [MediaItem(kind="photo", url="https://pbs.twimg.com/media/example.jpg")]
        update = _update(text="", media=media)
        observation = _build_observation(
            collector,
            _tweet(text=""),
            raw_query="timeline:@source",
            update=update,
            status="converted",
        )
        self.assertIsNotNone(observation)
        assert observation is not None
        self.assertTrue(observation.is_media_only)
        self.assertEqual(observation.post_type, "media_only")
        self.assertEqual(json.loads(observation.media_json)[0]["kind"], "photo")
        self.assertEqual(collector.source_mode_gate.filter_posts([update]), [])

    def test_retweet_is_marked_and_can_be_stored_before_timeline_policy_skips_it(self) -> None:
        collector = self._collector(mode="full_feed")
        update = _update(text="RT something")
        observation = _build_observation(
            collector,
            _tweet(retweeted=SimpleNamespace(id_str="55")),
            raw_query="timeline:@source",
            update=update,
            status="converted",
        )
        self.assertIsNotNone(observation)
        assert observation is not None
        self.assertTrue(observation.is_retweet)
        self.assertEqual(observation.post_type, "retweet")

    def test_external_nonconfigured_author_is_not_admitted_to_source_truth(self) -> None:
        collector = self._collector()
        update = _update(author="outsider")
        observation = _build_observation(
            collector,
            _tweet(author="outsider"),
            raw_query="from:outsider",
            update=update,
            status="converted",
        )
        self.assertIsNone(observation)

    def test_conversion_failure_still_builds_minimal_configured_observation(self) -> None:
        collector = self._collector()
        observation = _build_observation(
            collector,
            _tweet(text="provider payload"),
            raw_query="timeline:@source",
            update=None,
            status="conversion_failed",
        )
        self.assertIsNotNone(observation)
        assert observation is not None
        self.assertEqual(observation.external_post_id, "101")
        self.assertEqual(observation.source_handle, "source")
        self.assertEqual(observation.observation_status, "conversion_failed")
        self.assertTrue(observation.provider_payload_hash)

    def test_runtime_hook_is_installed(self) -> None:
        self.assertTrue(XCollector.__dict__.get("_raw_observation_installed", False))


if __name__ == "__main__":
    unittest.main()
