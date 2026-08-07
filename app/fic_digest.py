from __future__ import annotations

import asyncio
import html
import json
import logging
import re
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
HEADERS = {"User-Agent": "JeonghanDailyReviewBot/1.0 (personal reading digest; respectful request rate)"}

SHIP_ALIASES = [
    ("Jeongcheol", ("Choi Seungcheol", "S.Coups")),
    ("Jihan", ("Hong Jisoo", "Joshua Hong", "Joshua")),
    ("GyuHan", ("Kim Mingyu", "Mingyu")),
    ("WonHan", ("Jeon Wonwoo", "Wonwoo")),
    ("SoonHan", ("Kwon Soonyoung", "Hoshi", "Soonyoung")),
    ("JunHan", ("Wen Junhui", "Moon Junhui", "Junhui", "Jun")),
    ("SeokHan", ("Lee Seokmin", "DK", "Seokmin")),
    ("HaoHan", ("Xu Minghao", "The8", "Minghao")),
    ("WooHan", ("Lee Jihoon", "Woozi", "Jihoon")),
    ("KwanHan", ("Boo Seungkwan", "Seungkwan")),
    ("ChanHan", ("Lee Chan", "Dino")),
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


def _num(node: Any) -> int:
    if not node:
        return 0
    value = re.sub(r"[^0-9]", "", node.get_text(" ", strip=True))
    return int(value or 0)


def _clean(text: str) -> str:
    return re.sub(r"\s+", " ", html.unescape(text or "")).strip()


def _is_jeonghan(relationships: list[str], text: str = "") -> bool:
    value = (" | ".join(relationships) + " " + text).casefold()
    return any(term in value for term in ("jeonghan", "yoon jeonghan", "정한", "윤정한"))


def search_ao3(limit: int = 36) -> list[Fic]:
    params = {
        "work_search[query]": '"Yoon Jeonghan" OR Jeonghan',
        "work_search[language_id]": "en",
        "work_search[sort_column]": "kudos_count",
        "work_search[sort_direction]": "desc",
        "commit": "Search",
    }
    url = AO3 + "/works/search?" + urlencode(params)
    response = requests.get(url, headers=HEADERS, timeout=30)
    response.raise_for_status()
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
        author_nodes = work.select("h4.heading a[rel='author']")
        rating_node = work.select_one("span.rating")
        words_node = work.select_one("dd.words")
        fics.append(Fic(
            title=_clean(heading.get_text(" ", strip=True)),
            url=AO3 + href,
            author=", ".join(_clean(a.get_text(" ", strip=True)) for a in author_nodes) or "Anonymous",
            summary=summary,
            relationships=relationships,
            rating=_clean(rating_node.get_text(" ", strip=True) if rating_node else ""),
            words=_clean(words_node.get_text(" ", strip=True) if words_node else ""),
            kudos=_num(work.select_one("dd.kudos")),
            bookmarks=_num(work.select_one("dd.bookmarks")),
            hits=_num(work.select_one("dd.hits")),
        ))
        if len(fics) >= limit:
            break
    return fics


def fetch_ao3_work(url: str) -> Fic | None:
    match = re.search(r"archiveofourown\.org/works/(\d+)", url)
    if not match:
        return None
    canonical = f"{AO3}/works/{match.group(1)}"
    response = requests.get(canonical + "?view_adult=true", headers=HEADERS, timeout=25)
    if response.status_code != 200:
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
    author_nodes = soup.select("h3.byline.heading a[rel='author']")
    rating_node = soup.select_one("dd.rating.tags")
    words_node = soup.select_one("dd.words")
    return Fic(
        title=_clean(title.get_text(" ", strip=True)),
        url=canonical,
        author=", ".join(_clean(a.get_text(" ", strip=True)) for a in author_nodes) or "Anonymous",
        summary=_clean(summary_node.get_text(" ", strip=True) if summary_node else "No public summary provided."),
        relationships=relationships,
        rating=_clean(rating_node.get_text(" ", strip=True) if rating_node else ""),
        words=_clean(words_node.get_text(" ", strip=True) if words_node else ""),
        kudos=_num(soup.select_one("dd.kudos")),
        bookmarks=_num(soup.select_one("dd.bookmarks")),
        hits=_num(soup.select_one("dd.hits")),
    )


def _extract_ao3_urls(tweet: Any) -> list[str]:
    candidates: list[str] = []
    raw = str(getattr(tweet, "rawContent", "") or "")
    candidates.extend(re.findall(r"https?://(?:www\.)?archiveofourown\.org/works/\d+[^\s]*", raw))
    for link in list(getattr(tweet, "links", None) or []):
        for attr in ("url", "expandedUrl", "expanded_url", "href"):
            value = str(getattr(link, attr, "") or "")
            if value:
                candidates.append(value)
    resolved: list[str] = []
    for value in candidates:
        if "archiveofourown.org/works/" in value:
            resolved.append(value)
            continue
        if "t.co/" in value:
            try:
                final = requests.get(value, headers=HEADERS, timeout=10, allow_redirects=True).url
                if "archiveofourown.org/works/" in final:
                    resolved.append(final)
            except requests.RequestException:
                pass
    return list(dict.fromkeys(resolved))


async def search_x_recommendations(settings: Settings, limit: int = 24) -> list[Fic]:
    collector = XCollector(settings.x_cookies, settings.sources, settings.keyword_groups)
    api = await collector._get_api()
    queries = [
        '(JEONGHAN OR "Yoon Jeonghan" OR 윤정한 OR ジョンハン) (AO3 OR archiveofourown)',
        '(JEONGCHEOL OR JIHAN OR GYUHAN OR WONHAN OR SOONHAN) (AO3 OR fic OR fanfic)',
        '(JEONGHAN) (fic rec OR fic recommendation OR fanfic rec)',
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
                    work_id = re.search(r"/works/(\d+)", url)
                    if not work_id or work_id.group(1) in found:
                        continue
                    fic = await asyncio.to_thread(fetch_ao3_work, url)
                    if fic:
                        fic.x_score = score
                        fic.source_note = note
                        found[work_id.group(1)] = fic
                if len(found) >= limit:
                    break
        except Exception as exc:
            logger.warning("X fic recommendation query failed: %s", exc)
        await asyncio.sleep(0.5)
    return sorted(found.values(), key=lambda f: (f.x_score, f.kudos, f.bookmarks), reverse=True)[:limit]


def summarize_fics_persian(settings: Settings, fics: list[Fic]) -> dict[str, str]:
    if not fics or not settings.gemini_api_key:
        return {fic.url: fic.summary for fic in fics}
    try:
        from google import genai
        from google.genai import types

        client = genai.Client(api_key=settings.gemini_api_key)
        payload = [{"url": f.url, "title": f.title, "summary": f.summary} for f in fics]
        prompt = (
            "برای هر فن‌فیک فقط بر اساس summary رسمی AO3 یک خلاصه فارسی روان 1 تا 3 جمله‌ای بنویس. "
            "هیچ اتفاق، trope، پایان یا جزئیاتی که در summary نیست اختراع نکن. نام‌ها را حفظ کن. "
            "خروجی JSON با items شامل url و summary_fa باشد. داده‌ها: " + repr(payload)
        )
        response = client.models.generate_content(
            model=settings.gemini_model or "gemini-2.5-flash-lite",
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
                                    "summary_fa": {"type": "string"}
                                }
                            }
                        }
                    }
                },
            ),
        )
        parsed = json.loads(response.text or "{}")
        result = {
            str(x["url"]): str(x["summary_fa"]).strip()
            for x in parsed.get("items", [])
            if x.get("url")
        }
        return {fic.url: result.get(fic.url, fic.summary) for fic in fics}
    except Exception as exc:
        logger.warning("Fic summary translation failed: %s", exc)
        return {fic.url: fic.summary for fic in fics}


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


async def main_async() -> int:
    settings = Settings.load(require_secrets=True)
    bot = TelegramBot(settings.telegram_token, settings.admin_user_id, settings.review_chat_id)

    x_fics = await search_x_recommendations(settings)
    x_summaries = await asyncio.to_thread(summarize_fics_persian, settings, x_fics)
    x_text = format_digest("🌙 لیست شبانه فن‌فیک — پیشنهادهای پیدا شده در X", x_fics, x_summaries, "x")
    for chunk in _chunks(x_text):
        bot.send_message(chunk)

    ao3_fics = await asyncio.to_thread(search_ao3, 36)
    ao3_fics.sort(key=lambda f: (f.kudos, f.bookmarks, f.hits), reverse=True)
    ao3_summaries = await asyncio.to_thread(summarize_fics_persian, settings, ao3_fics)
    ao3_text = format_digest("📚 لیست شبانه فن‌فیک — بهترین‌های خود AO3", ao3_fics, ao3_summaries, "ao3")
    for chunk in _chunks(ao3_text):
        bot.send_message(chunk)
    return 0


def main() -> int:
    return asyncio.run(main_async())


if __name__ == "__main__":
    raise SystemExit(main())
