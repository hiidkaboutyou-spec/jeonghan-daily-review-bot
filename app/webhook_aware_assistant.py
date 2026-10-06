from __future__ import annotations

import logging
import os
from datetime import datetime, timedelta, timezone

from .personal_assistant import PersonalAssistantReviewApplication, assistant_main_keyboard
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
            headers = {"X-Assistant-Secret": secret}
            # GitHub Actions already owns the configured Gemini secret while the
            # long-lived webhook host may intentionally have no provider secret.
            # Lease it only to the authenticated, origin-pinned HTTPS endpoint and
            # never persist it in repository/state/Telegram backup storage.
            gemini_key = str(getattr(self.settings, "gemini_api_key", "") or "").strip()
            if gemini_key:
                if not trusted_origins:
                    logger.error(
                        "Gemini is configured but no trusted webhook origin exists; refusing provider credential handoff."
                    )
                    if safe_auto:
                        return WEBHOOK_MAINTENANCE_FAILED_EXIT_CODE
                else:
                    headers["X-Hani-Gemini-Key"] = gemini_key
            try:
                response = self.telegram.session.post(
                    maintenance_url,
                    headers=headers,
                    timeout=90,
                    allow_redirects=False,
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

    def _degraded_recovery_pass_complete(self) -> bool:
        """Return true only when public recovery touched every active source without a source failure.

        This is deliberately *not* authenticated completeness. It is only strong
        enough to advance a separate realtime cadence checkpoint; last_auto_run
        remains the authoritative backfill cursor until authenticated X recovers.
        """
        if os.getenv("X_PROVIDER_PREFLIGHT", "").strip().casefold() != "degraded":
            return False
        collector = getattr(self, "collector", None)
        if collector is None:
            return False

        configured_sources = getattr(collector, "sources", None) or self.settings.sources
        active = {
            normalize_handle(str(source.get("handle", "")))
            for source in configured_sources
            if source.get("enabled", True)
        }
        active.discard("")
        attempted = {
            normalize_handle(str(handle))
            for handle in (getattr(collector, "_hani_degraded_attempted_sources", []) or [])
        }
        attempted.discard("")
        failed = {
            normalize_handle(str(handle))
            for handle in (getattr(collector, "_hani_degraded_failed_sources", {}) or {})
        }
        failed.discard("")

        return bool(active) and active.issubset(attempted) and not (active & failed)

    async def run_scheduled_scan(self) -> None:
        """Run the production scan at the configured near-real-time cadence."""
        now = datetime.now(timezone.utc)
        authoritative_last = self._state_datetime("last_auto_run") or (now - timedelta(hours=2))
        last = authoritative_last
        provider_degraded = (
            os.getenv("X_PROVIDER_PREFLIGHT", "").strip().casefold() == "degraded"
        )
        degraded_last = (
            self._state_datetime("last_degraded_scan_at") if provider_degraded else None
        )
        if degraded_last and degraded_last > last:
            last = degraded_last

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
        # Public recovery is intentionally non-authoritative, but once one pass
        # reaches every configured source without a source-level failure we can
        # keep realtime retries bounded. A two-hour overlap is deliberately much
        # wider than the normal 30-minute overlap so bursty fan accounts are
        # re-read across several 12-minute maintenance ticks.
        overlap = timedelta(hours=2) if degraded_last else timedelta(minutes=30)
        start = max(last - overlap, now - timedelta(hours=lookback))
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

        if self._degraded_recovery_pass_complete():
            # This checkpoint is scheduling-only. It must never become the
            # authenticated success cursor: last_auto_run remains untouched so a
            # future healthy X pass can backfill the entire unproven interval.
            self.state.data["last_degraded_scan_at"] = now.isoformat()
            self.state.data["last_failed_sources"] = []
            self.state.data["last_x_error_notice"] = ""
            self.state.data["x_scan_failure_streak"] = 0
            logger.warning(
                "Scheduled X scan used public recovery across every active source; "
                "realtime degraded checkpoint advanced while authoritative cursor stayed held."
            )
            return

        if getattr(self.collector, "last_errors", []):
            self.state.data["last_failed_sources"] = list(self.collector.last_errors)[:10]
            logger.warning(
                "Scheduled X scan returned partial results (%s paths); cursor retained for retry.",
                len(self.collector.last_errors),
            )
            self._record_x_scan_failure(now)
            return

        self.state.data["last_auto_run"] = now.isoformat()
        self.state.data["last_degraded_scan_at"] = ""
        self.state.data["last_x_error_notice"] = ""
        self.state.data["last_failed_sources"] = []
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
