from __future__ import annotations

import logging
from pathlib import Path

from .callback_store import CallbackStore
from .media_delivery import MediaDeliveryLedger
from .message_delivery import MessageDeliveryStore
from .models import Update
from .private_telegram import telegram_file_identity
from .reminder_runtime import ReminderReviewApplication
from .telegram import TelegramError

logger = logging.getLogger(__name__)


class MediaDedupReviewApplication(ReminderReviewApplication):
    """Production review runtime with persistent exact-media duplicate suppression."""

    def __init__(self, settings):
        super().__init__(settings)
        state_path = getattr(getattr(self, "state", None), "path", None)
        if state_path is None:
            state_path = getattr(settings, "state_path", None)
        durable_db = Path(state_path).with_name("private-review.sqlite3") if state_path is not None else None
        self.media_delivery = MediaDeliveryLedger(durable_db) if durable_db is not None else None

        # The same private SQLite file is already persisted for review state. Callback
        # payloads that exceed Telegram's 64-byte UTF-8 limit are mapped here to
        # short opaque tokens so routing survives across scheduled workflow runs.
        telegram = getattr(self, "telegram", None)
        if durable_db is not None and telegram is not None:
            if getattr(telegram, "callback_store", None) is None:
                telegram.callback_store = CallbackStore(durable_db)
            if getattr(telegram, "message_delivery_store", None) is None:
                telegram.message_delivery_store = MessageDeliveryStore(durable_db)

    async def _deliver_private_media(self, update: Update) -> None:
        # Intentional lightweight test doubles may omit persistent state entirely.
        # Real production always has StateStore.path; in the no-state test case,
        # preserve the parent behavior instead of constructing a fake durable path.
        if self.media_delivery is None:
            return await super()._deliver_private_media(update)

        media = list(update.media[:20])
        if not media:
            return

        # Fast path: Telegram already knows every source media URL. file_unique_id
        # gives an additional stable identity that survives changing file_ids.
        cached = self.media_cache.get_all(media)
        if cached is not None and cached:
            pending_identities: set[str] = set()
            send_cached = []
            send_items = []
            send_identities: list[tuple[str, ...]] = []
            for item, cached_item in zip(media, cached):
                identities = self.media_delivery.identities_for(
                    item,
                    file_unique_id=cached_item.file_unique_id,
                )
                if self.media_delivery.any_recent(identities) or pending_identities.intersection(identities):
                    logger.info("Skipping recently delivered exact media for update %s", update.id)
                    continue
                pending_identities.update(identities)
                send_cached.append(cached_item)
                send_items.append(item)
                send_identities.append(identities)

            if not send_cached:
                return
            try:
                sent = self.telegram.send_cached_media(send_cached)
            except TelegramError:
                # A stale/invalid Telegram file_id must not poison the source URL
                # cache. Do not record a delivery receipt for a failed API call.
                for item in send_items:
                    self.media_cache.delete(item)
            else:
                for item, cached_item, identities, message in zip(
                    send_items, send_cached, send_identities, sent
                ):
                    _, returned_unique = telegram_file_identity(message, item.kind)
                    final_identities = self.media_delivery.identities_for(
                        item,
                        file_unique_id=returned_unique or cached_item.file_unique_id,
                    )
                    self.media_delivery.mark_delivered(
                        final_identities,
                        kind=item.kind,
                        update_id=update.id,
                    )
                return

        # Slow path: prepare the real bytes, then SHA-256 them before upload. This
        # catches the same image/video served from a different source URL.
        temp_handles = []
        prepared = []
        prepared_items = []
        prepared_identities: list[tuple[str, ...]] = []
        pending_identities: set[str] = set()
        try:
            for item in media:
                single = Update.from_dict(update.to_dict())
                single.media = [item]
                temp, values = self.media.prepare(single)
                temp_handles.append(temp)
                if not values:
                    continue
                value = values[0]
                content_identity = self.media_delivery.content_identity(value.path)
                identities = self.media_delivery.identities_for(
                    item,
                    content_identity=content_identity,
                )
                if self.media_delivery.any_recent(identities) or pending_identities.intersection(identities):
                    logger.info("Skipping recently delivered exact media for update %s", update.id)
                    continue
                pending_identities.update(identities)
                prepared.append(value)
                prepared_items.append(item)
                prepared_identities.append(identities)

            if not prepared:
                return

            sent = self.telegram.send_media(prepared)
            for item, identities, message in zip(prepared_items, prepared_identities, sent):
                file_id, unique_id = telegram_file_identity(message, item.kind)
                if file_id:
                    self.media_cache.put(item, file_id, unique_id)
                final_identities = tuple(
                    dict.fromkeys(
                        (*identities, self.media_delivery.telegram_identity(unique_id))
                    )
                )
                self.media_delivery.mark_delivered(
                    final_identities,
                    kind=item.kind,
                    update_id=update.id,
                )
        finally:
            for temp in temp_handles:
                temp.cleanup()
