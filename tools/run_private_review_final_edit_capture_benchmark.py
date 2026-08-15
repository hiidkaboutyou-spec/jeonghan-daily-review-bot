from __future__ import annotations

import json
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

from app.final_edit_capture import (
    AWAITING_CONFIRMATION,
    CONFIRMED_FINAL_EDIT,
    FINAL_EDIT_CAPTURE_MODE,
    FinalEditStore,
    fingerprint,
    metadata_contains_private_body,
    privacy_ref,
    record_metadata,
)
from app.user_voice_calibration import AUTO_LEARN, build_calibration_record

ROOT = Path(__file__).parents[1]
NOW = datetime(2026, 8, 15, 12, 0, tzinfo=timezone.utc)


def _run_case(name, fn):
    try:
        ok = bool(fn())
    except Exception as exc:
        print(f"FAIL {name}: {type(exc).__name__}")
        return False
    print(("PASS " if ok else "FAIL ") + name)
    return ok


def main() -> int:
    with tempfile.TemporaryDirectory() as tmp:
        store = FinalEditStore(Path(tmp) / "private-review.sqlite3", ttl_seconds=300)
        chat_ref = privacy_ref("private-review-chat-v1", "-100123")
        draft_text = "جونگهان امروز اومد."
        draft_fp = fingerprint("authoritative-review-draft-v1", draft_text)

        def start(suffix="a", offset=0):
            return store.start_session(
                draft_id=f"draft-{suffix}", update_id=f"u-{suffix}", event_id="evt:1", segment_id="seg:1",
                review_chat_ref=chat_ref, authoritative_review_draft_fingerprint=draft_fp,
                original_factual_fingerprint="f" * 64, shadow_style_candidate_fingerprint="s" * 64,
                content_type="SHORT_REACTION", now=NOW + timedelta(seconds=offset),
            )

        def receive(session, text="جونگهان امروز اومد. 🩷", prompt=101, offset=1):
            store.set_prompt_message(session.session_id, prompt, now=NOW + timedelta(seconds=offset))
            return store.receive_user_text(
                session.session_id, text, current_draft_fingerprint=draft_fp,
                review_chat_ref=chat_ref, now=NOW + timedelta(seconds=offset),
            )

        cases = []

        s1 = start("unconfirmed", 0)
        receive(s1, offset=1)
        cases.append(("01 unconfirmed is not evidence", lambda: store.confirmed_real_final_edit_count() == 0))
        cases.append(("02 awaiting explicit confirmation", lambda: store.get_session(s1.session_id, expire=False).status == AWAITING_CONFIRMATION))
        r1 = store.confirm_session(s1.session_id, current_draft_fingerprint=draft_fp, review_chat_ref=chat_ref, now=NOW + timedelta(seconds=2))
        cases.append(("03 explicit confirmation creates record", lambda: r1 is not None and r1.confirmation_status == CONFIRMED_FINAL_EDIT))
        cases.append(("04 confirmed count increments", lambda: store.confirmed_real_final_edit_count() == 1))

        s2 = start("cancel", 10); receive(s2, prompt=102, offset=11); store.cancel_session(s2.session_id, now=NOW + timedelta(seconds=12))
        cases.append(("05 cancelled edit is not evidence", lambda: store.confirmed_real_final_edit_count() == 1))

        s3 = start("wrong-fp", 20); store.set_prompt_message(s3.session_id, 103, now=NOW + timedelta(seconds=21))
        wrong_fp = store.receive_user_text(s3.session_id, "متن", current_draft_fingerprint="bad", review_chat_ref=chat_ref, now=NOW + timedelta(seconds=21))
        cases.append(("06 wrong draft fingerprint rejected", lambda: wrong_fp is None))
        wrong_chat = store.find_live_by_prompt(103, review_chat_ref="wrong", now=NOW + timedelta(seconds=21))
        cases.append(("07 wrong review chat rejected", lambda: wrong_chat is None))
        right_prompt = store.find_live_by_prompt(103, review_chat_ref=chat_ref, now=NOW + timedelta(seconds=21))
        cases.append(("08 prompt correlation exact", lambda: right_prompt is not None and right_prompt.draft_id == "draft-wrong-fp"))

        sa = start("concurrent-a", 30); store.set_prompt_message(sa.session_id, 201, now=NOW + timedelta(seconds=31))
        sb = start("concurrent-b", 32); store.set_prompt_message(sb.session_id, 202, now=NOW + timedelta(seconds=33))
        cases.append(("09 concurrent A remains bound", lambda: store.find_live_by_prompt(201, review_chat_ref=chat_ref, now=NOW + timedelta(seconds=34)).draft_id == "draft-concurrent-a"))
        cases.append(("10 concurrent B remains bound", lambda: store.find_live_by_prompt(202, review_chat_ref=chat_ref, now=NOW + timedelta(seconds=34)).draft_id == "draft-concurrent-b"))

        sr1 = start("revision", 40); receive(sr1, "جونگهان امروز اومد. 🩷", prompt=301, offset=41)
        first = store.confirm_session(sr1.session_id, current_draft_fingerprint=draft_fp, review_chat_ref=chat_ref, now=NOW + timedelta(seconds=42))
        sr2 = start("revision", 50); receive(sr2, "جونگهان امروز اومد. 🩷🩷", prompt=302, offset=51)
        second = store.confirm_session(sr2.session_id, current_draft_fingerprint=draft_fp, review_chat_ref=chat_ref, now=NOW + timedelta(seconds=52))
        cases.append(("11 revision supersedes previous", lambda: second is not None and first is not None and second.supersedes_final_edit_id == first.final_edit_id))
        cases.append(("12 superseded revision inactive", lambda: not store.get_final_edit(first.final_edit_id).active))
        cases.append(("13 revisions count once per active draft", lambda: store.confirmed_real_final_edit_count() == 2))

        cases.append(("14 final fingerprint stable", lambda: second.final_user_edit_fingerprint == fingerprint("final-user-edit-v1", "جونگهان امروز اومد. 🩷🩷")))
        cases.append(("15 event segment preserved", lambda: second.event_id == "evt:1" and second.segment_id == "seg:1"))
        cases.append(("16 canonical body private sqlite", lambda: store.final_body(second.final_edit_id) == "جونگهان امروز اومد. 🩷🩷" and store.path.name == "private-review.sqlite3"))
        cases.append(("17 metadata body absent", lambda: not metadata_contains_private_body(record_metadata(second))))
        cases.append(("18 category counts body-free", lambda: isinstance(store.confirmed_counts_by_content_type().get("SHORT_REACTION"), int)))

        factual = build_calibration_record(
            update_id="fact", factual_text="جونگهان ساعت 19:30 اومد.", shadow_candidate="جونگهان ساعت 19:30 اومد.",
            final_user_text="جونگهان ساعت 20:30 اومد.", content_type="FACTUAL_INFORMATION", review_action="user_confirmed_final_edit",
        )
        style = build_calibration_record(
            update_id="style", factual_text="جونگهان 오늘 اومد.".replace("오늘", "امروز"), shadow_candidate="جونگهان امروز اومد.",
            final_user_text="جونگهان امروز اومد. 🩷", content_type="SHORT_REACTION", review_action="user_confirmed_final_edit",
        )
        cases.append(("19 factual correction ineligible", lambda: not factual.eligible_for_learning and "factual_correction" in factual.labels))
        cases.append(("20 style-only edit can be candidate", lambda: style.eligible_for_learning and "style_preference" in style.labels))
        cases.append(("21 auto learn remains false", lambda: AUTO_LEARN is False))
        cases.append(("22 capture mode only", lambda: FINAL_EDIT_CAPTURE_MODE == "capture_only"))

        runtime_source = (ROOT / "app" / "final_edit_capture_runtime.py").read_text(encoding="utf-8")
        store_source = (ROOT / "app" / "final_edit_capture.py").read_text(encoding="utf-8")
        cases.append(("23 no seen authority", lambda: "mark_seen(" not in store_source))
        cases.append(("24 no delivery receipt authority", lambda: "MessageDeliveryStore" not in store_source and "telegram_media_delivery" not in store_source))
        cases.append(("25 no retrieval authority", lambda: "collect_window" not in runtime_source and "provider_cursor" not in runtime_source))
        cases.append(("26 no media redesign", lambda: "media_receipt" not in runtime_source and "album_group" not in runtime_source))
        cases.append(("27 Daily-only install boundary", lambda: "final_edit_capture_runtime" in (ROOT / "app" / "sentry_runtime.py").read_text(encoding="utf-8") and "final_edit_capture" not in (ROOT / "app" / "__init__.py").read_text(encoding="utf-8")))

        sources = json.loads((ROOT / "config" / "sources.json").read_text(encoding="utf-8"))["sources"]
        cases.append(("28 configured sources remain 24", lambda: len(sources) == 24 and sum(bool(item.get("enabled", True)) for item in sources) == 24))
        requirements = (ROOT / "requirements.txt").read_text(encoding="utf-8").casefold()
        cases.append(("29 no paid/new infra dependency", lambda: all(token not in requirements for token in ("supabase", "redis", "celery", "pinecone", "qdrant", "weaviate"))))
        cases.append(("30 no synthetic production seed", lambda: "INSERT INTO final_edits" not in runtime_source and "seed" not in runtime_source.casefold()))

        passed = sum(_run_case(name, fn) for name, fn in cases)
        store.close()

    print(f"PRIVATE REVIEW FINAL EDIT CAPTURE BENCHMARK: {passed}/{len(cases)} passed")
    return 0 if passed == len(cases) else 1


if __name__ == "__main__":
    raise SystemExit(main())
