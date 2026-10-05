from __future__ import annotations

import logging
import os
from datetime import datetime, timedelta, timezone

from .personal_assistant import PersonalAssistantReviewApplication, assistant_main_keyboard
from .translation_lease import TranslationLeaseError, build_translation_lease
from .webhook_runtime_utils import derive_runtime_secret, maintenance_url_from_webhook
from .x_client import XCollectionError, normalize_handle

logger = logging.getLogger(__name__)
WEBHOOK_DELEGATED_EXIT_CODE = 3
WEBHOOK_MAINTENANCE_FAILED_EXIT_CODE = 4


def github_actions_polling_only() -> bool:
    """Return true when production intentionally runs without an external host."""
    return os.getenv("ASSISTANT_RUNTIME_MODE", "").strip().lower() == "github_actions_polling"


def github_actions_auto_mode() -> bool:
    """Return true when Actions must defer to an existing webhook owner."""
    return os.getenv("ASSISTANT_RUNTIME_MODE", "").strip().lower() == "github_actions_auto"


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

        safe_auto = github_actions_auto_mode()
        try:
            info = self.telegram.api("getWebhookInfo", timeout=30, attempts=2) or {}
        except Exception as exc:
            if safe_auto:
                logger.error(
                    "Could not inspect Telegram webhook ownership (%s); refusing to race a possible webhook owner.",
                    type(exc).__name__,
                )
                return WEBHOOK_MAINTENANCE_FAILED_EXIT_CODE
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

        runtime_settings = getattr(self.settings, "runtime", {}) or {}
        trusted_origins = [
            str(item).strip()
            for item in runtime_settings.get("trusted_webhook_origins", [])
            if str(item).strip()
        ]
        maintenance_url = maintenance_url_from_webhook(
            webhook_url,
            trusted_origins=trusted_origins,
        )
        if maintenance_url:
            secret = derive_runtime_secret(self.settings.telegram_token)
            request_kwargs: dict[str, object] = {
                "headers": {"X-Assistant-Secret": secret},
                "timeout": 90,
                # A credential-bearing maintenance request must never carry custom
                # headers/body to a redirected host.
                "allow_redirects": False,
            }
            gemini_api_key = str(
                getattr(self.settings, "gemini_api_key", "") or ""
            ).strip()
            if gemini_api_key:
                if not trusted_origins:
                    logger.error(
                        "Gemini is configured but no trusted webhook origin exists; refusing to attach a translation lease."
                    )
                    if safe_auto:
                        return WEBHOOK_MAINTENANCE_FAILED_EXIT_CODE
                else:
                    try:
                        lease = build_translation_lease(
                            gemini_api_key,
                            str(
                                getattr(
                                    self.settings,
                                    "gemini_model",
                                    "gemini-3.5-flash-lite",
                                )
                                or "gemini-3.5-flash-lite"
                            ).strip(),
                        )
                    except TranslationLeaseError as exc:
                        logger.error(
                            "Could not build translation lease (%s); refusing unsafe provider handoff.",
                            type(exc).__name__,
                        )
                        if safe_auto:
                            return WEBHOOK_MAINTENANCE_FAILED_EXIT_CODE
                    else:
                        request_kwargs["json"] = {"translation_lease": lease}
            try:
                response = self.telegram.session.post(
                    maintenance_url,
                    **request_kwargs,
                )
                if 200 <= response.status_code < 300:
                    logger.info(
                        "Webhook runtime owns Telegram; maintenance wake accepted with HTTP %s",
                        response.status_code,
                    )
                    return WEBHOOK_DELEGATED_EXIT_CODE
                if safe_auto:
                    logger.error(
                        "Webhook maintenance returned HTTP %s; retaining webhook ownership instead of starting a competing poller.",
                        response.status_code,
                    )
                    return WEBHOOK_MAINTENANCE_FAILED_EXIT_CODE
                logger.warning(
                    "Webhook maintenance returned HTTP %s; reclaiming polling for this monitor pass",
                    response.status_code,
                )
            except Exception as exc:
                if safe_auto:
                    logger.error(
                        "Webhook maintenance failed (%s); retaining webhook ownership instead of starting a competing poller.",
                        type(exc).__name__,
                    )
                    return WEBHOOK_MAINTENANCE_FAILED_EXIT_CODE
                logger.warning(
                    "Webhook maintenance failed (%s); reclaiming polling for this monitor pass",
                    type(exc).__name__,
                )
        else:
            if safe_auto:
                logger.error(
                    "Telegram webhook URL is unusable; retaining ownership and refusing a competing poller."
                )
                return WEBHOOK_MAINTENANCE_FAILED_EXIT_CODE
            logger.warning("Telegram webhook URL is unusable; reclaiming polling for this monitor pass")

        # Legacy non-auto fallback only. Auto mode above fails closed so an Actions
        # runner can never delete or race an existing webhook consumer.
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
        if getattr(self.collector, "provider_preflight_blocked", lambda: False)():
            logger.warning(
                "Scheduled X scan skipped because the immediately preceding provider probe was offline."
            )
            self.state.data["last_failed_sources"] = list(self.collector.last_errors)[:10]
            self._record_x_scan_failure(now)
            return
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
