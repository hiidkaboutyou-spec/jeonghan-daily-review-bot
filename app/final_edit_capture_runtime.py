from __future__ import annotations

from typing import Any

from .final_edit_capture import (
    AWAITING_CONFIRMATION,
    FINAL_EDIT_CAPTURE_MODE,
    PENDING_EDIT,
    FinalEditSession,
    FinalEditStore,
    fingerprint,
    privacy_ref,
)
from .telegram import inline_keyboard

# Telegram user-authored text messages are bounded by Telegram itself. A logical
# Draft that was split into multiple bot messages is therefore not silently treated
# as one editable fragment in this foundation.
TELEGRAM_USER_TEXT_LIMIT = 4096


def _review_chat_ref(app: Any) -> str:
    return privacy_ref("private-review-chat-v1", getattr(app.settings, "review_chat_id", ""))


def _draft_fingerprint(caption: str) -> str:
    return fingerprint("authoritative-review-draft-v1", str(caption or ""))


def _state_linkage(app: Any, draft: Any) -> dict[str, str]:
    update_id = str(getattr(draft, "update_id", "") or "")
    fusion = app.state.data.get("event_fusion")
    fusion = fusion if isinstance(fusion, dict) else {}
    event_id = ""
    segment_id = ""
    memberships = fusion.get("segment_memberships")
    if isinstance(memberships, dict):
        member = memberships.get(update_id)
        if isinstance(member, dict):
            event_id = str(member.get("event_id", "") or "")[:80]
            segment_id = str(member.get("segment_id", "") or "")[:80]
    if not event_id:
        memberships = fusion.get("memberships")
        if isinstance(memberships, dict):
            member = memberships.get(update_id)
            if isinstance(member, dict):
                event_id = str(member.get("event_id", "") or "")[:80]

    style_meta: dict[str, Any] = {}
    style_results = fusion.get("style_rewrite_results")
    if segment_id and isinstance(style_results, dict):
        raw = style_results.get(segment_id)
        if isinstance(raw, dict):
            style_meta = raw

    update = app.state.get_update(update_id)
    content_type = str(style_meta.get("content_type", "") or "")
    if not content_type and update is not None:
        content_type = str(getattr(update, "category", "") or "")
    if not content_type:
        item = app.inbox.get(str(getattr(draft, "id", "") or ""))
        content_type = str(getattr(item, "category", "") or "") if item is not None else "general"
    return {
        "update_id": update_id,
        "event_id": event_id,
        "segment_id": segment_id,
        "content_type": content_type[:80] or "general",
        "original_factual_fingerprint": str(style_meta.get("factual_draft_fingerprint", "") or "")[:64],
        "shadow_style_candidate_fingerprint": str(style_meta.get("style_candidate_fingerprint", "") or "")[:64],
    }


def _add_edit_row(markup: dict[str, Any]) -> dict[str, Any]:
    result = {key: value for key, value in (markup or {}).items() if key != "inline_keyboard"}
    rows = [list(row) for row in (markup or {}).get("inline_keyboard", [])]
    if any(str(button.get("callback_data", "")).startswith("draft:edit:") for row in rows for button in row):
        result["inline_keyboard"] = rows
        return result
    edit_button = {"text": "✏️ ویرایش نهایی", "callback_data": ""}
    # The caller-specific wrapper fills the exact Draft ID after building markup.
    reject_index = next(
        (index for index, row in enumerate(rows) if any(str(item.get("callback_data", "")).startswith("draft:reject:") for item in row)),
        len(rows),
    )
    rows.insert(reject_index, [edit_button])
    result["inline_keyboard"] = rows
    return result


def _with_edit_button(markup: dict[str, Any], draft_id: str) -> dict[str, Any]:
    value = _add_edit_row(markup)
    for row in value.get("inline_keyboard", []):
        for button in row:
            if button.get("text") == "✏️ ویرایش نهایی" and not button.get("callback_data"):
                button["callback_data"] = f"draft:edit:{draft_id}"
    return value


def _confirmation_keyboard(session_id: str) -> dict[str, Any]:
    return inline_keyboard(
        [
            [("✅ تأیید ادیت نهایی", f"fedit:confirm:{session_id}")],
            [("✏️ جایگزین", f"fedit:replace:{session_id}"), ("❌ لغو", f"fedit:cancel:{session_id}")],
        ]
    )


def _cancel_keyboard(session_id: str) -> dict[str, Any]:
    return inline_keyboard([[('❌ لغو ویرایش', f"fedit:cancel:{session_id}")]])


def _send_edit_prompt(app: Any, session: FinalEditSession) -> int:
    sent = app.telegram.send_message(
        "✏️ متن نهاییِ کامل این پیش‌نویس را با Reply به همین پیام بفرست.\n\n"
        "این ویرایش فقط بعد از تأیید جداگانه ثبت می‌شود و هیچ چیزی را خودکار منتشر یا یادگیری نمی‌کند.",
        reply_markup=_cancel_keyboard(session.session_id),
    )
    message_id = int((sent or {}).get("message_id", 0) or 0)
    if message_id <= 0 or not app.final_edit_store.set_prompt_message(session.session_id, message_id):
        app.final_edit_store.cancel_session(session.session_id)
        return 0
    return message_id


def _start_edit(app: Any, draft_id: str) -> None:
    draft = app.state.get_draft(str(draft_id))
    if draft is None:
        app.telegram.send_message("این پیش‌نویس دیگر در حافظهٔ private review نیست؛ چیزی ثبت نشد.")
        return
    # A multi-part bot Draft must never turn one user-authored Telegram fragment into
    # a canonical whole-Draft final edit. Keep the existing Draft untouched instead.
    if len(str(draft.caption or "")) > TELEGRAM_USER_TEXT_LIMIT:
        app.telegram.send_message(
            "این پیش‌نویس منطقی چندبخشی است. برای جلوگیری از ثبت اشتباهِ یک تکه به‌عنوان متن نهاییِ کل Draft، "
            "ویرایش نهاییِ آن در این foundation ثبت نمی‌شود. خود پیش‌نویس بدون تغییر باقی ماند."
        )
        return
    linkage = _state_linkage(app, draft)
    session = app.final_edit_store.start_session(
        draft_id=draft.id,
        update_id=draft.update_id,
        event_id=linkage["event_id"],
        segment_id=linkage["segment_id"],
        review_chat_ref=_review_chat_ref(app),
        authoritative_review_draft_fingerprint=_draft_fingerprint(draft.caption),
        original_factual_fingerprint=linkage["original_factual_fingerprint"],
        shadow_style_candidate_fingerprint=linkage["shadow_style_candidate_fingerprint"],
        content_type=linkage["content_type"],
    )
    if not _send_edit_prompt(app, session):
        app.telegram.send_message("ثبت ویرایش نهایی فعلاً در دسترس نیست؛ Draft اصلی دست‌نخورده ماند.")


def _reply_message_id(message: dict[str, Any]) -> int:
    reply = message.get("reply_to_message")
    if not isinstance(reply, dict):
        return 0
    return int(reply.get("message_id", 0) or 0)


def _receive_edit_reply(app: Any, message: dict[str, Any]) -> bool:
    if not app.telegram.is_admin_message(message):
        return False
    prompt_id = _reply_message_id(message)
    if prompt_id <= 0:
        return False
    session = app.final_edit_store.find_live_by_prompt(
        prompt_id,
        review_chat_ref=_review_chat_ref(app),
    )
    if session is None:
        return False
    draft = app.state.get_draft(session.draft_id)
    if draft is None:
        app.final_edit_store.cancel_session(session.session_id)
        app.telegram.send_message("Draft هدف دیگر وجود ندارد؛ این ویرایش لغو شد و هیچ evidenceی ثبت نشد.")
        return True
    current_fp = _draft_fingerprint(draft.caption)
    if current_fp != session.authoritative_review_draft_fingerprint:
        app.final_edit_store.cancel_session(session.session_id)
        app.telegram.send_message(
            "این Draft بعد از شروع ویرایش تغییر کرده؛ برای جلوگیری از اتصال اشتباه، session لغو شد. دوباره روی «ویرایش نهایی» بزن."
        )
        return True
    text = str(message.get("text", "") or message.get("caption", "")).strip()
    received = app.final_edit_store.receive_user_text(
        session.session_id,
        text,
        current_draft_fingerprint=current_fp,
        review_chat_ref=_review_chat_ref(app),
    )
    if received is None:
        app.telegram.send_message("متن ویرایش معتبر نبود یا session منقضی شده؛ هیچ final editی ثبت نشد.")
        return True
    app.telegram.send_message(
        "🧾 پیش‌نمایش ویرایش نهایی — هنوز ثبت نشده\n\n"
        + text
        + "\n\nفقط اگر همین نسخه واقعاً نسخهٔ نهایی توست، تأییدش کن.",
        reply_markup=_confirmation_keyboard(received.session_id),
    )
    return True


def _translation_conflict(app: Any, segment_id: str) -> bool:
    fusion = app.state.data.get("event_fusion")
    if not isinstance(fusion, dict):
        return False
    results = fusion.get("translation_fusion_results")
    if not isinstance(results, dict):
        return False
    row = results.get(segment_id)
    if not isinstance(row, dict):
        return False
    conflicts = row.get("conflict_update_ids")
    unresolved = row.get("unresolved_conflicts")
    return bool(conflicts) or bool(unresolved)


def _reconstruct_calibration_metadata(app: Any, session: FinalEditSession, final_text: str) -> tuple[str, dict[str, Any]]:
    """Classify a confirmed edit without persisting/reusing private bodies elsewhere.

    Translation/Style shadow texts are reconstructed transiently from the same
    canonical update/state. Their persisted fingerprints must match the capture
    session before the edit is considered a calibration candidate.
    """
    if not session.segment_id or not session.original_factual_fingerprint or not session.shadow_style_candidate_fingerprint:
        return "undecided", {}
    update = app.state.get_update(session.update_id)
    if update is None:
        return "undecided", {}
    try:
        from .channel_style_rewrite import preview_factual_results, rewrite_shadow_candidate
        from .user_voice_calibration import AUTO_LEARN, build_calibration_record

        if AUTO_LEARN:
            return "undecided", {}
        handles = [
            str(item.get("handle", "")).lstrip("@").strip()
            for item in getattr(app.settings, "sources", [])
            if isinstance(item, dict) and item.get("enabled", True) and str(item.get("handle", "")).strip()
        ]
        factual_results = preview_factual_results(app.state, [update], handles)
        factual = next((item for item in factual_results if item.segment_id == session.segment_id), None)
        if factual is None or not factual.fused_factual_text:
            return "undecided", {}
        style = rewrite_shadow_candidate(
            app.memory,
            factual.fused_factual_text,
            event_id=session.event_id or factual.event_id,
            segment_id=session.segment_id,
            content_type=session.content_type,
        )
        if style.factual_fingerprint != session.original_factual_fingerprint:
            return "undecided", {}
        if style.candidate_fingerprint != session.shadow_style_candidate_fingerprint or not style.candidate_text:
            return "undecided", {}
        record = build_calibration_record(
            update_id=session.update_id,
            factual_text=factual.fused_factual_text,
            shadow_candidate=style.candidate_text,
            final_user_text=final_text,
            content_type=session.content_type,
            event_id=session.event_id or factual.event_id,
            segment_id=session.segment_id,
            traceable=True,
            translation_conflict=_translation_conflict(app, session.segment_id),
            review_action="user_confirmed_final_edit",
        )
        return ("eligible" if record.eligible_for_learning else "ineligible"), record.metadata()
    except Exception:
        # Capture must remain independently useful even if optional calibration
        # analysis cannot be reconstructed. Do not leak private text to logs/errors.
        return "undecided", {}


def _handle_final_edit_callback(app: Any, callback: dict[str, Any]) -> bool:
    if not app.telegram.is_admin_callback(callback):
        return False
    data = str(callback.get("data", ""))
    if not data.startswith("fedit:"):
        return False
    parts = data.split(":", 2)
    if len(parts) != 3:
        return True
    action, session_id = parts[1], parts[2]
    callback_id = str(callback.get("id", ""))
    try:
        app._answer_callback_safely(callback_id)
    except Exception:
        pass
    session = app.final_edit_store.get_session(session_id)
    if session is None:
        app.telegram.send_message("این session ویرایش دیگر معتبر نیست؛ Draft اصلی دست‌نخورده است.")
        return True

    if action == "cancel":
        app.final_edit_store.cancel_session(session_id)
        app.telegram.send_message("ویرایش لغو شد؛ هیچ final edit یا calibration evidenceی ثبت نشد.")
        return True

    if action == "replace":
        if session.status != AWAITING_CONFIRMATION or not app.final_edit_store.replace_candidate(session_id):
            app.telegram.send_message("این نسخه دیگر قابل جایگزینی نیست؛ دوباره از روی Draft شروع کن.")
            return True
        refreshed = app.final_edit_store.get_session(session_id, expire=False)
        if refreshed is None or not _send_edit_prompt(app, refreshed):
            app.final_edit_store.cancel_session(session_id)
            app.telegram.send_message("جایگزینی لغو شد؛ Draft اصلی دست‌نخورده ماند.")
        return True

    if action != "confirm" or session.status != AWAITING_CONFIRMATION:
        return True
    draft = app.state.get_draft(session.draft_id)
    if draft is None:
        app.final_edit_store.cancel_session(session_id)
        app.telegram.send_message("Draft هدف دیگر وجود ندارد؛ هیچ final editی ثبت نشد.")
        return True
    current_fp = _draft_fingerprint(draft.caption)
    if current_fp != session.authoritative_review_draft_fingerprint:
        app.final_edit_store.cancel_session(session_id)
        app.telegram.send_message("Draft هدف تغییر کرده؛ برای جلوگیری از cross-draft mixup این تأیید رد شد.")
        return True
    final_text = app.final_edit_store.candidate_body(session_id)
    if not final_text:
        return True
    eligibility, calibration_metadata = _reconstruct_calibration_metadata(app, session, final_text)
    record = app.final_edit_store.confirm_session(
        session_id,
        current_draft_fingerprint=current_fp,
        review_chat_ref=_review_chat_ref(app),
        calibration_eligible=eligibility,
        calibration_metadata=calibration_metadata,
    )
    if record is None:
        app.telegram.send_message("تأیید ثبت نشد؛ Draft و وضعیت delivery هیچ تغییری نکرد.")
        return True
    app.telegram.send_message(
        "✅ ادیت نهاییِ تأییدشده به‌صورت خصوصی ثبت شد.\n"
        f"Final Edit: {record.final_edit_id}\n"
        "این فقط review data است: نه منتشر شد، نه Style Rewrite را فعال کرد و نه AUTO_LEARN را روشن کرد."
    )
    return True


def install() -> None:
    """Install only into the normal Daily private-review runtime.

    This module is intentionally imported by sentry_runtime, not app.__init__, so
    `python -m app.fic_digest` does not require or install Final Edit Capture.
    """
    from . import private_inbox_ui, private_runtime, telegram
    from .personal_assistant import PersonalAssistantReviewApplication

    if getattr(PersonalAssistantReviewApplication, "_final_edit_capture_installed", False):
        return

    original_init = PersonalAssistantReviewApplication.__init__
    original_message = PersonalAssistantReviewApplication.handle_message
    original_callback = PersonalAssistantReviewApplication.handle_callback
    original_draft_action = PersonalAssistantReviewApplication.handle_draft_action

    def init(self, settings):
        original_init(self, settings)
        self.final_edit_store = FinalEditStore(settings.state_path.with_name("private-review.sqlite3"))
        # Expiration is lazy/bounded; startup cleanup never changes Draft or delivery state.
        self.final_edit_store.expire_stale()

    async def handle_message(self, message):
        try:
            if _receive_edit_reply(self, message):
                return
        except Exception:
            # Fail independently; never make ordinary private-review handling depend
            # on capture. The body is intentionally absent from logs/telemetry.
            self.telegram.send_message("ثبت ویرایش با خطای فنی روبه‌رو شد؛ Draft اصلی امن است.")
            return
        await original_message(self, message)

    async def handle_callback(self, callback):
        try:
            if _handle_final_edit_callback(self, callback):
                return
        except Exception:
            self.telegram.send_message("عملیات ویرایش نهایی انجام نشد؛ پردازش عادی Draft دست‌نخورده است.")
            return
        await original_callback(self, callback)

    async def handle_draft_action(self, action: str, draft_id: str, message_id: int) -> None:
        if action != "edit":
            await original_draft_action(self, action, draft_id, message_id)
            return
        try:
            _start_edit(self, draft_id)
        except Exception:
            self.telegram.send_message("ویرایش نهایی فعلاً در دسترس نیست؛ Draft اصلی دست‌نخورده ماند.")

    PersonalAssistantReviewApplication.__init__ = init
    PersonalAssistantReviewApplication.handle_message = handle_message
    PersonalAssistantReviewApplication.handle_callback = handle_callback
    PersonalAssistantReviewApplication.handle_draft_action = handle_draft_action
    PersonalAssistantReviewApplication._final_edit_capture_installed = True
    PersonalAssistantReviewApplication.final_edit_capture_mode = FINAL_EDIT_CAPTURE_MODE

    original_draft_keyboard = telegram.draft_keyboard
    original_inbox_keyboard = private_inbox_ui.inbox_draft_keyboard

    def draft_keyboard(draft_id: str):
        return _with_edit_button(original_draft_keyboard(draft_id), draft_id)

    def inbox_draft_keyboard(draft_id: str, status: str, page: int):
        return _with_edit_button(original_inbox_keyboard(draft_id, status, page), draft_id)

    telegram.draft_keyboard = draft_keyboard
    private_runtime.draft_keyboard = draft_keyboard
    private_inbox_ui.inbox_draft_keyboard = inbox_draft_keyboard
    private_runtime.inbox_draft_keyboard = inbox_draft_keyboard


__all__ = ["install", "TELEGRAM_USER_TEXT_LIMIT"]
