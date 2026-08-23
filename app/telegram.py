from __future__ import annotations

import hashlib
import json
import logging
import random
import re
import threading
import time
from contextlib import ExitStack
from typing import Any

import requests

from .callback_store import CALLBACK_MAX_BYTES, CallbackDataError, CallbackStore
from .media import PreparedMedia
from .message_delivery import MessageDeliveryStore

logger = logging.getLogger(__name__)
TELEGRAM_TEXT_LIMIT = 4096
TELEGRAM_SAFE_TEXT_TARGET = 4000
DEFAULT_SEND_PACING_SECONDS = 1.1
MAX_RETRY_SLEEP_SECONDS = 120.0
_PART_LABEL_RE = re.compile(r"^بخش ([0-9]+) از ([0-9]+)\n\n")


class TelegramError(RuntimeError):
    pass


class TelegramTransientError(TelegramError):
    """Network/5xx failure that may succeed in a later workflow run."""


class TelegramRateLimitError(TelegramTransientError):
    def __init__(self, retry_after: int):
        self.retry_after = max(1, int(retry_after or 1))
        super().__init__("Telegram rate limit reached; retry later.")


class TelegramPermanentError(TelegramError):
    """Non-retryable Bot API failure for the current request."""


class TelegramBot:
    def __init__(
        self,
        token: str,
        admin_user_id: int,
        review_chat_id: int,
        *,
        callback_store: CallbackStore | None = None,
        message_delivery_store: MessageDeliveryStore | None = None,
        send_pacing_seconds: float = DEFAULT_SEND_PACING_SECONDS,
        sleep_fn=None,
    ):
        self.token = token
        self.admin_user_id = int(admin_user_id)
        self.review_chat_id = int(review_chat_id)
        self.base = f"https://api.telegram.org/bot{token}"
        self.session = requests.Session()
        self.callback_store = callback_store
        self.message_delivery_store = message_delivery_store
        self.send_pacing_seconds = max(0.0, float(send_pacing_seconds))
        self._sleep_fn = sleep_fn
        self._delivery_lock = threading.RLock()
        self._last_api_had_rate_limit = False

    def _sleep_before_retry(self, attempt: int, *, retry_after: int | None = None) -> None:
        if retry_after is not None:
            base = min(MAX_RETRY_SLEEP_SECONDS, float(max(1, retry_after)))
            (self._sleep_fn or time.sleep)(base)
            return
        else:
            base = min(8.0, float(2 ** max(0, attempt)))
        # Small jitter avoids synchronized retries while never materially ignoring
        # Telegram's server-provided retry_after value.
        (self._sleep_fn or time.sleep)(base + random.uniform(0.0, 0.25))

    def _safe_api_description(self, payload: dict[str, Any]) -> str:
        description = str(payload.get("description") or "Telegram API error")[:600]
        if self.token:
            description = description.replace(self.token, "<redacted>")
        return description.replace(self.base, "<telegram-api>")

    def api(
        self,
        method: str,
        *,
        data: dict[str, Any] | None = None,
        files=None,
        timeout: int = 60,
        attempts: int = 3,
    ) -> Any:
        attempts = max(1, int(attempts))
        self._last_api_had_rate_limit = False
        for attempt in range(attempts):
            _rewind_files(files)
            try:
                response = self.session.post(
                    f"{self.base}/{method}",
                    data=data or {},
                    files=files,
                    timeout=timeout,
                )
            except requests.RequestException as exc:
                if attempt + 1 < attempts:
                    self._sleep_before_retry(attempt)
                    continue
                # Do not include/chains requests exception: it may contain the Bot API
                # URL, which embeds TELEGRAM_BOT_TOKEN.
                raise TelegramTransientError(
                    f"Telegram network request failed ({type(exc).__name__})."
                ) from None

            try:
                payload = response.json()
            except ValueError:
                if response.status_code >= 500:
                    if attempt + 1 < attempts:
                        self._sleep_before_retry(attempt)
                        continue
                    raise TelegramTransientError(
                        f"Telegram returned HTTP {response.status_code} without valid JSON."
                    ) from None
                raise TelegramPermanentError(
                    f"Telegram returned HTTP {response.status_code} without valid JSON."
                ) from None
            if not isinstance(payload, dict):
                raise TelegramPermanentError("Telegram returned an invalid JSON payload.")

            try:
                error_code = int(payload.get("error_code", 0) or 0)
            except (TypeError, ValueError):
                raise TelegramPermanentError("Telegram returned an invalid error_code.") from None
            if response.status_code == 429 or error_code == 429:
                self._last_api_had_rate_limit = True
                try:
                    retry_after = int((payload.get("parameters") or {}).get("retry_after", 1) or 1)
                except (TypeError, ValueError):
                    retry_after = 1
                if attempt + 1 < attempts:
                    self._sleep_before_retry(attempt, retry_after=retry_after)
                    continue
                raise TelegramRateLimitError(retry_after)

            if response.status_code >= 500 or error_code >= 500:
                if attempt + 1 < attempts:
                    self._sleep_before_retry(attempt)
                    continue
                raise TelegramTransientError(f"Telegram {method} temporarily failed with HTTP {response.status_code}.")

            if not response.ok or not payload.get("ok"):
                raise TelegramPermanentError(
                    f"Telegram {method} failed: {self._safe_api_description(payload)}"
                )
            return payload.get("result")

        raise TelegramTransientError("Telegram request exhausted its retry budget.")

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

    def _encode_reply_markup(self, reply_markup: dict[str, Any] | None) -> dict[str, Any] | None:
        if not reply_markup or "inline_keyboard" not in reply_markup:
            return reply_markup
        encoded = {key: value for key, value in reply_markup.items() if key != "inline_keyboard"}
        rows = []
        for row in reply_markup.get("inline_keyboard", []):
            encoded_row = []
            for button in row:
                item = dict(button)
                if "callback_data" in item:
                    raw = str(item["callback_data"])
                    byte_length = len(raw.encode("utf-8"))
                    if byte_length < 1:
                        raise CallbackDataError("Callback data must not be empty.")
                    if byte_length > CALLBACK_MAX_BYTES:
                        if self.callback_store is None:
                            raise CallbackDataError(
                                "Callback data exceeds 64 UTF-8 bytes and no durable callback store is configured."
                            )
                        raw = self.callback_store.encode(raw)
                    item["callback_data"] = raw
                encoded_row.append(item)
            rows.append(encoded_row)
        encoded["inline_keyboard"] = rows
        return encoded

    def decode_callback_data(self, value: str) -> str:
        value = str(value)
        byte_length = len(value.encode("utf-8"))
        if byte_length < 1 or byte_length > CALLBACK_MAX_BYTES:
            raise CallbackDataError("Callback data has an invalid UTF-8 byte length.")
        if value.startswith("cb:"):
            if self.callback_store is None:
                raise CallbackDataError("Callback token cannot be resolved.")
            return self.callback_store.decode(value)
        return value

    def send_message(
        self,
        text: str,
        *,
        chat_id: int | None = None,
        reply_markup: dict[str, Any] | None = None,
        disable_preview: bool = True,
        delivery_key: str | None = None,
    ) -> dict[str, Any]:
        """Send every character, splitting long Unicode text at safe boundaries.

        Inline keyboards are attached only to the final part. When delivery_key is
        supplied and a durable MessageDeliveryStore is configured, each successful
        part is checkpointed so a later retry skips already-confirmed parts.
        """
        with self._delivery_lock:
            full_text = str(text)
            parts: list[str] | None = None
            if delivery_key and self.message_delivery_store is not None:
                persisted = self.message_delivery_store.get_plan(delivery_key)
                if persisted is not None:
                    _persisted_text, parts = persisted
            if parts is None:
                parts = split_telegram_text(full_text)
                if delivery_key and self.message_delivery_store is not None and parts:
                    self.message_delivery_store.save_plan(delivery_key, full_text, parts)
            if not parts:
                raise TelegramPermanentError("Telegram message text must not be empty.")
            target_chat = self.review_chat_id if chat_id is None else chat_id
            last_result: dict[str, Any] = {}
            sent_network_part = False
            previous_send_was_rate_limited = False
            for index, part in enumerate(parts):
                is_final = index == len(parts) - 1
                if delivery_key and self.message_delivery_store is not None:
                    confirmed = self.message_delivery_store.confirmed_message_id(delivery_key, index, part)
                    if confirmed is not None:
                        last_result = {"message_id": confirmed}
                        continue
                if sent_network_part and not previous_send_was_rate_limited and self.send_pacing_seconds:
                    (self._sleep_fn or time.sleep)(self.send_pacing_seconds)
                data: dict[str, Any] = {
                    "chat_id": target_chat,
                    "text": part,
                    "disable_web_page_preview": "true" if disable_preview else "false",
                }
                if is_final:
                    encoded_markup = self._encode_reply_markup(reply_markup)
                    if encoded_markup:
                        data["reply_markup"] = json.dumps(encoded_markup, ensure_ascii=False)
                result = self.api("sendMessage", data=data)
                sent_network_part = True
                previous_send_was_rate_limited = self._last_api_had_rate_limit
                if not isinstance(result, dict):
                    result = {}
                last_result = result
                if delivery_key and self.message_delivery_store is not None:
                    self.message_delivery_store.confirm(
                        delivery_key,
                        index,
                        part,
                        int(result.get("message_id", 0) or 0),
                    )
            return last_result

    def edit_message_text(
        self,
        message_id: int,
        text: str,
        *,
        reply_markup: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        with self._delivery_lock:
            return self._edit_message_text_locked(message_id, text, reply_markup=reply_markup)

    def _edit_message_text_locked(
        self,
        message_id: int,
        text: str,
        *,
        reply_markup: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        parts = split_telegram_text(str(text))
        if not parts:
            raise TelegramPermanentError("Edited Telegram message text must not be empty.")
        first_markup = reply_markup if len(parts) == 1 else None
        data: dict[str, Any] = {
            "chat_id": self.review_chat_id,
            "message_id": message_id,
            "text": parts[0],
            "disable_web_page_preview": "true",
        }
        if first_markup is not None:
            data["reply_markup"] = json.dumps(self._encode_reply_markup(first_markup), ensure_ascii=False)
        result = self.api("editMessageText", data=data)
        if not isinstance(result, dict):
            result = {}
        if len(parts) == 1:
            return result

        # Re-editing part 1 is idempotent. Extra parts use a deterministic receipt
        # key so retries after a partial split do not duplicate already-sent tails.
        digest = hashlib.sha256(str(text).encode("utf-8")).hexdigest()[:24]
        previous_network_send = True
        previous_send_was_rate_limited = self._last_api_had_rate_limit
        for index, part in enumerate(parts[1:], start=1):
            tail_key = f"edit:{message_id}:{digest}:{index}"
            confirmed = None
            if self.message_delivery_store is not None:
                confirmed = self.message_delivery_store.confirmed_message_id(tail_key, 0, part)
            if confirmed is None and previous_network_send and not previous_send_was_rate_limited and self.send_pacing_seconds:
                (self._sleep_fn or time.sleep)(self.send_pacing_seconds)
            result = self.send_message(
                part,
                reply_markup=reply_markup if index == len(parts) - 1 else None,
                delivery_key=tail_key,
            )
            if confirmed is None:
                previous_network_send = True
                previous_send_was_rate_limited = self._last_api_had_rate_limit
        return result

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
        with ExitStack() as stack:
            handle = stack.enter_context(item.path.open("rb"))
            files = {field: (item.path.name, handle, item.content_type)}
            data: dict[str, Any] = {"chat_id": self.review_chat_id}
            if item.kind == "video":
                data["supports_streaming"] = "true"
                for key in ("duration", "width", "height"):
                    value = int(item.metadata.get(key) or 0)
                    if value > 0:
                        data[key] = str(value)
                if item.thumbnail_path is not None and item.thumbnail_path.exists():
                    thumbnail = stack.enter_context(item.thumbnail_path.open("rb"))
                    files["thumbnail"] = (item.thumbnail_path.name, thumbnail, "image/jpeg")
                    data["thumbnail"] = "attach://thumbnail"
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
                    for metadata_key in ("duration", "width", "height"):
                        value = int(item.metadata.get(metadata_key) or 0)
                        if value > 0:
                            descriptor[metadata_key] = value
                    if item.thumbnail_path is not None and item.thumbnail_path.exists():
                        thumbnail_key = f"thumbnail{index}"
                        thumbnail = item.thumbnail_path.open("rb")
                        handles.append(thumbnail)
                        files[thumbnail_key] = (item.thumbnail_path.name, thumbnail, "image/jpeg")
                        descriptor["thumbnail"] = f"attach://{thumbnail_key}"
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


def split_telegram_text(text: str, limit: int = TELEGRAM_SAFE_TEXT_TARGET) -> list[str]:
    """Create dynamic, labeled Telegram parts using semantic boundaries."""
    text = str(text)
    if not text:
        return []
    limit = min(TELEGRAM_TEXT_LIMIT, max(1, int(limit)))
    if len(text) <= limit:
        return [text]

    total = 2
    while True:
        label_lengths = [len(f"بخش {index} از {total}\n\n") for index in range(1, total + 1)]
        content_limit = max(1, limit - max(label_lengths))
        chunks = _split_unlabeled_text(text, content_limit)
        actual_total = len(chunks)
        if actual_total == total:
            break
        total = actual_total
    parts = [f"بخش {index} از {total}\n\n{chunk}" for index, chunk in enumerate(chunks, start=1)]
    if any(len(part) > limit for part in parts):
        raise TelegramPermanentError("Unable to construct Telegram parts within the safe limit.")
    return parts


def strip_part_label(part: str) -> str:
    return _PART_LABEL_RE.sub("", str(part), count=1)


def _split_unlabeled_text(text: str, limit: int) -> list[str]:
    parts: list[str] = []
    remaining = text
    while len(remaining) > limit:
        window = remaining[:limit]
        floor = max(1, limit // 3)
        split_at = 0
        paragraph = window.rfind("\n\n")
        if paragraph >= floor:
            split_at = paragraph + 2
        if not split_at:
            newline = window.rfind("\n")
            if newline >= floor:
                split_at = newline + 1
        if not split_at:
            sentence = max(window.rfind(mark) for mark in (". ", "! ", "? ", "؟ ", "。", "！", "？"))
            if sentence >= floor:
                split_at = sentence + (1 if window[sentence] in "。！？" else 2)
        if not split_at:
            whitespace = max(window.rfind(" "), window.rfind("\t"))
            if whitespace >= floor:
                split_at = whitespace + 1
        if not split_at:
            split_at = limit
        parts.append(remaining[:split_at])
        remaining = remaining[split_at:]
    if remaining:
        parts.append(remaining)
    return parts


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
    """Build raw callback markup without ever truncating routing identifiers.

    TelegramBot encodes any payload over 64 UTF-8 bytes into a durable opaque token
    immediately before transmission.
    """
    return {
        "inline_keyboard": [
            [{"text": label, "callback_data": str(data)} for label, data in row]
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
