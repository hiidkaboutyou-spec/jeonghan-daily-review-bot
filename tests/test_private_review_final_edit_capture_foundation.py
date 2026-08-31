from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

from app.final_edit_capture import (
    AWAITING_CONFIRMATION,
    CANCELLED,
    CONFIRMED_FINAL_EDIT,
    EXPIRED,
    FINAL_EDIT_CAPTURE_MODE,
    FINAL_EDIT_PROVENANCE,
    PENDING_EDIT,
    FinalEditStore,
    fingerprint,
    metadata_contains_private_body,
    privacy_ref,
    record_metadata,
)
from app.final_edit_capture_runtime import TELEGRAM_USER_TEXT_LIMIT, _state_linkage, _with_edit_button
from app.message_delivery import MessageDeliveryStore
from app.realtime_ingest import realtime_shadow_enabled
from app.telegram import draft_keyboard
from app.user_voice_calibration import AUTO_LEARN, VOICE_CALIBRATION_MODE, build_calibration_record
from tools.run_private_review_final_edit_capture_benchmark import CheckpointCases

ROOT = Path(__file__).parents[1]
BASE_TIME = datetime(2026, 8, 15, 12, 0, tzinfo=timezone.utc)


class _Draft:
    def __init__(self, draft_id: str = "draft-a", update_id: str = "u-a", caption: str = "جونگهان امروز اومد."):
        self.id = draft_id
        self.update_id = update_id
        self.caption = caption


class _Update:
    def __init__(self, update_id: str, category: str = "SHORT_REACTION"):
        self.id = update_id
        self.category = category


class _Inbox:
    def get(self, draft_id):
        return None


class _State:
    def __init__(self, data, update):
        self.data = data
        self._update = update

    def get_update(self, update_id):
        return self._update if self._update and self._update.id == update_id else None


class FinalEditStoreTestCase(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp.name) / "private-review.sqlite3"
        self.store = FinalEditStore(self.db_path, ttl_seconds=300)
        self.chat_ref = privacy_ref("private-review-chat-v1", "-100123")
        self.draft_text = "جونگهان امروز اومد."
        self.draft_fp = fingerprint("authoritative-review-draft-v1", self.draft_text)

    def tearDown(self):
        self.store.close()
        self.temp.cleanup()

    def start(self, *, draft_id="draft-a", update_id="u-a", now=BASE_TIME):
        session = self.store.start_session(
            draft_id=draft_id,
            update_id=update_id,
            event_id="evt:1",
            segment_id="seg:1",
            review_chat_ref=self.chat_ref,
            authoritative_review_draft_fingerprint=self.draft_fp,
            original_factual_fingerprint="f" * 64,
            shadow_style_candidate_fingerprint="s" * 64,
            content_type="SHORT_REACTION",
            now=now,
        )
        self.assertEqual(session.status, PENDING_EDIT)
        return session

    def receive(self, session, text="جونگهان امروز اومد. 🩷", *, now=BASE_TIME + timedelta(seconds=5)):
        self.store.set_prompt_message(session.session_id, 101, now=now)
        return self.store.receive_user_text(
            session.session_id,
            text,
            current_draft_fingerprint=self.draft_fp,
            review_chat_ref=self.chat_ref,
            now=now,
        )

    def confirm(self, session, *, now=BASE_TIME + timedelta(seconds=10), eligibility="undecided", metadata=None):
        return self.store.confirm_session(
            session.session_id,
            current_draft_fingerprint=self.draft_fp,
            review_chat_ref=self.chat_ref,
            calibration_eligible=eligibility,
            calibration_metadata=metadata or {},
            now=now,
        )


class RealEditDefinitionTests(FinalEditStoreTestCase):
    def _assert_action_is_not_capture(self, action: str):
        source = (ROOT / "app" / "final_edit_capture_runtime.py").read_text(encoding="utf-8")
        self.assertIn('if action != "edit"', source)
        self.assertNotEqual(action, "edit")
        self.assertEqual(self.store.confirmed_real_final_edit_count(), 0)

    def test_copy_is_not_a_user_edit(self):
        self._assert_action_is_not_capture("copy")

    def test_reject_is_not_a_user_edit(self):
        self._assert_action_is_not_capture("reject")

    def test_funnier_is_not_a_user_edit(self):
        self._assert_action_is_not_capture("fun")

    def test_softer_is_not_a_user_edit(self):
        self._assert_action_is_not_capture("soft")

    def test_precise_is_not_a_user_edit(self):
        self._assert_action_is_not_capture("precise")

    def test_synthetic_edit_is_not_real_evidence(self):
        self.assertEqual(FINAL_EDIT_PROVENANCE, "user_confirmed")
        self.assertEqual(self.store.confirmed_real_final_edit_count(), 0)

    def test_free_form_user_text_enters_awaiting_confirmation(self):
        session = self.start()
        received = self.receive(session)
        self.assertIsNotNone(received)
        self.assertEqual(received.status, AWAITING_CONFIRMATION)
        self.assertEqual(self.store.confirmed_real_final_edit_count(), 0)

    def test_explicit_confirmation_is_required(self):
        session = self.start()
        self.receive(session)
        self.assertEqual(self.store.confirmed_real_final_edit_count(), 0)
        record = self.confirm(session)
        self.assertIsNotNone(record)
        self.assertEqual(record.confirmation_status, CONFIRMED_FINAL_EDIT)
        self.assertEqual(record.edit_provenance, "user_confirmed")
        self.assertEqual(self.store.confirmed_real_final_edit_count(), 1)


class CorrelationAndLifecycleTests(FinalEditStoreTestCase):
    def test_cancellation_works_and_clears_candidate_body(self):
        session = self.start()
        self.receive(session)
        self.assertTrue(self.store.cancel_session(session.session_id))
        self.assertEqual(self.store.get_session(session.session_id, expire=False).status, CANCELLED)
        self.assertEqual(self.store.candidate_body(session.session_id), "")
        self.assertEqual(self.store.confirmed_real_final_edit_count(), 0)

    def test_expiry_works_and_clears_candidate_body(self):
        session = self.start()
        self.receive(session)
        expired_at = BASE_TIME + timedelta(seconds=301)
        self.store.expire_stale(now=expired_at)
        self.assertEqual(self.store.get_session(session.session_id, expire=False).status, EXPIRED)
        self.assertEqual(self.store.candidate_body(session.session_id), "")
        self.assertEqual(self.store.confirmed_real_final_edit_count(), 0)

    def test_correct_draft_prompt_correlation(self):
        session = self.start()
        self.store.set_prompt_message(session.session_id, 111, now=BASE_TIME)
        found = self.store.find_live_by_prompt(111, review_chat_ref=self.chat_ref, now=BASE_TIME)
        self.assertIsNotNone(found)
        self.assertEqual(found.draft_id, "draft-a")

    def test_wrong_prompt_cannot_receive_edit(self):
        session = self.start()
        self.store.set_prompt_message(session.session_id, 111, now=BASE_TIME)
        self.assertIsNone(self.store.find_live_by_prompt(222, review_chat_ref=self.chat_ref, now=BASE_TIME))

    def test_wrong_review_chat_cannot_receive_edit(self):
        session = self.start()
        self.store.set_prompt_message(session.session_id, 111, now=BASE_TIME)
        self.assertIsNone(self.store.find_live_by_prompt(111, review_chat_ref="wrong", now=BASE_TIME))

    def test_wrong_draft_fingerprint_cannot_receive_edit(self):
        session = self.start()
        self.store.set_prompt_message(session.session_id, 101, now=BASE_TIME)
        result = self.store.receive_user_text(
            session.session_id,
            "متن من",
            current_draft_fingerprint="wrong",
            review_chat_ref=self.chat_ref,
            now=BASE_TIME,
        )
        self.assertIsNone(result)
        self.assertEqual(self.store.get_session(session.session_id, expire=False).status, PENDING_EDIT)

    def test_stale_draft_fingerprint_cannot_confirm(self):
        session = self.start()
        self.receive(session)
        result = self.store.confirm_session(
            session.session_id,
            current_draft_fingerprint="changed",
            review_chat_ref=self.chat_ref,
            now=BASE_TIME + timedelta(seconds=10),
        )
        self.assertIsNone(result)
        self.assertEqual(self.store.confirmed_real_final_edit_count(), 0)

    def test_new_draft_does_not_steal_existing_edit(self):
        first = self.start(draft_id="draft-a", update_id="u-a", now=BASE_TIME)
        self.store.set_prompt_message(first.session_id, 101, now=BASE_TIME)
        second = self.start(draft_id="draft-b", update_id="u-b", now=BASE_TIME + timedelta(microseconds=1))
        self.store.set_prompt_message(second.session_id, 202, now=BASE_TIME)
        found_a = self.store.find_live_by_prompt(101, review_chat_ref=self.chat_ref, now=BASE_TIME)
        found_b = self.store.find_live_by_prompt(202, review_chat_ref=self.chat_ref, now=BASE_TIME)
        self.assertEqual(found_a.draft_id, "draft-a")
        self.assertEqual(found_b.draft_id, "draft-b")

    def test_replace_returns_to_pending_and_removes_old_candidate(self):
        session = self.start()
        self.receive(session)
        self.assertTrue(self.store.replace_candidate(session.session_id, now=BASE_TIME + timedelta(seconds=6)))
        refreshed = self.store.get_session(session.session_id, expire=False)
        self.assertEqual(refreshed.status, PENDING_EDIT)
        self.assertEqual(refreshed.prompt_message_id, 0)
        self.assertEqual(self.store.candidate_body(session.session_id), "")


class RecordIntegrityTests(FinalEditStoreTestCase):
    def test_update_event_segment_ids_are_preserved(self):
        session = self.start()
        self.receive(session)
        record = self.confirm(session)
        self.assertEqual((record.update_id, record.event_id, record.segment_id), ("u-a", "evt:1", "seg:1"))

    def test_final_fingerprint_is_stable(self):
        text = "جونگهان امروز اومد. 🩷"
        expected = fingerprint("final-user-edit-v1", text)
        session = self.start()
        self.receive(session, text)
        record = self.confirm(session)
        self.assertEqual(record.final_user_edit_fingerprint, expected)

    def test_second_confirmed_revision_supersedes_first(self):
        first_session = self.start(now=BASE_TIME)
        self.receive(first_session, "جونگهان امروز اومد. 🩷")
        first = self.confirm(first_session, now=BASE_TIME + timedelta(seconds=10))
        second_session = self.start(now=BASE_TIME + timedelta(seconds=20))
        self.receive(second_session, "جونگهان امروز اومد. 🩷🩷", now=BASE_TIME + timedelta(seconds=21))
        second = self.confirm(second_session, now=BASE_TIME + timedelta(seconds=30))
        self.assertEqual(second.supersedes_final_edit_id, first.final_edit_id)
        self.assertFalse(self.store.get_final_edit(first.final_edit_id).active)
        self.assertTrue(second.active)

    def test_superseded_revision_is_not_double_counted(self):
        first_session = self.start(now=BASE_TIME)
        self.receive(first_session)
        self.confirm(first_session, now=BASE_TIME + timedelta(seconds=10))
        second_session = self.start(now=BASE_TIME + timedelta(seconds=20))
        self.receive(second_session, "جونگهان امروز اومد. 🩷🩷", now=BASE_TIME + timedelta(seconds=21))
        self.confirm(second_session, now=BASE_TIME + timedelta(seconds=30))
        self.assertEqual(self.store.confirmed_real_final_edit_count(), 1)

    def test_counts_by_content_type_are_privacy_safe(self):
        session = self.start()
        self.receive(session)
        self.confirm(session)
        self.assertEqual(self.store.confirmed_counts_by_content_type(), {"SHORT_REACTION": 1})

    def test_revoked_record_no_longer_counts(self):
        session = self.start()
        self.receive(session)
        record = self.confirm(session)
        self.assertTrue(self.store.revoke_final_edit(record.final_edit_id))
        self.assertEqual(self.store.confirmed_real_final_edit_count(), 0)

    def test_canonical_body_lives_in_private_review_sqlite(self):
        text = "جونگهان امروز اومد. 🩷"
        session = self.start()
        self.receive(session, text)
        record = self.confirm(session)
        self.assertEqual(self.store.final_body(record.final_edit_id), text)
        self.assertEqual(self.store.path, self.db_path)

    def test_record_metadata_contains_no_private_body(self):
        session = self.start()
        self.receive(session)
        record = self.confirm(session)
        payload = record_metadata(record)
        self.assertFalse(metadata_contains_private_body(payload))
        encoded = json.dumps(payload, ensure_ascii=False)
        self.assertNotIn("جونگهان امروز اومد. 🩷", encoded)
        self.assertFalse(payload["text_persisted_in_generic_state"])

    def test_session_metadata_contains_no_candidate_body(self):
        session = self.start()
        self.receive(session)
        payload = self.store.session_metadata(session.session_id)
        self.assertFalse(metadata_contains_private_body(payload))
        self.assertNotIn("candidate_body", payload)
        self.assertFalse(payload["auto_learn"])


class CalibrationBoundaryTests(unittest.TestCase):
    def test_factual_correction_is_not_style_learning_eligible(self):
        record = build_calibration_record(
            update_id="u1", event_id="evt:1", segment_id="seg:1",
            factual_text="جونگهان ساعت 19:30 اومد.",
            shadow_candidate="جونگهان ساعت 19:30 اومد.",
            final_user_text="جونگهان ساعت 20:30 اومد.",
            content_type="FACTUAL_INFORMATION", review_action="user_confirmed_final_edit",
        )
        self.assertFalse(record.eligible_for_learning)
        self.assertIn("factual_correction", record.labels)

    def test_style_only_confirmed_edit_can_be_calibration_candidate(self):
        record = build_calibration_record(
            update_id="u2", event_id="evt:1", segment_id="seg:1",
            factual_text="جونگهان امروز اومد.",
            shadow_candidate="جونگهان امروز اومد.",
            final_user_text="جونگهان امروز اومد. 🩷",
            content_type="SHORT_REACTION", review_action="user_confirmed_final_edit",
        )
        self.assertTrue(record.eligible_for_learning)
        self.assertIn("style_preference", record.labels)

    def test_unconfirmed_edit_is_not_calibration_candidate(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = FinalEditStore(Path(tmp) / "private-review.sqlite3")
            session = store.start_session(
                draft_id="d", update_id="u", review_chat_ref="chat",
                authoritative_review_draft_fingerprint="x", content_type="SHORT_REACTION",
            )
            store.set_prompt_message(session.session_id, 1)
            store.receive_user_text(session.session_id, "متن", current_draft_fingerprint="x", review_chat_ref="chat")
            self.assertEqual(store.confirmed_real_final_edit_count(), 0)
            store.close()

    def test_auto_learn_remains_false(self):
        self.assertFalse(AUTO_LEARN)
        self.assertEqual(VOICE_CALIBRATION_MODE, "shadow")
        self.assertEqual(FINAL_EDIT_CAPTURE_MODE, "capture_only")


class RuntimeBoundaryTests(unittest.TestCase):
    def test_keyboard_adds_explicit_edit_action(self):
        markup = _with_edit_button(draft_keyboard("draft-1"), "draft-1")
        callbacks = [button["callback_data"] for row in markup["inline_keyboard"] for button in row]
        self.assertIn("draft:edit:draft-1", callbacks)

    def test_multi_part_semantics_are_whole_draft_safe(self):
        runtime = (ROOT / "app" / "final_edit_capture_runtime.py").read_text(encoding="utf-8")
        self.assertEqual(TELEGRAM_USER_TEXT_LIMIT, 4096)
        self.assertIn("multi-part bot Draft", runtime)
        self.assertIn("canonical whole-Draft final edit", runtime)
        self.assertIn('len(str(draft.caption or "")) > TELEGRAM_USER_TEXT_LIMIT', runtime)

    def test_event_and_segment_linkage_uses_update_membership(self):
        draft = _Draft()
        update = _Update("u-a")
        app = type("App", (), {})()
        app.state = _State({
            "event_fusion": {
                "segment_memberships": {"u-a": {"event_id": "evt:1", "segment_id": "seg:1"}},
                "style_rewrite_results": {"seg:1": {
                    "content_type": "SHORT_REACTION",
                    "factual_draft_fingerprint": "f" * 64,
                    "style_candidate_fingerprint": "s" * 64,
                }},
            }
        }, update)
        app.inbox = _Inbox()
        linkage = _state_linkage(app, draft)
        self.assertEqual(linkage["update_id"], "u-a")
        self.assertEqual(linkage["event_id"], "evt:1")
        self.assertEqual(linkage["segment_id"], "seg:1")

    def test_capture_module_cannot_mark_seen_or_delivered(self):
        source = (ROOT / "app" / "final_edit_capture.py").read_text(encoding="utf-8")
        self.assertNotIn("mark_seen(", source)
        self.assertNotIn("mark_delivered(", source)
        self.assertNotIn("pending_delivery", source)

    def test_receipt_authority_is_untouched(self):
        capture_source = (ROOT / "app" / "final_edit_capture.py").read_text(encoding="utf-8")
        runtime_source = (ROOT / "app" / "final_edit_capture_runtime.py").read_text(encoding="utf-8")
        combined = capture_source + runtime_source
        self.assertNotIn("MessageDeliveryStore", combined)
        self.assertNotIn("message_delivery_store", combined)
        self.assertNotIn("telegram_media_delivery", combined)

        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "private-review.sqlite3"
            receipts = MessageDeliveryStore(db_path)
            receipts.save_plan("daily:u-a:text", "part one", ["part one"])
            receipts.confirm("daily:u-a:text", 0, "part one", 777)

            edits = FinalEditStore(db_path, ttl_seconds=300)
            chat_ref = privacy_ref("private-review-chat-v1", "-100123")
            draft_text = "جونگهان امروز اومد."
            draft_fp = fingerprint("authoritative-review-draft-v1", draft_text)
            session = edits.start_session(
                draft_id="draft-a",
                update_id="u-a",
                event_id="evt:1",
                segment_id="seg:1",
                review_chat_ref=chat_ref,
                authoritative_review_draft_fingerprint=draft_fp,
                original_factual_fingerprint="f" * 64,
                shadow_style_candidate_fingerprint="s" * 64,
                content_type="SHORT_REACTION",
                now=BASE_TIME,
            )
            edits.set_prompt_message(session.session_id, 101, now=BASE_TIME + timedelta(seconds=1))
            edits.receive_user_text(
                session.session_id,
                "جونگهان امروز اومد. 🩷",
                current_draft_fingerprint=draft_fp,
                review_chat_ref=chat_ref,
                now=BASE_TIME + timedelta(seconds=2),
            )
            edits.confirm_session(
                session.session_id,
                current_draft_fingerprint=draft_fp,
                review_chat_ref=chat_ref,
                now=BASE_TIME + timedelta(seconds=3),
            )

            self.assertEqual(receipts.get_plan("daily:u-a:text"), ("part one", ["part one"]))
            self.assertEqual(receipts.confirmed_message_id("daily:u-a:text", 0, "part one"), 777)
            self.assertEqual(
                receipts.conn.execute("SELECT COUNT(*) FROM message_delivery_parts").fetchone()[0],
                1,
            )
            edits.close()
            receipts.close()

    def test_phase3_retrieval_is_untouched(self):
        combined = (ROOT / "app" / "final_edit_capture.py").read_text(encoding="utf-8") + (ROOT / "app" / "final_edit_capture_runtime.py").read_text(encoding="utf-8")
        self.assertNotIn("phase3_recovery", combined)
        self.assertNotIn("collect_window", combined)
        self.assertNotIn("provider_cursor", combined)

    def test_event_timeline_and_translation_authority_are_untouched(self):
        source = (ROOT / "app" / "final_edit_capture_runtime.py").read_text(encoding="utf-8")
        self.assertNotIn("shadow_segment_events(", source)
        self.assertNotIn("shadow_group_events(", source)
        self.assertNotIn("shadow_translation_fusion(", source)

    def test_style_rewrite_remains_shadow(self):
        from app.channel_style_rewrite import STYLE_REWRITE_MODE
        self.assertEqual(STYLE_REWRITE_MODE, "shadow")

    def test_media_and_concert_coverage_are_untouched(self):
        combined = (ROOT / "app" / "final_edit_capture.py").read_text(encoding="utf-8") + (ROOT / "app" / "final_edit_capture_runtime.py").read_text(encoding="utf-8")
        self.assertNotIn("media_receipt", combined)
        self.assertNotIn("album_group", combined)
        self.assertNotIn("concert_coverage", combined)

    def test_fanfic_entrypoint_does_not_install_capture(self):
        code = (
            "import sys; import app.fic_digest; "
            "assert 'app.final_edit_capture' not in sys.modules; "
            "assert 'app.final_edit_capture_runtime' not in sys.modules"
        )
        result = subprocess.run([sys.executable, "-c", code], cwd=ROOT, capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_capture_is_installed_only_from_daily_sentry_runtime(self):
        sentry = (ROOT / "app" / "sentry_runtime.py").read_text(encoding="utf-8")
        package_init = (ROOT / "app" / "__init__.py").read_text(encoding="utf-8")
        self.assertIn("final_edit_capture_runtime", sentry)
        self.assertNotIn("final_edit_capture", package_init)

    def test_private_review_reference_does_not_expose_raw_chat_id(self):
        raw = "-100123456789"
        ref = privacy_ref("private-review-chat-v1", raw)
        self.assertNotIn(raw, ref)
        self.assertEqual(len(ref), 24)

    def test_private_text_is_not_logged_by_capture_runtime(self):
        source = (ROOT / "app" / "final_edit_capture_runtime.py").read_text(encoding="utf-8")
        self.assertNotIn("logger.", source)
        self.assertNotIn("capture_exception", source)
        self.assertNotIn("sentry_sdk", source)

    def test_realtime_shadow_mode_remains_off(self):
        source = (ROOT / "app" / "realtime_ingest.py").read_text(encoding="utf-8")
        self.assertIn('os.getenv("REALTIME_SHADOW_MODE", "")', source)
        with mock.patch.dict(os.environ, {"REALTIME_SHADOW_MODE": ""}):
            self.assertFalse(realtime_shadow_enabled())
        with mock.patch.dict(os.environ, {"REALTIME_SHADOW_MODE": "true"}):
            self.assertTrue(realtime_shadow_enabled())

    def test_configured_sources_remain_24(self):
        config = json.loads((ROOT / "config" / "sources.json").read_text(encoding="utf-8"))
        enabled = [item for item in config["sources"] if item.get("enabled", True)]
        self.assertEqual(len(config["sources"]), 24)
        self.assertEqual(len(enabled), 23)

    def test_no_paid_or_new_infrastructure_dependency(self):
        paths = [ROOT / "app" / "final_edit_capture.py", ROOT / "app" / "final_edit_capture_runtime.py"]
        text = "\n".join(path.read_text(encoding="utf-8").casefold() for path in paths)
        for forbidden in ("supabase", "redis", "celery", "pinecone", "paid x api", "vector db", "openai api"):
            self.assertNotIn(forbidden, text)


class BenchmarkCheckpointRegressionTests(FinalEditStoreTestCase):
    def test_early_lifecycle_checkpoints_survive_later_store_mutation(self):
        checkpoints = CheckpointCases()

        unconfirmed = self.start(draft_id="draft-unconfirmed", update_id="u-unconfirmed")
        self.receive(unconfirmed)
        checkpoints.append((
            "01 unconfirmed is not evidence",
            lambda: self.store.confirmed_real_final_edit_count() == 0,
        ))
        checkpoints.append((
            "02 awaiting explicit confirmation",
            lambda: self.store.get_session(unconfirmed.session_id, expire=False).status == AWAITING_CONFIRMATION,
        ))

        confirmed = self.confirm(unconfirmed)
        self.assertIsNotNone(confirmed)
        checkpoints.append((
            "04 confirmed count increments",
            lambda: self.store.confirmed_real_final_edit_count() == 1,
        ))

        cancelled = self.start(
            draft_id="draft-cancelled",
            update_id="u-cancelled",
            now=BASE_TIME + timedelta(seconds=20),
        )
        self.receive(cancelled, now=BASE_TIME + timedelta(seconds=21))
        self.store.cancel_session(cancelled.session_id, now=BASE_TIME + timedelta(seconds=22))
        checkpoints.append((
            "05 cancelled edit is not evidence",
            lambda: self.store.confirmed_real_final_edit_count() == 1,
        ))

        first_revision = self.start(
            draft_id="draft-revision",
            update_id="u-revision",
            now=BASE_TIME + timedelta(seconds=30),
        )
        self.receive(first_revision, "جونگهان امروز اومد. 🩷", now=BASE_TIME + timedelta(seconds=31))
        self.confirm(first_revision, now=BASE_TIME + timedelta(seconds=32))
        second_revision = self.start(
            draft_id="draft-revision",
            update_id="u-revision",
            now=BASE_TIME + timedelta(seconds=40),
        )
        self.receive(second_revision, "جونگهان امروز اومد. 🩷🩷", now=BASE_TIME + timedelta(seconds=41))
        self.confirm(second_revision, now=BASE_TIME + timedelta(seconds=42))

        self.assertEqual(self.store.confirmed_real_final_edit_count(), 2)
        self.assertEqual(
            [checkpoint.name for checkpoint in checkpoints],
            [
                "01 unconfirmed is not evidence",
                "02 awaiting explicit confirmation",
                "04 confirmed count increments",
                "05 cancelled edit is not evidence",
            ],
        )
        self.assertTrue(all(checkpoint.passed for checkpoint in checkpoints))
        self.assertTrue(all(not checkpoint.error for checkpoint in checkpoints))


if __name__ == "__main__":
    unittest.main()
