from __future__ import annotations

import asyncio
import inspect
import json
import os
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from dataclasses import fields
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from app.config import Settings
from app.models import MediaItem, Update
from app.observability import safe_metadata
from app.realtime_ingest import (
    DetectedMedia,
    LatencyTrace,
    LogicalUpdateGate,
    RealtimeCandidate,
    ShadowRealtimeIngestor,
    ShadowRunSummary,
    SourceAuthorityGate,
    realtime_shadow_enabled,
)
from app.state import StateStore


NOW = datetime(2026, 8, 14, 20, 0, tzinfo=timezone.utc)


def candidate(
    *,
    source: str = "flamehanie",
    post_id: str = "100",
    method: str = "fast_poll",
    reply: bool = False,
    repost: bool = False,
    media: tuple[DetectedMedia, ...] = (),
) -> RealtimeCandidate:
    return RealtimeCandidate(
        source_handle=source,
        post_id=post_id,
        created_at=NOW,
        detected_at=NOW + timedelta(seconds=7),
        detection_method=method,
        is_reply=reply,
        is_repost=repost,
        media=media,
        retrieval_attempt_id="attempt-safe",
    )


def update(
    *,
    source: str = "flamehanie",
    post_id: str = "100",
    reply: bool = False,
    media: list[MediaItem] | None = None,
    text: str = "authoritative",
) -> Update:
    return Update(
        id=post_id,
        url=f"https://x.com/{source}/status/{post_id}",
        author=source,
        author_name=source,
        text=text,
        created_at=NOW,
        conversation_id=post_id,
        reply_to_id="99" if reply else "",
        media=list(media or []),
        is_reply=reply,
    )


class ListDetector:
    def __init__(self, items: list[RealtimeCandidate], *, error: Exception | None = None):
        self.items = items
        self.error = error

    async def candidates(self):
        for item in self.items:
            yield item
        if self.error is not None:
            raise self.error


class RealtimeArchitectureTests(unittest.TestCase):
    def test_shadow_feature_flag_defaults_off(self):
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("REALTIME_SHADOW_MODE", None)
            self.assertFalse(realtime_shadow_enabled())

    def test_shadow_feature_flag_accepts_explicit_true(self):
        with patch.dict(os.environ, {"REALTIME_SHADOW_MODE": "true"}):
            self.assertTrue(realtime_shadow_enabled())

    def test_fast_detection_of_configured_source_post_is_accepted(self):
        gate = SourceAuthorityGate([{"handle": "flamehanie", "enabled": True, "include_replies": True}])
        decision = gate.decide_candidate(candidate())
        self.assertTrue(decision.accepted)
        self.assertEqual(decision.reason, "configured_source")

    def test_external_source_is_rejected_before_hydration(self):
        ingestor = ShadowRealtimeIngestor(
            sources=[{"handle": "flamehanie", "enabled": True, "include_replies": True}],
            enabled=True,
        )
        observation = ingestor.inspect(candidate(source="randomfan"))
        self.assertFalse(observation.claimed)
        self.assertEqual(observation.decision.reason, "external_source")

    def test_reply_policy_is_preserved(self):
        gate = SourceAuthorityGate([{"handle": "source", "enabled": True, "include_replies": False}])
        decision = gate.decide_candidate(candidate(source="source", reply=True))
        self.assertFalse(decision.accepted)
        self.assertEqual(decision.reason, "reply_excluded")

    def test_reply_is_allowed_when_configured(self):
        gate = SourceAuthorityGate([{"handle": "source", "enabled": True, "include_replies": True}])
        self.assertTrue(gate.decide_candidate(candidate(source="source", reply=True)).accepted)

    def test_retweet_policy_is_preserved(self):
        gate = SourceAuthorityGate([{"handle": "source", "enabled": True, "include_replies": True}])
        decision = gate.decide_candidate(candidate(source="source", repost=True))
        self.assertFalse(decision.accepted)
        self.assertEqual(decision.reason, "repost_excluded")

    def test_fast_path_then_same_fast_candidate_is_one_logical_claim(self):
        ingestor = ShadowRealtimeIngestor(
            sources=[{"handle": "flamehanie", "enabled": True, "include_replies": True}],
            enabled=True,
        )
        self.assertTrue(ingestor.inspect(candidate()).claimed)
        duplicate = ingestor.inspect(candidate())
        self.assertFalse(duplicate.claimed)
        self.assertEqual(duplicate.decision.reason, "already_claimed")

    def test_backfill_first_then_fast_path_is_duplicate(self):
        gate = LogicalUpdateGate(is_seen=lambda value: value == "100")
        ingestor = ShadowRealtimeIngestor(
            sources=[{"handle": "flamehanie", "enabled": True, "include_replies": True}],
            logical_gate=gate,
            enabled=True,
        )
        observation = ingestor.inspect(candidate())
        self.assertFalse(observation.claimed)
        self.assertEqual(observation.decision.reason, "already_seen")

    def test_pending_backfill_item_blocks_fast_duplicate(self):
        gate = LogicalUpdateGate(is_pending=lambda value: value == "100")
        ingestor = ShadowRealtimeIngestor(
            sources=[{"handle": "flamehanie", "enabled": True, "include_replies": True}],
            logical_gate=gate,
            enabled=True,
        )
        self.assertEqual(ingestor.inspect(candidate()).decision.reason, "already_pending")

    def test_near_simultaneous_local_workers_claim_only_once(self):
        gate = LogicalUpdateGate()
        barrier = threading.Barrier(8)

        def work():
            barrier.wait()
            return gate.claim("same")[0]

        with ThreadPoolExecutor(max_workers=8) as pool:
            results = list(pool.map(lambda _: work(), range(8)))
        self.assertEqual(sum(results), 1)

    def test_same_update_id_is_authoritative_across_detection_methods(self):
        gate = LogicalUpdateGate()
        sources = [{"handle": "flamehanie", "enabled": True, "include_replies": True}]
        ingestor = ShadowRealtimeIngestor(sources=sources, logical_gate=gate, enabled=True)
        first = ingestor.inspect(candidate(method="filtered_stream"))
        second = ingestor.inspect(candidate(method="scheduled_backfill"))
        self.assertTrue(first.claimed)
        self.assertFalse(second.claimed)
        self.assertEqual(second.decision.reason, "already_claimed")

    def test_authoritative_hydration_rejects_id_mismatch(self):
        ingestor = ShadowRealtimeIngestor(
            sources=[{"handle": "flamehanie", "enabled": True, "include_replies": True}],
            enabled=True,
        )
        observation = ingestor.inspect(candidate())
        with self.assertRaises(ValueError):
            ingestor.hydrate(observation, update(post_id="other"))

    def test_authoritative_hydration_rejects_source_mismatch(self):
        ingestor = ShadowRealtimeIngestor(
            sources=[
                {"handle": "flamehanie", "enabled": True, "include_replies": True},
                {"handle": "other", "enabled": True, "include_replies": True},
            ],
            enabled=True,
        )
        observation = ingestor.inspect(candidate())
        with self.assertRaises(ValueError):
            ingestor.hydrate(observation, update(source="other"))

    def test_authoritative_reply_policy_is_rechecked(self):
        ingestor = ShadowRealtimeIngestor(
            sources=[{"handle": "flamehanie", "enabled": True, "include_replies": False}],
            enabled=True,
        )
        observation = ingestor.inspect(candidate())
        self.assertTrue(observation.claimed)
        with self.assertRaises(ValueError):
            ingestor.hydrate(observation, update(reply=True))

    def test_fast_path_partial_metadata_can_be_enriched_by_authoritative_retrieval(self):
        detected_media = DetectedMedia(
            kind="photo",
            url="https://pbs.twimg.com/media/example?format=jpg&name=orig",
            width=2048,
            height=2048,
        )
        ingestor = ShadowRealtimeIngestor(
            sources=[{"handle": "flamehanie", "enabled": True, "include_replies": True}],
            enabled=True,
        )
        observation = ingestor.inspect(candidate(media=(detected_media,)))
        hydrated = ingestor.hydrate(observation, update(media=[]))
        self.assertEqual(len(hydrated.media), 1)
        self.assertEqual(hydrated.media[0].width, 2048)

    def test_authoritative_media_wins_when_already_complete(self):
        fast_media = DetectedMedia(kind="photo", url="https://example.invalid/fast.jpg")
        authoritative_media = MediaItem(kind="video", url="https://video.twimg.com/authoritative.mp4")
        ingestor = ShadowRealtimeIngestor(
            sources=[{"handle": "flamehanie", "enabled": True, "include_replies": True}],
            enabled=True,
        )
        observation = ingestor.inspect(candidate(media=(fast_media,)))
        hydrated = ingestor.hydrate(observation, update(media=[authoritative_media]))
        self.assertEqual(hydrated.media, [authoritative_media])

    def test_media_metadata_roundtrips_without_loss(self):
        original = MediaItem(
            kind="video",
            url="https://video.twimg.com/test.mp4",
            preview_url="https://pbs.twimg.com/preview.jpg",
            bitrate=2176000,
            width=1920,
            height=1080,
            duration_ms=12000,
            content_type="video/mp4",
        )
        detected = DetectedMedia.from_media_item(original)
        self.assertEqual(detected.to_media_item(), original)

    def test_fast_path_failure_backfill_still_recovers(self):
        sources = [{"handle": "flamehanie", "enabled": True, "include_replies": True}]
        ingestor = ShadowRealtimeIngestor(sources=sources, enabled=True)

        async def hydrate(item):
            return update(post_id=item.post_id)

        summary = asyncio.run(
            ingestor.run_shadow(ListDetector([], error=RuntimeError("provider unavailable")), hydrate)
        )
        self.assertEqual(summary.deferred_to_backfill, 1)
        with tempfile.TemporaryDirectory() as temp_dir:
            recovered = StateStore(Path(temp_dir) / "state.json")
            recovered.queue_updates([update(post_id="recovered")])
            self.assertEqual(recovered.pop_pending(10)[0][0].id, "recovered")

    def test_hydration_failure_releases_claim_for_backfill_recovery(self):
        sources = [{"handle": "flamehanie", "enabled": True, "include_replies": True}]
        ingestor = ShadowRealtimeIngestor(sources=sources, enabled=True)

        async def hydrate(_item):
            raise RuntimeError("temporary")

        summary = asyncio.run(ingestor.run_shadow(ListDetector([candidate()]), hydrate))
        self.assertEqual(summary.deferred_to_backfill, 1)
        self.assertTrue(ingestor.inspect(candidate()).claimed)

    def test_disabled_shadow_does_not_consume_logical_claim(self):
        gate = LogicalUpdateGate()
        sources = [{"handle": "flamehanie", "enabled": True, "include_replies": True}]
        disabled = ShadowRealtimeIngestor(sources=sources, logical_gate=gate, enabled=False)
        enabled = ShadowRealtimeIngestor(sources=sources, logical_gate=gate, enabled=True)
        self.assertFalse(disabled.inspect(candidate()).claimed)
        self.assertTrue(enabled.inspect(candidate()).claimed)

    def test_state_queue_dedupes_same_update_id(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            state = StateStore(Path(temp_dir) / "state.json")
            item = update()
            state.queue_updates([item])
            state.queue_updates([item])
            self.assertEqual(len(state.data["pending_delivery"]), 1)

    def test_shadow_inspection_does_not_touch_backfill_cursor_or_phase3_checkpoint(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            state = StateStore(Path(temp_dir) / "state.json")
            state.data["last_auto_run"] = "2026-08-14T19:00:00+00:00"
            state.data["last_auto_attempt"] = "2026-08-14T19:05:00+00:00"
            state.data.setdefault("x_retrieval_checkpoints", {})["sentinel"] = {"opaque": "state"}
            before = json.loads(json.dumps(state.data))
            ingestor = ShadowRealtimeIngestor(
                sources=[{"handle": "flamehanie", "enabled": True, "include_replies": True}],
                logical_gate=LogicalUpdateGate(
                    is_seen=state.is_seen,
                    is_pending=lambda item_id: any(
                        str(raw.get("id", "")) == item_id
                        for raw in state.data.get("pending_delivery", [])
                        if isinstance(raw, dict)
                    ),
                ),
                enabled=True,
            )
            self.assertTrue(ingestor.inspect(candidate()).claimed)
            self.assertEqual(state.data, before)

    def test_phase3_checkpoint_interface_remains_installed(self):
        self.assertTrue(StateStore.__dict__.get("_phase3_checkpoint_installed", False))
        with tempfile.TemporaryDirectory() as temp_dir:
            state = StateStore(Path(temp_dir) / "state.json")
            self.assertIn("x_retrieval_checkpoints", state.data)

    def test_scheduling_reliability_interval_remains_twelve_minutes(self):
        settings = Settings.load(require_secrets=False)
        self.assertEqual(int(settings.runtime["scheduled_min_interval_minutes"]), 12)

    def test_all_24_configured_sources_remain_unique_and_enabled(self):
        settings = Settings.load(require_secrets=False)
        enabled = [source for source in settings.sources if source.get("enabled", True)]
        handles = [str(source["handle"]).lower() for source in enabled]
        self.assertEqual(len(handles), 24)
        self.assertEqual(len(set(handles)), 24)

    def test_private_review_only_remains_enabled(self):
        settings = Settings.load(require_secrets=False)
        self.assertTrue(settings.runtime["review_only"])

    def test_realtime_module_has_no_telegram_or_public_publish_surface(self):
        import app.realtime_ingest as realtime_ingest

        source = inspect.getsource(realtime_ingest)
        self.assertNotIn("TelegramBot", source)
        self.assertNotIn(".send_message(", source)
        self.assertNotIn(".send_media", source)
        self.assertNotIn("public_channel", source)

    def test_fanfic_ao3_subsystem_is_not_imported_by_realtime_module(self):
        import app.realtime_ingest as realtime_ingest

        source = inspect.getsource(realtime_ingest)
        self.assertNotIn("fic_digest", source)
        self.assertNotIn("ao3", source.lower())

    def test_latency_metrics_are_privacy_safe(self):
        trace = LatencyTrace(
            post_id="100",
            source="flamehanie",
            created_at=NOW,
            detected_at=NOW + timedelta(seconds=7),
            detection_method="fast_poll",
        )
        with patch("app.realtime_ingest.observe") as mocked:
            trace.mark_processing_started(NOW + timedelta(seconds=8))
        metadata = mocked.call_args.kwargs
        self.assertEqual(metadata["detection_latency_ms"], 7000)
        self.assertNotIn("text", metadata)
        self.assertNotIn("caption", metadata)
        self.assertNotIn("url", metadata)
        self.assertNotIn("cookie", metadata)

    def test_observability_drops_content_urls_and_secret_fields(self):
        safe = safe_metadata(
            {
                "event": "realtime_candidate",
                "detection_method": "fast_poll",
                "detection_latency_ms": 7000,
                "text": "PRIVATE POST BODY",
                "caption": "PRIVATE CAPTION",
                "url": "https://private.example/content",
                "cookie": "auth_token=SECRET",
                "authorization": "Bearer SECRET",
            }
        )
        self.assertEqual(safe["detection_method"], "fast_poll")
        self.assertEqual(safe["detection_latency_ms"], "7000")
        self.assertNotIn("text", safe)
        self.assertNotIn("caption", safe)
        self.assertNotIn("url", safe)
        self.assertNotIn("cookie", safe)
        self.assertNotIn("authorization", safe)

    def test_private_delivery_metric_requires_receipt_but_performs_no_delivery(self):
        trace = LatencyTrace(
            post_id="100",
            source="flamehanie",
            created_at=NOW,
            detected_at=NOW + timedelta(seconds=7),
            detection_method="fast_poll",
        )
        with self.assertRaises(ValueError):
            trace.mark_private_delivery(0)
        with patch("app.realtime_ingest.observe") as mocked:
            trace.mark_private_delivery(123, NOW + timedelta(seconds=20))
        self.assertEqual(mocked.call_args.kwargs["delivery_receipt_id"], 123)
        self.assertEqual(trace.end_to_end_latency_ms, 20000)

    def test_normalized_source_matching_is_case_insensitive(self):
        gate = SourceAuthorityGate([{"handle": "@FlameHanie", "enabled": True, "include_replies": True}])
        self.assertTrue(gate.decide_candidate(candidate(source="@FLAMEHANIE")).accepted)

    def test_shadow_run_hydrates_configured_candidate_without_delivery(self):
        sources = [{"handle": "flamehanie", "enabled": True, "include_replies": True}]
        ingestor = ShadowRealtimeIngestor(sources=sources, enabled=True)

        async def hydrate(item):
            return update(post_id=item.post_id)

        summary = asyncio.run(ingestor.run_shadow(ListDetector([candidate()]), hydrate))
        self.assertEqual(summary.observed, 1)
        self.assertEqual(summary.accepted, 1)
        self.assertEqual(summary.hydrated, 1)
        self.assertEqual(summary.deferred_to_backfill, 0)

    def test_fast_and_backfill_queue_same_update_id_still_queue_once(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            state = StateStore(Path(temp_dir) / "state.json")
            fast = update(text="fast-enriched")
            backfill = update(text="backfill-authoritative")
            state.queue_updates([fast])
            state.queue_updates([backfill])
            self.assertEqual(len(state.data["pending_delivery"]), 1)
            self.assertEqual(state.data["pending_delivery"][0]["id"], "100")

    def test_shadow_summary_exposes_no_success_cursor_or_completeness_fields(self):
        names = {item.name for item in fields(ShadowRunSummary)}
        self.assertNotIn("complete", names)
        self.assertNotIn("partial", names)
        self.assertNotIn("cursor", names)
        self.assertNotIn("cursor_advanced", names)


if __name__ == "__main__":
    unittest.main()
