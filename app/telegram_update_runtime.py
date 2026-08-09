from __future__ import annotations

import logging
from typing import Any

from .config import ConfigError
from .media_delivery_runtime import MediaDedupReviewApplication
from .telegram import TelegramError
from .x_client import XCollectionError

logger = logging.getLogger(__name__)
MAX_TELEGRAM_UPDATE_ATTEMPTS = 3


class TelegramSafeReviewApplication(MediaDedupReviewApplication):
    """Process polling updates before advancing the durable getUpdates offset.

    Telegram considers updates confirmed once a later getUpdates call uses an offset
    greater than their update_id. A transient command failure therefore must not
    advance our durable offset first. Failed updates are retried in a later run, in
    order, and quarantined after a small bounded number of failures so one poisoned
    update cannot block the private bot forever.
    """

    async def process_telegram_updates(self) -> None:
        updates = self.telegram.get_updates(self.state.telegram_offset)
        for item in updates:
            try:
                update_id = int(item.get("update_id", 0) or 0)
            except (TypeError, ValueError):
                logger.warning("Ignoring malformed Telegram update without a numeric update_id")
                continue
            if update_id < self.state.telegram_offset:
                continue

            try:
                await self._process_one_telegram_update(item)
            except (XCollectionError, TelegramError, ConfigError) as exc:
                if not self._handle_telegram_update_failure(update_id, exc):
                    break
            except Exception as exc:
                logger.exception("Unexpected Telegram update handler failure")
                if not self._handle_telegram_update_failure(update_id, exc):
                    break
            else:
                self.state.clear_telegram_failure(update_id)
                self.state.telegram_offset = max(self.state.telegram_offset, update_id + 1)

    async def _process_one_telegram_update(self, item: dict[str, Any]) -> None:
        if "message" in item:
            await self.handle_message(item["message"])
        elif "callback_query" in item:
            await self.handle_callback(item["callback_query"])
        # Unsupported/malformed update types are intentionally treated as handled.
        # allowed_updates requests only message/callback_query, but Telegram notes
        # that old queued update types can briefly still appear after filters change.

    def _handle_telegram_update_failure(self, update_id: int, exc: Exception) -> bool:
        """Return True only when the failed update has been quarantined and may be skipped."""
        attempts = self.state.record_telegram_failure(update_id, type(exc).__name__)
        logger.warning(
            "Telegram update %s failed on attempt %s/%s (%s)",
            update_id,
            attempts,
            MAX_TELEGRAM_UPDATE_ATTEMPTS,
            type(exc).__name__,
        )
        if attempts < MAX_TELEGRAM_UPDATE_ATTEMPTS:
            self._safe_send(
                f"❌ انجام درخواست تلگرام کامل نشد؛ تلاش {attempts}/{MAX_TELEGRAM_UPDATE_ATTEMPTS}. "
                "در اجرای بعدی دوباره امتحان می‌کنم."
            )
            return False

        # No exception text is persisted or shown. Advance only after the bounded
        # retry budget is exhausted, otherwise this single update would permanently
        # block every later admin command in Telegram's ordered queue.
        self.state.clear_telegram_failure(update_id)
        self.state.telegram_offset = max(self.state.telegram_offset, update_id + 1)
        self._safe_send(
            "⚠️ یک درخواست تلگرام بعد از چند تلاش متوالی قرنطینه شد تا صف قفل نشود. "
            "درخواست را اگر هنوز لازم است دوباره بفرست."
        )
        return True
