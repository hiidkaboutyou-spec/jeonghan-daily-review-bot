"""Runtime boundary guards for the configured-sources-only non-Fanfic policy.

Collector enforcement is the primary boundary. These guards additionally protect
legacy runnable application classes and durable state created by older releases:
external queued/archive items must not re-enter private review or media recovery,
and bounded complete windows must not be silently sliced before queue/delivery.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Callable

from .main import Application, _parse_state_datetime
from .private_runtime import PrivateReviewApplication
from .style import ensure_rtl_line
from .telegram import main_keyboard
from .x_client import XCollectionError, normalize_handle

logger = logging.getLogger(__name__)
_INSTALLED = False


def _configured_updates(app: Application, updates):
    safe_input = list(updates)
    # A few unit-level tests intentionally construct Application subclasses via
    # object.__new__ to exercise delivery-resume behavior without running the real
    # constructor. Production applications always own a collector; preserve those
    # partial test doubles while keeping every real runtime fail-closed.
    collector = getattr(app, "collector", None)
    if collector is None:
        return safe_input
    filter_fn = getattr(collector, "filter_configured_updates", None)
    if not callable(filter_fn):
        raise RuntimeError("configured-source collector policy is not installed")
    return filter_fn(safe_input)


def _configured_handle(app: Application, value: str) -> str:
    handle = normalize_handle(value)
    if not handle:
        return ""
    checker = getattr(app.collector, "is_configured_source", None)
    if not callable(checker) or not checker(handle):
        return ""
    return handle


def _purge_external_pending(app: Application) -> int:
    queue = app.state.data.get("pending_delivery", [])
    if not isinstance(queue, list):
        app.state.data["pending_delivery"] = []
        return 0
    # Some unit-level callers construct a partial Application solely to exercise
    # queue backoff behavior. Production applications always own a collector; when
    # the collector is intentionally absent there is no retrieval boundary to
    # validate and the legacy queue behavior must remain testable in isolation.
    collector = getattr(app, "collector", None)
    if collector is None:
        return 0
    checker = getattr(collector, "is_configured_source", None)
    if not callable(checker):
        raise RuntimeError("configured-source collector policy is not installed")
    kept: list[dict[str, Any]] = []
    dropped = 0
    for raw in queue:
        if not isinstance(raw, dict):
            dropped += 1
            continue
        author = str(raw.get("author", "") or "")
        if checker(author):
            kept.append(raw)
        else:
            dropped += 1
    if dropped:
        logger.warning("Purged %s non-configured item(s) from durable delivery queue", dropped)
        app.state.data["pending_delivery"] = kept
    return dropped


def _install_application_guards() -> None:
    if Application.__dict__.get("_configured_source_runtime_guarded", False):
        return

    original_deliver_updates = Application.deliver_updates
    original_deliver_pending = Application.deliver_pending
    original_source24 = Application.run_source24

    async def deliver_updates(self, updates, *, force: bool):
        safe = _configured_updates(self, updates)
        if not safe:
            return
        return await original_deliver_updates(self, safe, force=force)

    async def deliver_pending(self):
        _purge_external_pending(self)
        return await original_deliver_pending(self)

    async def run_source24(self, value: str):
        if value == "custom":
            self.show_sources()
            return
        handle = _configured_handle(self, value)
        if not handle:
            self.telegram.send_message(
                "این حساب در لیست منابع تنظیم‌شده نیست؛ دریافت فقط از منابع تنظیم‌شده انجام می‌شود.",
                reply_markup=main_keyboard(),
            )
            return
        return await original_source24(self, handle)

    async def run_recent2h(self):
        self.telegram.send_message(
            "🕑 دارم تمام آپدیت‌های دو ساعت اخیر را دوباره جمع می‌کنم…",
            reply_markup=main_keyboard(),
        )
        end = datetime.now(timezone.utc)
        start = end - timedelta(hours=2)
        updates = await self.collector.collect_window(start, end, max_per_query=200)
        updates = _configured_updates(
            self,
            [item for item in updates if start <= item.created_at < end],
        )
        if getattr(self.collector, "last_errors", []):
            self.telegram.send_message(
                "⚠️ این بازه کامل تأیید نشد؛ موارد بازیابی‌شده را می‌فرستم اما نتیجه کامل حساب نمی‌شود.",
                reply_markup=main_keyboard(),
            )
        if not updates:
            self.telegram.send_message("در دو ساعت اخیر چیزی پیدا نشد.", reply_markup=main_keyboard())
            return
        await self.deliver_updates(updates, force=True)

    async def run_scheduled_scan(self):
        """Legacy runnable path with the same no-truncation/partial-cursor contract."""
        now = datetime.now(timezone.utc)
        last = _parse_state_datetime(self.state.data.get("last_auto_run")) or (
            now - timedelta(hours=2)
        )
        if now - last < timedelta(minutes=10):
            return
        lookback = max(2, int(self.settings.runtime.get("scheduled_lookback_hours", 24)))
        start = max(last - timedelta(minutes=30), now - timedelta(hours=lookback))
        try:
            updates = await self.collector.collect_window(start, now, max_per_query=200)
        except XCollectionError as exc:
            logger.warning("Scheduled X scan failed: %s", exc)
            self._record_x_scan_failure(now)
            return

        fresh = _configured_updates(
            self,
            [item for item in updates if not self.state.is_seen(item.id)],
        )
        # Queue the entire collected window. Delivery is separately bounded by
        # max_auto_items_per_run; retrieval completeness must never be defined by a
        # delivery cap.
        self.state.queue_updates(fresh, force=False)

        if getattr(self.collector, "last_errors", []):
            logger.warning(
                "Scheduled X scan returned partial results (%s paths); cursor retained for retry.",
                len(self.collector.last_errors),
            )
            self._record_x_scan_failure(now)
            return
        self.state.data["last_auto_run"] = now.isoformat()
        self.state.data["last_x_error_notice"] = ""
        self.state.data["x_scan_failure_streak"] = 0

    Application.deliver_updates = deliver_updates
    Application.deliver_pending = deliver_pending
    Application.run_source24 = run_source24
    Application.run_recent2h = run_recent2h
    Application.run_scheduled_scan = run_scheduled_scan
    Application._configured_source_runtime_guarded = True


def _install_private_guards() -> None:
    # Use the class' own marker, not getattr(): Application's marker is inherited
    # and previously made this subclass guard silently skip installation.
    if PrivateReviewApplication.__dict__.get("_configured_source_runtime_guarded", False):
        return

    original_deliver_updates = PrivateReviewApplication.deliver_updates
    original_run_search = PrivateReviewApplication.run_search

    async def deliver_updates(self, updates, *, force: bool):
        safe = _configured_updates(self, updates)
        if not safe:
            return
        return await original_deliver_updates(self, safe, force=force)

    async def run_search(self, query: str):
        # Older releases could index external X accounts in the private SQLite
        # archive. Filter those historical rows at read time so a stale local hit
        # cannot re-enter candidate selection after the collector policy is fixed.
        original_archive_search: Callable[..., Any] = self.archive_db.search

        def configured_archive_search(*args, **kwargs):
            return _configured_updates(self, original_archive_search(*args, **kwargs))

        self.archive_db.search = configured_archive_search
        try:
            return await original_run_search(self, query)
        finally:
            self.archive_db.search = original_archive_search

    PrivateReviewApplication.deliver_updates = deliver_updates
    PrivateReviewApplication.run_search = run_search
    PrivateReviewApplication._configured_source_runtime_guarded = True


def _install_webhook_source24_guard() -> None:
    # Imported lazily after the base/private classes have been patched. This avoids
    # making configured-source policy depend on webhook hosting while still closing
    # the conversational/custom 24h path used by production.
    from .webhook_aware_assistant import WebhookAwarePersonalAssistant

    if WebhookAwarePersonalAssistant.__dict__.get("_configured_source24_guarded", False):
        return
    original_source24 = WebhookAwarePersonalAssistant.run_source24

    async def run_source24(self, value: str):
        if value == "custom":
            self.show_sources()
            return
        handle = _configured_handle(self, value)
        if not handle:
            self.telegram.send_message(
                ensure_rtl_line(
                    "این حساب در لیست منابع تنظیم‌شده نیست؛ ۲۴ ساعت فقط برای منابع تنظیم‌شده قابل دریافت است."
                ),
                reply_markup=self.telegram_main_keyboard() if hasattr(self, "telegram_main_keyboard") else main_keyboard(),
            )
            return
        return await original_source24(self, handle)

    WebhookAwarePersonalAssistant.run_source24 = run_source24
    WebhookAwarePersonalAssistant._configured_source24_guarded = True


def install() -> None:
    global _INSTALLED
    if _INSTALLED:
        return
    _install_application_guards()
    _install_private_guards()
    _install_webhook_source24_guard()
    _INSTALLED = True


install()
