from __future__ import annotations

import logging
import os
from datetime import datetime, timedelta, timezone

from .main import parse_date_query, rank_groups, short_id
from .models import Update
from .organizer import organize_updates
from .personal_assistant import PersonalAssistantReviewApplication, assistant_main_keyboard
from .private_inbox_ui import inbox_list_keyboard
from .source_authority_hardening import (
    configured_handles,
    filter_configured_updates,
    is_configured_author,
)
from .style import ensure_rtl_line
from .telegram import inline_keyboard
from .webhook_runtime_utils import derive_runtime_secret, maintenance_url_from_webhook
from .x_client import XCollectionError, normalize_handle

logger = logging.getLogger(__name__)
WEBHOOK_DELEGATED_EXIT_CODE = 3


def github_actions_polling_only() -> bool:
    """Return true when production intentionally runs without an external host."""
    return os.getenv("ASSISTANT_RUNTIME_MODE", "").strip().lower() == "github_actions_polling"


class WebhookAwarePersonalAssistant(PersonalAssistantReviewApplication):
    """Private production assistant with completeness-safe configured-source retrieval.

    All non-Fanfic update flows are restricted to the enabled configured X source
    list. Fanfic/AO3 remains independent. If a healthy webhook runtime exists,
    GitHub Actions sends it one authenticated maintenance wake. If the webhook is
    stale, unreachable, or unusable, Actions reclaims Telegram polling without
    dropping queued updates and performs the same assistant pass itself.
    """

    def _configured_updates(self, updates: list[Update]) -> list[Update]:
        return filter_configured_updates(self.collector, updates)

    def _configured_source_handles(self) -> set[str]:
        return configured_handles(self.collector)

    def _is_configured_author(self, author: str) -> bool:
        return is_configured_author(self.collector, author)

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
        updates = self._configured_updates(
            [item for item in updates if start <= item.created_at < end]
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
        """Replay a proven-complete 24h window for one configured source only."""
        if value == "custom":
            self.telegram.send_message(
                "⚠️ دریافت آپدیت فقط برای منابع تنظیم‌شدهٔ بات مجاز است. از فهرست منابع یکی را انتخاب کن.",
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
        if not self._is_configured_author(handle):
            self.telegram.send_message(
                f"⚠️ @{handle} در فهرست منابع تنظیم‌شده نیست؛ این مسیر فقط منابع تأییدشده را می‌خواند.",
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
        updates = self._configured_updates(
            [item for item in updates if start <= item.created_at < end]
        )
        if not updates:
            self.telegram.send_message(
                f"برای @{handle} در ۲۴ ساعت گذشته چیزی پیدا نشد.",
                reply_markup=assistant_main_keyboard(),
            )
            return
        await self.deliver_updates(updates, force=True)

    async def run_search(self, query: str) -> None:
        """Search local/X history while keeping every candidate source-authoritative."""
        self.telegram.send_message(
            f"🔎 اول آرشیو خود بات را برای «{query[:200]}» می‌گردم، بعد در صورت نیاز X را هم چک می‌کنم…",
            reply_markup=assistant_main_keyboard(),
        )
        date_range = parse_date_query(query, self.settings.timezone)
        if date_range:
            start, end = date_range
        else:
            start = end = None
        local_updates = self._configured_updates(
            self.archive_db.search(query, start=start, end=end, limit=120)
        )
        expanded = self.writer.expand_search(query)
        if date_range:
            base_queries = []
            for group in self.settings.keyword_groups:
                terms = [str(term) for term in group.get("terms", []) if str(term).strip()]
                if terms:
                    base_queries.append(
                        " OR ".join(f'\"{term}\"' if " " in term else term for term in terms)
                    )
            expanded = base_queries + expanded
        external_updates: list[Update] = []
        external_error: XCollectionError | None = None
        try:
            external_updates = self._configured_updates(
                await self.collector.search_archive(
                    expanded,
                    start=start,
                    end=end,
                    max_per_query=140,
                )
            )
        except XCollectionError as exc:
            external_error = exc
        combined = {item.id: item for item in local_updates}
        for item in external_updates:
            combined[item.id] = item
            self.archive_db.index_update(item)
        updates = self._configured_updates(list(combined.values()))
        if not updates:
            if external_error is not None and not local_updates:
                raise external_error
            self.telegram.send_message(
                "هیچ نتیجهٔ قابل‌استفاده‌ای از منابع تنظیم‌شده پیدا نشد.",
                reply_markup=assistant_main_keyboard(),
            )
            return
        candidate_limit = max(1, min(8, int(self.settings.runtime.get("max_search_candidates", 8))))
        groups = rank_groups(query, organize_updates(updates))[:candidate_limit]
        titles = self.writer.candidate_titles(query, groups)
        session_id = short_id(query + datetime.now(timezone.utc).isoformat())
        self.state.create_session(
            session_id,
            {
                "query": query,
                "candidates": [
                    {
                        "key": group.key,
                        "title": titles.get(group.key) or group.title,
                        "started_at": group.started_at.isoformat(),
                        "selected": group.updates[0].to_dict(),
                        "preview_ids": [item.id for item in group.updates],
                    }
                    for group in groups
                ],
            },
        )
        local_ids = {item.id for item in local_updates}
        lines = [f"نتیجه‌های پیشنهادی برای «{query}»:"]
        rows = []
        for index, group in enumerate(groups):
            local_date = group.started_at.astimezone(self.settings.timezone).strftime("%Y-%m-%d %H:%M")
            title = titles.get(group.key) or group.title
            origin = "آرشیو/\u200fX" if any(item.id in local_ids for item in group.updates) else "X"
            lines.append(f"{index + 1}. {title} — {local_date} — {len(group.updates)} مورد — {origin}")
            rows.append([(f"{index + 1}. {title[:40]}", f"pick:{session_id}:{index}")])
        self.telegram.send_message(
            ensure_rtl_line("\n".join(lines)),
            reply_markup=inline_keyboard(rows),
        )

    async def run_selected_event(self, session_id: str, index: int) -> None:
        """Block stale pre-policy sessions from reconstructing external authors."""
        session = self.state.get_session(session_id)
        if session:
            candidates = list(session.get("candidates", []))
            if 0 <= index < len(candidates):
                try:
                    selected = Update.from_dict(candidates[index]["selected"])
                except (KeyError, TypeError, ValueError):
                    selected = None
                if selected is not None and not self._is_configured_author(selected.author):
                    self.telegram.send_message(
                        "⚠️ این نتیجه متعلق به منبع تنظیم‌شده نیست و طبق سیاست فعلی قابل بازیابی نیست.",
                        reply_markup=assistant_main_keyboard(),
                    )
                    return
        await super().run_selected_event(session_id, index)

    async def deliver_pending(self) -> None:
        """Drop stale external queue rows before any replay/resend attempt."""
        queue = self.state.data.get("pending_delivery", [])
        if isinstance(queue, list):
            kept = []
            dropped = 0
            for raw in queue:
                if not isinstance(raw, dict):
                    continue
                payload = dict(raw)
                payload.pop("force", None)
                try:
                    update = Update.from_dict(payload)
                except (TypeError, ValueError):
                    kept.append(raw)
                    continue
                if self._is_configured_author(update.author):
                    kept.append(raw)
                else:
                    dropped += 1
            if dropped:
                logger.warning(
                    "Dropped %s stale non-configured update(s) from private delivery queue",
                    dropped,
                )
            self.state.data["pending_delivery"] = kept
        await super().deliver_pending()

    async def deliver_updates(self, updates: list[Update], *, force: bool) -> None:
        """Final source-authority boundary before media/text enter private review."""
        allowed = self._configured_updates(updates)
        dropped = len(updates) - len(allowed)
        if dropped:
            logger.warning(
                "Blocked %s non-configured update(s) before private review delivery",
                dropped,
            )
        if not allowed:
            return
        await super().deliver_updates(allowed, force=force)

    async def handle_draft_action(self, action: str, draft_id: str, message_id: int) -> None:
        """Do not resend/reconstruct an old draft from an external author."""
        draft = self.state.get_draft(draft_id)
        if draft is not None:
            update = self.state.get_update(draft.update_id)
            if update is not None and not self._is_configured_author(update.author):
                self.telegram.send_message(
                    "⚠️ این پیش‌نویس قدیمی از منبع تنظیم‌شده نیست و دوباره ارسال یا بازنویسی نمی‌شود.",
                    reply_markup=assistant_main_keyboard(),
                )
                return
        await super().handle_draft_action(action, draft_id, message_id)

    def show_inbox(self, *, status: str = "pending", page: int = 0, message_id: int | None = None) -> None:
        """Hide stale external-author drafts from the review inbox listing."""
        status = status if status in {"pending", "ready", "rejected", "all"} else "pending"
        allowed = self._configured_source_handles()
        items, page, pages = self.inbox.list_items(
            status=status,
            allowed_sources=allowed,
            page=page,
            page_size=5,
        )
        total = self.inbox.count(status, allowed_sources=allowed)
        text = f"📥 صندوق پیش‌نویس‌های خصوصی — {status} — {total} مورد"
        if not items:
            text += "\n\nچیزی در این فیلتر نیست."
        markup = inbox_list_keyboard(items, status, page, pages)
        if message_id:
            self.telegram.edit_message_text(message_id, text, reply_markup=markup)
        else:
            self.telegram.send_message(text, reply_markup=markup)

    def open_inbox_draft(self, draft_id: str, *, status: str, page: int, message_id: int) -> None:
        item = self.inbox.get(draft_id)
        if item is not None and item.source and not self._is_configured_author(item.source):
            self.telegram.edit_message_text(
                message_id,
                "این پیش‌نویس قدیمی متعلق به منبع تنظیم‌شده نیست و طبق سیاست فعلی نمایش داده نمی‌شود.",
                reply_markup=inline_keyboard([[("◀️ برگشت", f"inbox:page:{status}:{page}")]]),
            )
            return
        super().open_inbox_draft(draft_id, status=status, page=page, message_id=message_id)

    async def run_scheduled_scan(self) -> None:
        """Run the production source-only scan at the configured near-real-time cadence."""
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

        fresh = self._configured_updates(
            [item for item in updates if not self.state.is_seen(item.id)]
        )
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
