from __future__ import annotations

import re
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from .channel_style_application import ChannelStyleReviewApplication
from .style import ensure_rtl_line
from .telegram import main_keyboard as legacy_main_keyboard


_PERSIAN_DIGITS = str.maketrans("۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩", "01234567890123456789")
_DATE_RE = re.compile(r"(?:^|\s)(?:20\d{2}[-/.]\d{1,2}[-/.]\d{1,2}|\d{6})(?:$|\s)")
_SOURCE_24_RE = re.compile(
    r"(?:24|۲۴)\s*(?:ساعت|ساعته|h|hours?)?(?:\s*(?:از|برای|منبع))?\s*"
    r"(?P<source>@?[A-Za-z0-9_]{1,30}|https?://(?:www\.)?(?:x\.com|twitter\.com)/[A-Za-z0-9_]{1,30})",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class AssistantIntent:
    kind: str
    argument: str = ""


def _normalized(text: str) -> str:
    value = str(text or "").strip().translate(_PERSIAN_DIGITS)
    value = value.replace("ي", "ی").replace("ك", "ک")
    value = re.sub(r"\s+", " ", value)
    return value


def parse_assistant_intent(text: str) -> AssistantIntent:
    """Route ordinary Persian messages without requiring slash commands.

    Explicit Telegram commands are delegated unchanged. Unknown normal text is
    treated as an archive search because search is the safest useful default for
    this private single-user assistant.
    """

    raw = str(text or "").strip()
    value = _normalized(raw)
    lowered = value.lower()
    if not value:
        return AssistantIntent("empty")
    if value.startswith("/"):
        return AssistantIntent("delegate")

    if raw in {
        "🕑 ۲ ساعت اخیر",
        "🗂 ۲۴ ساعت منبع",
        "🔎 سرچ آرشیو",
        "📚 فن‌فیک",
        "📋 وضعیت",
        "❔ راهنما",
    }:
        return AssistantIntent("delegate")

    if raw == "✨ دستیار من":
        return AssistantIntent("dashboard")
    if raw == "📥 پیش‌نویس‌ها":
        return AssistantIntent("inbox")
    if raw == "⏰ یادآورها":
        return AssistantIntent("reminders")

    if any(token in lowered for token in ("پیش نویس", "پیش‌نویس", "صندوق", "inbox")):
        return AssistantIntent("inbox")
    if any(token in lowered for token in ("یادآور", "یاداور", "reminder")):
        return AssistantIntent("reminders")
    if any(token in lowered for token in ("فن فیک", "فن‌فیک", "fanfic", "ao3")):
        return AssistantIntent("fic")

    source_match = _SOURCE_24_RE.search(value)
    if source_match:
        return AssistantIntent("source24", source_match.group("source"))
    if any(token in lowered for token in ("24 ساعت", "۲۴ ساعت", "بیست و چهار ساعت")) and any(
        token in lowered for token in ("منبع", "سورس", "source")
    ):
        return AssistantIntent("sources")

    recent_phrases = (
        "2 ساعت اخیر",
        "دو ساعت اخیر",
        "2 ساعت گذشته",
        "دو ساعت گذشته",
        "آپدیت جدید",
        "اپدیت جدید",
        "چه خبر",
        "چی اومده",
        "چی جدید",
    )
    if any(token in lowered for token in recent_phrases):
        return AssistantIntent("recent2h")

    if any(token in lowered for token in ("وضعیت", "بات سالم", "ربات سالم", "status")):
        return AssistantIntent("dashboard")
    if any(token in lowered for token in ("راهنما", "کمک", "چیکار کنم", "چی کار کنم", "help")):
        return AssistantIntent("dashboard")
    if lowered in {"سلام", "های", "hi", "hello", "شروع", "منو", "menu"}:
        return AssistantIntent("dashboard")

    for prefix in ("سرچ ", "جستجو ", "جست‌وجو ", "پیدا کن ", "بگرد ", "search "):
        if lowered.startswith(prefix):
            query = value[len(prefix) :].strip()
            return AssistantIntent("search", query) if query else AssistantIntent("search_prompt")

    if _DATE_RE.search(value):
        return AssistantIntent("search", value)

    # The bot is private and archive-centric. Falling back to archive search is
    # more useful than returning "command not recognized" and still has no public
    # publishing side effect.
    return AssistantIntent("search", raw)


def assistant_main_keyboard() -> dict[str, Any]:
    return {
        "keyboard": [
            [{"text": "✨ دستیار من"}, {"text": "📥 پیش‌نویس‌ها"}],
            [{"text": "🕑 ۲ ساعت اخیر"}, {"text": "🗂 ۲۴ ساعت منبع"}],
            [{"text": "🔎 سرچ آرشیو"}, {"text": "📚 فن‌فیک"}],
            [{"text": "⏰ یادآورها"}, {"text": "📋 وضعیت"}],
        ],
        "resize_keyboard": True,
        "is_persistent": True,
        "one_time_keyboard": False,
        "input_field_placeholder": "فارسی بگو چی می‌خوای؛ لازم نیست دستور حفظ کنی…",
    }


def install_assistant_keyboard() -> None:
    """Replace already-imported legacy keyboard aliases across the runtime chain."""

    package = __package__ or "app"
    prefix = package + "."
    for module_name, module in list(sys.modules.items()):
        if module is None or not module_name.startswith(prefix):
            continue
        if getattr(module, "main_keyboard", None) is legacy_main_keyboard:
            setattr(module, "main_keyboard", assistant_main_keyboard)


class PersonalAssistantReviewApplication(ChannelStyleReviewApplication):
    """Single-user Persian assistant layered over the production review bot.

    It does not add public publishing. It only routes the existing private fetch,
    archive, review, reminder and fic features through a simpler conversational UI.
    """

    def __init__(self, settings):
        super().__init__(settings)
        install_assistant_keyboard()

    async def handle_message(self, message):
        if not self.telegram.is_admin_message(message):
            return
        text = str(message.get("text", "") or message.get("caption", "")).strip()
        if not text:
            return

        # Preserve the existing two-step search/custom-source flows. Their next
        # free-text message belongs to the pending flow, not to the assistant parser.
        awaiting = self.state.data.get("awaiting", {})
        if isinstance(awaiting, dict) and awaiting.get(str(self.settings.admin_user_id)) and not text.startswith("/"):
            await super().handle_message(message)
            return

        intent = parse_assistant_intent(text)
        if intent.kind in {"empty", "delegate"}:
            await super().handle_message(message)
            return
        if intent.kind == "dashboard":
            self.send_assistant_dashboard()
            return
        if intent.kind == "inbox":
            self.show_inbox(status="pending", page=0)
            return
        if intent.kind == "reminders":
            self.show_reminders()
            return
        if intent.kind == "recent2h":
            await self.run_recent2h()
            return
        if intent.kind == "sources":
            self.show_sources()
            return
        if intent.kind == "source24":
            await self.run_source24(intent.argument)
            return
        if intent.kind == "search_prompt":
            self.ask_for_search()
            return
        if intent.kind == "search":
            await self.run_search(intent.argument)
            return
        if intent.kind == "fic":
            copy = dict(message)
            copy["text"] = "/fic"
            await super().handle_message(copy)
            return
        await super().handle_message(message)

    def send_start(self) -> None:
        self.send_assistant_dashboard(first_run=True)

    def send_status(self) -> None:
        self.send_assistant_dashboard()

    def send_help(self) -> None:
        text = (
            "من مثل یک دستیار خصوصی روی آرشیو و آپدیت‌های جونگهان کار می‌کنم؛ لازم نیست دستور حفظ کنی.\n\n"
            "مثلاً همین‌ها را فارسی بفرست:\n"
            "• چه خبر؟ / آپدیت جدید → دو ساعت اخیر\n"
            "• ۲۴ ساعت @username → محتوای کامل آن منبع\n"
            "• پیدا کن لایوی که داشت بازی می‌کرد → سرچ آرشیو + X\n"
            "• پیش‌نویس‌ها → صندوق بررسی\n"
            "• فن‌فیک → X و AO3\n"
            "• وضعیت → داشبورد سلامت و کار بعدی\n\n"
            "هر متن معمولی دیگری هم به‌عنوان چیزی که باید در آرشیو دنبالش بگردم در نظر گرفته می‌شود."
        )
        self.telegram.send_message(ensure_rtl_line(text), reply_markup=assistant_main_keyboard())

    def send_assistant_dashboard(self, *, first_run: bool = False) -> None:
        data = self.state.data
        pending = self.inbox.count("pending")
        ready = self.inbox.count("ready")
        rejected = self.inbox.count("rejected")
        queue = len(data.get("pending_delivery", []))
        sources = sum(bool(item.get("enabled", True)) for item in self.settings.sources)
        last_run = str(data.get("last_auto_run") or "").strip()
        translation = "فعال" if getattr(self, "channel_style_enabled", False) else "حالت امن جایگزین"
        indexed = int(getattr(self, "channel_style_indexed_examples", 0) or 0)

        if pending:
            next_action = f"اول {pending} پیش‌نویس منتظر را مرور کن."
        elif queue:
            next_action = f"{queue} مورد در صف تحویل مانده؛ وضعیت اجرای بعدی را چک کن."
        elif not last_run:
            next_action = "هنوز اسکن موفق ثبت نشده؛ «۲ ساعت اخیر» را بزن تا دریافت را تست کنیم."
        else:
            next_action = "همه‌چیز مرتب است؛ برای خبرهای تازه «چه خبر؟» بفرست یا چیزی را با توضیحش سرچ کن."

        heading = "✨ دستیار شخصی جونگهان آماده است." if first_run else "✨ داشبورد دستیار"
        text = (
            f"{heading}\n\n"
            f"ترجمهٔ سبک چنل: {translation}"
            + (f" · حافظهٔ سبک: {indexed:,} نمونه" if indexed else "")
            + "\n"
            f"منابع فعال: {sources}\n"
            f"پیش‌نویس‌ها: {pending} منتظر · {ready} آماده · {rejected} ردشده\n"
            f"صف تحویل: {queue}\n"
            f"آخرین اسکن موفق: {self._friendly_last_run(last_run)}\n\n"
            f"پیشنهاد من: {next_action}\n\n"
            "از این به بعد می‌تونی عادی فارسی تایپ کنی؛ لازم نیست اسم commandها را یادت بماند."
        )
        self.telegram.send_message(ensure_rtl_line(text), reply_markup=assistant_main_keyboard())

    def _friendly_last_run(self, value: str) -> str:
        if not value:
            return "ثبت نشده"
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=timezone.utc)
            local = parsed.astimezone(self.settings.timezone)
            return local.strftime("%Y-%m-%d %H:%M")
        except ValueError:
            return value[:80]
