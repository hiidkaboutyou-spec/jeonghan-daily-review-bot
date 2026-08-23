from __future__ import annotations

import asyncio
import logging
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable

from .models import MediaItem, Update, ensure_utc
from .source_modes import SourceMode, SourceModeGate

logger = logging.getLogger(__name__)
ROOT = Path(__file__).resolve().parents[1]
HANDLE_RE = re.compile(r"^[A-Za-z0-9_]{1,15}$")
X_SOURCE_PACE_SECONDS = 0.35
X_QUERY_PACE_SECONDS = 0.75


class XCollectionError(RuntimeError):
    pass


def _keyword_queries(keyword_groups: list[dict[str, Any]], date_suffix: str) -> list[str]:
    """Build one OR query per language/topic group instead of one per synonym.

    The old settings expand to thirteen nearly-identical searches after twenty-two
    source timelines. That burst is both wasteful and much more likely to leave a
    partially collected window. X supports OR expressions, so preserve discovery
    coverage with three bounded searches instead.
    """
    queries: list[str] = []
    for group in keyword_groups:
        if not group.get("enabled", True):
            continue
        terms: list[str] = []
        seen: set[str] = set()
        for raw in group.get("terms", []):
            term = str(raw).strip()
            if not term or term.casefold() in seen:
                continue
            seen.add(term.casefold())
            terms.append(f'"{term}"' if " " in term else term)
        if not terms:
            continue
        expression = terms[0] if len(terms) == 1 else "(" + " OR ".join(terms) + ")"
        queries.append(f"{expression} {date_suffix}".strip())
    return _unique(queries)


_TRANSACTION_PATTERNS = [
    r"\bwts\b", r"\bwtb\b", r"\bwtt\b", r"\blfb\b", r"\blfs\b", r"\biso\b",
    r"\bfor sale\b", r"\bselling\b", r"\bsell\b", r"\bbuying\b", r"\btrade(?:ing)?\b",
    r"\bprice\b", r"\bpayment\b", r"\bshipping\b", r"\bship(?:ping)? fee\b",
    r"\bclaim(?:ing)?\b", r"\bgroup order\b", r"\bgo manager\b", r"\bmeetup\b",
    r"\bproof of payment\b", r"\bproof of transaction\b",
    r"\bphp\s*\d", r"\bkrw\s*\d", r"\busd\s*\d", r"[$₩₱€£]\s*\d",
    r"양도", r"판매", r"구매", r"교환", r"가격",
    r"譲ります", r"交換", r"買取", r"販売",
]
_COLLECTIBLE_PATTERNS = [
    r"\bphotocard(?:s)?\b", r"\bphoto card(?:s)?\b", r"\bpob\b",
    r"\bmerch(?:andise)?\b", r"\bsealed\b", r"\bpre[- ]?order\b",
    r"포카", r"포토카드", r"앨범\s*판매", r"トレカ", r"フォトカード",
]
# X-only solicitation spam. Adult/sexual content inside AO3 fiction summaries is
# explicitly allowed and is never filtered by this collection rule.
_SOLICITATION_SPAM_PATTERNS = [
    r"스폰서", r"알바", r"아르바이트", r"고수익", r"일일\s*일탈", r"조건\s*만남",
    r"섹스\s*(?:파트너|알바)", r"투잡", r"고액\s*급여", r"단기\s*알바",
    r"\bsponsor(?:ed)?\s+(?:job|work|dating)\b", r"\bpart[- ]?time\s+(?:job|work)\b",
    r"\bhigh[- ]?income\b", r"\bhigh[- ]?pay(?:ing)?\b", r"\bdaily\s+deviation\b",
    r"\bsex\s+(?:job|work|partner)\b", r"\btelegram\s*[:@]", r"\bwhatsapp\s*[:+]",
]
_TRANSACTION_RE = re.compile("|".join(f"(?:{p})" for p in _TRANSACTION_PATTERNS), re.I)
_COLLECTIBLE_RE = re.compile("|".join(f"(?:{p})" for p in _COLLECTIBLE_PATTERNS), re.I)
_SOLICITATION_SPAM_RE = re.compile("|".join(f"(?:{p})" for p in _SOLICITATION_SPAM_PATTERNS), re.I)
_STRONG_JH_RE = re.compile(
    r"\bjeonghan\b|\byoon\s+jeonghan\b|#jeonghan\b|#yoonjeonghan\b|윤정한|ジョンハン|ユンジョンハン",
    re.I,
)
_AMBIGUOUS_KOREAN_JH_RE = re.compile(r"(?<![가-힣])정한(?![가-힣])")
_FANDOM_CONTEXT_RE = re.compile(
    r"세븐틴|캐럿|정하니|윤정한|jeonghan|seventeen|carat|ジョンハン|ユンジョンハン",
    re.I,
)
_OTHER_MEMBER_RE = re.compile(
    r"\b(?:scoups|s\.coups|seungcheol|joshua|jisoo|junhui|jun\b|hoshi|soonyoung|wonwoo|woozi|jihoon|minghao|the8|mingyu|dk\b|seokmin|seungkwan|vernon|hansol|dino|lee chan)\b|승철|지수|준휘|순영|원우|지훈|명호|민규|석민|승관|한솔|찬",
    re.I,
)


def is_relevant_jeonghan_update(update: Update, *, trusted_source: bool = False) -> bool:
    """Reject trading/sales noise while keeping genuine Jeonghan updates."""
    text = "\n".join(part for part in (update.text, update.quoted_text) if part).strip()
    has_jh = bool(_STRONG_JH_RE.search(text))
    # 정한 is also an ordinary Korean modifier meaning roughly "chosen/set".
    # Treat it as the member name only when a short fan post, media, or explicit
    # fandom context supports that reading. This blocks long unrelated keyword hits.
    if not has_jh and _AMBIGUOUS_KOREAN_JH_RE.search(text):
        has_jh = bool(update.media or update.quoted_media or len(text) <= 280 or _FANDOM_CONTEXT_RE.search(text))
    if text:
        if _SOLICITATION_SPAM_RE.search(text):
            return False
        if _TRANSACTION_RE.search(text):
            return False
        if _COLLECTIBLE_RE.search(text) and not has_jh:
            return False

    if not trusted_source:
        return has_jh
    if has_jh:
        return True
    if not text:
        return bool(update.media)
    if _OTHER_MEMBER_RE.search(text):
        return False
    return bool(update.media) or len(text) <= 280


class XCollector:
    def __init__(self, cookies: dict[str, str], sources: list[dict[str, Any]], keyword_groups: list[dict[str, Any]]):
        self.cookies = cookies
        self.sources = sources
        self.source_mode_gate = SourceModeGate(sources)
        self.keyword_groups = keyword_groups
        self.api = None
        self.last_errors: list[str] = []
        self.db_path = ROOT / ".state" / "x_accounts.db"
        self.source_priority = {
            str(item.get("handle", "")).lstrip("@").lower(): int(item.get("priority", 100))
            for item in sources
        }
        self.dedicated_sources = {
            str(item.get("handle", "")).lstrip("@").lower()
            for item in sources
            if item.get("enabled", True)
            and str(item.get("mode", "")).strip().lower() == SourceMode.FULL_FEED.value
        }

    async def _get_api(self):
        if self.api is not None:
            return self.api
        missing = [name for name in ("auth_token", "ct0") if not self.cookies.get(name)]
        if missing:
            raise XCollectionError("X_COOKIE is missing required cookies: " + ", ".join(missing))
        try:
            from twscrape import API
        except ImportError as exc:
            raise XCollectionError("twscrape is not installed.") from exc

        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        cookie_string = "; ".join(f"{key}={value}" for key, value in self.cookies.items())

        async def configure(db_path: Path):
            api = API(str(db_path), raise_when_no_account=True, wait_timeout=12.0, wait_interval=1.0)
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
            logger.warning("Rebuilding X account database: %s", _safe_error(first_error))
            for path in self.db_path.parent.glob(self.db_path.name + "*"):
                try:
                    path.unlink()
                except OSError:
                    pass
            try:
                self.api = await configure(self.db_path)
            except Exception as exc:
                raise XCollectionError("Could not initialize the X reader: " + _safe_error(exc)) from exc
        return self.api

    async def healthcheck(self) -> None:
        api = await self._get_api()
        try:
            user = await api.user_by_login("pledis_17")
            if user is None:
                raise RuntimeError("X returned no profile data")
        except Exception as exc:
            raise XCollectionError(f"X cookie validation failed: {_safe_error(exc)}") from exc

    def _filter_relevant(self, updates: list[Update]) -> list[Update]:
        kept: list[Update] = []
        for item in updates:
            dedicated = item.author.lower() in self.dedicated_sources
            if is_relevant_jeonghan_update(item, trusted_source=dedicated):
                kept.append(item)
            else:
                logger.info("Filtered non-Jeonghan/noise post %s from @%s", item.id, item.author)
        return kept

    async def collect_window(
        self,
        start: datetime,
        end: datetime,
        *,
        include_sources: bool = True,
        include_keywords: bool = True,
        max_per_query: int = 60,
    ) -> list[Update]:
        self.last_errors = []
        results: list[Update] = []
        errors: list[str] = []
        if include_sources:
            enabled_sources = [source for source in self.sources if source.get("enabled", True)]
            for source_index, source in enumerate(enabled_sources):
                handle = normalize_handle(str(source.get("handle", "")))
                if not handle:
                    continue
                try:
                    results.extend(
                        await self._collect_source_timeline(
                            handle,
                            start,
                            end,
                            limit=max(200, min(1000, max_per_query * 3)),
                            include_replies=bool(source.get("include_replies", True)),
                        )
                    )
                except XCollectionError as exc:
                    errors.append(f"@{handle}: {_safe_error(exc)}")
                if source_index + 1 < len(enabled_sources):
                    await asyncio.sleep(X_SOURCE_PACE_SECONDS)

        if include_keywords:
            date_suffix = _date_suffix(start, end)
            queries = _keyword_queries(self.keyword_groups, date_suffix)
            try:
                results.extend(await self._run_queries(queries, start, end, max_per_query=max_per_query))
            except XCollectionError as exc:
                errors.append(_safe_error(exc))

        self.last_errors = _unique(self.last_errors + errors)
        results = self._filter_relevant(_dedupe(results))
        if not results and self.last_errors:
            raise XCollectionError("X returned no usable result. " + " | ".join(self.last_errors[:3]))
        return results

    async def collect_source(self, handle: str, start: datetime, end: datetime) -> list[Update]:
        """Explicit 24h source mode: return that source completely, up to 1000 items."""
        handle = normalize_handle(handle)
        if not handle:
            raise XCollectionError("Source handle is invalid.")
        try:
            results = await self._collect_source_timeline(handle, start, end, limit=1000, include_replies=True)
        except XCollectionError as timeline_error:
            query = f"from:{handle} -filter:retweets {_date_suffix(start, end)}"
            try:
                results = await self._run_queries([query], start, end, max_per_query=1000)
            except XCollectionError as search_error:
                raise XCollectionError(
                    f"Could not read @{handle}: {_safe_error(timeline_error)} | {_safe_error(search_error)}"
                ) from search_error
        return _dedupe(results)[:1000]

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
            generator = api.user_tweets_and_replies(user.id, limit=limit) if include_replies else api.user_tweets(user.id, limit=limit)
            updates: list[Update] = []
            async for tweet in generator:
                if getattr(tweet, "retweetedTweet", None) is not None:
                    continue
                update = self._convert_tweet(tweet, raw_query=f"timeline:@{handle}")
                if update is None:
                    continue
                if update.created_at < start:
                    break
                if update.created_at < end:
                    updates.append(update)
            return _dedupe(updates)
        except Exception as exc:
            raise XCollectionError(f"X profile timeline failed for @{handle}: {_safe_error(exc)}") from exc

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
        latest = await self._run_queries(query_list, lower, upper, max_per_query=max_per_query, product="Latest")
        top = await self._run_queries(query_list[:4], lower, upper, max_per_query=min(60, max_per_query), product="Top")
        return self._filter_relevant(_dedupe(latest + top))

    async def collect_event(self, selected: Update) -> list[Update]:
        api = await self._get_api()
        start = selected.created_at - timedelta(hours=8)
        end = selected.created_at + timedelta(hours=18)
        results: list[Update] = []
        errors: list[str] = []
        thread_id = selected.conversation_id or selected.id
        if thread_id.isdigit():
            try:
                async for tweet in api.tweet_thread(int(thread_id), limit=300):
                    update = self._convert_tweet(tweet, raw_query=f"thread:{thread_id}")
                    if update and start <= update.created_at < end:
                        results.append(update)
            except Exception as exc:
                errors.append("thread: " + _safe_error(exc))

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
            results.extend(await self._run_queries(queries, start, end, max_per_query=220))
        except XCollectionError as exc:
            errors.append(_safe_error(exc))
        if selected.id not in {item.id for item in results}:
            results.append(selected)

        kept: list[Update] = []
        selected_author = selected.author.lower()
        for item in results:
            same_author = item.author.lower() == selected_author
            same_conversation = item.conversation_id == selected.conversation_id
            close = abs((item.created_at - selected.created_at).total_seconds()) <= 8 * 3600
            live_related = _looks_live(item.text) and _looks_live(selected.text)
            if item.id == selected.id or (same_author and same_conversation) or (same_author and close and live_related):
                kept.append(item)
        kept = self._filter_relevant(_dedupe(kept))
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
                async for tweet in api.search(query, limit=max_per_query, kv={"product": product}):
                    update = self._convert_tweet(tweet, raw_query=query)
                    if update is not None and start <= update.created_at < end:
                        all_updates.append(update)
            except Exception as exc:
                errors.append(f"{query[:60]}: {_safe_error(exc)}")
            await asyncio.sleep(X_QUERY_PACE_SECONDS)
        if errors:
            self.last_errors = _unique(self.last_errors + errors)
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
            return None
        conversation_id = str(getattr(tweet, "conversationIdStr", "") or getattr(tweet, "conversationId", "") or tweet_id)
        reply_to_id = str(getattr(tweet, "inReplyToTweetIdStr", "") or getattr(tweet, "inReplyToTweetId", "") or "")
        quoted = getattr(tweet, "quotedTweet", None)
        quoted_id = str(getattr(quoted, "id_str", "") or getattr(quoted, "id", "") or "")
        quoted_text = str(getattr(quoted, "rawContent", "") or "").strip()
        quoted_user = getattr(quoted, "user", None)
        quoted_author = str(getattr(quoted_user, "username", "") or "").lstrip("@")
        quoted_media = self._convert_media(getattr(quoted, "media", None))
        lang = str(getattr(tweet, "lang", "") or "")
        media = self._convert_media(getattr(tweet, "media", None))
        url = str(getattr(tweet, "url", "") or "") or (f"https://x.com/{author}/status/{tweet_id}" if author else f"https://x.com/i/status/{tweet_id}")
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
            quoted_text=quoted_text,
            quoted_author=quoted_author,
            quoted_media=quoted_media,
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
    value = str(value or "").strip()
    match = re.search(r"(?:https?://)?(?:www\.)?(?:x\.com|twitter\.com)/([A-Za-z0-9_]{1,15})(?:[/?#]|$)", value, re.I)
    handle = match.group(1) if match else value.lstrip("@").split("?", 1)[0].strip("/ ")
    return handle if HANDLE_RE.fullmatch(handle) else ""


def _date_suffix(start, end) -> str:
    if not start or not end:
        return ""
    start = ensure_utc(start)
    end = ensure_utc(end)
    until_day = (end + timedelta(days=1)).strftime("%Y-%m-%d")
    return f"since:{start:%Y-%m-%d} until:{until_day}"


def _event_terms(text: str) -> list[str]:
    value = re.sub(r"https?://\S+", " ", text)
    tokens = re.findall(r"[A-Za-z]{3,}|[\uac00-\ud7af]{2,}|[\u3040-\u30ff]{2,}", value)
    stop = {"jeonghan", "update", "with", "from", "this", "that", "live", "정한", "윤정한", "ジョンハン"}
    return [token for token in _unique(tokens) if token.casefold() not in stop]


def _looks_live(text: str) -> bool:
    return any(word in text.lower() for word in ("live", "weverse", "라이브", "위버스", "لایو", "ترجمه"))


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
        value = str(value).strip()
        key = value.casefold()
        if key and key not in seen:
            seen.add(key)
            result.append(value)
    return result


def _safe_error(exc: Exception) -> str:
    value = str(exc)
    value = re.sub(
        r"(?i)(auth_token|ct0|cookie|token)(?:['\"]?\s*[:=]\s*['\"]?)[^\s,'\";}{]+",
        r"\1=<redacted>",
        value,
    )
    return value[:600]
