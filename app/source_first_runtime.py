from __future__ import annotations

from datetime import datetime, timedelta, timezone

from .source_first_inbox_ui import (
    source_first_draft_keyboard,
    source_first_keyboard,
    source_first_text,
)
from .source_first_queue import SourceFirstQueueStore
from .telegram import inline_keyboard
from .x_client import XCollectionError

_INSTALLED_ATTR = "_phase5_source_first_installed"


def install(application_cls) -> None:
    """Make source-first review the normal private inbox without changing delivery.

    The old chronological inbox stays reachable as an explicit compatibility
    view. Phase 4 completeness is displayed as shadow evidence only and never
    becomes a delivery/cursor authority here.
    """
    if getattr(application_cls, _INSTALLED_ATTR, False):
        return

    original_init = application_cls.__init__
    original_show_inbox = application_cls.show_inbox
    original_handle_callback = application_cls.handle_callback
    original_handle_draft_action = application_cls.handle_draft_action
    original_deliver_updates = application_cls.deliver_updates

    def patched_init(self, settings):
        original_init(self, settings)
        db_path = settings.state_path.with_name("private-review.sqlite3")
        self.source_first_queue = SourceFirstQueueStore(db_path)
        self.source_first_queue.sync(settings.sources)

    def show_source_first_inbox(self, *, message_id: int | None = None) -> None:
        session_id = self.source_first_queue.sync(self.settings.sources)
        snapshot = self.source_first_queue.snapshot(session_id)
        text = source_first_text(snapshot)
        markup = source_first_keyboard(snapshot)
        if message_id:
            self.telegram.edit_message_text(message_id, text, reply_markup=markup)
        else:
            self.telegram.send_message(text, reply_markup=markup)

    def patched_show_inbox(self, *, status: str = "pending", page: int = 0, message_id: int | None = None) -> None:
        if status != "pending":
            original_show_inbox(self, status=status, page=page, message_id=message_id)
            return
        show_source_first_inbox(self, message_id=message_id)

    def open_source_first_draft(self, message_id: int) -> None:
        session_id = self.source_first_queue.sync(self.settings.sources)
        snapshot = self.source_first_queue.snapshot(session_id)
        if not snapshot.current_draft_id or not snapshot.active_source:
            show_source_first_inbox(self, message_id=message_id)
            return
        draft = self.state.get_draft(snapshot.current_draft_id)
        if draft is None:
            self.telegram.edit_message_text(
                message_id,
                "این پیش‌نویس دیگر در حافظه نیست؛ صف را دوباره همگام کن.",
                reply_markup=inline_keyboard([[("◀️ برگشت", "sq:home")]]),
            )
            return
        item = self.inbox.get(draft.id)
        category = item.category if item is not None else "general"
        details = (
            f"\n\nمنبع {snapshot.active_position}/{snapshot.total_sources}: "
            f"@{snapshot.active_source} · پست {snapshot.current_item_number}/{snapshot.current_item_total}"
            f" · دسته: {category}"
        )
        self.telegram.edit_message_text(
            message_id,
            draft.caption + details,
            reply_markup=source_first_draft_keyboard(draft.id, snapshot.active_source),
        )

    async def retry_source_first_source(self, source: str, message_id: int) -> None:
        session_id = self.source_first_queue.sync(self.settings.sources)
        snapshot = self.source_first_queue.snapshot(session_id)
        source = str(source or "").strip().lstrip("@").casefold()
        if not source or not session_id:
            show_source_first_inbox(self, message_id=message_id)
            return
        if source not in {item.source for item in snapshot.sources}:
            self.telegram.edit_message_text(
                message_id,
                "این منبع عضو صف فعلی نیست.",
                reply_markup=inline_keyboard([[("◀️ برگشت", "sq:home")]]),
            )
            return

        self.source_first_queue.begin_retry(session_id, source)
        self.telegram.edit_message_text(
            message_id,
            f"🔁 فقط @{source} را دوباره برای ۲۴ ساعت اخیر بررسی می‌کنم…",
            reply_markup=inline_keyboard([[("◀️ برگشت به صف", "sq:home")]]),
        )
        end = datetime.now(timezone.utc)
        start = end - timedelta(hours=24)
        try:
            updates = await self.collector.collect_source(source, start, end)
            errors = list(getattr(self.collector, "last_errors", []) or [])
            if errors:
                raise XCollectionError("source retry returned partial retrieval evidence")
            fresh = sorted(
                (item for item in updates if not self.state.is_seen(item.id)),
                key=lambda item: (item.created_at, item.id),
            )
            if fresh:
                # Never force replay here: a source retry is retrieval recovery, not
                # permission to duplicate already-seen Telegram deliveries.
                await original_deliver_updates(self, fresh, force=False)
            self.source_first_queue.finish_retry(session_id, source, success=True)
        except XCollectionError as exc:
            self.source_first_queue.finish_retry(
                session_id, source, success=False, error=type(exc).__name__
            )
        self.source_first_queue.sync(self.settings.sources)
        show_source_first_inbox(self, message_id=message_id)

    async def patched_handle_callback(self, callback):
        if not self.telegram.is_admin_callback(callback):
            return
        data = str(callback.get("data", ""))
        callback_id = str(callback.get("id", ""))
        message_id = int(callback.get("message", {}).get("message_id", 0) or 0)

        if data == "sq:home":
            self._answer_callback_safely(callback_id)
            show_source_first_inbox(self, message_id=message_id)
            return
        if data == "sq:open":
            self._answer_callback_safely(callback_id)
            open_source_first_draft(self, message_id)
            return
        if data.startswith("sq:defer:"):
            source = data.split(":", 2)[2]
            self._answer_callback_safely(callback_id)
            session_id = self.source_first_queue.sync(self.settings.sources)
            self.source_first_queue.defer(session_id, source)
            show_source_first_inbox(self, message_id=message_id)
            return
        if data.startswith("sq:resume:"):
            source = data.split(":", 2)[2]
            self._answer_callback_safely(callback_id)
            session_id = self.source_first_queue.sync(self.settings.sources)
            self.source_first_queue.resume(session_id, source)
            show_source_first_inbox(self, message_id=message_id)
            return
        if data.startswith("sq:retry:"):
            source = data.split(":", 2)[2]
            self._answer_callback_safely(callback_id)
            await retry_source_first_source(self, source, message_id)
            return
        if data.startswith("sq:legacy:"):
            parts = data.split(":")
            status = parts[2] if len(parts) > 2 else "pending"
            try:
                page = int(parts[3])
            except (IndexError, ValueError):
                page = 0
            self._answer_callback_safely(callback_id)
            original_show_inbox(self, status=status, page=page, message_id=message_id)
            return
        if data.startswith("inbox:page:"):
            # Preserve navigation inside the explicit chronological compatibility
            # view; otherwise the inherited callback would route back to Phase 5.
            parts = data.split(":")
            status = parts[2] if len(parts) > 2 else "pending"
            try:
                page = int(parts[3])
            except (IndexError, ValueError):
                page = 0
            self._answer_callback_safely(callback_id)
            original_show_inbox(self, status=status, page=page, message_id=message_id)
            return
        await original_handle_callback(self, callback)

    async def patched_handle_draft_action(self, action: str, draft_id: str, message_id: int) -> None:
        await original_handle_draft_action(self, action, draft_id, message_id)
        self.source_first_queue.sync(self.settings.sources)

    async def patched_deliver_updates(self, updates, *, force: bool) -> None:
        await original_deliver_updates(self, updates, force=force)
        self.source_first_queue.sync(self.settings.sources)

    application_cls.__init__ = patched_init
    application_cls.show_inbox = patched_show_inbox
    application_cls.handle_callback = patched_handle_callback
    application_cls.handle_draft_action = patched_handle_draft_action
    application_cls.deliver_updates = patched_deliver_updates
    application_cls.show_source_first_inbox = show_source_first_inbox
    application_cls.open_source_first_draft = open_source_first_draft
    application_cls.retry_source_first_source = retry_source_first_source
    setattr(application_cls, _INSTALLED_ATTR, True)
