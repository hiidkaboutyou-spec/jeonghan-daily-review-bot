from __future__ import annotations

import json
from typing import Any

from .media_file_cache import CachedTelegramMedia
from .telegram import TelegramBot


class PrivateReviewTelegramBot(TelegramBot):
    """Telegram helpers that never target anything except the configured review chat."""

    def send_cached_media(self, cached: list[CachedTelegramMedia]) -> list[dict[str, Any]]:
        if not cached:
            return []
        sent: list[dict[str, Any]] = []
        for offset in range(0, len(cached), 10):
            chunk = cached[offset : offset + 10]
            if len(chunk) == 1:
                item = chunk[0]
                method = "sendPhoto" if item.kind == "photo" else "sendVideo"
                field = "photo" if item.kind == "photo" else "video"
                data: dict[str, Any] = {"chat_id": self.review_chat_id, field: item.file_id}
                if item.kind == "video":
                    data["supports_streaming"] = "true"
                sent.append(self.api(method, data=data, timeout=90))
            else:
                media = []
                for item in chunk:
                    descriptor: dict[str, Any] = {
                        "type": "photo" if item.kind == "photo" else "video",
                        "media": item.file_id,
                    }
                    if item.kind == "video":
                        descriptor["supports_streaming"] = True
                    media.append(descriptor)
                sent.extend(
                    list(
                        self.api(
                            "sendMediaGroup",
                            data={"chat_id": self.review_chat_id, "media": json.dumps(media)},
                            timeout=120,
                        )
                        or []
                    )
                )
        return sent


def telegram_file_identity(message: dict[str, Any], kind: str) -> tuple[str, str]:
    if kind == "photo":
        photos = list(message.get("photo") or [])
        if not photos:
            return "", ""
        best = photos[-1]
        return str(best.get("file_id") or ""), str(best.get("file_unique_id") or "")
    if kind == "video":
        video = message.get("video") or {}
        return str(video.get("file_id") or ""), str(video.get("file_unique_id") or "")
    return "", ""
