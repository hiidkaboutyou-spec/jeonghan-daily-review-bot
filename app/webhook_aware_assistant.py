from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from .personal_assistant import PersonalAssistantReviewApplication
from .webhook_runtime_utils import derive_runtime_secret, maintenance_url_from_webhook
from .x_client import XCollectionError

logger = logging.getLogger(__name__)
WEBHOOK_DELEGATED_EXIT_CODE = 3


class WebhookAwarePersonalAssistant(PersonalAssistantReviewApplication):
    """Keep automatic monitoring alive with or without an external webhook host.

    If a healthy webhook runtime exists, GitHub Actions sends it one authenticated
    maintenance wake. If the webhook is stale, unreachable, or unusable, Actions
    reclaims Telegram polling without dropping queued updates and performs the full
    assistant pass itself. This prevents a dead hosting experiment from silently
    disabling automatic Jeonghan monitoring.
    """

    async def run(self) -> int:
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
                response = self.telegram.session.post(
                    maintenance_url,
                    headers={"X-Assistant-Secret": secret},
                    timeout=25,
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
        # five-minute schedule retries instead of pretending monitoring succeeded.
        self.telegram.ensure_polling_mode()
        self.state.data["polling_mode_checked"] = datetime.now(timezone.utc).isoformat()
        await super().run()
        return 0

    async def run_scheduled_scan(self) -> None:
        """Run the production scan at the configured near-real-time cadence."""
        now = datetime.now(timezone.utc)
        last = self._state_datetime("last_auto_run") or (now - timedelta(hours=2))
        interval = max(
            1,
            int(self.settings.runtime.get("scheduled_min_interval_minutes", 4)),
        )
        if now - last < timedelta(minutes=interval):
            return

        lookback = max(2, int(self.settings.runtime.get("scheduled_lookback_hours", 24)))
        start = max(last - timedelta(minutes=30), now - timedelta(hours=lookback))
        try:
            updates = await self.collector.collect_window(start, now, max_per_query=200)
        except XCollectionError as exc:
            logger.warning("Scheduled X scan failed: %s", exc)
            self._notify_x_failure_if_due(now)
            return

        fresh = [item for item in updates if not self.state.is_seen(item.id)]
        fresh.sort(key=lambda item: (item.created_at, item.id))
        ceiling = max(1, int(self.settings.runtime.get("max_collection_items", 1000)))
        self.state.queue_updates(fresh[:ceiling], force=False)

        if getattr(self.collector, "last_errors", []):
            logger.warning("Scheduled X scan returned partial results; cursor retained for retry.")
            self._notify_x_failure_if_due(now)
            return

        self.state.data["last_auto_run"] = now.isoformat()
        self.state.data["last_x_error_notice"] = ""

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
