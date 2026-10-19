from __future__ import annotations

import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from app.final_edit_capture import FinalEditStore, fingerprint
from app.models import Draft, Update
from tools.export_human_preference_pairs import build_preference_rows, write_jsonl_atomic


class HumanPreferenceExportTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.db = self.root / "private-review.sqlite3"
        self.store = FinalEditStore(self.db)

    def tearDown(self) -> None:
        self.store.close()
        self.temp.cleanup()

    def make_state(self, *, caption: str = "جونگهان امروز اومد.") -> tuple[dict, Draft, Update]:
        update = Update(
            id="u1",
            url="https://x.com/source/status/1",
            author="source",
            author_name="Source",
            text="Jeonghan came today.",
            created_at=datetime(2026, 10, 5, tzinfo=timezone.utc),
            lang="en",
            category="SHORT_REACTION",
        )
        draft = Draft(
            id="d1",
            update_id=update.id,
            event_key="evt1",
            caption=caption,
            created_at="2026-10-05T00:00:00+00:00",
        )
        return {
            "drafts": {draft.id: draft.to_dict()},
            "archive": {update.id: update.to_dict()},
        }, draft, update

    def confirm_edit(self, draft: Draft, final: str, *, eligible: str = "eligible") -> str:
        draft_fp = fingerprint("authoritative-review-draft-v1", draft.caption)
        session = self.store.start_session(
            draft_id=draft.id,
            update_id=draft.update_id,
            review_chat_ref="private",
            authoritative_review_draft_fingerprint=draft_fp,
            content_type="SHORT_REACTION",
        )
        self.store.receive_user_text(
            session.session_id,
            final,
            current_draft_fingerprint=draft_fp,
            review_chat_ref="private",
        )
        record = self.store.confirm_session(
            session.session_id,
            current_draft_fingerprint=draft_fp,
            review_chat_ref="private",
            calibration_eligible=eligible,
        )
        self.assertIsNotNone(record)
        return record.final_edit_id

    def test_exports_user_confirmed_pair_in_dpo_shape(self):
        state, draft, _ = self.make_state()
        final_id = self.confirm_edit(draft, "جونگهان امروز اومد 🩷")

        rows, summary = build_preference_rows(state, self.store)

        self.assertEqual(summary["pairs_exported"], 1)
        self.assertEqual(len(rows), 1)
        row = rows[0]
        self.assertIn("Jeonghan came today.", row["prompt"])
        self.assertEqual(row["chosen"], "جونگهان امروز اومد 🩷")
        self.assertEqual(row["rejected"], draft.caption)
        self.assertEqual(row["metadata"]["pair_id"], final_id)
        self.assertEqual(row["metadata"]["provenance"], "user_confirmed_final_edit")

    def test_default_export_excludes_undecided_edits(self):
        state, draft, _ = self.make_state()
        self.confirm_edit(draft, "جونگهان امروز اومد 🩷", eligible="undecided")
        rows, summary = build_preference_rows(state, self.store)
        self.assertEqual(rows, [])
        self.assertEqual(summary["records_considered"], 0)

        rows, summary = build_preference_rows(state, self.store, include_undecided=True)
        self.assertEqual(summary["pairs_exported"], 1)

    def test_ineligible_edit_is_never_exported_even_with_undecided_flag(self):
        state, draft, _ = self.make_state()
        self.confirm_edit(draft, "جونگهان امروز اومد 🩷", eligible="ineligible")
        rows, summary = build_preference_rows(
            state,
            self.store,
            include_undecided=True,
        )
        self.assertEqual(rows, [])
        self.assertEqual(summary["pairs_exported"], 0)

    def test_stale_draft_fingerprint_is_not_exported(self):
        state, draft, _ = self.make_state()
        self.confirm_edit(draft, "جونگهان امروز اومد 🩷")
        state["drafts"][draft.id]["caption"] = "این draft بعداً عوض شد."

        rows, summary = build_preference_rows(state, self.store)

        self.assertEqual(rows, [])
        self.assertEqual(summary["stale_draft"], 1)

    def test_atomic_writer_keeps_utf8_private_text(self):
        out = self.root / "exports" / "pairs.jsonl"
        rows = [{"prompt": "سلام", "chosen": "بهتر", "rejected": "بدتر"}]
        write_jsonl_atomic(rows, out)
        parsed = json.loads(out.read_text(encoding="utf-8").strip())
        self.assertEqual(parsed["chosen"], "بهتر")
        self.assertFalse(out.with_name(out.name + ".tmp").exists())


if __name__ == "__main__":
    unittest.main()
