from __future__ import annotations

import copy
import json
import os
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

from app.event_fusion import (
    EVENT_MODE,
    EVENT_TYPES,
    build_fingerprint,
    make_event_id,
    match_fingerprints,
    reclassify_event_membership,
    remove_event_membership,
    shadow_group_updates,
)
from app.fic_state import FicObservation, FicStateStore
from app.message_delivery import MessageDeliveryStore
from app.models import MediaItem, Update
from app.realtime_ingest import realtime_shadow_enabled
from app.state import StateStore
from app.zero_silent_miss import media_asset_id

ROOT = Path(__file__).resolve().parents[1]
CONFIGURED = {"hani_berry_1004", "honeyya_hanihae", "pledis_17", "pledis_17jp"}
SAME = {"confident_same_event", "probable_same_event"}


def update(
    update_id: str,
    *,
    author: str = "hani_berry_1004",
    text: str = "Jeonghan update",
    created_at: str = "2026-08-15T01:00:00+00:00",
    conversation_id: str = "",
    reply_to_id: str = "",
    quoted_id: str = "",
    category: str = "general",
    media: list[MediaItem] | None = None,
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
        category=category,
        media=list(media or []),
    )


def benchmark_update(raw: dict) -> Update:
    media = [MediaItem(**item) for item in raw.get("media", [])]
    return update(
        str(raw["id"]), author=str(raw["author"]), text=str(raw.get("text", "")),
        created_at=str(raw["created_at"]), conversation_id=str(raw.get("conversation_id", "")),
        reply_to_id=str(raw.get("reply_to_id", "")), quoted_id=str(raw.get("quoted_id", "")),
        category=str(raw.get("category", "general")), media=media,
    )


class EventFusionFoundationTests(unittest.TestCase):
    def state(self, directory: str) -> StateStore:
        return StateStore(Path(directory) / "state.json")

    def shared_pair(self, suffix: str = "1") -> tuple[Update, Update]:
        ref = f"https://youtube.com/watch/shared-{suffix}"
        return (
            update(f"a{suffix}", text=f"Jeonghan interview detail {ref}"),
            update(f"b{suffix}", author="honeyya_hanihae", text=f"More interview context {ref}", created_at="2026-08-15T01:05:00+00:00"),
        )

    def test_event_id_is_stable_and_independent(self):
        first = make_event_id(("u2", "u1"))
        second = make_event_id(("u1", "u2"))
        self.assertEqual(first, second)
        self.assertTrue(first.startswith("evt:"))
        self.assertNotIn(first, {"u1", "u2", "conversation:u1", "single:u1", "draft:u1"})

    def test_event_id_requires_two_distinct_updates(self):
        with self.assertRaises(ValueError):
            make_event_id(("u1", "u1"))

    def test_additive_membership_preserves_three_updates(self):
        with tempfile.TemporaryDirectory() as tmp:
            state = self.state(tmp)
            ref = "https://youtube.com/watch/additive"
            items = [
                update("u1", text=f"Interview source one {ref}"),
                update("u2", author="honeyya_hanihae", text=f"Interview source two {ref}", created_at="2026-08-15T01:03:00+00:00"),
                update("u3", author="pledis_17", text=f"Official interview source {ref}", created_at="2026-08-15T01:04:00+00:00"),
            ]
            shadow_group_updates(state, items, CONFIGURED)
            events = state.data["event_fusion"]["events"]
            self.assertEqual(len(events), 1)
            self.assertEqual(set(next(iter(events.values()))["member_update_ids"]), {"u1", "u2", "u3"})

    def test_membership_persists_across_restart(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "state.json"
            state = StateStore(path)
            left, right = self.shared_pair("persist")
            shadow_group_updates(state, [left, right], CONFIGURED)
            event_before = copy.deepcopy(state.data["event_fusion"])
            state.save()
            restored = StateStore(path)
            self.assertEqual(restored.data["event_fusion"]["events"], event_before["events"])
            self.assertEqual(restored.data["event_fusion"]["memberships"], event_before["memberships"])

    def test_membership_removal_is_reversible_metadata_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            state = self.state(tmp)
            left, right = self.shared_pair("remove")
            state.archive_update(left)
            archive_before = copy.deepcopy(state.data["archive"])
            shadow_group_updates(state, [left, right], CONFIGURED)
            self.assertTrue(remove_event_membership(state, left.id))
            self.assertNotIn(left.id, state.data["event_fusion"]["memberships"])
            self.assertEqual(state.data["archive"], archive_before)
            self.assertIn(right.id, next(iter(state.data["event_fusion"]["events"].values()))["member_update_ids"])

    def test_membership_reclassification_does_not_touch_source_state(self):
        with tempfile.TemporaryDirectory() as tmp:
            state = self.state(tmp)
            a, b = self.shared_pair("one")
            c, d = self.shared_pair("two")
            c.id, d.id = "c2", "d2"
            c.author, d.author = "hani_berry_1004", "honeyya_hanihae"
            shadow_group_updates(state, [a, b, c, d], CONFIGURED)
            memberships = state.data["event_fusion"]["memberships"]
            event_one, event_two = memberships[a.id]["event_id"], memberships[c.id]["event_id"]
            self.assertNotEqual(event_one, event_two)
            state.data["seen"][a.id] = "keep"
            state.data["update_lifecycle"] = {a.id: {"status": "delivered", "delivery_receipt_id": 77}}
            before_seen = copy.deepcopy(state.data["seen"])
            before_lifecycle = copy.deepcopy(state.data["update_lifecycle"])
            reclassify_event_membership(state, a.id, event_two)
            self.assertEqual(state.data["event_fusion"]["memberships"][a.id]["event_id"], event_two)
            self.assertEqual(state.data["seen"], before_seen)
            self.assertEqual(state.data["update_lifecycle"], before_lifecycle)

    def test_original_update_archive_is_preserved(self):
        with tempfile.TemporaryDirectory() as tmp:
            state = self.state(tmp)
            left, right = self.shared_pair("archive")
            state.archive_update(left); before = copy.deepcopy(state.data["archive"][left.id])
            shadow_group_updates(state, [left, right], CONFIGURED)
            self.assertEqual(state.data["archive"][left.id], before)

    def test_update_lifecycle_is_unaffected(self):
        with tempfile.TemporaryDirectory() as tmp:
            state = self.state(tmp)
            left, right = self.shared_pair("life")
            state.data["update_lifecycle"] = {left.id: {"update_id": left.id, "status": "pending_delivery", "event_id": "processing:old"}}
            before = copy.deepcopy(state.data["update_lifecycle"])
            shadow_group_updates(state, [left, right], CONFIGURED)
            self.assertEqual(state.data["update_lifecycle"], before)

    def test_phase3_checkpoint_is_unaffected(self):
        with tempfile.TemporaryDirectory() as tmp:
            state = self.state(tmp)
            left, right = self.shared_pair("checkpoint")
            state.data["x_retrieval_checkpoints"] = {"cp": {"cursor": "opaque", "complete": False}}
            before = copy.deepcopy(state.data["x_retrieval_checkpoints"])
            shadow_group_updates(state, [left, right], CONFIGURED)
            self.assertEqual(state.data["x_retrieval_checkpoints"], before)

    def test_message_delivery_receipt_is_unaffected(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = MessageDeliveryStore(Path(tmp) / "private-review.sqlite3")
            db.save_plan("draft:one", "hello", ["hello"])
            db.confirm("draft:one", 0, "hello", 4242)
            state = self.state(tmp)
            left, right = self.shared_pair("receipt")
            shadow_group_updates(state, [left, right], CONFIGURED)
            self.assertEqual(db.confirmed_message_id("draft:one", 0, "hello"), 4242)
            db.close()

    def test_external_unconfigured_source_is_not_persisted_as_member(self):
        with tempfile.TemporaryDirectory() as tmp:
            state = self.state(tmp)
            external = update("ext", author="random_external", text="#CARATLAND2026 Super performance")
            configured = update("cfg", text="#CARATLAND2026 Super performance", created_at="2026-08-15T01:01:00+00:00")
            shadow_group_updates(state, [external, configured], CONFIGURED)
            fusion = state.data["event_fusion"]
            self.assertNotIn("ext", fusion["fingerprints"])
            self.assertNotIn("ext", fusion["memberships"])
            self.assertTrue(any(row["decision"] == "source_blocked" for row in fusion["decisions"]))

    def test_ambiguous_updates_stay_separate(self):
        left = update("x1", text="Dokyeom said Jeonghan looked happy after rehearsal")
        right = update("x2", author="honeyya_hanihae", text="Dokyeom said Jeonghan was happy during practice", created_at="2026-08-15T01:10:00+00:00")
        self.assertNotIn(match_fingerprints(build_fingerprint(left), build_fingerprint(right)).decision, SAME)

    def test_strong_shared_reference_groups(self):
        left, right = self.shared_pair("strong")
        match = match_fingerprints(build_fingerprint(left), build_fingerprint(right))
        self.assertIn(match.decision, SAME)
        self.assertIn("shared_external_reference", match.matching_signals)

    def test_time_proximity_alone_never_groups(self):
        left = update("t1", text="Jeonghan airport departure")
        right = update("t2", author="honeyya_hanihae", text="Jeonghan brand campaign poster", created_at="2026-08-15T01:01:00+00:00")
        match = match_fingerprints(build_fingerprint(left), build_fingerprint(right))
        self.assertNotIn(match.decision, SAME)

    def test_unrelated_similar_text_does_not_force_grouping(self):
        left = update("s1", text="Dokyeom said Jeonghan was happy during practice")
        right = update("s2", author="honeyya_hanihae", text="Dokyeom said Jeonghan was happy after rehearsal", created_at="2026-08-15T01:05:00+00:00")
        self.assertNotIn(match_fingerprints(build_fingerprint(left), build_fingerprint(right)).decision, SAME)

    def test_same_event_from_several_sources_forms_one_event(self):
        with tempfile.TemporaryDirectory() as tmp:
            state = self.state(tmp); ref = "https://youtu.be/multi-event"
            items = [
                update("m1", text=f"Interview first detail {ref}"),
                update("m2", author="honeyya_hanihae", text=f"Interview translation {ref}", created_at="2026-08-15T01:02:00+00:00"),
                update("m3", author="pledis_17", text=f"Interview official clip {ref}", created_at="2026-08-15T01:04:00+00:00"),
            ]
            shadow_group_updates(state, items, CONFIGURED)
            self.assertEqual(len(state.data["event_fusion"]["events"]), 1)

    def test_different_moments_from_same_live_remain_distinguishable(self):
        left = update("l1", text="Weverse Live Jeonghan talks about dinner")
        right = update("l2", author="honeyya_hanihae", text="Weverse Live Jeonghan plays a game", created_at="2026-08-15T01:06:00+00:00")
        self.assertNotIn(match_fingerprints(build_fingerprint(left), build_fingerprint(right)).decision, SAME)

    def test_different_fancams_remain_distinct_media(self):
        first = media_asset_id("video", "https://media.example/fancam-a.mp4")
        second = media_asset_id("video", "https://media.example/fancam-b.mp4")
        self.assertNotEqual(first, second)

    def test_different_photos_remain_distinct_media(self):
        self.assertNotEqual(media_asset_id("photo", "https://media.example/a.jpg"), media_asset_id("photo", "https://media.example/b.jpg"))

    def test_different_videos_remain_distinct_media(self):
        self.assertNotEqual(media_asset_id("video", "https://media.example/a.mp4"), media_asset_id("video", "https://media.example/b.mp4"))

    def test_event_id_never_becomes_media_dedupe_identity(self):
        event_id = make_event_id(("u1", "u2"))
        media_id = media_asset_id("video", "https://media.example/a.mp4")
        self.assertNotEqual(event_id, media_id)
        self.assertTrue(event_id.startswith("evt:")); self.assertTrue(media_id.startswith("media:"))

    def test_exact_media_duplicate_identity_is_unchanged(self):
        first = media_asset_id("video", "https://media.example/exact.mp4")
        second = media_asset_id("video", "https://media.example/exact.mp4")
        self.assertEqual(first, second)

    def test_fanfic_state_remains_independent(self):
        with tempfile.TemporaryDirectory() as tmp:
            fic = FicStateStore(Path(tmp) / "fic.sqlite3")
            observation = FicObservation("123", "2/5", "2026-08-15")
            self.assertEqual(fic.classify(observation), "new")
            state = self.state(tmp); left, right = self.shared_pair("fic")
            shadow_group_updates(state, [left, right], CONFIGURED)
            self.assertEqual(fic.classify(observation), "unchanged")
            fic.close()

    def test_private_review_only_configuration_is_preserved(self):
        settings = json.loads((ROOT / "config" / "settings.json").read_text(encoding="utf-8"))
        self.assertIs(settings["runtime"]["review_only"], True)

    def test_realtime_shadow_mode_remains_off_by_default(self):
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("REALTIME_SHADOW_MODE", None)
            self.assertFalse(realtime_shadow_enabled())

    def test_exactly_24_unique_enabled_configured_sources(self):
        data = json.loads((ROOT / "config" / "sources.json").read_text(encoding="utf-8"))
        enabled = [str(item["handle"]).lstrip("@").casefold() for item in data["sources"] if item.get("enabled", True)]
        self.assertEqual(len(enabled), 24)
        self.assertEqual(len(set(enabled)), 24)

    def test_processing_event_key_is_not_semantic_event_id(self):
        with tempfile.TemporaryDirectory() as tmp:
            state = self.state(tmp); left, right = self.shared_pair("identity")
            left.event_key = "conversation:processing-only"; right.event_key = "conversation:processing-only"
            shadow_group_updates(state, [left, right], CONFIGURED)
            event_id = next(iter(state.data["event_fusion"]["events"]))
            self.assertNotEqual(event_id, left.event_key)
            self.assertTrue(event_id.startswith("evt:"))

    def test_shadow_grouping_does_not_mutate_update_or_media(self):
        with tempfile.TemporaryDirectory() as tmp:
            state = self.state(tmp)
            left = update("immut1", text="Interview https://youtu.be/immutable", media=[MediaItem(kind="video", url="https://media.example/one.mp4")])
            right = update("immut2", author="honeyya_hanihae", text="Interview context https://youtu.be/immutable", created_at="2026-08-15T01:02:00+00:00")
            before = copy.deepcopy(left.to_dict())
            shadow_group_updates(state, [left, right], CONFIGURED)
            self.assertEqual(left.to_dict(), before)

    def test_shadow_state_does_not_store_raw_body_or_media_urls(self):
        with tempfile.TemporaryDirectory() as tmp:
            state = self.state(tmp)
            phrase = "UNIQUE_PRIVATE_BODY_PHRASE"
            left = update("privacy1", text=f"{phrase} interview https://youtu.be/private-ref", media=[MediaItem(kind="video", url="https://media.example/private-video.mp4")])
            right = update("privacy2", author="honeyya_hanihae", text="Interview context https://youtu.be/private-ref", created_at="2026-08-15T01:02:00+00:00")
            shadow_group_updates(state, [left, right], CONFIGURED)
            serialized = json.dumps(state.data["event_fusion"], ensure_ascii=False)
            self.assertNotIn(phrase, serialized)
            self.assertNotIn("https://media.example/private-video.mp4", serialized)
            self.assertNotIn("https://youtu.be/private-ref", serialized)

    def test_shadow_mode_is_explicit(self):
        with tempfile.TemporaryDirectory() as tmp:
            state = self.state(tmp)
            self.assertEqual(state.data["event_fusion"]["mode"], EVENT_MODE)
            self.assertEqual(EVENT_MODE, "shadow")

    def test_taxonomy_contains_required_extensible_types(self):
        required = {
            "unknown", "live", "interview", "variety", "reality", "going_seventeen",
            "fansign_or_video_call", "concert", "award_show", "brand_event",
            "airport_or_public_appearance", "official_content", "social_update", "other",
        }
        self.assertTrue(required.issubset(EVENT_TYPES))

    def test_representative_benchmark(self):
        data = json.loads((ROOT / "data" / "event_fusion_benchmark.json").read_text(encoding="utf-8"))
        sources = json.loads((ROOT / "config" / "sources.json").read_text(encoding="utf-8"))["sources"]
        allowed = {str(item["handle"]).lstrip("@").casefold() for item in sources if item.get("enabled", True)}
        self.assertGreaterEqual(len(data["cases"]), 20)
        failures = []
        for case in data["cases"]:
            left, right = benchmark_update(case["left"]), benchmark_update(case["right"])
            if left.author.casefold() not in allowed or right.author.casefold() not in allowed:
                actual = "blocked"
            else:
                decision = match_fingerprints(build_fingerprint(left), build_fingerprint(right)).decision
                actual = "same_event" if decision in SAME else "separate"
            if actual != case["expected"]:
                failures.append((case["id"], case["expected"], actual))
        self.assertEqual(failures, [])


if __name__ == "__main__":
    unittest.main()
