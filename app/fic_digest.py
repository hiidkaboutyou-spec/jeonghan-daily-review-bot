from __future__ import annotations

import asyncio
import html
import json
import logging
import re
import time
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlencode

import requests
from bs4 import BeautifulSoup

from .config import Settings
from .telegram import TelegramBot
from .x_client import XCollector

logger = logging.getLogger(__name__)
AO3 = "https://archiveofourown.org"
HEADERS = {"User-Agent": "Mozilla/5.0 JeonghanDailyReviewBot/1.0 personal-reading-digest"}

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

    @property
    def ship(self) -> str:
        joined = " | ".join(self.relationships).casefold()
        for label, aliases in SHIP_ALIASES:
            if any(alias.casefold() in joined for alias in aliases):
                return label
        return "Other Jeonghan ships"


def _clean(text: str) -> str:
    return re.sub(r"\s+", " ", html.unescape(text or "")).strip()


def _num(node: Any) -> int:
    if not node:
        return 0
    digits = re.sub(r"[^0-9]", "", node.get_text(" ", strip=True))
    return int(digits or 0)


def _is_jeonghan(relationships: list[str], text: str = "") -> bool:
    value = (" | ".join(relationships) + " " + text).casefold()
    return any(term in value for term in ("jeonghan", "yoon jeonghan", "정한", "윤정한", "ジョンハン"))


def _get(url: str, *, timeout: int = 20, attempts: int = 3) -> requests.Response | None:
    for attempt in range(attempts):
        try:
            response = requests.get(url, headers=HEADERS, timeout=timeout, allow_redirects=True)
            if response.status_code == 429:
                time.sleep(2 + attempt * 2)
                continue
            response.raise_for_status()
            return response
        except requests.RequestException as exc:
            logger.warning("HTTP attempt %s/%s failed for %s: %s", attempt + 1, attempts, url[:90], exc)
            if attempt + 1 < attempts:
                time.sleep(1.5 + attempt)
    return None


def search_ao3(limit: int = 36) -> list[Fic]:
    params = {
        "work_search[query]": '"Yoon Jeonghan" OR Jeonghan',
        "work_search[language_id]": "en",
        "work_search[sort_column]": "kudos_count",
        "work_search[sort_direction]": "desc",
        "commit": "Search",
    }
    response = _get(AO3 + "/works/search?" + urlencode(params), timeout=25, attempts=3)
    if response is None:
        return []
    soup = BeautifulSoup(response.text, "html.parser")
    fics: list[Fic] = []
    for work in soup.select("li.work.blurb"):
        heading = work.select_one("h4.heading a[href^='/works/']")
        if not heading:
            continue
        href = str(heading.get("href") or "").split("#", 1)[0]
        relationships = [_clean(a.get_text(" ", strip=True)) for a in work.select("li.relationships a.tag")]
        summary_node = work.select_one("blockquote.userstuff.summary")
        summary = _clean(summary_node.get_text(" ", strip=True) if summary_node else "No public summary provided.")
        if not _is_jeonghan(relationships, heading.get_text(" ", strip=True) + " " + summary):
            continue
        authors = work.select("h4.heading a[rel='author']")
        words_node = work.select_one("dd.words")
        rating_node = work.select_one("span.rating")
        fics.append(
            Fic(
                title=_clean(heading.get_text(" ", strip=True)),
                url=AO3 + href,
                author=", ".join(_clean(a.get_text(" ", strip=True)) for a in authors) or "Anonymous",
                summary=summary,
                relationships=relationships,
                rating=_clean(rating_node.get_text(" ", strip=True) if rating_node else ""),
                words=_clean(words_node.get_text(" ", strip=True) if words_node else ""),
                kudos=_num(work.select_one("dd.kudos")),
                bookmarks=_num(work.select_one("dd.bookmarks")),
                hits=_num(work.select_one("dd.hits")),
            )
        )
        if len(fics) >= limit:
            break
    return fics


def fetch_ao3_work(url: str) -> Fic | None:
    match = re.search(r"archiveofourown\.org/works/(\d+)", url)
    if not match:
        return None
    canonical = f"{AO3}/works/{match.group(1)}"
    response = _get(canonical + "?view_adult=true", timeout=15, attempts=2)
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
    if not _is_jeonghan(relationships, _clean(title.get_text())):
        return None
    summary_node = soup.select_one("div.summary blockquote.userstuff")
    authors = soup.select("h3.byline.heading a[rel='author']")
    return Fic(
        title=_clean(title.get_text(" ", strip=True)),
        url=canonical,
        author=", ".join(_clean(a.get_text(" ", strip=True)) for a in authors) or "Anonymous",
        summary=_clean(summary_node.get_text(" ", strip=True) if summary_node else "No public summary provided."),
        relationships=relationships,
        rating=_clean(soup.select_one("dd.rating.tags").get_text(" ", strip=True) if soup.select_one("dd.rating.tags") else ""),
        words=_clean(soup.select_one("dd.words").get_text(" ", strip=True) if soup.select_one("dd.words") else ""),
        kudos=_num(soup.select_one("dd.kudos")),
        bookmarks=_num(soup.select_one("dd.bookmarks")),
        hits=_num(soup.select_one("dd.hits")),
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
    found: dict[str, Fic] = {}
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
                    if work_id in found:
                        found[work_id].x_score = max(found[work_id].x_score, score)
                        continue
                    fic = await asyncio.to_thread(fetch_ao3_work, url)
                    if fic is None:
                        continue
                    fic.x_score = score
                    fic.source_note = note
                    found[work_id] = fic
                    if len(found) >= limit:
                        break
                if len(found) >= limit:
                    break
        except Exception as exc:
            logger.warning("X fic recommendation query failed but digest will continue: %s", exc)
        await asyncio.sleep(0.5)
        if len(found) >= limit:
            break
    return sorted(found.values(), key=lambda f: (f.x_score, f.kudos, f.bookmarks, f.hits), reverse=True)[:limit]


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
        logger.warning("Summary translation fallback failed: %s", exc)
        return summary


def summarize_fics_persian(settings: Settings, fics: list[Fic]) -> dict[str, str]:
    if not fics:
        return {}
    fallback = {fic.url: _translate_summary(fic.summary) for fic in fics}
    if not settings.gemini_api_key:
        return fallback
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
        candidates = list(dict.fromkeys([settings.gemini_model, "gemini-2.5-flash", "gemini-3.1-flash-lite"]))
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
                result = {str(x.get("url")): str(x.get("summary_fa", "")).strip() for x in parsed.get("items", []) if x.get("url")}
                if result:
                    return {fic.url: result.get(fic.url) or fallback[fic.url] for fic in fics}
            except Exception as exc:
                logger.warning("Fic Gemini model %s failed; trying fallback: %s", model, exc)
    except Exception as exc:
        logger.warning("Gemini summary layer unavailable: %s", exc)
    return fallback


def _chunks(text: str, max_len: int = 3800) -> list[str]:
    chunks: list[str] = []
    current = ""
    for block in text.split("\n\n"):
        candidate = block if not current else current + "\n\n" + block
        if len(candidate) <= max_len:
            current = candidate
        else:
            if current:
                chunks.append(current)
            current = block[:max_len]
    if current:
        chunks.append(current)
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
            rel = "; ".join(fic.relationships[:3]) or ship
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


async def build_digests(settings: Settings) -> tuple[str, str]:
    x_fics = await search_x_recommendations(settings)
    x_summaries = await asyncio.to_thread(summarize_fics_persian, settings, x_fics)
    x_text = format_digest("🌙 لیست شبانه فن‌فیک — پیشنهادهای پیدا شده در X", x_fics, x_summaries, "x")

    ao3_fics = await asyncio.to_thread(search_ao3, 36)
    ao3_fics.sort(key=lambda f: (f.kudos, f.bookmarks, f.hits), reverse=True)
    ao3_summaries = await asyncio.to_thread(summarize_fics_persian, settings, ao3_fics)
    ao3_text = format_digest("📚 لیست شبانه فن‌فیک — بهترین‌های خود AO3", ao3_fics, ao3_summaries, "ao3")
    return x_text, ao3_text


async def send_digests(settings: Settings, bot: TelegramBot | None = None) -> None:
    bot = bot or TelegramBot(settings.telegram_token, settings.admin_user_id, settings.review_chat_id)
    x_text, ao3_text = await build_digests(settings)
    for chunk in _chunks(x_text):
        bot.send_message(chunk)
    for chunk in _chunks(ao3_text):
        bot.send_message(chunk)


async def main_async() -> int:
    settings = Settings.load(require_secrets=True)
    await send_digests(settings)
    return 0


def main() -> int:
    return asyncio.run(main_async())


if __name__ == "__main__":
    raise SystemExit(main())
