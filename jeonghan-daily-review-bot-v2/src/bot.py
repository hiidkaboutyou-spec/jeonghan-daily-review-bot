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
from difflib import SequenceMatcher
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
- اگر discovery_only=true است، آن را خبر رسمی معرفی نکن مگر خود منبع رسمی باشد.
- اگر چند منبع مقایسه شده‌اند و با هم تعارض دارند، تعارض را در notes بگو و چیزی را قطعی نکن.
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

وضعیت کشف: {tweet.get('origin', 'trusted')}
فقط خارج از منابع اصلی پیدا شده: {bool(tweet.get('discovery_only', False))}
نسخه با کمک جست‌وجوی عمومی کامل‌تر شده: {bool(tweet.get('completed_from_discovery', False))}
منابع مقایسه‌شده:
{truncate(str(tweet.get('source_context', '')), 3500) or 'فقط همان منبع'}

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
- اگر discovery_only=true است، آن را خبر رسمی معرفی نکن مگر خود منبع رسمی باشد.
- اگر چند منبع مقایسه شده‌اند و با هم تعارض دارند، تعارض را در notes بگو و چیزی را قطعی نکن.
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

وضعیت کشف: {tweet.get('origin', 'trusted')}
فقط خارج از منابع اصلی پیدا شده: {bool(tweet.get('discovery_only', False))}
نسخه با کمک جست‌وجوی عمومی کامل‌تر شده: {bool(tweet.get('completed_from_discovery', False))}
منابع مقایسه‌شده:
{truncate(str(tweet.get('source_context', '')), 3500) or 'فقط همان منبع'}

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


def _append_unique_media(
    target: list[dict[str, str]],
    extra: list[dict[str, str]],
) -> None:
    existing: set[tuple[str, str]] = {
        (str(item.get("type", "")), media_key(item)) for item in target
    }
    for item in extra:
        key = (str(item.get("type", "")), media_key(item))
        if not key[1] or key in existing:
            continue
        target.append(item)
        existing.add(key)


def tweet_to_record(tweet: Any) -> dict[str, Any]:
    media = media_from_tweet(tweet)
    quoted = getattr(tweet, "quotedTweet", None)
    retweeted = getattr(tweet, "retweetedTweet", None)

    # Fan accounts often quote the original update. Include the quoted media so
    # the review copy can be complete even when the wrapper tweet has no media.
    if quoted:
        _append_unique_media(media, media_from_tweet(quoted))
    if retweeted:
        _append_unique_media(media, media_from_tweet(retweeted))

    text = getattr(tweet, "rawContent", "") or ""
    if quoted and getattr(quoted, "rawContent", ""):
        text += f"\n\nQuoted post: {quoted.rawContent}"

    preview = ""
    for item in media:
        preview = item.get("url", "") if item["type"] == "photo" else item.get("thumbnail", "")
        if preview:
            break

    user = getattr(tweet, "user", None)
    links = [
        str(getattr(item, "url", "") or "")
        for item in (getattr(tweet, "links", None) or [])
        if getattr(item, "url", None)
    ]
    hashtags = [str(item) for item in (getattr(tweet, "hashtags", None) or [])]

    return {
        "tweet_id": str(tweet.id),
        "source_username": str(getattr(user, "username", "") or ""),
        "source_url": str(getattr(tweet, "url", "") or ""),
        "date": tweet.date.astimezone(timezone.utc).isoformat(),
        "text": text.strip(),
        "is_reply": getattr(tweet, "inReplyToTweetId", None) is not None,
        "is_retweet": retweeted is not None,
        "media": media,
        "photo_count": sum(item["type"] == "photo" for item in media),
        "video_count": sum(item["type"] == "video" for item in media),
        "preview_image_url": preview,
        "conversation_id": str(getattr(tweet, "conversationId", "") or ""),
        "quoted_tweet_id": str(getattr(quoted, "id", "") or "") if quoted else "",
        "retweeted_tweet_id": str(getattr(retweeted, "id", "") or "") if retweeted else "",
        "lang": str(getattr(tweet, "lang", "") or ""),
        "hashtags": hashtags,
        "links": links,
        "followers_count": int(getattr(user, "followersCount", 0) or 0),
        "verified": bool(getattr(user, "verified", False) or getattr(user, "blue", False)),
        "like_count": int(getattr(tweet, "likeCount", 0) or 0),
        "retweet_count": int(getattr(tweet, "retweetCount", 0) or 0),
        "quote_count": int(getattr(tweet, "quoteCount", 0) or 0),
        "view_count": int(getattr(tweet, "viewCount", 0) or 0),
    }


def contains_keyword(text: str, keywords: list[str]) -> bool:
    haystack = text.casefold()
    return any(keyword.casefold() in haystack for keyword in keywords if keyword.strip())


def normalize_match_text(text: str) -> str:
    text = text.casefold()
    text = re.sub(r"https?://\S+", " ", text)
    text = text.replace("quoted post:", " ")
    text = re.sub(r"[@#]([\w_]+)", r"\1", text, flags=re.UNICODE)
    text = re.sub(r"[^\w]+", " ", text, flags=re.UNICODE)
    return " ".join(text.split())


def media_key(item: dict[str, str]) -> str:
    raw = str(item.get("thumbnail") or item.get("url") or "").strip()
    if not raw:
        return ""
    parsed = urlparse(raw)
    # Query parameters on X media URLs usually describe size/format and should
    # not make the same image look different.
    return f"{parsed.netloc.casefold()}{parsed.path.casefold()}"


def record_media_keys(record: dict[str, Any]) -> set[str]:
    cached = record.get("media_keys")
    if isinstance(cached, list):
        return {str(item) for item in cached if str(item)}
    return {
        key
        for item in record.get("media", [])
        if (key := media_key(item))
    }


def record_date(record: dict[str, Any]) -> datetime:
    value = str(record.get("date", "") or "")
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return datetime(1970, 1, 1, tzinfo=timezone.utc)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def text_similarity(first: str, second: str) -> float:
    a = normalize_match_text(first)
    b = normalize_match_text(second)
    if not a or not b:
        return 0.0
    if a == b:
        return 1.0
    sequence = SequenceMatcher(None, a, b).ratio()
    left = set(a.split())
    right = set(b.split())
    union = left | right
    jaccard = len(left & right) / len(union) if union else 0.0
    return max(sequence, jaccard)


def records_are_same_update(
    first: dict[str, Any],
    second: dict[str, Any],
    *,
    similarity_threshold: float,
    merge_window_hours: float,
) -> bool:
    if str(first.get("tweet_id")) == str(second.get("tweet_id")):
        return True

    first_quoted = str(first.get("quoted_tweet_id", "") or "")
    second_quoted = str(second.get("quoted_tweet_id", "") or "")
    if first_quoted and first_quoted == second_quoted:
        return True

    first_keys = record_media_keys(first)
    second_keys = record_media_keys(second)
    if first_keys and second_keys and first_keys.intersection(second_keys):
        return True

    time_gap = abs((record_date(first) - record_date(second)).total_seconds()) / 3600
    if time_gap > merge_window_hours:
        return False

    first_text = normalize_match_text(str(first.get("text", "")))
    second_text = normalize_match_text(str(second.get("text", "")))
    if not first_text or not second_text:
        return False

    score = text_similarity(first_text, second_text)
    minimum_length = min(len(first_text), len(second_text))
    if minimum_length >= 24 and score >= similarity_threshold:
        return True

    # Reposts with media often add a short reaction or a translation. Permit a
    # lower text threshold only when both sides contain media.
    if first_keys and second_keys and minimum_length >= 12 and score >= 0.66:
        return True
    return False


def record_completeness_score(record: dict[str, Any]) -> float:
    media_count = len(record.get("media", []))
    video_count = sum(item.get("type") == "video" for item in record.get("media", []))
    text_length = min(len(str(record.get("text", ""))), 1600)
    trusted_bonus = 7.0 if record.get("origin") == "trusted" else 0.0
    verified_bonus = 3.0 if record.get("verified") else 0.0
    followers = max(int(record.get("followers_count", 0) or 0), 0)
    engagement = (
        int(record.get("like_count", 0) or 0)
        + int(record.get("retweet_count", 0) or 0) * 2
        + int(record.get("quote_count", 0) or 0) * 2
    )
    return (
        media_count * 28.0
        + video_count * 8.0
        + text_length / 45.0
        + trusted_bonus
        + verified_bonus
        + min(followers, 1_000_000) / 100_000.0
        + min(engagement, 10_000) / 2_000.0
    )


def merge_record_group(
    group: list[tuple[dict[str, Any], dict[str, Any]]],
) -> tuple[dict[str, Any], dict[str, Any]]:
    records = [record for record, _ in group]
    sources = [source for _, source in group]
    primary = max(records, key=record_completeness_score)
    merged = dict(primary)

    merged_media: list[dict[str, str]] = []
    for record in sorted(records, key=record_completeness_score, reverse=True):
        _append_unique_media(merged_media, list(record.get("media", [])))
    merged["media"] = merged_media
    merged["photo_count"] = sum(item.get("type") == "photo" for item in merged_media)
    merged["video_count"] = sum(item.get("type") == "video" for item in merged_media)
    merged["preview_image_url"] = next(
        (
            item.get("url", "") if item.get("type") == "photo" else item.get("thumbnail", "")
            for item in merged_media
            if item.get("url") or item.get("thumbnail")
        ),
        "",
    )

    # Prefer the most informative text while giving trusted sources a modest
    # advantage. Media completeness remains the strongest factor overall.
    def text_score(record: dict[str, Any]) -> float:
        return min(len(str(record.get("text", ""))), 2500) + (
            250 if record.get("origin") == "trusted" else 0
        )

    text_record = max(records, key=text_score)
    merged["text"] = str(text_record.get("text", "")).strip()

    source_urls: list[str] = []
    source_usernames: list[str] = []
    source_context: list[str] = []
    search_queries: list[str] = []
    member_ids: list[str] = []
    for record in records:
        url = str(record.get("source_url", "") or "")
        username = str(record.get("source_username", "") or "")
        tweet_id = str(record.get("tweet_id", "") or "")
        query = str(record.get("search_query", "") or "")
        if url and url not in source_urls:
            source_urls.append(url)
        if username and username.casefold() not in {u.casefold() for u in source_usernames}:
            source_usernames.append(username)
        if tweet_id and tweet_id not in member_ids:
            member_ids.append(tweet_id)
        if query and query not in search_queries:
            search_queries.append(query)
        text = str(record.get("text", "") or "").strip()
        if text:
            block = f"@{username}: {truncate(text, 900)}"
            if block not in source_context:
                source_context.append(block)

    trusted_records = [record for record in records if record.get("origin") == "trusted"]
    discovery_records = [record for record in records if record.get("origin") == "discovery"]
    trusted_max_media = max((len(record.get("media", [])) for record in trusted_records), default=0)
    trusted_max_text = max((len(str(record.get("text", ""))) for record in trusted_records), default=0)

    merged["source_urls"] = source_urls
    merged["source_usernames"] = source_usernames
    merged["source_context"] = "\n\n".join(source_context[:6])
    merged["search_queries"] = search_queries
    merged["member_tweet_ids"] = member_ids
    merged["discovery_only"] = bool(discovery_records and not trusted_records)
    merged["completed_from_discovery"] = bool(
        trusted_records
        and discovery_records
        and (
            len(merged_media) > trusted_max_media
            or len(str(merged.get("text", ""))) > trusted_max_text + 80
        )
    )
    merged["origin"] = (
        "mixed"
        if trusted_records and discovery_records
        else "discovery"
        if discovery_records
        else "trusted"
    )
    merged["date"] = min(record_date(record) for record in records).isoformat()
    merged["media_keys"] = sorted(record_media_keys(merged))

    # Keep the primary URL first, then alternatives.
    primary_url = str(primary.get("source_url", "") or "")
    if primary_url:
        merged["source_url"] = primary_url
        merged["source_urls"] = [primary_url] + [
            item for item in source_urls if item != primary_url
        ]

    require_keywords = True
    if merged["discovery_only"]:
        # The search query itself already required a Jeonghan term.
        require_keywords = False
    elif any(not source.get("require_keywords", True) for source in sources):
        require_keywords = False

    merged_source = {
        "username": merged.get("source_username", ""),
        "enabled": True,
        "require_keywords": require_keywords,
        "origin": merged["origin"],
    }
    return merged, merged_source


def merge_related_records(
    pairs: list[tuple[dict[str, Any], dict[str, Any]]],
    config: dict[str, Any],
) -> list[tuple[dict[str, Any], dict[str, Any]]]:
    discovery = config.get("discovery", {})
    threshold = float(discovery.get("similarity_threshold", 0.82))
    window = float(discovery.get("merge_window_hours", 24))
    groups: list[list[tuple[dict[str, Any], dict[str, Any]]]] = []

    for pair in sorted(pairs, key=lambda item: record_date(item[0])):
        record, _ = pair
        matched: list[tuple[dict[str, Any], dict[str, Any]]] | None = None
        for group in groups:
            if any(
                records_are_same_update(
                    record,
                    existing,
                    similarity_threshold=threshold,
                    merge_window_hours=window,
                )
                for existing, _ in group
            ):
                matched = group
                break
        if matched is None:
            groups.append([pair])
        else:
            matched.append(pair)

    return [merge_record_group(group) for group in groups]


def recent_update_summary(record: dict[str, Any]) -> dict[str, Any]:
    return {
        "tweet_id": str(record.get("tweet_id", "")),
        "date": str(record.get("date", "")),
        "text": str(record.get("text", "")),
        "media_keys": sorted(record_media_keys(record)),
        "media_count": len(record.get("media", [])),
        "video_count": sum(item.get("type") == "video" for item in record.get("media", [])),
        "text_length": len(str(record.get("text", ""))),
        "source_count": len(record.get("source_urls", []) or [record.get("source_url", "")]),
        "quoted_tweet_id": str(record.get("quoted_tweet_id", "") or ""),
        "last_seen": iso_now(),
    }


def find_recent_update(
    record: dict[str, Any],
    recent: list[dict[str, Any]],
    config: dict[str, Any],
) -> dict[str, Any] | None:
    discovery = config.get("discovery", {})
    threshold = float(discovery.get("similarity_threshold", 0.82))
    window = float(discovery.get("cross_run_merge_window_hours", 48))
    for old in reversed(recent):
        if records_are_same_update(
            record,
            old,
            similarity_threshold=threshold,
            merge_window_hours=window,
        ):
            return old
    return None


def is_more_complete_than(
    record: dict[str, Any],
    previous: dict[str, Any],
) -> bool:
    media_count = len(record.get("media", []))
    video_count = sum(item.get("type") == "video" for item in record.get("media", []))
    text_length = len(str(record.get("text", "")))
    source_count = len(record.get("source_urls", []) or [record.get("source_url", "")])
    return bool(
        media_count > int(previous.get("media_count", 0) or 0)
        or video_count > int(previous.get("video_count", 0) or 0)
        or text_length > int(previous.get("text_length", 0) or 0) + 100
        or (
            source_count > int(previous.get("source_count", 0) or 0)
            and media_count >= int(previous.get("media_count", 0) or 0)
        )
    )


def remember_recent_update(
    state: dict[str, Any],
    record: dict[str, Any],
    config: dict[str, Any],
) -> None:
    recent = list(state.get("recent_updates", []))
    existing = find_recent_update(record, recent, config)
    summary = recent_update_summary(record)
    if existing is not None:
        try:
            recent.remove(existing)
        except ValueError:
            pass
    recent.append(summary)

    cutoff = utc_now() - timedelta(days=14)
    retained = [
        item
        for item in recent
        if record_date(item) >= cutoff
    ]
    state["recent_updates"] = retained[-400:]

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

    if pending.get("is_upgrade"):
        headline = "🧩 نسخهٔ کامل‌تر از یک آپدیت قبلی"
    elif pending.get("completed_from_discovery"):
        headline = "🧩 آپدیت کامل‌تر با مقایسهٔ منابع"
    elif pending.get("discovery_only"):
        headline = "🔎 کشف‌شده خارج از منابع اصلی"
    else:
        headline = "🪽 پیش‌نویس جدید"

    usernames = pending.get("source_usernames", []) or [pending.get("source_username", "")]
    source_urls = pending.get("source_urls", []) or [pending.get("source_url", "")]
    sections = [
        headline,
        "منبع/منابع: " + "، ".join(f"@{item}" for item in usernames[:6] if item),
    ]
    sections.extend(url for url in source_urls[:4] if url)
    sections.extend(
        [
            "",
            "متن اصلی:",
            truncate(pending.get("source_text", ""), 1000) or "—",
        ]
    )
    if translation:
        sections.extend(["", "ترجمه:", truncate(translation, 1000)])
    sections.extend(["", "کپشن پیشنهادی:", truncate(pending["caption"], 1400)])
    if notes:
        sections.extend(["", "یادداشت:", truncate(notes, 700)])
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
            "origin": pending.get("origin", "trusted"),
            "discovery_only": pending.get("discovery_only", False),
            "completed_from_discovery": pending.get("completed_from_discovery", False),
            "source_context": pending.get("source_context", ""),
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


async def fetch_records(
    config: dict[str, Any],
    x_cookie: str,
) -> list[tuple[dict[str, Any], dict[str, Any]]]:
    from twscrape import API, gather

    db_path = Path(tempfile.gettempdir()) / "jeonghan-twscrape.db"
    try:
        db_path.unlink()
    except FileNotFoundError:
        pass

    api = API(str(db_path), raise_when_no_account=True, wait_timeout=25)
    await api.pool.add_account_cookies("reader", x_cookie)

    polling = config.get("polling", {})
    limit = int(polling.get("tweets_per_source", 12))
    results: list[tuple[dict[str, Any], dict[str, Any]]] = []

    # 1) Trusted source timelines.
    for source in config.get("sources", []):
        if not source.get("enabled", False):
            continue
        username = str(source.get("username", "")).lstrip("@").strip()
        if not username or username.startswith("REPLACE_"):
            continue
        source_meta = dict(source)
        source_meta["origin"] = "trusted"
        try:
            user = await api.user_by_login(username)
            tweets = await gather(api.user_tweets(user.id, limit=limit))
            for tweet in tweets:
                record = tweet_to_record(tweet)
                record["origin"] = "trusted"
                record["trusted_source"] = True
                results.append((record, source_meta))
        except Exception as exc:
            log.exception("Could not fetch @%s", username)
            results.append(
                (
                    {
                        "fetch_error": str(exc),
                        "source_username": username,
                        "error_kind": "trusted_source",
                    },
                    source_meta,
                )
            )

    # 2) General X search as a safety net for missed or incomplete updates.
    discovery = config.get("discovery", {})
    if bool(discovery.get("enabled", True)):
        default_queries = [
            '(JEONGHAN OR "Yoon Jeonghan" OR #JEONGHAN OR #YOONJEONGHAN) '
            '-filter:replies -filter:retweets',
            '(윤정한 OR #윤정한 OR (#정한 세븐틴) OR ("정한" "SEVENTEEN")) '
            '-filter:replies -filter:retweets',
        ]
        configured = discovery.get("queries")
        queries = configured if isinstance(configured, list) and configured else default_queries
        query_limit = int(discovery.get("results_per_query", 25))
        max_queries = int(discovery.get("max_queries_per_run", 3))

        for query in [str(item).strip() for item in queries[:max_queries] if str(item).strip()]:
            source_meta = {
                "username": "X search",
                "enabled": True,
                "require_keywords": False,
                "origin": "discovery",
                "search_query": query,
            }
            try:
                tweets = await gather(api.search(query, limit=query_limit))
                for tweet in tweets:
                    record = tweet_to_record(tweet)
                    record["origin"] = "discovery"
                    record["trusted_source"] = False
                    record["search_query"] = query
                    results.append((record, source_meta))
            except Exception as exc:
                log.exception("Could not search X for %s", query)
                results.append(
                    (
                        {
                            "fetch_error": str(exc),
                            "source_username": "X search",
                            "error_kind": "discovery_search",
                            "search_query": query,
                        },
                        source_meta,
                    )
                )

    # Exact tweet IDs can appear both in a trusted timeline and in general search.
    # Prefer the trusted copy while preserving any discovery metadata.
    by_id: dict[str, tuple[dict[str, Any], dict[str, Any]]] = {}
    errors: list[tuple[dict[str, Any], dict[str, Any]]] = []
    for record, source in results:
        if record.get("fetch_error"):
            errors.append((record, source))
            continue
        tweet_id = str(record.get("tweet_id", ""))
        previous = by_id.get(tweet_id)
        if previous is None:
            by_id[tweet_id] = (record, source)
            continue
        old_record, old_source = previous
        if old_record.get("origin") == "discovery" and record.get("origin") == "trusted":
            by_id[tweet_id] = (record, source)
        elif record.get("search_query") and not old_record.get("search_query"):
            old_record["search_query"] = record["search_query"]

    return errors + list(by_id.values())

def make_pending(record: dict[str, Any], ai: dict[str, Any]) -> dict[str, Any]:
    caption = str(ai.get("caption", "")).strip()
    if not caption:
        caption = record["text"].strip() or "آپدیت جدید جونگهان 🪽"

    ai_notes = str(ai.get("notes", "")).strip()
    system_notes: list[str] = []
    if record.get("is_upgrade"):
        system_notes.append("این نسخه نسبت به آپدیت قبلی مدیا یا اطلاعات کامل‌تری دارد.")
    elif record.get("completed_from_discovery"):
        system_notes.append("چند منبع مقایسه شدند و جست‌وجوی عمومی نسخهٔ کامل‌تری پیدا کرد.")
    elif record.get("discovery_only"):
        system_notes.append(
            "این مورد در منابع اصلی دیده نشد و از جست‌وجوی عمومی X پیدا شده؛ قبل از انتشار منبع را بررسی کن."
        )
    notes = "\n".join([item for item in system_notes + [ai_notes] if item])

    return {
        "tweet_id": record["tweet_id"],
        "member_tweet_ids": record.get("member_tweet_ids", [record["tweet_id"]]),
        "source_username": record["source_username"],
        "source_usernames": record.get("source_usernames", [record["source_username"]]),
        "source_url": record["source_url"],
        "source_urls": record.get("source_urls", [record["source_url"]]),
        "source_context": record.get("source_context", ""),
        "source_text": record["text"],
        "date": record["date"],
        "media": record["media"],
        "caption": caption,
        "translation": str(ai.get("translation", "")).strip(),
        "notes": notes,
        "category": str(ai.get("category", "other")),
        "confidence": float(ai.get("confidence", 0.0) or 0.0),
        "origin": record.get("origin", "trusted"),
        "discovery_only": bool(record.get("discovery_only", False)),
        "completed_from_discovery": bool(record.get("completed_from_discovery", False)),
        "is_upgrade": bool(record.get("is_upgrade", False)),
        "created_at": iso_now(),
    }

def run() -> None:
    config = load_config()
    state = load_json(
        STATE_PATH,
        {
            "initialized": False,
            "seen_tweet_ids": [],
            "recent_updates": [],
            "pending": {},
            "telegram_update_offset": 0,
            "last_heartbeat_week": "",
        },
    )
    state.setdefault("seen_tweet_ids", [])
    state.setdefault("recent_updates", [])
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
    discovery_enabled = bool(config.get("discovery", {}).get("enabled", True))
    if not enabled_sources and not discovery_enabled:
        log.warning("No enabled X sources or discovery search in config.yml")
        save_state(state)
        return

    records = asyncio.run(fetch_records(config, x_cookie))
    for record, _source in records:
        if not record.get("fetch_error"):
            continue
        kind = record.get("error_kind", "")
        if kind == "discovery_search":
            label = "جست‌وجوی عمومی X"
        else:
            label = f"@{record.get('source_username', 'unknown')}"
        telegram.send_message(
            review_chat_id,
            f"⚠️ دریافت {label} ناموفق بود:\n{record['fetch_error']}",
        )

    valid_pairs = [pair for pair in records if not pair[0].get("fetch_error")]
    valid_pairs = merge_related_records(valid_pairs, config)
    valid_pairs.sort(key=lambda pair: record_date(pair[0]))

    seen = set(str(item) for item in state.get("seen_tweet_ids", []))
    polling = config.get("polling", {})
    discovery = config.get("discovery", {})
    keywords = [str(item) for item in config.get("keywords", [])]
    include_replies = bool(polling.get("include_replies", False))
    include_retweets = bool(polling.get("include_retweets", False))
    first_cutoff = utc_now() - timedelta(
        hours=float(polling.get("first_run_lookback_hours", 2))
    )
    max_drafts = int(polling.get("max_new_drafts_per_run", 8))
    minimum_confidence = float(ai_config.get("minimum_relevance_confidence", 0.6))
    discovery_confidence = float(
        discovery.get("minimum_discovery_confidence", max(minimum_confidence, 0.68))
    )
    humor_level = int(ai_config.get("default_humor_level", 1))
    drafts_created = 0

    for record, source in valid_pairs:
        member_ids = [
            str(item)
            for item in record.get("member_tweet_ids", [record.get("tweet_id", "")])
            if str(item)
        ]
        all_seen = bool(member_ids) and all(item in seen for item in member_ids)

        date = record_date(record)
        if not state.get("initialized", False) and date < first_cutoff:
            seen.update(member_ids)
            continue
        if record["is_reply"] and not include_replies:
            seen.update(member_ids)
            continue
        if record["is_retweet"] and not include_retweets:
            seen.update(member_ids)
            continue
        if source.get("require_keywords", True) and not contains_keyword(record["text"], keywords):
            seen.update(member_ids)
            continue

        recent_match = find_recent_update(record, list(state.get("recent_updates", [])), config)
        if recent_match is not None:
            if not is_more_complete_than(record, recent_match):
                seen.update(member_ids)
                continue
            record["is_upgrade"] = True
        elif all_seen:
            continue

        # Keep overflow for the next run rather than losing it permanently.
        if drafts_created >= max_drafts:
            continue

        try:
            ai = gemini.generate(record, humor_level=humor_level)
        except BotError as exc:
            log.exception("AI generation failed for %s", record["tweet_id"])
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
        required_confidence = (
            discovery_confidence if record.get("discovery_only") else minimum_confidence
        )
        if not relevant or confidence < required_confidence:
            log.info(
                "Skipped low-confidence/non-relevant update %s (%.2f < %.2f)",
                record["tweet_id"],
                confidence,
                required_confidence,
            )
            seen.update(member_ids)
            continue

        pending = make_pending(record, ai)
        message = telegram.send_message(
            review_chat_id,
            review_text(pending) + "\n\n⬇️ پست تمیز و آمادهٔ فوروارد در پیام بعدی است.",
            reply_markup=review_keyboard(record["tweet_id"]),
        )
        pending["review_message_id"] = message["message_id"]
        state["pending"][record["tweet_id"]] = pending
        publish_pending(telegram, pending, review_chat_id, config)
        drafts_created += 1
        seen.update(member_ids)
        remember_recent_update(state, record, config)

    state["initialized"] = True
    state["seen_tweet_ids"] = list(seen)[-12000:]
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
