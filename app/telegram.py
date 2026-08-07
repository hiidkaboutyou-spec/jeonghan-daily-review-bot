from __future__ import annotations

import json
import logging
import time
from typing import Any

import requests

from .media import PreparedMedia

logger = logging.getLogger(__name__)
TELEGRAM_TEXT_LIMIT = 4096


class TelegramError(RuntimeError):
    pass


class TelegramBot:
    def __init__(self, token: str, admin_user_id: int, review_chat_id: int):
        self.token = token
        self.admin_user_id = int(admin_user_id)
        self.review_chat_id = int(review_chat_id)
        self.base = f"https://api.telegram.org/bot{token}"
        self.session = requests.Session()

    def api(
        self,
        method: str,
        *,
        data: dict[str, Any] | None = None,
        files=None,
        timeout: int = 60,
        attempts: int = 3,
    ) -> Any:
        last_description = "Telegram request failed."
        for attempt in range(max(1, attempts)):
            _rewind_files(files)
            try:
                response = self.session.post(
                    f"{self.base}/{method}",
                    data=data or {},
                    files=files,
                    timeout=timeout,
                )
            except requests.RequestException as exc:
                # Never include or chain the requests exception here: its message may
                # contain the full Bot API URL, which embeds TELEGRAM_BOT_TOKEN.
                last_description = f"Telegram network request failed ({type(exc).__name__})."
                if attempt + 1 < attempts:
                    time.sleep(1.0 + attempt)
                    continue
                raise TelegramError(last_description) from None

            try:
                payload = response.json()
            except ValueError as exc:
                if response.status_code >= 500 and attempt + 1 < attempts:
                    time.sleep(1.0 + attempt)
                    continue
                raise TelegramError(
                    f"Telegram returned HTTP {response.status_code} without valid JSON."
                ) from exc

            if response.status_code == 429 or int(payload.get("error_code", 0) or 0) == 429:
                retry_after = int((payload.get("parameters") or {}).get("retry_after", 1) or 1)
                last_description = "Telegram rate limit reached."
                if attempt + 1 < attempts:
                    time.sleep(max(1, min(retry_after, 60)))
                    continue

            if response.status_code >= 500 and attempt + 1 < attempts:
                time.sleep(1.0 + attempt)
                continue

            if not response.ok or not payload.get("ok"):
                description = str(payload.get("description") or "Telegram API error")[:600]
                raise TelegramError(f"Telegram {method} failed: {description}")
            return payload.get("result")

        raise TelegramError(last_description)

    def ensure_polling_mode(self) -> None:
        """Remove an old webhook without dropping queued updates before getUpdates polling."""
        self.api("deleteWebhook", data={"drop_pending_updates": "false"}, timeout=30)

    def get_updates(self, offset: int) -> list[dict[str, Any]]:
        result = self.api(
            "getUpdates",
            data={
                "offset": offset,
                "timeout": 0,
                "limit": 100,
                "allowed_updates": json.dumps(["message", "callback_query"]),
            },
            timeout=30,
        )
        return list(result or [])

    def send_message(
        self,
        text: str,
        *,
        chat_id: int | None = None,
        reply_markup: dict[str, Any] | None = None,
        disable_preview: bool = True,
    ) -> dict[str, Any]:
        if len(text) > TELEGRAM_TEXT_LIMIT:
            raise TelegramError(
                f"Telegram message is {len(text)} characters; caller must split it before sending."
            )
        data: dict[str, Any] = {
            "chat_id": self.review_chat_id if chat_id is None else chat_id,
            "text": text,
            "disable_web_page_preview": "true" if disable_preview else "false",
        }
        if reply_markup:
            data["reply_markup"] = json.dumps(reply_markup, ensure_ascii=False)
        return self.api("sendMessage", data=data)

    def edit_message_text(
        self,
        message_id: int,
        text: str,
        *,
        reply_markup: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        if len(text) > TELEGRAM_TEXT_LIMIT:
            raise TelegramError(
                f"Edited Telegram message is {len(text)} characters; it cannot be edited as one message."
            )
        data: dict[str, Any] = {
            "chat_id": self.review_chat_id,
            "message_id": message_id,
            "text": text,
            "disable_web_page_preview": "true",
        }
        if reply_markup is not None:
            data["reply_markup"] = json.dumps(reply_markup, ensure_ascii=False)
        return self.api("editMessageText", data=data)

    def answer_callback(self, callback_id: str, text: str = "") -> None:
        self.api("answerCallbackQuery", data={"callback_query_id": callback_id, "text": text[:180]})

    def send_media(self, media: list[PreparedMedia]) -> list[dict[str, Any]]:
        sent: list[dict[str, Any]] = []
        for offset in range(0, len(media), 10):
            chunk = media[offset : offset + 10]
            if len(chunk) == 1:
                sent.append(self._send_single_media(chunk[0]))
            else:
                sent.extend(self._send_media_group(chunk))
        return sent

    def _send_single_media(self, item: PreparedMedia) -> dict[str, Any]:
        method = "sendPhoto" if item.kind == "photo" else "sendVideo"
        field = "photo" if item.kind == "photo" else "video"
        with item.path.open("rb") as handle:
            files = {field: (item.path.name, handle, item.content_type)}
            data: dict[str, Any] = {"chat_id": self.review_chat_id}
            if item.kind == "video":
                data["supports_streaming"] = "true"
            return self.api(method, data=data, files=files, timeout=180)

    def _send_media_group(self, items: list[PreparedMedia]) -> list[dict[str, Any]]:
        descriptors: list[dict[str, Any]] = []
        handles = []
        files: dict[str, Any] = {}
        try:
            for index, item in enumerate(items):
                key = f"file{index}"
                handle = item.path.open("rb")
                handles.append(handle)
                files[key] = (item.path.name, handle, item.content_type)
                descriptor: dict[str, Any] = {
                    "type": "photo" if item.kind == "photo" else "video",
                    "media": f"attach://{key}",
                }
                if item.kind == "video":
                    descriptor["supports_streaming"] = True
                descriptors.append(descriptor)
            return list(
                self.api(
                    "sendMediaGroup",
                    data={"chat_id": self.review_chat_id, "media": json.dumps(descriptors)},
                    files=files,
                    timeout=240,
                )
                or []
            )
        finally:
            for handle in handles:
                handle.close()

    def is_admin_message(self, message: dict[str, Any]) -> bool:
        sender = message.get("from", {})
        chat = message.get("chat", {})
        return (
            int(sender.get("id", 0)) == self.admin_user_id
            and int(chat.get("id", 0)) == self.review_chat_id
        )

    def is_admin_callback(self, callback: dict[str, Any]) -> bool:
        sender = callback.get("from", {})
        message = callback.get("message", {})
        chat = message.get("chat", {})
        return (
            int(sender.get("id", 0)) == self.admin_user_id
            and int(chat.get("id", 0)) == self.review_chat_id
        )


def _rewind_files(files) -> None:
    if not files:
        return
    for value in files.values():
        try:
            handle = value[1] if isinstance(value, tuple) and len(value) > 1 else value
            handle.seek(0)
        except (AttributeError, OSError):
            continue


def inline_keyboard(rows: list[list[tuple[str, str]]]) -> dict[str, Any]:
    return {
        "inline_keyboard": [
            [{"text": label, "callback_data": data[:64]} for label, data in row]
            for row in rows
        ]
    }


def main_keyboard() -> dict[str, Any]:
    """Persistent keyboard above Telegram's message field."""
    return {
        "keyboard": [
            [{"text": "🕑 ۲ ساعت اخیر"}, {"text": "🗂 ۲۴ ساعت منبع"}],
            [{"text": "🔎 سرچ آرشیو"}, {"text": "📚 فن‌فیک"}],
            [{"text": "📋 وضعیت"}, {"text": "❔ راهنما"}],
        ],
        "resize_keyboard": True,
        "is_persistent": True,
        "one_time_keyboard": False,
        "input_field_placeholder": "یک قابلیت را انتخاب کن…",
    }


def draft_keyboard(draft_id: str) -> dict[str, Any]:
    return inline_keyboard(
        [
            [("😂 بامزه‌تر", f"draft:fun:{draft_id}"), ("🪽 نرم‌تر", f"draft:soft:{draft_id}")],
            [("📰 دقیق‌تر", f"draft:precise:{draft_id}"), ("📋 متن تمیز", f"draft:copy:{draft_id}")],
            [("🗑 رد", f"draft:reject:{draft_id}")],
        ]
    )
