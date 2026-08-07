from __future__ import annotations

import asyncio
import logging
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable

from .models import MediaItem, Update, ensure_utc

logger = logging.getLogger(__name__)
ROOT = Path(__file__).resolve().parents[1]


class XCollectionError(RuntimeError):
    pass


class XCollector:
    """Collect real X posts through one cookie-backed twscrape account.

    No mock or synthetic X result is ever returned. If X changes, the cookie expires,
    or the upstream client fails, callers receive a clear XCollectionError instead.
    """

    def __init__(
        self,
        cookies: dict[str, str],
        sources: list[dict[str, Any]],
        keyword_groups: list[dict[str, Any]],
    ):
        self.cookies = cookies
        self.sources = sources
        self.keyword_groups = keyword_groups
        self.api = None
        self.db_path = ROOT / ".state" / "x_accounts.db"
        self.source_priority = {
            str(item.get("handle", "")).lstrip("@").lower(): int(item.get("priority", 100))
            for item in sources
        }

    async def _get_api(self):
        if self.api is not None:
            return self.api
        missing = [name for name in ("auth_token", "ct0") if not self.cookies.get(name)]
        if missing:
            raise XCollectionError(
                "X_COOKIE is missing required cookies: " + ", ".join(missing)
            )
        try:
            from twscrape import API
        except ImportError as exc:
            raise XCollectionError("twscrape is not installed.") from exc

        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        cookie_string = "; ".join(f"{key}={value}" for key, value in self.cookies.items())

        async def configure(db_path: Path):
            api = API(
                str(db_path),
                raise_when_no_account=True,
                wait_timeout=12.0,
                wait_interval=1.0,
            )
            account = await api.pool.get_account("reader_cookie")
            if account is None:
                await api.pool.add_account_cookies("reader_cookie", cookie_string)
            else:
                account.cookies = dict(self.cookies)
                account.active = True
                account.error_msg = None
                account.locks = {}
                await api.pool.save(account)
            return api

        try:
            self.api = await configure(self.db_path)
        except Exception as first_error:
            # A partially written Actions cache must not permanently break the bot.
            logger.warning("Rebuilding X account database: %s", _safe_error(first_error))
            for path in self.db_path.parent.glob(self.db_path.name + "*"):
                try:
                    path.unlink()
                except OSError:
                    pass
            try:
                self.api = await configure(self.db_path)
            except Exception as exc:
                raise XCollectionError(
                    "Could not initialize the X reader: " + _safe_error(exc)
                ) from exc
        return self.api

    async def healthcheck(self) -> None:
        api = await self._get_api()
        try:
            user = await api.user_by_login("pledis_17")
            if user is None:
                raise RuntimeError("X returned no profile data")
        except Exception as exc:
            raise XCollectionError(f"X cookie validation failed: {_safe_error(exc)}") from exc

    async def collect_window(
        self,
        start: datetime,
        end: datetime,
        *,
        include_sources: bool = True,
        include_keywords: bool = True,
        max_per_query: int = 60,
    ) -> list[Update]:
        results: list[Update] = []
        errors: list[str] = []

        # Named sources use their profile timeline (including replies) rather than
        # depending only on X search indexing. This is the more complete path for
        # the 2h/24h commands.
        if include_sources:
            for source in self.sources:
                if not source.get("enabled", True):
                    continue
                handle = normalize_handle(str(source.get("handle", "")))
                if not handle:
                    continue
                try:
                    results.extend(
                        await self._collect_source_timeline(
                            handle,
                            start,
                            end,
                            limit=max(100, max_per_query * 2),
                            include_replies=bool(source.get("include_replies", True)),
                        )
                    )
                except XCollectionError as exc:
                    errors.append(f"@{handle}: {_safe_error(exc)}")

        queries: list[str] = []
        if include_keywords:
            date_suffix = _date_suffix(start, end)
            for group in self.keyword_groups:
                if not group.get("enabled", True):
                    continue
                terms = [str(term).strip() for term in group.get("terms", []) if str(term).strip()]
                if terms:
                    quoted = [f'"{term}"' if " " in term else term for term in terms]
                    queries.append(f"({' OR '.join(quoted)}) {date_suffix}")
        if queries:
            try:
                results.extend(
                    await self._run_queries(
                        queries, start, end, max_per_query=max_per_query
                    )
                )
            except XCollectionError as exc:
                errors.append(_safe_error(exc))

        results = _dedupe(results)
        if not results and errors:
            raise XCollectionError("X returned no usable result. " + " | ".join(errors[:3]))
        return results

    async def collect_source(self, handle: str, start: datetime, end: datetime) -> list[Update]:
        handle = normalize_handle(handle)
        if not handle:
            raise XCollectionError("Source handle is empty.")
        try:
            return await self._collect_source_timeline(
                handle, start, end, limit=400, include_replies=True
            )
        except XCollectionError as timeline_error:
            # Search is a fallback only; it may be less complete than the profile
            # timeline but prevents a temporary profile endpoint failure from
            # making the command unusable.
            query = f"from:{handle} -filter:retweets {_date_suffix(start, end)}"
            try:
                return await self._run_queries(
                    [query], start, end, max_per_query=240
                )
            except XCollectionError as search_error:
                raise XCollectionError(
                    f"Could not read @{handle}: {_safe_error(timeline_error)} | "
                    f"{_safe_error(search_error)}"
                ) from search_error

    async def _collect_source_timeline(
        self,
        handle: str,
        start: datetime,
        end: datetime,
        *,
        limit: int,
        include_replies: bool,
    ) -> list[Update]:
        api = await self._get_api()
        start = ensure_utc(start)
        end = ensure_utc(end)
        try:
            user = await api.user_by_login(handle)
            if user is None:
                raise RuntimeError("profile was not found")
            generator = (
                api.user_tweets_and_replies(user.id, limit=limit)
                if include_replies
                else api.user_tweets(user.id, limit=limit)
            )
            updates: list[Update] = []
            async for tweet in generator:
                if getattr(tweet, "retweetedTweet", None) is not None:
                    continue
                update = self._convert_tweet(tweet, raw_query=f"timeline:@{handle}")
                if update is None:
                    continue
                if update.created_at < start:
                    # Timelines are newest-first. Once sufficiently old content is
                    # reached, additional pages cannot be inside the requested window.
                    break
                if update.created_at < end:
                    updates.append(update)
            return _dedupe(updates)
        except Exception as exc:
            raise XCollectionError(
                f"X profile timeline failed for @{handle}: {_safe_error(exc)}"
            ) from exc

    async def search_archive(
        self,
        queries: Iterable[str],
        *,
        start: datetime | None = None,
        end: datetime | None = None,
        max_per_query: int = 100,
    ) -> list[Update]:
        query_list: list[str] = []
        suffix = _date_suffix(start, end) if start and end else ""
        for query in queries:
            query = query.strip()
            if query:
                query_list.append(f"{query} {suffix}".strip())
        if not query_list:
            return []
        lower = start or datetime(2006, 1, 1, tzinfo=timezone.utc)
        upper = end or datetime.now(timezone.utc) + timedelta(days=1)
        latest = await self._run_queries(
            query_list,
            lower,
            upper,
            max_per_query=max_per_query,
            product="Latest",
        )
        top = await self._run_queries(
            query_list[:4],
            lower,
            upper,
            max_per_query=min(60, max_per_query),
            product="Top",
        )
        return _dedupe(latest + top)

    async def collect_event(self, selected: Update) -> list[Update]:
        api = await self._get_api()
        start = selected.created_at - timedelta(hours=8)
        end = selected.created_at + timedelta(hours=18)
        results: list[Update] = []
        errors: list[str] = []

        # First use X's official conversation timeline exposed by twscrape.
        thread_id = selected.conversation_id or selected.id
        if thread_id.isdigit():
            try:
                async for tweet in api.tweet_thread(int(thread_id), limit=250):
                    update = self._convert_tweet(tweet, raw_query=f"thread:{thread_id}")
                    if update and start <= update.created_at < end:
                        results.append(update)
            except Exception as exc:
                errors.append("thread: " + _safe_error(exc))

        # Fanbases sometimes publish each live part as a new root tweet. Search the
        # same author/day and multilingual event terms as a deliberate fallback.
        queries = [f"from:{selected.author} {_date_suffix(start, end)}"]
        tokens = _event_terms(selected.text)
        if tokens:
            joined = " ".join(tokens[:4])
            queries.extend(
                [
                    f"{joined} JEONGHAN {_date_suffix(start, end)}",
                    f"{joined} 윤정한 {_date_suffix(start, end)}",
                    f"{joined} ジョンハン {_date_suffix(start, end)}",
                ]
            )
        try:
            results.extend(
                await self._run_queries(queries, start, end, max_per_query=180)
            )
        except XCollectionError as exc:
            errors.append(_safe_error(exc))

        if selected.id not in {item.id for item in results}:
            results.append(selected)

        kept: list[Update] = []
        for item in results:
            same_conversation = item.conversation_id == selected.conversation_id
            same_author = item.author.lower() == selected.author.lower()
            close = abs((item.created_at - selected.created_at).total_seconds()) <= 8 * 3600
            live_related = _looks_live(item.text) and _looks_live(selected.text)
            if same_conversation or (same_author and close and live_related) or item.id == selected.id:
                kept.append(item)

        kept = _dedupe(kept)
        if not kept and errors:
            raise XCollectionError("Could not collect the selected event. " + " | ".join(errors[:3]))
        return kept

    async def _run_queries(
        self,
        queries: list[str],
        start: datetime,
        end: datetime,
        *,
        max_per_query: int,
        product: str = "Latest",
    ) -> list[Update]:
        api = await self._get_api()
        start = ensure_utc(start)
        end = ensure_utc(end)
        all_updates: list[Update] = []
        errors: list[str] = []
        for query in _unique(queries):
            try:
                async for tweet in api.search(
                    query,
                    limit=max_per_query,
                    kv={"product": product},
                ):
                    update = self._convert_tweet(tweet, raw_query=query)
                    if update is None:
                        continue
                    if start <= update.created_at < end:
                        all_updates.append(update)
            except Exception as exc:
                errors.append(f"{query[:60]}: {_safe_error(exc)}")
            await asyncio.sleep(0.2)
        if not all_updates and errors:
            raise XCollectionError("X returned no usable result. " + " | ".join(errors[:3]))
        return _dedupe(all_updates)

    def _convert_tweet(self, tweet: Any, *, raw_query: str) -> Update | None:
        tweet_id = str(getattr(tweet, "id_str", "") or getattr(tweet, "id", "") or "")
        if not tweet_id:
            return None
        user = getattr(tweet, "user", None)
        author = str(getattr(user, "username", "") or "").lstrip("@")
        author_name = str(getattr(user, "displayname", "") or author)
        text = str(getattr(tweet, "rawContent", "") or "").strip()
        created = getattr(tweet, "date", None) or datetime.now(timezone.utc)
        try:
            created_at = ensure_utc(created)
        except Exception:
            logger.warning("Could not parse X timestamp for %s", tweet_id)
            return None
        conversation_id = str(
            getattr(tweet, "conversationIdStr", "")
            or getattr(tweet, "conversationId", "")
            or tweet_id
        )
        reply_to_id = str(
            getattr(tweet, "inReplyToTweetIdStr", "")
            or getattr(tweet, "inReplyToTweetId", "")
            or ""
        )
        quoted = getattr(tweet, "quotedTweet", None)
        quoted_id = str(
            getattr(quoted, "id_str", "") or getattr(quoted, "id", "") or ""
        )
        lang = str(getattr(tweet, "lang", "") or "")
        media = self._convert_media(getattr(tweet, "media", None))
        url = str(getattr(tweet, "url", "") or "")
        if not url:
            url = f"https://x.com/{author}/status/{tweet_id}" if author else f"https://x.com/i/status/{tweet_id}"
        return Update(
            id=tweet_id,
            url=url,
            author=author,
            author_name=author_name,
            text=text,
            created_at=created_at,
            conversation_id=conversation_id,
            reply_to_id=reply_to_id,
            quoted_id=quoted_id,
            lang=lang,
            media=media,
            source_priority=self.source_priority.get(author.lower(), 100),
            is_reply=bool(reply_to_id),
            raw_query=raw_query,
        )

    @staticmethod
    def _convert_media(media: Any) -> list[MediaItem]:
        if media is None:
            return []
        result: list[MediaItem] = []

        for photo in list(getattr(media, "photos", None) or []):
            url = str(getattr(photo, "url", "") or "")
            if not url:
                continue
            sep = "&" if "?" in url else "?"
            if "name=" not in url:
                url = f"{url}{sep}format=jpg&name=orig"
            result.append(MediaItem(kind="photo", url=url, content_type="image/jpeg"))

        for video in list(getattr(media, "videos", None) or []):
            variants: list[MediaItem] = []
            for variant in list(getattr(video, "variants", None) or []):
                url = str(getattr(variant, "url", "") or "")
                content_type = str(getattr(variant, "contentType", "") or "")
                if not url or ("mp4" not in content_type.lower() and ".mp4" not in url.lower()):
                    continue
                variants.append(
                    MediaItem(
                        kind="video",
                        url=url,
                        preview_url=str(getattr(video, "thumbnailUrl", "") or ""),
                        bitrate=int(getattr(variant, "bitrate", 0) or 0),
                        duration_ms=int(getattr(video, "duration", 0) or 0),
                        content_type=content_type or "video/mp4",
                    )
                )
            if variants:
                variants.sort(key=lambda item: item.bitrate, reverse=True)
                result.append(variants[0])

        for animated in list(getattr(media, "animated", None) or []):
            url = str(getattr(animated, "videoUrl", "") or "")
            if url:
                result.append(
                    MediaItem(
                        kind="video",
                        url=url,
                        preview_url=str(getattr(animated, "thumbnailUrl", "") or ""),
                        content_type="video/mp4",
                    )
                )
        return result


def normalize_handle(value: str) -> str:
    value = value.strip()
    match = re.search(r"(?:x\.com|twitter\.com)/([A-Za-z0-9_]+)", value)
    if match:
        return match.group(1)
    return value.lstrip("@").split("?")[0].strip("/ ")


def _date_suffix(start: datetime | None, end: datetime | None) -> str:
    if not start or not end:
        return ""
    start = ensure_utc(start)
    end = ensure_utc(end)
    # X operators are day-granular; precise filtering happens after collection.
    until_day = (end + timedelta(days=1)).strftime("%Y-%m-%d")
    return f"since:{start:%Y-%m-%d} until:{until_day}"


def _event_terms(text: str) -> list[str]:
    value = re.sub(r"https?://\S+", " ", text)
    tokens = re.findall(r"[A-Za-z]{3,}|[\uac00-\ud7af]{2,}|[\u3040-\u30ff]{2,}", value)
    stop = {"jeonghan", "update", "with", "from", "this", "that", "live", "정한", "윤정한", "ジョンハン"}
    return [token for token in _unique(tokens) if token.casefold() not in stop]


def _looks_live(text: str) -> bool:
    value = text.lower()
    return any(word in value for word in ("live", "weverse", "라이브", "위버스", "لایو", "ترجمه"))


def _dedupe(updates: list[Update]) -> list[Update]:
    result: dict[str, Update] = {}
    for item in updates:
        current = result.get(item.id)
        if current is None or (len(item.media), len(item.text)) > (len(current.media), len(current.text)):
            result[item.id] = item
    return list(result.values())


def _unique(values: Iterable[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        key = str(value).strip().casefold()
        if key and key not in seen:
            seen.add(key)
            result.append(str(value).strip())
    return result


def _safe_error(exc: Exception) -> str:
    value = str(exc)
    value = re.sub(r"(?i)(auth_token|ct0|cookie|token)=[^\s,;]+", r"\1=<redacted>", value)
    return value[:600]
