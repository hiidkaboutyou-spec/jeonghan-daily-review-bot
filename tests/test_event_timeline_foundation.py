from __future__ import annotations

import copy
import json
import os
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

from app import event_fusion, event_timeline
from app.event_timeline import (
    SegmentFingerprint,
    make_segment_id,
    match_segment_fingerprints,
    merge_segments,
    reclassify_segment_membership,
    shadow_segment_events,
    split_segment,
)
from app.fic_state import FicObservation, FicStateStore
from app.message_delivery import MessageDeliveryStore
from app.models import Update
from app.realtime_ingest import realtime_shadow_enabled
from app.state import StateStore
from app.zero_silent_miss import media_asset_id
from tools.run_event_timeline_benchmark import run as run_benchmark

ROOT = Path(__file__).resolve().parents[1]
CONFIGURED = {"hani_berry_1004", "honeyya_hanihae", "pledis_17", "pledis_17jp"}


def update(
    update_id: str,
    *,
    author: str = "hani_berry_1004",
    text: str = "Weverse Live Jeonghan update",
    created_at: str = "2026-08-15T01:00:00+00:00",
    conversation_id: str = "",
    reply_to_id: str = "",
    quoted_id: str = "",
    lang: str = "",
) -> Update:
    return Update(
        id=update_id,
        url=f"https://x.com/{author}/status/{update_id}",
        author=author,
        author_name=author,
        text=text,
        created_at=datetime.fromisoformat(created_at.replace("Z", "+00:00")),
        conversation_id=conversation_id,
        reply_to_id=reply_to_id,
        quoted_id=quoted_id,
        lang=lang,
    )


def fingerprint(
    update_id: str,
    *,
    event_id: str,
    event_type: str = "live",
    source: str = "hani_berry_1004",
    created_at: str = "2026-08-15T01:00:00+00:00",
    **kwargs,
) -> SegmentFingerprint:
    raw = {
        "update_id": update_id,
        "event_id": event_id,
        "source": source,
        "created_at": created_at,
        "event_type": event_type,
    }
    raw.update(kwargs)
    return SegmentFingerprint.from_dict(raw)


class EventTimelineFoundationTests(unittest.TestCase):
    def state(self, directory: str) -> StateStore:
        return StateStore(Path(directory) / "state.json")

    def seed_event(self, state: StateStore, items: list[Update], *, event_type: str = "live") -> str:
        event_id = event_fusion.make_event_id([item.id for item in items[:2]])
        fusion = event_fusion._event_state(state)
        now = "2026-08-15T01:00:00+00:00"
        fusion["events"][event_id] = {
            "event_id": event_id,
            "event_type": event_type,
            "created_at": now,
            "updated_at": now,
            "member_update_ids": sorted(item.id for item in items),
            "confidence": 0.95,
            "status": "shadow_candidate",
            "subject_key": "subject:test",
        }
        for item in items:
            fusion["memberships"][item.id] = {
                "event_id": event_id,
                "confidence": 0.95,
                "matching_signals": ["fixture"],
                "conflicts": [],
                "decision": "probable_same_event",
                "updated_at": now,
            }
        return event_id

    def test_segment_identity_is_stable_and_independent(self):
        event_id = event_fusion.make_event_id(("u1", "u2"))
        first = make_segment_id(event_id, ("u1",))
        second = make_segment_id(event_id, ("u1",))
        self.assertEqual(first, second)
        self.assertTrue(first.startswith("seg:"))
        self.assertNotEqual(first, event_id)
        self.assertNotIn(first, {"u1", "conversation:u1", "processing:u1", "draft:u1"})

    def test_segment_id_changes_with_event_identity(self):
        one = event_fusion.make_event_id(("u1", "u2"))
        two = event_fusion.make_event_id(("u1", "u3"))
        self.assertNotEqual(make_segment_id(one, ("u1",)), make_segment_id(two, ("u1",)))

    def test_time_only_does_not_merge(self):
        event_id = event_fusion.make_event_id(("u1", "u2"))
        left = fingerprint("u1", event_id=event_id)
        right = fingerprint("u2", event_id=event_id, source="honeyya_hanihae", created_at="2026-08-15T01:00:10+00:00")
        self.assertFalse(match_segment_fingerprints(left, right).same_segment)

    def test_text_only_does_not_merge(self):
        event_id = event_fusion.make_event_id(("u1", "u2"))
        common = ["a", "b", "c", "d"]
        left = fingerprint("u1", event_id=event_id, topic_hashes=common)
        right = fingerprint("u2", event_id=event_id, source="honeyya_hanihae", topic_hashes=common, created_at="2026-08-15T01:05:00+00:00")
        self.assertFalse(match_segment_fingerprints(left, right).same_segment)

    def test_same_container_is_not_same_segment(self):
        event_id = event_fusion.make_event_id(("u1", "u2"))
        left = fingerprint("u1", event_id=event_id, event_type="going_seventeen", reference_hashes=["episode"], content_timestamp_seconds=120)
        right = fingerprint("u2", event_id=event_id, event_type="going_seventeen", source="honeyya_hanihae", reference_hashes=["episode"], content_timestamp_seconds=900)
        result = match_segment_fingerprints(left, right, container_reference_hashes={"episode"})
        self.assertFalse(result.same_segment)
        self.assertIn("shared_container_reference", result.matching_signals)
        self.assertIn("content_timestamp_mismatch", result.conflicts)

    def test_strong_reference_groups_same_moment(self):
        event_id = event_fusion.make_event_id(("u1", "u2"))
        left = fingerprint("u1", event_id=event_id, quoted_id="clip")
        right = fingerprint("u2", event_id=event_id, source="honeyya_hanihae", quoted_id="clip")
        result = match_segment_fingerprints(left, right)
        self.assertTrue(result.same_segment)
        self.assertEqual(result.relationship, "same_moment")

    def test_reply_thread_can_be_continuation(self):
        event_id = event_fusion.make_event_id(("u1", "u2"))
        left = fingerprint("u1", event_id=event_id, conversation_id="thread", topic_hashes=["a", "b", "c"])
        right = fingerprint("u2", event_id=event_id, source="honeyya_hanihae", conversation_id="thread", reply_to_id="u1", topic_hashes=["a", "b", "c"])
        result = match_segment_fingerprints(left, right)
        self.assertTrue(result.same_segment)
        self.assertEqual(result.relationship, "continuation")

    def test_cross_language_strong_anchor_groups(self):
        event_id = event_fusion.make_event_id(("u1", "u2"))
        left = fingerprint("u1", event_id=event_id, language="ko", media_hashes=["clip"])
        right = fingerprint("u2", event_id=event_id, language="en", source="honeyya_hanihae", media_hashes=["clip"])
        result = match_segment_fingerprints(left, right)
        self.assertTrue(result.same_segment)
        self.assertIn("shared_media_reference", result.matching_signals)

    def test_conflict_remains_unresolved(self):
        event_id = event_fusion.make_event_id(("u1", "u2"))
        left = fingerprint("u1", event_id=event_id, quoted_id="same-clip", topic_hashes=["a", "b", "c"], fact_numbers=["3"])
        right = fingerprint("u2", event_id=event_id, source="honeyya_hanihae", quoted_id="same-clip", topic_hashes=["a", "b", "c"], fact_numbers=["5"])
        result = match_segment_fingerprints(left, right)
        self.assertTrue(result.same_segment)
        self.assertEqual(result.relationship, "conflicting")
        self.assertIn("fact_value_conflict", result.conflicts)

    def test_chronology_prefers_content_timestamp(self):
        event_id = event_fusion.make_event_id(("u1", "u2"))
        fps = {
            "u1": fingerprint("u1", event_id=event_id, content_timestamp_seconds=600),
            "u2": fingerprint("u2", event_id=event_id, content_timestamp_seconds=120, created_at="2026-08-15T01:10:00+00:00"),
        }
        fusion = event_timeline._fresh_timeline_fields()
        for uid in fps:
            sid = make_segment_id(event_id, [uid])
            fusion["segments"][sid] = {"segment_id": sid, "event_id": event_id, "member_update_ids": [uid], "created_at": "", "updated_at": "", "confidence": 1.0, "status": "shadow_candidate", "order_index": 0, "order_evidence": {}}
        order = event_timeline._reorder_event_segments(fusion, event_id, fps)
        self.assertEqual([fusion["segments"][sid]["member_update_ids"][0] for sid in order], ["u2", "u1"])
        self.assertEqual(fusion["segments"][order[0]]["order_evidence"]["kind"], "content_timestamp")

    def test_part_order_beats_source_created_order(self):
        event_id = event_fusion.make_event_id(("u1", "u2"))
        left = fingerprint("u1", event_id=event_id, part_number=2, created_at="2026-08-15T01:00:00+00:00")
        right = fingerprint("u2", event_id=event_id, part_number=1, created_at="2026-08-15T01:10:00+00:00")
        ordered = [fp.update_id for fp in sorted([left, right], key=event_timeline._fingerprint_sort_key)]
        self.assertEqual(ordered, ["u2", "u1"])

    def test_shadow_segments_persist_across_restart(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "state.json"
            state = StateStore(path)
            a = update("u1", quoted_id="clip")
            b = update("u2", author="honeyya_hanihae", quoted_id="clip", created_at="2026-08-15T01:03:00+00:00")
            self.seed_event(state, [a, b])
            shadow_segment_events(state, [a, b], CONFIGURED)
            before = copy.deepcopy(state.data["event_fusion"]["segments"])
            self.assertTrue(before)
            state.save()
            restored = StateStore(path)
            self.assertEqual(restored.data["event_fusion"]["segments"], before)

    def test_archived_prior_member_rebuilds_only_bounded_fingerprint(self):
        with tempfile.TemporaryDirectory() as tmp:
            state = self.state(tmp)
            a = update("u1", quoted_id="clip")
            b = update("u2", author="honeyya_hanihae", quoted_id="clip", created_at="2026-08-15T01:03:00+00:00")
            event_id = self.seed_event(state, [a, b])
            state.archive_update(a)
            shadow_segment_events(state, [b], CONFIGURED)
            stored = state.data["event_fusion"]["timeline_fingerprints"]["u1"]
            self.assertEqual(stored["event_id"], event_id)
            self.assertNotIn("text", stored)
            self.assertNotIn("media", stored)

    def test_reclassification_preserves_source_lifecycle_and_checkpoint(self):
        with tempfile.TemporaryDirectory() as tmp:
            state = self.state(tmp)
            a = update("u1", text="Weverse Live dinner topic")
            b = update("u2", author="honeyya_hanihae", text="Weverse Live game topic")
            self.seed_event(state, [a, b])
            shadow_segment_events(state, [a, b], CONFIGURED)
            fusion = state.data["event_fusion"]
            segments = list(fusion["segments"])
            self.assertGreaterEqual(len(segments), 2)
            state.archive_update(a)
            state.data["seen"][a.id] = "keep"
            state.data["update_lifecycle"] = {a.id: {"status": "pending_delivery"}}
            state.data["x_retrieval_checkpoints"] = {"cp": {"complete": False}}
            before = {key: copy.deepcopy(state.data[key]) for key in ("archive", "seen", "update_lifecycle", "x_retrieval_checkpoints")}
            current = fusion["segment_memberships"][a.id]["segment_id"]
            target = next(sid for sid in segments if sid != current)
            reclassify_segment_membership(state, a.id, target)
            for key, value in before.items():
                self.assertEqual(state.data[key], value)

    def test_split_and_merge_are_metadata_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            state = self.state(tmp)
            a = update("u1", quoted_id="clip")
            b = update("u2", author="honeyya_hanihae", quoted_id="clip", created_at="2026-08-15T01:02:00+00:00")
            self.seed_event(state, [a, b])
            state.archive_update(a)
            shadow_segment_events(state, [a, b], CONFIGURED)
            fusion = state.data["event_fusion"]
            original = fusion["segment_memberships"][a.id]["segment_id"]
            self.assertEqual(set(fusion["segments"][original]["member_update_ids"]), {"u1", "u2"})
            archive_before = copy.deepcopy(state.data["archive"])
            split_id = split_segment(state, original, ["u2"])
            remaining = state.data["event_fusion"]["segment_memberships"]["u1"]["segment_id"]
            merged = merge_segments(state, remaining, split_id)
            self.assertEqual(set(state.data["event_fusion"]["segments"][merged]["member_update_ids"]), {"u1", "u2"})
            self.assertEqual(state.data["archive"], archive_before)

    def test_phase2_phase3_seen_pending_are_untouched(self):
        with tempfile.TemporaryDirectory() as tmp:
            state = self.state(tmp)
            a = update("u1", quoted_id="clip")
            b = update("u2", author="honeyya_hanihae", quoted_id="clip")
            self.seed_event(state, [a, b])
            state.data["update_lifecycle"] = {"u1": {"status": "pending_translation"}}
            state.data["x_retrieval_checkpoints"] = {"cp": {"cursor": "opaque", "complete": False}}
            state.data["seen"] = {"old": "2026-08-15T00:00:00+00:00"}
            state.data["pending_delivery"] = [a.to_dict()]
            before = {key: copy.deepcopy(state.data[key]) for key in ("update_lifecycle", "x_retrieval_checkpoints", "seen", "pending_delivery")}
            shadow_segment_events(state, [a, b], CONFIGURED)
            for key, value in before.items():
                self.assertEqual(state.data[key], value)

    def test_telegram_receipt_is_untouched(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = MessageDeliveryStore(Path(tmp) / "private-review.sqlite3")
            db.save_plan("draft:timeline", "hello", ["hello"])
            db.confirm("draft:timeline", 0, "hello", 8842)
            state = self.state(tmp)
            a = update("u1", quoted_id="clip")
            b = update("u2", author="honeyya_hanihae", quoted_id="clip")
            self.seed_event(state, [a, b])
            shadow_segment_events(state, [a, b], CONFIGURED)
            self.assertEqual(db.confirmed_message_id("draft:timeline", 0, "hello"), 8842)
            db.close()

    def test_unconfigured_source_gets_no_timeline_fingerprint(self):
        with tempfile.TemporaryDirectory() as tmp:
            state = self.state(tmp)
            a = update("u1")
            external = update("u2", author="random_external")
            self.seed_event(state, [a, external])
            shadow_segment_events(state, [a, external], CONFIGURED)
            self.assertNotIn("u2", state.data["event_fusion"].get("timeline_fingerprints", {}))

    def test_concert_segment_never_becomes_media_identity(self):
        event_id = event_fusion.make_event_id(("u1", "u2"))
        segment_id = make_segment_id(event_id, ["u1"])
        media_id = media_asset_id("video", "https://media.example/fancam.mp4")
        self.assertTrue(segment_id.startswith("seg:"))
        self.assertTrue(media_id.startswith("media:"))
        self.assertNotEqual(segment_id, media_id)

    def test_concert_distinct_media_remain_distinct(self):
        self.assertNotEqual(media_asset_id("video", "https://media.example/fancam-a.mp4"), media_asset_id("video", "https://media.example/fancam-b.mp4"))
        self.assertNotEqual(media_asset_id("photo", "https://media.example/photo-a.jpg"), media_asset_id("photo", "https://media.example/photo-b.jpg"))

    def test_exact_media_identity_is_unchanged(self):
        first = media_asset_id("video", "https://media.example/exact.mp4")
        second = media_asset_id("video", "https://media.example/exact.mp4")
        self.assertEqual(first, second)

    def test_runtime_wrapper_is_shadow_only(self):
        self.assertTrue(getattr(event_fusion.shadow_group_updates, "_event_timeline_shadow_installed", False))
        self.assertEqual(event_timeline.TIMELINE_MODE, "shadow")

    def test_fanfic_state_remains_independent(self):
        with tempfile.TemporaryDirectory() as tmp:
            fic = FicStateStore(Path(tmp) / "fic.sqlite3")
            observation = FicObservation("123", "2/5", "2026-08-15")
            self.assertEqual(fic.classify(observation), "new")
            state = self.state(tmp)
            a = update("u1", quoted_id="clip")
            b = update("u2", author="honeyya_hanihae", quoted_id="clip")
            self.seed_event(state, [a, b])
            shadow_segment_events(state, [a, b], CONFIGURED)
            self.assertEqual(fic.classify(observation), "unchanged")
            fic.close()

    def test_private_review_configured_sources_and_realtime_off(self):
        settings = json.loads((ROOT / "config" / "settings.json").read_text(encoding="utf-8"))
        sources = json.loads((ROOT / "config" / "sources.json").read_text(encoding="utf-8"))
        enabled = [str(item["handle"]).lstrip("@").casefold() for item in sources["sources"] if item.get("enabled", True)]
        self.assertIs(settings["runtime"]["review_only"], True)
        self.assertEqual(len(sources["sources"]), 24)
        self.assertEqual(len(enabled), 23)
        self.assertEqual(len(set(enabled)), 23)
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("REALTIME_SHADOW_MODE", None)
            self.assertFalse(realtime_shadow_enabled())

    def test_no_paid_or_new_queue_database_dependency(self):
        requirements = "\n".join((ROOT / path).read_text(encoding="utf-8").casefold() for path in ("requirements.txt", "requirements-optional-media.txt"))
        for name in ("supabase", "redis", "celery", "pinecone", "weaviate", "qdrant", "openai", "anthropic"):
            self.assertNotIn(name, requirements)

    def test_timeline_benchmark_has_30_cases_and_zero_false_merges(self):
        result = run_benchmark(ROOT / "data" / "event_timeline_benchmark.json")
        self.assertTrue(result["passed"])
        self.assertGreaterEqual(result["case_count"], 30)
        self.assertEqual(result["false_merge_count"], 0)
        self.assertEqual(result["false_split_count"], 0)
        self.assertEqual(result["same_moment_precision"], 1.0)
        self.assertEqual(result["same_moment_recall"], 1.0)
        self.assertEqual(result["chronology_accuracy"], 1.0)
        self.assertEqual(result["ambiguous_deferral_rate"], 1.0)


if __name__ == "__main__":
    unittest.main()
