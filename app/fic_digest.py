from __future__ import annotations

import asyncio
import html
import json
import logging
import re
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

import requests
from bs4 import BeautifulSoup

from .config import Settings
from .fic_state import FicObservation, FicStateStore
from .message_delivery import MessageDeliveryStore
from .telegram import TelegramBot
from .x_client import XCollector

logger = logging.getLogger(__name__)
AO3 = "https://archiveofourown.org"
HEADERS = {"User-Agent": "JeonghanDailyReviewBot/1.0 (+personal private reading digest; low-rate requests)"}
JEONGHAN_TERMS = ("jeonghan", "yoon jeonghan", "정한", "윤정한", "ジョンハン")
AO3_PAGE_LIMIT = 25
AO3_PACE_SECONDS = 1.0

SHIP_ALIASES = [
    ("Jeongcheol", ("Choi Seungcheol", "S.Coups")),
    ("Jihan", ("Hong Jisoo", "Joshua Hong", "Joshua")),
    ("GyuHan", ("Kim Mingyu", "Mingyu")),
    ("WonHan", ("Jeon Wonwoo", "Wonwoo")),
    ("SoonHan", ("Kwon Soonyoung", "Hoshi", "Soonyoung")),
    ("JunHan", ("Wen Junhui", "Moon Junhui", "Junhui", "Jun")),
    ("SeokHan", ("Lee Seokmin", "DK", "Seokmin")),
    ("HaoHan", ("Xu Minghao", "The8", "Minghao")),
    ("JiHan (Woozi)", ("Lee Jihoon", "Woozi", "Jihoon")),
    ("KwanHan", ("Boo Seungkwan", "Seungkwan")),
    ("ChanHan", ("Lee Chan", "Dino", "Chan")),
    ("VerHan", ("Chwe Hansol", "Vernon", "Hansol")),
]


@dataclass
class Fic:
    title: str
    url: str
    author: str
    summary: str
    relationships: list[str]
    rating: str = ""
    words: str = ""
    kudos: int = 0
    bookmarks: int = 0
    hits: int = 0
    source_note: str = ""
    x_score: int = 0
    chapters: str = ""
    updated: str = ""
    observation_status: str = ""

    @property
    def ship(self) -> str:
        joined = " | ".join(_jeonghan_relationships(self.relationships)).casefold()
        for label, aliases in SHIP_ALIASES:
            if any(alias.casefold() in joined for alias in aliases):
                return label
        return "Other Jeonghan ships"

    @property
    def work_id(self) -> str:
        match = re.search(r"/works/(\d+)", self.url)
        return match.group(1) if match else ""


def _clean(text: str) -> str:
    return re.sub(r"\s+", " ", html.unescape(text or "")).strip()


def _num(node: Any) -> int:
    if not node:
        return 0
    digits = re.sub(r"[^0-9]", "", node.get_text(" ", strip=True))
    return int(digits or 0)


def _relationship_has_jeonghan(value: str) -> bool:
    folded = value.casefold()
    return any(term in folded for term in JEONGHAN_TERMS)


def _jeonghan_relationships(relationships: list[str]) -> list[str]:
    return [relationship for relationship in relationships if _relationship_has_jeonghan(relationship)]


def _is_jeonghan(relationships: list[str], text: str = "") -> bool:
    del text
    return bool(_jeonghan_relationships(relationships))


def _retry_after_seconds(response: requests.Response, default: float) -> float:
    raw = str(response.headers.get("Retry-After", "") or "").strip()
    if raw.isdigit():
        return max(0.0, min(float(raw), 120.0))
    if raw:
        try:
            when = parsedate_to_datetime(raw)
            if when.tzinfo is None:
                when = when.replace(tzinfo=timezone.utc)
            return max(0.0, min((when - datetime.now(timezone.utc)).total_seconds(), 120.0))
        except (TypeError, ValueError, OverflowError):
            pass
    return max(0.0, min(default, 120.0))


def _get(url: str, *, timeout: int = 20, attempts: int = 3) -> requests.Response | None:
    attempts = max(1, int(attempts))
    for attempt in range(attempts):
        try:
            response = requests.get(url, headers=HEADERS, timeout=timeout, allow_redirects=True)
            if response.status_code == 429:
                if attempt + 1 < attempts:
                    time.sleep(_retry_after_seconds(response, 2.0 * (attempt + 1)))
                    continue
                return None
            if response.status_code in {502, 503, 504}:
                if attempt + 1 < attempts:
                    time.sleep(min(8.0, 2.0 ** attempt))
                    continue
                return None
            response.raise_for_status()
            return response
        except requests.RequestException as exc:
            logger.warning("HTTP attempt %s/%s failed for AO3 request (%s)", attempt + 1, attempts, type(exc).__name__)
            if attempt + 1 < attempts:
                time.sleep(min(8.0, 1.5 * (attempt + 1)))
    return None


def _node_text(node: Any) -> str:
    return _clean(node.get_text(" ", strip=True) if node else "")


def _fic_from_search_blurb(work: Any) -> Fic | None:
    heading = work.select_one("h4.heading a[href^='/works/']")
    if not heading:
        return None
    href = str(heading.get("href") or "").split("#", 1)[0]
    relationships = [_clean(a.get_text(" ", strip=True)) for a in work.select("li.relationships a.tag")]
    if not _is_jeonghan(relationships):
        return None
    language_node = work.select_one("dd.language")
    if language_node and "english" not in _clean(language_node.get_text()).casefold():
        return None
    summary_node = work.select_one("blockquote.userstuff.summary")
    authors = work.select("h4.heading a[rel='author']")
    return Fic(
        title=_clean(heading.get_text(" ", strip=True)),
        url=AO3 + href,
        author=", ".join(_clean(a.get_text(" ", strip=True)) for a in authors) or "Anonymous",
        summary=_clean(summary_node.get_text(" ", strip=True) if summary_node else "No public summary provided."),
        relationships=relationships,
        rating=_node_text(work.select_one("span.rating")),
        words=_node_text(work.select_one("dd.words")),
        kudos=_num(work.select_one("dd.kudos")),
        bookmarks=_num(work.select_one("dd.bookmarks")),
        hits=_num(work.select_one("dd.hits")),
        chapters=_node_text(work.select_one("dd.chapters")),
        updated=_node_text(work.select_one("p.datetime")),
    )


def search_ao3(limit: int = 36, *, max_pages: int = AO3_PAGE_LIMIT, pace_seconds: float = AO3_PACE_SECONDS) -> list[Fic]:
    if limit <= 0:
        return []
    base_params = {
        "work_search[query]": '"Yoon Jeonghan" OR Jeonghan',
        "work_search[language_id]": "en",
        "work_search[sort_column]": "kudos_count",
        "work_search[sort_direction]": "desc",
        "commit": "Search",
    }
    fics: list[Fic] = []
    seen: set[str] = set()
    for page in range(1, max(1, int(max_pages)) + 1):
        params = dict(base_params)
        params["page"] = str(page)
        response = _get(AO3 + "/works/search?" + urlencode(params), timeout=25, attempts=3)
        if response is None:
            # A failed page means completeness is unknown; do not skip ahead and
            # present later pages as though the gap were complete.
            break
        soup = BeautifulSoup(response.text, "html.parser")
        works = soup.select("li.work.blurb")
        if not works:
            break
        for work in works:
            fic = _fic_from_search_blurb(work)
            if fic is None or fic.url in seen:
                continue
            seen.add(fic.url)
            fics.append(fic)
            if len(fics) >= limit:
                return fics
        # A page can legitimately contain no qualifying Jeonghan relationship tags;
        # that is not evidence that AO3 has no later search pages.
        if pace_seconds > 0 and page < max_pages:
            time.sleep(float(pace_seconds))
    return fics


def fetch_ao3_work(url: str) -> Fic | None:
    match = re.search(r"archiveofourown\.org/works/(\d+)", url)
    if not match:
        return None
    canonical = f"{AO3}/works/{match.group(1)}"
    response = _get(canonical + "?view_adult=true", timeout=12, attempts=2)
    if response is None:
        return None
    soup = BeautifulSoup(response.text, "html.parser")
    title = soup.select_one("h2.title.heading")
    if not title:
        return None
    language = soup.select_one("dd.language")
    if language and "english" not in _clean(language.get_text()).casefold():
        return None
    relationships = [_clean(a.get_text(" ", strip=True)) for a in soup.select("dd.relationship.tags a.tag")]
    if not _is_jeonghan(relationships):
        return None
    summary_node = soup.select_one("div.summary blockquote.userstuff")
    authors = soup.select("h3.byline.heading a[rel='author']")
    return Fic(
        title=_clean(title.get_text(" ", strip=True)),
        url=canonical,
        author=", ".join(_clean(a.get_text(" ", strip=True)) for a in authors) or "Anonymous",
        summary=_clean(summary_node.get_text(" ", strip=True) if summary_node else "No public summary provided."),
        relationships=relationships,
        rating=_node_text(soup.select_one("dd.rating.tags")),
        words=_node_text(soup.select_one("dd.words")),
        kudos=_num(soup.select_one("dd.kudos")),
        bookmarks=_num(soup.select_one("dd.bookmarks")),
        hits=_num(soup.select_one("dd.hits")),
        chapters=_node_text(soup.select_one("dd.chapters")),
        updated=_node_text(soup.select_one("dd.status")) or _node_text(soup.select_one("dd.published")),
    )


def _extract_ao3_urls(tweet: Any) -> list[str]:
    values: list[str] = []
    raw = str(getattr(tweet, "rawContent", "") or "")
    values.extend(re.findall(r"https?://(?:www\.)?archiveofourown\.org/works/\d+[^\s]*", raw))
    for link in list(getattr(tweet, "links", None) or []):
        for attr in ("url", "expandedUrl", "expanded_url", "href"):
            value = str(getattr(link, attr, "") or "")
            if value:
                values.append(value)
    resolved: list[str] = []
    for value in values:
        if "archiveofourown.org/works/" in value:
            resolved.append(value)
        elif "t.co/" in value:
            response = _get(value, timeout=8, attempts=1)
            if response is not None and "archiveofourown.org/works/" in response.url:
                resolved.append(response.url)
    return list(dict.fromkeys(resolved))


async def search_x_recommendations(settings: Settings, limit: int = 24) -> list[Fic]:
    collector = XCollector(settings.x_cookies, settings.sources, settings.keyword_groups)
    api = await collector._get_api()
    queries = [
        '(JEONGHAN OR "Yoon Jeonghan" OR 윤정한 OR ジョンハン) (AO3 OR archiveofourown)',
        '(JEONGCHEOL OR JIHAN OR GYUHAN OR WONHAN OR SOONHAN OR JUNHAN) (AO3 OR fic OR fanfic)',
        '(JEONGHAN) ("fic rec" OR "fic recommendation" OR "fanfic rec")',
    ]
    candidates: dict[str, tuple[str, int, str]] = {}
    for query in queries:
        try:
            async for tweet in api.search(query, limit=120, kv={"product": "Top"}):
                urls = _extract_ao3_urls(tweet)
                if not urls:
                    continue
                score = int(getattr(tweet, "likeCount", 0) or 0) + 2 * int(getattr(tweet, "retweetCount", 0) or 0)
                note = _clean(str(getattr(tweet, "rawContent", "") or ""))[:500]
                for url in urls:
                    work_id_match = re.search(r"/works/(\d+)", url)
                    if not work_id_match:
                        continue
                    work_id = work_id_match.group(1)
                    current = candidates.get(work_id)
                    if current is None or score > current[1]:
                        candidates[work_id] = (url, score, note)
                if len(candidates) >= max(limit * 2, 36):
                    break
        except Exception as exc:
            logger.warning("X fic recommendation query failed but digest will continue: %s", type(exc).__name__)
        await asyncio.sleep(0.5)

    ranked = sorted(candidates.values(), key=lambda item: item[1], reverse=True)[: max(limit * 2, limit)]
    found: list[Fic] = []
    # AO3 has no supported public API. Fetch detail pages serially and paced instead
    # of opening four simultaneous requests against a volunteer-run service.
    for index, (url, score, note) in enumerate(ranked):
        try:
            fic = await asyncio.to_thread(fetch_ao3_work, url)
        except Exception as exc:
            logger.warning("AO3 work lookup from X recommendation failed: %s", type(exc).__name__)
            fic = None
        if fic is not None:
            fic.x_score = score
            fic.source_note = note
            found.append(fic)
        if index + 1 < len(ranked):
            await asyncio.sleep(AO3_PACE_SECONDS)
    return sorted(found, key=lambda f: (f.x_score, f.kudos, f.bookmarks, f.hits), reverse=True)[:limit]


def _translate_summary(summary: str) -> str:
    if not summary or summary == "No public summary provided.":
        return "خلاصهٔ عمومی برای این فیک ثبت نشده."
    if re.search(r"[\u0600-\u06ff]", summary):
        return summary
    try:
        response = requests.get(
            "https://translate.googleapis.com/translate_a/single",
            params={"client": "gtx", "sl": "auto", "tl": "fa", "dt": "t", "q": summary},
            timeout=15,
        )
        response.raise_for_status()
        payload = response.json()
        translated = "".join(str(part[0]) for part in payload[0] if isinstance(part, list) and part and part[0]).strip()
        return translated or summary
    except Exception as exc:
        logger.warning("Summary translation fallback failed: %s", type(exc).__name__)
        return summary


def _fallback_summaries(fics: list[Fic]) -> dict[str, str]:
    return {fic.url: _translate_summary(fic.summary) for fic in fics}


def summarize_fics_persian(settings: Settings, fics: list[Fic]) -> dict[str, str]:
    if not fics:
        return {}
    if not settings.gemini_api_key:
        return _fallback_summaries(fics)
    try:
        from google import genai
        from google.genai import types

        client = genai.Client(api_key=settings.gemini_api_key)
        payload = [{"url": f.url, "title": f.title, "summary": f.summary} for f in fics]
        prompt = (
            "برای هر فن‌فیک فقط بر اساس summary رسمی AO3 یک خلاصه فارسی روان 1 تا 3 جمله‌ای بنویس. "
            "هیچ جزئیات، پایان یا trope جدیدی اختراع نکن. خروجی JSON با items شامل url و summary_fa باشد. "
            + json.dumps(payload, ensure_ascii=False)
        )
        candidates = list(dict.fromkeys([settings.gemini_model, "gemini-2.5-flash-lite", "gemini-2.5-flash"]))
        for model in [m for m in candidates if m]:
            try:
                response = client.models.generate_content(
                    model=model,
                    contents=prompt,
                    config=types.GenerateContentConfig(
                        temperature=0.1,
                        response_mime_type="application/json",
                        response_json_schema={
                            "type": "object",
                            "required": ["items"],
                            "properties": {
                                "items": {
                                    "type": "array",
                                    "items": {
                                        "type": "object",
                                        "required": ["url", "summary_fa"],
                                        "properties": {
                                            "url": {"type": "string"},
                                            "summary_fa": {"type": "string"},
                                        },
                                    },
                                }
                            },
                        },
                    ),
                )
                parsed = json.loads(response.text or "{}")
                result = {
                    str(x.get("url")): str(x.get("summary_fa", "")).strip()
                    for x in parsed.get("items", [])
                    if x.get("url") and str(x.get("summary_fa", "")).strip()
                }
                if result:
                    missing = [fic for fic in fics if fic.url not in result]
                    if missing:
                        result.update(_fallback_summaries(missing))
                    return {fic.url: result.get(fic.url, fic.summary) for fic in fics}
            except Exception as exc:
                logger.warning("Fic Gemini model %s failed; trying fallback: %s", model, type(exc).__name__)
    except Exception as exc:
        logger.warning("Gemini summary layer unavailable: %s", type(exc).__name__)
    return _fallback_summaries(fics)


def _chunks(text: str, max_len: int = 3800) -> list[str]:
    if max_len < 1:
        raise ValueError("max_len must be positive")
    chunks: list[str] = []
    current = ""

    def flush() -> None:
        nonlocal current
        if current:
            chunks.append(current)
            current = ""

    for block in text.split("\n\n"):
        pieces: list[str] = []
        remaining = block
        while len(remaining) > max_len:
            cut = remaining.rfind("\n", 0, max_len + 1)
            if cut < max_len // 2:
                cut = max_len
            pieces.append(remaining[:cut])
            remaining = remaining[cut:]
            if remaining.startswith("\n"):
                remaining = remaining[1:]
        pieces.append(remaining)
        for piece in pieces:
            if not piece and not current:
                continue
            candidate = piece if not current else current + "\n\n" + piece
            if len(candidate) <= max_len:
                current = candidate
            else:
                flush()
                current = piece
    flush()
    return chunks


def format_digest(title: str, fics: list[Fic], summaries: dict[str, str], source: str) -> str:
    if not fics:
        return title + "\n\nاین نوبت نتیجهٔ قابل‌اعتماد پیدا نشد؛ لیست خالی به معنی نبودن فن‌فیک نیست و اجرای بعدی دوباره بررسی می‌کند."
    lines = [title, "", f"تعداد انتخاب‌ها: {len(fics)} | زبان همه: English", ""]
    grouped: dict[str, list[Fic]] = {}
    for fic in fics:
        grouped.setdefault(fic.ship, []).append(fic)
    order = [x[0] for x in SHIP_ALIASES] + ["Other Jeonghan ships"]
    number = 1
    for ship in order:
        items = grouped.get(ship, [])
        if not items:
            continue
        lines += [f"━━ {ship} ━━", ""]
        for fic in items:
            stats = f"Kudos {fic.kudos:,} · Bookmarks {fic.bookmarks:,} · Hits {fic.hits:,}"
            if source == "x":
                stats += f" · X score {fic.x_score:,}"
            if fic.chapters:
                stats += f" · Chapters {fic.chapters}"
            jh_relationships = _jeonghan_relationships(fic.relationships)
            rel = "; ".join(jh_relationships[:3]) or ship
            lines += [
                f"{number}) {fic.title}",
                f"by {fic.author}",
                rel,
                stats + (f" · Words {fic.words}" if fic.words else ""),
                fic.url,
                "خلاصه: " + summaries.get(fic.url, fic.summary),
                "",
            ]
            number += 1
    return "\n".join(lines).strip()


def observe_fics(store: FicStateStore, fics: list[Fic]) -> None:
    for fic in fics:
        if not fic.work_id:
            continue
        fic.observation_status = store.classify(
            FicObservation(work_id=fic.work_id, chapters=fic.chapters, updated=fic.updated)
        )


async def build_digests(settings: Settings, *, fic_store: FicStateStore | None = None) -> tuple[str, str]:
    x_fics = await search_x_recommendations(settings)
    if fic_store is not None:
        observe_fics(fic_store, x_fics)
    x_summaries = await asyncio.to_thread(summarize_fics_persian, settings, x_fics)
    x_text = format_digest("🌙 لیست شبانه فن‌فیک — پیشنهادهای پیدا شده در X", x_fics, x_summaries, "x")

    ao3_fics = await asyncio.to_thread(search_ao3, 36)
    ao3_fics.sort(key=lambda f: (f.kudos, f.bookmarks, f.hits), reverse=True)
    if fic_store is not None:
        observe_fics(fic_store, ao3_fics)
    ao3_summaries = await asyncio.to_thread(summarize_fics_persian, settings, ao3_fics)
    ao3_text = format_digest("📚 لیست شبانه فن‌فیک — بهترین‌های خود AO3", ao3_fics, ao3_summaries, "ao3")
    return x_text, ao3_text


async def send_digests(settings: Settings, bot: TelegramBot | None = None) -> None:
    db_path = settings.state_path.with_name("private-review.sqlite3")
    message_store = MessageDeliveryStore(db_path)
    fic_store = FicStateStore(db_path)
    bot = bot or TelegramBot(
        settings.telegram_token,
        settings.admin_user_id,
        settings.review_chat_id,
        message_delivery_store=message_store,
    )
    if getattr(bot, "message_delivery_store", None) is None:
        bot.message_delivery_store = message_store
    try:
        x_text, ao3_text = await build_digests(settings, fic_store=fic_store)
        day = datetime.now(settings.timezone).strftime("%Y-%m-%d")
        bot.send_message(x_text, delivery_key=f"fic:{day}:x")
        bot.send_message(ao3_text, delivery_key=f"fic:{day}:ao3")
    finally:
        fic_store.close()
        # Do not close an externally supplied bot's pre-existing store.
        if bot.message_delivery_store is message_store:
            message_store.close()
            bot.message_delivery_store = None


async def main_async() -> int:
    settings = Settings.load(require_secrets=True)
    await send_digests(settings)
    return 0


def main() -> int:
    return asyncio.run(main_async())


if __name__ == "__main__":
    raise SystemExit(main())
