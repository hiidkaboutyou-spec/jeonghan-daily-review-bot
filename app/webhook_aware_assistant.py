from __future__ import annotations

import logging
import os
from datetime import datetime, timedelta, timezone

from .personal_assistant import PersonalAssistantReviewApplication, assistant_main_keyboard
from .webhook_runtime_utils import derive_runtime_secret, maintenance_url_from_webhook
from .x_client import XCollectionError, normalize_handle

logger = logging.getLogger(__name__)
WEBHOOK_DELEGATED_EXIT_CODE = 3


def github_actions_polling_only() -> bool:
    """Return true when production intentionally runs without an external host."""
    return os.getenv("ASSISTANT_RUNTIME_MODE", "").strip().lower() == "github_actions_polling"


class WebhookAwarePersonalAssistant(PersonalAssistantReviewApplication):
    """Keep automatic monitoring alive with or without an external webhook host.

    If a healthy webhook runtime exists, GitHub Actions sends it one authenticated
    maintenance wake. If the webhook is stale, unreachable, or unusable, Actions
    reclaims Telegram polling without dropping queued updates and performs the full
    assistant pass itself. This prevents a dead hosting experiment from silently
    disabling automatic Jeonghan monitoring.
    """

    async def run(self) -> int:
        if github_actions_polling_only():
            # Render/Koyeb are intentionally not part of this deployment. Remove any
            # stale webhook without dropping queued updates, then use Telegram
            # getUpdates immediately instead of waiting for a dead host first.
            self.telegram.ensure_polling_mode()
            self.state.data["polling_mode_checked"] = datetime.now(timezone.utc).isoformat()
            await super().run()
            return 0

        try:
            info = self.telegram.api("getWebhookInfo", timeout=30, attempts=2) or {}
        except Exception as exc:
            logger.warning(
                "Could not inspect Telegram webhook ownership; using polling fallback (%s)",
                type(exc).__name__,
            )
            await super().run()
            return 0

        webhook_url = str(info.get("url", "") or "").strip() if isinstance(info, dict) else ""
        if not webhook_url:
            await super().run()
            return 0

        maintenance_url = maintenance_url_from_webhook(webhook_url)
        if maintenance_url:
            secret = derive_runtime_secret(self.settings.telegram_token)
            try:
                # Render Free can need roughly a minute to wake from idle. Give the
                # webhook enough time to cold-start before reclaiming polling, or a
                # healthy sleeping service would be treated as dead every cycle.
                response = self.telegram.session.post(
                    maintenance_url,
                    headers={"X-Assistant-Secret": secret},
                    timeout=90,
                )
                if 200 <= response.status_code < 300:
                    logger.info(
                        "Webhook runtime owns Telegram; maintenance wake accepted with HTTP %s",
                        response.status_code,
                    )
                    return WEBHOOK_DELEGATED_EXIT_CODE
                logger.warning(
                    "Webhook maintenance returned HTTP %s; reclaiming polling for this monitor pass",
                    response.status_code,
                )
            except Exception as exc:
                logger.warning(
                    "Webhook maintenance failed (%s); reclaiming polling for this monitor pass",
                    type(exc).__name__,
                )
        else:
            logger.warning("Telegram webhook URL is unusable; reclaiming polling for this monitor pass")

        # deleteWebhook(drop_pending_updates=false) keeps Telegram's queued updates.
        # If Telegram itself is temporarily unavailable, let the run fail so the next
        # scheduled run retries instead of pretending monitoring succeeded.
        self.telegram.ensure_polling_mode()
        self.state.data["polling_mode_checked"] = datetime.now(timezone.utc).isoformat()
        await super().run()
        return 0

    async def run_recent2h(self) -> None:
        """Replay a complete two-hour configured-source window without truncation."""
        self.telegram.send_message(
            "🕑 دارم تمام آپدیت‌های دو ساعت اخیر را دوباره جمع می‌کنم…",
            reply_markup=assistant_main_keyboard(),
        )
        end = datetime.now(timezone.utc)
        start = end - timedelta(hours=2)
        updates = await self.collector.collect_window(start, end, max_per_query=200)
        updates = sorted(
            (item for item in updates if start <= item.created_at < end),
            key=lambda item: (item.created_at, item.id),
        )
        if getattr(self.collector, "last_errors", []):
            self.telegram.send_message(
                "⚠️ این بازه از X کامل تأیید نشد؛ موارد بازیابی‌شده را می‌فرستم، اما نتیجه را کامل حساب نمی‌کنم.",
                reply_markup=assistant_main_keyboard(),
            )
        if not updates:
            self.telegram.send_message(
                "در دو ساعت اخیر چیزی پیدا نشد.",
                reply_markup=assistant_main_keyboard(),
            )
            return
        await self.deliver_updates(updates, force=True)

    async def run_source24(self, value: str) -> None:
        """Replay a proven-complete 24h source window without a post-delivery cap."""
        if value == "custom":
            self.state.set_awaiting(self.settings.admin_user_id, "source")
            self.telegram.send_message(
                "لینک X یا یوزرنیم منبع را بفرست.",
                reply_markup=assistant_main_keyboard(),
            )
            return
        handle = normalize_handle(value)
        if not handle:
            self.telegram.send_message(
                "یوزرنیم منبع درست نیست.",
                reply_markup=assistant_main_keyboard(),
            )
            return
        self.telegram.send_message(
            f"🗂 دارم ۲۴ ساعت کامل @{handle} را از قدیمی به جدید می‌گیرم…",
            reply_markup=assistant_main_keyboard(),
        )
        end = datetime.now(timezone.utc)
        start = end - timedelta(hours=24)
        updates = await self.collector.collect_source(handle, start, end)
        updates = sorted(
            (item for item in updates if start <= item.created_at < end),
            key=lambda item: (item.created_at, item.id),
        )
        if not updates:
            self.telegram.send_message(
                f"برای @{handle} در ۲۴ ساعت گذشته چیزی پیدا نشد.",
                reply_markup=assistant_main_keyboard(),
            )
            return
        await self.deliver_updates(updates, force=True)

    async def run_scheduled_scan(self) -> None:
        """Run the production scan at the configured near-real-time cadence."""
        now = datetime.now(timezone.utc)
        last = self._state_datetime("last_auto_run") or (now - timedelta(hours=2))
        last_attempt = self._state_datetime("last_auto_attempt")
        interval = max(
            1,
            int(self.settings.runtime.get("scheduled_min_interval_minutes", 12)),
        )
        if last_attempt and now - last_attempt < timedelta(minutes=interval):
            return
        # Persisted even when X returns partial results, so a temporary X rate
        # limit cannot make every chained Actions pass repeat the full 24h scan.
        self.state.data["last_auto_attempt"] = now.isoformat()

        lookback = max(2, int(self.settings.runtime.get("scheduled_lookback_hours", 24)))
        start = max(last - timedelta(minutes=30), now - timedelta(hours=lookback))
        try:
            updates = await self.collector.collect_window(start, now, max_per_query=200)
        except XCollectionError as exc:
            logger.warning("Scheduled X scan failed: %s", exc)
            self._record_x_scan_failure(now)
            return

        fresh = [item for item in updates if not self.state.is_seen(item.id)]
        fresh.sort(key=lambda item: (item.created_at, item.id))
        # Retrieval completeness is determined by the source collector, not by a
        # delivery cap. Queue every fresh item from a complete/partial collection;
        # max_auto_items_per_run can still drain the durable queue in bounded batches.
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

    def _record_x_scan_failure(self, now: datetime) -> None:
        """Retry transient X gaps silently before alarming the private inbox."""
        try:
            streak = max(0, int(self.state.data.get("x_scan_failure_streak", 0))) + 1
        except (TypeError, ValueError):
            streak = 1
        self.state.data["x_scan_failure_streak"] = streak
        if streak >= 3:
            self._notify_x_failure_if_due(now)

    def _state_datetime(self, key: str) -> datetime | None:
        raw = self.state.data.get(key)
        if not raw:
            return None
        try:
            value = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
        except ValueError:
            return None
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)
