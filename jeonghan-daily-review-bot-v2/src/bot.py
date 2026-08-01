from __future__ import annotations

import asyncio
import base64
import json
import logging
import mimetypes
import os
import re
import subprocess
import tempfile
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlencode, urlparse, urlunparse

import requests
import yaml

ROOT = Path(__file__).resolve().parents[1]
CONFIG_PATH = ROOT / "config.yml"
STYLE_PATH = ROOT / "style_guide.md"
STATE_PATH = ROOT / "state" / "state.json"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
)
log = logging.getLogger("jeonghan-daily-bot")


class BotError(RuntimeError):
    pass


def env(name: str, required: bool = True) -> str:
    value = os.getenv(name, "").strip()
    if required and not value:
        raise BotError(f"Missing required environment variable: {name}")
    return value


def load_json(path: Path, fallback: dict[str, Any]) -> dict[str, Any]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return fallback.copy()
    except json.JSONDecodeError as exc:
        raise BotError(f"Invalid JSON in {path}: {exc}") from exc


def save_state(state: dict[str, Any]) -> None:
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = STATE_PATH.with_suffix(".tmp")
    tmp.write_text(
        json.dumps(state, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    tmp.replace(STATE_PATH)


def load_config() -> dict[str, Any]:
    try:
        config = yaml.safe_load(CONFIG_PATH.read_text(encoding="utf-8")) or {}
    except FileNotFoundError as exc:
        raise BotError("config.yml not found") from exc
    if not isinstance(config, dict):
        raise BotError("config.yml must contain a YAML object")
    return config


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def iso_now() -> str:
    return utc_now().isoformat()


def truncate(text: str, limit: int) -> str:
    text = text.strip()
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def safe_json_from_text(text: str) -> dict[str, Any]:
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    try:
        value = json.loads(text)
        return value if isinstance(value, dict) else {}
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", text, flags=re.DOTALL)
        if not match:
            return {}
        try:
            value = json.loads(match.group(0))
            return value if isinstance(value, dict) else {}
        except json.JSONDecodeError:
            return {}


class Telegram:
    def __init__(self, token: str) -> None:
        self.base = f"https://api.telegram.org/bot{token}"
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": "jeonghan-daily-bot/1.0"})

    def call(
        self,
        method: str,
        *,
        data: dict[str, Any] | None = None,
        files: dict[str, Any] | None = None,
        timeout: int = 60,
    ) -> Any:
        response = self.session.post(
            f"{self.base}/{method}", data=data or {}, files=files, timeout=timeout
        )
        try:
            payload = response.json()
        except ValueError as exc:
            raise BotError(f"Telegram {method}: invalid response {response.status_code}") from exc
        if not response.ok or not payload.get("ok"):
            raise BotError(f"Telegram {method}: {payload}")
        return payload.get("result")

    def send_message(
        self,
        chat_id: str,
        text: str,
        *,
        reply_markup: dict[str, Any] | None = None,
        disable_preview: bool = True,
        reply_to_message_id: int | None = None,
    ) -> dict[str, Any]:
        data: dict[str, Any] = {
            "chat_id": chat_id,
            "text": truncate(text, 4096),
            "disable_web_page_preview": json.dumps(disable_preview),
        }
        if reply_markup:
            data["reply_markup"] = json.dumps(reply_markup, ensure_ascii=False)
        if reply_to_message_id:
            data["reply_parameters"] = json.dumps({"message_id": reply_to_message_id})
        return self.call("sendMessage", data=data)

    def edit_message_text(self, chat_id: str, message_id: int, text: str) -> None:
        try:
            self.call(
                "editMessageText",
                data={
                    "chat_id": chat_id,
                    "message_id": str(message_id),
                    "text": truncate(text, 4096),
                    "disable_web_page_preview": "true",
                },
            )
        except BotError as exc:
            log.warning("Could not edit review message: %s", exc)

    def answer_callback(self, callback_id: str, text: str = "") -> None:
        data = {"callback_query_id": callback_id}
        if text:
            data["text"] = truncate(text, 200)
        try:
            self.call("answerCallbackQuery", data=data)
        except BotError as exc:
            log.warning("Could not answer callback: %s", exc)

    def get_updates(self, offset: int) -> list[dict[str, Any]]:
        return self.call(
            "getUpdates",
            data={
                "offset": str(offset),
                "timeout": "0",
                "allowed_updates": json.dumps(["message", "callback_query"]),
            },
            timeout=30,
        )

    def send_remote_photo_preview(
        self, chat_id: str, url: str, reply_to_message_id: int
    ) -> None:
        try:
            self.call(
                "sendPhoto",
                data={
                    "chat_id": chat_id,
                    "photo": url,
                    "caption": "پیش‌نمایش مدیا",
                    "reply_parameters": json.dumps({"message_id": reply_to_message_id}),
                },
            )
        except BotError as exc:
            log.info("Telegram could not fetch preview media: %s", exc)

    def send_local_single(
        self,
        chat_id: str,
        path: Path,
        media_type: str,
        caption: str,
    ) -> None:
        mime = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        method = "sendPhoto" if media_type == "photo" else "sendVideo"
        field = "photo" if media_type == "photo" else "video"
        with path.open("rb") as handle:
            self.call(
                method,
                data={"chat_id": chat_id, "caption": truncate(caption, 1024)},
                files={field: (path.name, handle, mime)},
                timeout=180,
            )

    def send_local_album(
        self,
        chat_id: str,
        items: list[tuple[Path, str]],
        caption: str,
    ) -> None:
        media: list[dict[str, Any]] = []
        files: dict[str, Any] = {}
        handles = []
        try:
            for index, (path, media_type) in enumerate(items):
                key = f"media{index}"
                mime = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
                handle = path.open("rb")
                handles.append(handle)
                files[key] = (path.name, handle, mime)
                entry: dict[str, Any] = {
                    "type": "photo" if media_type == "photo" else "video",
                    "media": f"attach://{key}",
                }
                if index == 0:
                    entry["caption"] = truncate(caption, 1024)
                media.append(entry)
            self.call(
                "sendMediaGroup",
                data={"chat_id": chat_id, "media": json.dumps(media, ensure_ascii=False)},
                files=files,
                timeout=240,
            )
        finally:
            for handle in handles:
                handle.close()


class Gemini:
    def __init__(self, api_key: str, model: str, style_guide: str) -> None:
        self.api_key = api_key
        self.model = model
        self.style_guide = style_guide
        self.session = requests.Session()

    def _image_part(self, url: str | None) -> dict[str, Any] | None:
        if not url:
            return None
        try:
            response = requests.get(
                original_photo_url(url),
                headers={"User-Agent": "Mozilla/5.0"},
                timeout=30,
            )
            response.raise_for_status()
            if len(response.content) > 5_000_000:
                return None
            mime = response.headers.get("content-type", "image/jpeg").split(";")[0]
            if not mime.startswith("image/"):
                return None
            return {
                "inlineData": {
                    "mimeType": mime,
                    "data": base64.b64encode(response.content).decode("ascii"),
                }
            }
        except requests.RequestException as exc:
            log.info("Gemini image preview unavailable: %s", exc)
            return None

    def generate(
        self,
        tweet: dict[str, Any],
        *,
        humor_level: int,
        rewrite_instruction: str = "",
    ) -> dict[str, Any]:
        prompt = f"""
تو ادیتور و مترجم کانال دیلی یون جونگهان از گروه SEVENTEEN هستی.
بر اساس منبع، یک پیش‌نویس فارسی دقیق و طبیعی تولید کن.

قوانین قطعی:
- هیچ اطلاعات، نقل‌قول، نام یا اتفاقی را اختراع نکن.
- اگر متن کره‌ای/انگلیسی دارد، معنی را دقیق و روان ترجمه کن.
- شوخی نباید معنی خبر یا ترجمه را تغییر دهد.
- اگر پست واقعاً دربارهٔ جونگهان نیست، relevant=false بده.
- اگر فقط به دلیل تصویر احتمال می‌دهی مربوط است، عدم قطعیت را در notes بنویس.
- caption باید آمادهٔ کپی در تلگرام، کوتاه و بدون توضیح متا باشد.
- لینک منبع را داخل caption نگذار.
- سطح هیومر: {humor_level} از ۲.
{rewrite_instruction}

راهنمای لحن ادمین:
---
{self.style_guide}
---

اطلاعات منبع:
نام اکانت: @{tweet['source_username']}
تاریخ UTC: {tweet['date']}
متن اصلی:
{tweet['text']}

تعداد عکس: {tweet['photo_count']}
تعداد ویدیو/GIF: {tweet['video_count']}

فقط JSON معتبر با این کلیدها برگردان:
{{
  "relevant": true,
  "confidence": 0.0,
  "category": "official_update|translation|photo|video|fan_update|other",
  "translation": "ترجمهٔ دقیق یا رشتهٔ خالی",
  "caption": "کپشن نهایی فارسی",
  "notes": "یادداشت کوتاه برای ادمین یا رشتهٔ خالی"
}}
""".strip()

        parts: list[dict[str, Any]] = [{"text": prompt}]
        image_part = self._image_part(tweet.get("preview_image_url"))
        if image_part:
            parts.append(image_part)

        payload = {
            "contents": [{"role": "user", "parts": parts}],
            "generationConfig": {
                "temperature": 0.8 if humor_level else 0.35,
                "responseMimeType": "application/json",
                "maxOutputTokens": 1200,
            },
        }
        endpoint = (
            f"https://generativelanguage.googleapis.com/v1beta/models/"
            f"{self.model}:generateContent?key={self.api_key}"
        )
        last_error: Exception | None = None
        for attempt in range(3):
            try:
                response = self.session.post(endpoint, json=payload, timeout=90)
                response.raise_for_status()
                result = response.json()
                text = result["candidates"][0]["content"]["parts"][0]["text"]
                parsed = safe_json_from_text(text)
                if parsed.get("caption") is None:
                    raise ValueError(f"Gemini returned unusable JSON: {text[:300]}")
                return parsed
            except (requests.RequestException, KeyError, IndexError, ValueError) as exc:
                last_error = exc
                time.sleep(2**attempt)
        raise BotError(f"Gemini request failed: {last_error}")


class Groq:
    """Text-only free-tier fallback with the same generate() interface as Gemini."""

    def __init__(self, api_key: str, model: str, style_guide: str) -> None:
        self.api_key = api_key
        self.model = model
        self.style_guide = style_guide
        self.session = requests.Session()

    def generate(
        self,
        tweet: dict[str, Any],
        *,
        humor_level: int,
        rewrite_instruction: str = "",
    ) -> dict[str, Any]:
        prompt = f"""
تو ادیتور و مترجم کانال دیلی یون جونگهان از گروه SEVENTEEN هستی.
بر اساس منبع، یک پیش‌نویس فارسی دقیق و طبیعی تولید کن.

قوانین قطعی:
- هیچ اطلاعات، نقل‌قول، نام یا اتفاقی را اختراع نکن.
- اگر متن کره‌ای/انگلیسی دارد، معنی را دقیق و روان ترجمه کن.
- شوخی نباید معنی خبر یا ترجمه را تغییر دهد.
- اگر پست واقعاً دربارهٔ جونگهان نیست، relevant=false بده.
- چون این مدل تصویر را نمی‌بیند، دربارهٔ محتوای عکس چیزی را حدس نزن.
- caption باید آمادهٔ کپی در تلگرام، کوتاه و بدون توضیح متا باشد.
- لینک منبع را داخل caption نگذار.
- سطح هیومر: {humor_level} از ۲.
{rewrite_instruction}

راهنمای لحن ادمین:
---
{self.style_guide}
---

اطلاعات منبع:
نام اکانت: @{tweet['source_username']}
تاریخ UTC: {tweet['date']}
متن اصلی:
{tweet['text']}

تعداد عکس: {tweet['photo_count']}
تعداد ویدیو/GIF: {tweet['video_count']}

فقط JSON معتبر با این کلیدها برگردان:
{{
  "relevant": true,
  "confidence": 0.0,
  "category": "official_update|translation|photo|video|fan_update|other",
  "translation": "ترجمهٔ دقیق یا رشتهٔ خالی",
  "caption": "کپشن نهایی فارسی",
  "notes": "یادداشت کوتاه برای ادمین یا رشتهٔ خالی"
}}
""".strip()

        payload = {
            "model": self.model,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0.8 if humor_level else 0.35,
            "max_tokens": 1200,
            "response_format": {"type": "json_object"},
        }
        last_error: Exception | None = None
        for attempt in range(3):
            try:
                response = self.session.post(
                    "https://api.groq.com/openai/v1/chat/completions",
                    headers={
                        "Authorization": f"Bearer {self.api_key}",
                        "Content-Type": "application/json",
                    },
                    json=payload,
                    timeout=90,
                )
                response.raise_for_status()
                text = response.json()["choices"][0]["message"]["content"]
                parsed = safe_json_from_text(text)
                if parsed.get("caption") is None:
                    raise ValueError(f"Groq returned unusable JSON: {text[:300]}")
                return parsed
            except (requests.RequestException, KeyError, IndexError, ValueError) as exc:
                last_error = exc
                time.sleep(2**attempt)
        raise BotError(f"Groq request failed: {last_error}")


def original_photo_url(url: str) -> str:
    parsed = urlparse(url)
    query = parse_qs(parsed.query)
    query["name"] = ["orig"]
    return urlunparse(parsed._replace(query=urlencode(query, doseq=True)))


def media_from_tweet(tweet: Any) -> list[dict[str, str]]:
    items: list[dict[str, str]] = []
    media = getattr(tweet, "media", None)
    if not media:
        return items
    for photo in getattr(media, "photos", []) or []:
        items.append({"type": "photo", "url": original_photo_url(photo.url)})
    for video in getattr(media, "videos", []) or []:
        variants = sorted(
            getattr(video, "variants", []) or [],
            key=lambda item: getattr(item, "bitrate", 0),
            reverse=True,
        )
        if variants:
            items.append(
                {
                    "type": "video",
                    "url": variants[0].url,
                    "thumbnail": getattr(video, "thumbnailUrl", ""),
                }
            )
    for animated in getattr(media, "animated", []) or []:
        items.append(
            {
                "type": "video",
                "url": animated.videoUrl,
                "thumbnail": animated.thumbnailUrl,
            }
        )
    return items


def tweet_to_record(tweet: Any) -> dict[str, Any]:
    media = media_from_tweet(tweet)
    quoted = getattr(tweet, "quotedTweet", None)
    text = getattr(tweet, "rawContent", "") or ""
    if quoted and getattr(quoted, "rawContent", ""):
        text += f"\n\nQuoted post: {quoted.rawContent}"
    preview = ""
    for item in media:
        preview = item.get("url", "") if item["type"] == "photo" else item.get("thumbnail", "")
        if preview:
            break
    return {
        "tweet_id": str(tweet.id),
        "source_username": tweet.user.username,
        "source_url": tweet.url,
        "date": tweet.date.astimezone(timezone.utc).isoformat(),
        "text": text.strip(),
        "is_reply": getattr(tweet, "inReplyToTweetId", None) is not None,
        "is_retweet": getattr(tweet, "retweetedTweet", None) is not None,
        "media": media,
        "photo_count": sum(item["type"] == "photo" for item in media),
        "video_count": sum(item["type"] == "video" for item in media),
        "preview_image_url": preview,
    }


def contains_keyword(text: str, keywords: list[str]) -> bool:
    haystack = text.casefold()
    return any(keyword.casefold() in haystack for keyword in keywords if keyword.strip())


def review_keyboard(tweet_id: str) -> dict[str, Any]:
    return {
        "inline_keyboard": [
            [
                {"text": "😂 بامزه‌تر", "callback_data": f"rewrite:{tweet_id}"},
                {"text": "✅ فرستادم", "callback_data": f"done:{tweet_id}"},
            ],
            [{"text": "🗑 رد", "callback_data": f"skip:{tweet_id}"}],
        ]
    }


def review_text(pending: dict[str, Any]) -> str:
    translation = pending.get("translation", "").strip()
    notes = pending.get("notes", "").strip()
    sections = [
        "🪽 پیش‌نویس جدید",
        f"منبع: @{pending['source_username']}",
        pending["source_url"],
        "",
        "متن اصلی:",
        truncate(pending.get("source_text", ""), 1000) or "—",
    ]
    if translation:
        sections.extend(["", "ترجمه:", truncate(translation, 1000)])
    sections.extend(["", "کپشن پیشنهادی:", truncate(pending["caption"], 1400)])
    if notes:
        sections.extend(["", "یادداشت:", truncate(notes, 500)])
    return "\n".join(sections)


def download_media_item(item: dict[str, str], directory: Path, index: int) -> tuple[Path, str]:
    url = item["url"]
    media_type = item["type"]
    suffix = ".jpg" if media_type == "photo" else ".mp4"
    path = directory / f"media-{index}{suffix}"
    with requests.get(
        url,
        headers={"User-Agent": "Mozilla/5.0"},
        stream=True,
        timeout=90,
    ) as response:
        response.raise_for_status()
        total = 0
        with path.open("wb") as handle:
            for chunk in response.iter_content(chunk_size=1024 * 1024):
                if not chunk:
                    continue
                total += len(chunk)
                if total > 49 * 1024 * 1024:
                    raise BotError("Media exceeds the conservative 49 MB bot limit")
                handle.write(chunk)
    return path, media_type


def channel_caption(pending: dict[str, Any], config: dict[str, Any]) -> str:
    caption = pending["caption"].strip()
    if config.get("telegram", {}).get("source_link_in_channel", False):
        caption += f"\n\nمنبع: {pending['source_url']}"
    return caption



def download_video_with_ytdlp(source_url: str, directory: Path) -> tuple[Path, str]:
    """Fallback when X's direct video URL has expired or cannot be fetched."""
    template = str(directory / "ytdlp-fallback.%(ext)s")
    command = [
        "yt-dlp",
        "--no-playlist",
        "--no-warnings",
        "--max-filesize",
        "49M",
        "-f",
        "best[ext=mp4]/best",
        "-o",
        template,
        source_url,
    ]
    result = subprocess.run(command, capture_output=True, text=True, timeout=180)
    if result.returncode != 0:
        raise BotError(f"yt-dlp failed: {truncate(result.stderr or result.stdout, 500)}")
    candidates = sorted(directory.glob("ytdlp-fallback.*"))
    if not candidates:
        raise BotError("yt-dlp finished without a downloadable file")
    path = candidates[0]
    if path.stat().st_size > 49 * 1024 * 1024:
        path.unlink(missing_ok=True)
        raise BotError("yt-dlp media exceeds the conservative 49 MB limit")
    return path, "video"


def publish_pending(
    telegram: Telegram,
    pending: dict[str, Any],
    channel_id: str,
    config: dict[str, Any],
) -> None:
    caption = channel_caption(pending, config)
    media = pending.get("media", [])
    if not media:
        telegram.send_message(channel_id, caption, disable_preview=False)
        return

    max_items = int(config.get("telegram", {}).get("max_album_items", 10))
    media = media[:max_items]
    with tempfile.TemporaryDirectory(prefix="jeonghan-media-") as tmp:
        directory = Path(tmp)
        downloaded: list[tuple[Path, str]] = []
        for index, item in enumerate(media):
            try:
                downloaded.append(download_media_item(item, directory, index))
            except (requests.RequestException, BotError) as exc:
                log.warning("Media download failed for %s: %s", item.get("url"), exc)

        requested_video = any(item.get("type") == "video" for item in media)
        downloaded_video = any(media_type == "video" for _, media_type in downloaded)
        if requested_video and not downloaded_video:
            try:
                downloaded.append(download_video_with_ytdlp(pending["source_url"], directory))
            except BotError as exc:
                log.warning("yt-dlp fallback failed for %s: %s", pending["source_url"], exc)

        if not downloaded:
            telegram.send_message(channel_id, caption + f"\n\n{pending['source_url']}", disable_preview=False)
        elif len(downloaded) == 1:
            telegram.send_local_single(channel_id, downloaded[0][0], downloaded[0][1], caption)
        else:
            telegram.send_local_album(channel_id, downloaded, caption)


def process_action(
    action: str,
    tweet_id: str,
    *,
    state: dict[str, Any],
    telegram: Telegram,
    gemini: Any,
    config: dict[str, Any],
    review_chat_id: str,
) -> str:
    pending = state.get("pending", {}).get(tweet_id)
    if not pending:
        return "این پیش‌نویس دیگر موجود نیست."

    if action == "done":
        message_id = pending.get("review_message_id")
        if message_id:
            telegram.edit_message_text(
                review_chat_id,
                int(message_id),
                review_text(pending) + "\n\n✅ خودت به کانال فرستادی",
            )
        state["pending"].pop(tweet_id, None)
        return "انجام‌شده علامت خورد ✅"

    if action == "skip":
        message_id = pending.get("review_message_id")
        if message_id:
            telegram.edit_message_text(
                review_chat_id,
                int(message_id),
                review_text(pending) + "\n\n🗑 رد شد",
            )
        state["pending"].pop(tweet_id, None)
        return "رد شد."

    if action == "rewrite":
        tweet = {
            "source_username": pending["source_username"],
            "date": pending["date"],
            "text": pending["source_text"],
            "photo_count": sum(item["type"] == "photo" for item in pending.get("media", [])),
            "video_count": sum(item["type"] == "video" for item in pending.get("media", [])),
            "preview_image_url": next(
                (
                    item.get("url") if item["type"] == "photo" else item.get("thumbnail")
                    for item in pending.get("media", [])
                    if item.get("url") or item.get("thumbnail")
                ),
                "",
            ),
        }
        result = gemini.generate(
            tweet,
            humor_level=2,
            rewrite_instruction=(
                "این بازنویسی دوم است. آن را واضحاً بامزه‌تر، خودمانی‌تر و شبیه واکنش زندهٔ "
                "ادمین کن، ولی هیچ واقعیتی را تغییر نده."
            ),
        )
        pending["caption"] = str(result.get("caption", pending["caption"])).strip()
        pending["translation"] = str(result.get("translation", pending.get("translation", ""))).strip()
        pending["notes"] = str(result.get("notes", "")).strip()
        old_message_id = pending.get("review_message_id")
        if old_message_id:
            telegram.edit_message_text(
                review_chat_id,
                int(old_message_id),
                review_text(pending) + "\n\n♻️ نسخهٔ جدیدِ آمادهٔ فوروارد پایین ارسال شد",
            )
        publish_pending(telegram, pending, review_chat_id, config)
        return "بامزه‌ترش کردم و نسخهٔ آماده را پایین فرستادم 😂"

    return "دستور ناشناخته است."


def process_telegram_updates(
    *,
    state: dict[str, Any],
    telegram: Telegram,
    gemini: Any,
    config: dict[str, Any],
    review_chat_id: str,
    admin_user_id: str,
) -> None:
    offset = int(state.get("telegram_update_offset", 0))
    updates = telegram.get_updates(offset + 1)
    for update in updates:
        update_id = int(update["update_id"])
        state["telegram_update_offset"] = max(
            int(state.get("telegram_update_offset", 0)), update_id
        )
        callback = update.get("callback_query")
        if callback:
            callback_id = callback["id"]
            user_id = str(callback.get("from", {}).get("id", ""))
            callback_chat = str(callback.get("message", {}).get("chat", {}).get("id", ""))
            if user_id != admin_user_id or callback_chat != review_chat_id:
                telegram.answer_callback(callback_id, "اجازهٔ این کار را نداری.")
                continue
            data = str(callback.get("data", ""))
            if ":" not in data:
                telegram.answer_callback(callback_id, "دستور نامعتبر")
                continue
            action, tweet_id = data.split(":", 1)
            try:
                result = process_action(
                    action,
                    tweet_id,
                    state=state,
                    telegram=telegram,
                    gemini=gemini,
                    config=config,
                    review_chat_id=review_chat_id,
                )
                telegram.answer_callback(callback_id, result)
            except Exception as exc:  # Keep the update loop alive and report to admin.
                log.exception("Telegram action failed")
                telegram.answer_callback(callback_id, "عملیات ناموفق بود؛ لاگ را ببین.")
                telegram.send_message(review_chat_id, f"⚠️ خطا برای {tweet_id}: {exc}")
            continue

        message = update.get("message") or {}
        user_id = str(message.get("from", {}).get("id", ""))
        chat_id = str(message.get("chat", {}).get("id", ""))
        text = str(message.get("text", "")).strip()
        if user_id != admin_user_id or chat_id != review_chat_id:
            continue
        match = re.fullmatch(r"/(done|skip|rewrite)(?:@\w+)?\s+(\d+)", text)
        if match:
            action, tweet_id = match.groups()
            try:
                result = process_action(
                    action,
                    tweet_id,
                    state=state,
                    telegram=telegram,
                    gemini=gemini,
                    config=config,
                    review_chat_id=review_chat_id,
                )
                telegram.send_message(review_chat_id, result)
            except Exception as exc:
                log.exception("Telegram command failed")
                telegram.send_message(review_chat_id, f"⚠️ خطا: {exc}")


async def fetch_records(config: dict[str, Any], x_cookie: str) -> list[tuple[dict[str, Any], dict[str, Any]]]:
    from twscrape import API, gather

    db_path = Path(tempfile.gettempdir()) / "jeonghan-twscrape.db"
    try:
        db_path.unlink()
    except FileNotFoundError:
        pass

    api = API(str(db_path), raise_when_no_account=True, wait_timeout=20)
    await api.pool.add_account_cookies("reader", x_cookie)

    polling = config.get("polling", {})
    limit = int(polling.get("tweets_per_source", 12))
    results: list[tuple[dict[str, Any], dict[str, Any]]] = []

    for source in config.get("sources", []):
        if not source.get("enabled", False):
            continue
        username = str(source.get("username", "")).lstrip("@").strip()
        if not username or username.startswith("REPLACE_"):
            continue
        try:
            user = await api.user_by_login(username)
            tweets = await gather(api.user_tweets(user.id, limit=limit))
            for tweet in tweets:
                results.append((tweet_to_record(tweet), source))
        except Exception as exc:
            log.exception("Could not fetch @%s", username)
            results.append(({"fetch_error": str(exc), "source_username": username}, source))

    return results


def make_pending(record: dict[str, Any], ai: dict[str, Any]) -> dict[str, Any]:
    caption = str(ai.get("caption", "")).strip()
    if not caption:
        caption = record["text"].strip() or "آپدیت جدید جونگهان 🪽"
    return {
        "tweet_id": record["tweet_id"],
        "source_username": record["source_username"],
        "source_url": record["source_url"],
        "source_text": record["text"],
        "date": record["date"],
        "media": record["media"],
        "caption": caption,
        "translation": str(ai.get("translation", "")).strip(),
        "notes": str(ai.get("notes", "")).strip(),
        "category": str(ai.get("category", "other")),
        "confidence": float(ai.get("confidence", 0.0) or 0.0),
        "created_at": iso_now(),
    }


def run() -> None:
    config = load_config()
    state = load_json(
        STATE_PATH,
        {
            "initialized": False,
            "seen_tweet_ids": [],
            "pending": {},
            "telegram_update_offset": 0,
            "last_heartbeat_week": "",
        },
    )
    state.setdefault("seen_tweet_ids", [])
    state.setdefault("pending", {})

    token = env("TELEGRAM_BOT_TOKEN")
    review_chat_id = env("TELEGRAM_REVIEW_CHAT_ID")
    admin_user_id = env("TELEGRAM_ADMIN_USER_ID")
    x_cookie = env("X_COOKIE")

    style_guide = STYLE_PATH.read_text(encoding="utf-8")
    ai_config = config.get("ai", {})
    provider = str(ai_config.get("provider", "gemini")).strip().lower()
    telegram = Telegram(token)
    if provider == "gemini":
        api_key = env("GEMINI_API_KEY")
        model = str(ai_config.get("gemini_model", "gemini-3.5-flash-lite"))
        gemini: Any = Gemini(api_key, model, style_guide)
    elif provider == "groq":
        api_key = env("GROQ_API_KEY")
        model = str(ai_config.get("groq_model", "llama-3.3-70b-versatile"))
        gemini = Groq(api_key, model, style_guide)
    else:
        raise BotError("ai.provider must be either 'gemini' or 'groq'")

    # First process approvals/rewrite requests that arrived since the previous run.
    process_telegram_updates(
        state=state,
        telegram=telegram,
        gemini=gemini,
        config=config,
        review_chat_id=review_chat_id,
        admin_user_id=admin_user_id,
    )

    enabled_sources = [s for s in config.get("sources", []) if s.get("enabled", False)]
    if not enabled_sources:
        log.warning("No enabled X sources in config.yml")
        current_week = utc_now().strftime("%G-W%V")
        if state.get("last_heartbeat_week") != current_week:
            state["last_heartbeat_week"] = current_week
        save_state(state)
        return

    records = asyncio.run(fetch_records(config, x_cookie))
    for record, _source in records:
        if record.get("fetch_error"):
            telegram.send_message(
                review_chat_id,
                f"⚠️ دریافت @{record['source_username']} ناموفق بود:\n{record['fetch_error']}",
            )

    valid_records = [pair for pair in records if not pair[0].get("fetch_error")]
    valid_records.sort(key=lambda pair: pair[0]["date"])

    seen = set(str(item) for item in state.get("seen_tweet_ids", []))
    polling = config.get("polling", {})
    keywords = [str(item) for item in config.get("keywords", [])]
    include_replies = bool(polling.get("include_replies", False))
    include_retweets = bool(polling.get("include_retweets", False))
    first_cutoff = utc_now() - timedelta(
        hours=float(polling.get("first_run_lookback_hours", 2))
    )
    max_drafts = int(polling.get("max_new_drafts_per_run", 8))
    minimum_confidence = float(config.get("ai", {}).get("minimum_relevance_confidence", 0.6))
    humor_level = int(config.get("ai", {}).get("default_humor_level", 1))
    drafts_created = 0

    for record, source in valid_records:
        tweet_id = record["tweet_id"]
        if tweet_id in seen:
            continue

        # Mark every fetched tweet as seen even when filtered, so it is not reconsidered forever.
        seen.add(tweet_id)

        date = datetime.fromisoformat(record["date"])
        if not state.get("initialized", False) and date < first_cutoff:
            continue
        if record["is_reply"] and not include_replies:
            continue
        if record["is_retweet"] and not include_retweets:
            continue
        if source.get("require_keywords", True) and not contains_keyword(record["text"], keywords):
            continue
        if drafts_created >= max_drafts:
            continue

        try:
            ai = gemini.generate(record, humor_level=humor_level)
        except BotError as exc:
            log.exception("Gemini generation failed for %s", tweet_id)
            ai = {
                "relevant": True,
                "confidence": 0.5,
                "category": "other",
                "translation": "",
                "caption": record["text"] or "آپدیت جدید جونگهان 🪽",
                "notes": f"ساخت خودکار کپشن ناموفق بود: {exc}",
            }

        relevant = bool(ai.get("relevant", False))
        confidence = float(ai.get("confidence", 0.0) or 0.0)
        if not relevant or confidence < minimum_confidence:
            log.info("Skipped low-confidence/non-relevant tweet %s", tweet_id)
            continue

        pending = make_pending(record, ai)
        message = telegram.send_message(
            review_chat_id,
            review_text(pending) + "\n\n⬇️ پست تمیز و آمادهٔ فوروارد در پیام بعدی است.",
            reply_markup=review_keyboard(tweet_id),
        )
        pending["review_message_id"] = message["message_id"]
        state["pending"][tweet_id] = pending
        publish_pending(telegram, pending, review_chat_id, config)
        drafts_created += 1

    state["initialized"] = True
    state["seen_tweet_ids"] = list(seen)[-5000:]
    current_week = utc_now().strftime("%G-W%V")
    if state.get("last_heartbeat_week") != current_week:
        state["last_heartbeat_week"] = current_week
    save_state(state)
    log.info("Run complete; created %d new drafts", drafts_created)


if __name__ == "__main__":
    try:
        run()
    except Exception as exc:
        log.exception("Fatal error")
        # Best-effort admin alert when Telegram credentials exist.
        token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
        review_chat = os.getenv("TELEGRAM_REVIEW_CHAT_ID", "").strip()
        if token and review_chat:
            try:
                Telegram(token).send_message(review_chat, f"🚨 خطای اصلی ربات:\n{exc}")
            except Exception:
                pass
        raise
