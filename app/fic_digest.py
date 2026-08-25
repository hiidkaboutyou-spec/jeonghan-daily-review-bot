from __future__ import annotations

import asyncio
import html
import json
import logging
import os
import re
import time
from dataclasses import dataclass, replace
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
from .ai import (
    GEMINI_FREE_FALLBACK_MODELS,
    GEMINI_REQUEST_TIMEOUT_MS,
    gemini_retryable_provider_failure,
    gemini_shared_failure_kind,
)
from .telegram import TelegramBot
from .x_client import XCollector
from .gemini_structured import translation_safety_settings
from .fic_summary_quality import fic_summary_quality_issues

logger = logging.getLogger(__name__)

# ── Spoiler mode constants ───────────────────────────────────────────────
SPOILER_NO = "nospoiler"    # premise only, no plot details
SPOILER_MEDIUM = "medium"   # key details but no ending
SPOILER_FULL = "full"       # all spoilers actually present in public metadata
SPOILER_MODES = frozenset({SPOILER_NO, SPOILER_MEDIUM, SPOILER_FULL})

# ── Quality tier thresholds (relative ranking within a batch) ────────────
_TIER_GEM_RATIO = 0.10      # top 10 %
_TIER_SOLID_RATIO = 0.30    # top 30 %
_TIER_FRESH_MIN_KUDOS = 5   # new/updated with at least this many kudos
AO3 = "https://archiveofourown.org"
HEADERS = {"User-Agent": "JeonghanDailyReviewBot/1.0 (+personal private reading digest; low-rate requests)"}
JEONGHAN_TERMS = ("jeonghan", "yoon jeonghan", "정한", "윤정한", "ジョンハン")
AO3_PAGE_LIMIT = 25
AO3_PACE_SECONDS = 1.0
AO3_BALANCED_PAGE_LIMIT = 12
AO3_SEARCH_BUDGET_SECONDS = 90.0
AO3_X_LOOKUP_BUDGET_SECONDS = 75.0
FIC_SUMMARY_BATCH_SIZE = 8
FIC_SUMMARY_PACE_SECONDS = 4.2

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
    warnings: list[str] | None = None
    freeforms: list[str] | None = None
    # Enhanced metadata for deeper understanding
    emotional_tone: str = ""       # e.g. angst, fluff, humor, hurt-comfort
    themes: list[str] | None = None  # e.g. enemies-to-lovers, found-family
    tropes: list[str] | None = None  # e.g. slow-burn, pining, miscommunication
    summary_fa: str = ""            # Generated Persian summary (cached)
    summary_fa_nospoiler: str = ""  # No-spoiler Persian summary
    summary_fa_medium: str = ""     # Key conflict/details without an ending
    summary_fa_full: str = ""       # Full spoiler Persian summary
    relationship_dynamic_fa: str = ""
    warnings_fa: list[str] | None = None
    why_read: str = ""              # Short recommendation in Persian
    quality_score: float = 0.0       # Computed quality score
    quality_tier: str = ""          # gem, solid, fresh, popular

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

    @property
    def completion_status(self) -> str:
        match = re.fullmatch(r"\s*(\d+)\s*/\s*(\d+|\?)\s*", self.chapters or "")
        if not match:
            return ""
        current, total = match.groups()
        if total != "?" and int(current) >= int(total):
            return "complete"
        return "in_progress"


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
            if 400 <= response.status_code < 500 and response.status_code not in {408, 425, 429}:
                # Deleted/private AO3 works are common in old X recommendation
                # posts. Retrying the same permanent response only delays /fic.
                logger.info("AO3 request is no longer publicly available (HTTP %s)", response.status_code)
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
        warnings=[_clean(a.get_text(" ", strip=True)) for a in work.select("li.warnings a.tag")],
        freeforms=[_clean(a.get_text(" ", strip=True)) for a in work.select("li.freeforms a.tag")],
    )


def search_ao3(
    limit: int = 36,
    *,
    max_pages: int = AO3_PAGE_LIMIT,
    pace_seconds: float = AO3_PACE_SECONDS,
    sort_column: str = "kudos_count",
    max_elapsed_seconds: float = AO3_SEARCH_BUDGET_SECONDS,
) -> list[Fic]:
    if limit <= 0:
        return []
    if sort_column not in {"kudos_count", "revised_at", "created_at", "bookmarks_count", "hits"}:
        raise ValueError("unsupported AO3 sort column")
    base_params = {
        # Use AO3's dedicated character filter instead of a broad full-text OR
        # query, then retain the strict relationship-tag check below.
        "work_search[character_names]": "Yoon Jeonghan",
        "work_search[language_id]": "en",
        "work_search[sort_column]": sort_column,
        "work_search[sort_direction]": "desc",
        "commit": "Search",
    }
    fics: list[Fic] = []
    seen: set[str] = set()
    started = time.monotonic()
    for page in range(1, max(1, int(max_pages)) + 1):
        if page > 1 and time.monotonic() - started >= max(1.0, float(max_elapsed_seconds)):
            logger.warning("AO3 search stopped at its bounded time budget after page %s", page - 1)
            break
        params = dict(base_params)
        params["page"] = str(page)
        response = _get(AO3 + "/works/search?" + urlencode(params), timeout=25, attempts=2)
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


def search_ao3_balanced(
    limit: int = 48,
    *,
    max_pages_each: int = AO3_BALANCED_PAGE_LIMIT,
    pace_seconds: float = AO3_PACE_SECONDS,
) -> list[Fic]:
    """Blend recently updated works with established popular works.

    The two searches share the old 25-page safety budget (12 pages each by
    default), so discovery improves without increasing the worst-case request
    pressure on AO3.
    """
    if limit <= 0:
        return []
    recent_quota = max(1, (limit * 2) // 3)
    popular_quota = max(1, limit - recent_quota)
    recent = search_ao3(
        recent_quota,
        max_pages=max_pages_each,
        pace_seconds=pace_seconds,
        sort_column="revised_at",
    )
    if pace_seconds > 0:
        time.sleep(float(pace_seconds))
    popular = search_ao3(
        popular_quota + max(4, limit // 6),
        max_pages=max_pages_each,
        pace_seconds=pace_seconds,
        sort_column="kudos_count",
    )
    merged: list[Fic] = []
    seen: set[str] = set()
    for fic in [*recent, *popular]:
        key = fic.work_id or fic.url
        if key in seen:
            continue
        seen.add(key)
        merged.append(fic)
        if len(merged) >= limit:
            break
    return merged


def fetch_ao3_work(url: str) -> Fic | None:
    match = re.search(r"archiveofourown\.org/works/(\d+)", url)
    if not match:
        return None
    canonical = f"{AO3}/works/{match.group(1)}"
    # X recommendation links are supplemental and frequently stale. One bounded
    # lookup keeps them from blocking the stronger AO3 search for many minutes.
    response = _get(canonical + "?view_adult=true", timeout=10, attempts=1)
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
        warnings=[_clean(a.get_text(" ", strip=True)) for a in soup.select("dd.warning.tags a.tag")],
        freeforms=[_clean(a.get_text(" ", strip=True)) for a in soup.select("dd.freeform.tags a.tag")],
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


async def search_x_recommendations(
    settings: Settings,
    limit: int = 24,
    *,
    known_fics: list[Fic] | None = None,
) -> list[Fic]:
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
                if len(candidates) >= max(limit + 8, 24):
                    break
        except Exception as exc:
            logger.warning("X fic recommendation query failed but digest will continue: %s", type(exc).__name__)
        await asyncio.sleep(0.5)

    ranked = sorted(candidates.values(), key=lambda item: item[1], reverse=True)[: limit + 8]
    known_by_id = {
        fic.work_id: fic for fic in (known_fics or []) if fic.work_id
    }
    found: list[Fic] = []
    # AO3 has no supported public API. Fetch detail pages serially and paced instead
    # of opening four simultaneous requests against a volunteer-run service.
    lookup_started = time.monotonic()
    for index, (url, score, note) in enumerate(ranked):
        work_id_match = re.search(r"/works/(\d+)", url)
        known = known_by_id.get(work_id_match.group(1) if work_id_match else "")
        if known is not None:
            fic = replace(known, x_score=score, source_note=note)
            found.append(fic)
            if len(found) >= limit:
                break
            continue
        if index and time.monotonic() - lookup_started >= AO3_X_LOOKUP_BUDGET_SECONDS:
            logger.warning("X fic detail lookups stopped at their bounded time budget")
            break
        try:
            fic = await asyncio.to_thread(fetch_ao3_work, url)
        except Exception as exc:
            logger.warning("AO3 work lookup from X recommendation failed: %s", type(exc).__name__)
            fic = None
        if fic is not None:
            fic.x_score = score
            fic.source_note = note
            found.append(fic)
            if len(found) >= limit:
                break
        if index + 1 < len(ranked):
            await asyncio.sleep(AO3_PACE_SECONDS)
    return sorted(found, key=lambda f: (f.x_score, f.kudos, f.bookmarks, f.hits), reverse=True)[:limit]


def _translate_summary(summary: str) -> str:
    if not summary or summary == "No public summary provided.":
        return "خلاصهٔ عمومی برای این فیک ثبت نشده."
    if re.search(r"[\u0600-\u06ff]", summary):
        return summary
    # The old unofficial Google Translate fallback produced misleading literal
    # Persian in real digests. Preserve the AO3 source honestly instead. Adult
    # fiction is allowed; this warning concerns model availability, not content.
    return "⚠️ ترجمهٔ خلاصه در دسترس نبود؛ متن اصلی AO3:\n" + summary


def compute_fic_quality_score(fic: Fic, source: str = "") -> tuple[float, str]:
    """Compute a deterministic quality score and tier for a single Fic.

    Returns (score, tier) where tier is one of: gem, solid, fresh, popular.
    Scoring is relative to a batch and computed after ranking. The score
    itself is a float in [0, 100] for cross-batch comparison.
    """
    # Engagement: weighted combination of kudos, bookmarks, hits
    # Kudos are the strongest signal of reader enjoyment on AO3.
    kudos_score = min(fic.kudos / 50.0, 40.0)       # max 40 pts
    bookmark_score = min(fic.bookmarks / 20.0, 25.0) # max 25 pts
    hit_score = min(fic.hits / 500.0, 15.0)          # max 15 pts
    x_score = min(fic.x_score / 10.0, 10.0) if source == "x" else 0.0
    engagement = kudos_score + bookmark_score + hit_score + x_score

    # Freshness bonus
    freshness = 0.0
    if fic.observation_status == "new":
        freshness = 10.0
    elif fic.observation_status == "updated":
        freshness = 6.0

    # Completion bonus: completed fics are more valuable recommendations
    completion = 0.0
    if fic.completion_status == "complete":
        completion = 5.0
    elif fic.completion_status == "in_progress":
        completion = 2.0

    # Word count bonus for substantial fics (3k+ words)
    word_bonus = 0.0
    try:
        word_count = int(fic.words.replace(",", "") or "0")
        if word_count >= 30000:
            word_bonus = 5.0
        elif word_count >= 10000:
            word_bonus = 3.0
        elif word_count >= 3000:
            word_bonus = 1.0
    except (ValueError, AttributeError):
        pass

    score = round(engagement + freshness + completion + word_bonus, 1)
    # Tier is computed relative to the batch in rank_digest_fics;
    # here we assign based on absolute score ranges.
    if score >= 50:
        tier = "gem"
    elif score >= 25:
        tier = "solid"
    elif score >= 10:
        tier = "popular"
    else:
        tier = "fresh" if fic.observation_status in ("new", "updated") else ""
    return score, tier


_TIER_FA = {
    "gem": "💎",
    "solid": "⭐",
    "popular": "🔥",
    "fresh": "🆕",
}


def _assign_relative_tiers(fics: list[Fic]) -> None:
    """Re-assign quality tiers based on batch-relative ranking.

    The absolute score from compute_fic_quality_score gives a baseline,
    but within a batch the top 10% should always be 'gem' and the next
    20% should be 'solid' regardless of absolute scores.
    """
    if not fics:
        return
    ranked = sorted(fics, key=lambda f: f.quality_score, reverse=True)
    n = len(ranked)
    gem_limit = max(1, int(n * _TIER_GEM_RATIO))
    solid_limit = max(gem_limit + 1, int(n * _TIER_SOLID_RATIO))
    for idx, fic in enumerate(ranked):
        if idx < gem_limit:
            fic.quality_tier = "gem"
        elif idx < solid_limit:
            fic.quality_tier = "solid"
        elif fic.quality_tier not in ("gem", "solid"):
            # Keep existing tier if it's already gem/solid; otherwise reset
            fic.quality_tier = "popular" if fic.quality_score >= 15 else (
                "fresh" if fic.observation_status in ("new", "updated") else ""
            )


_FIC_NAME_REPLACEMENTS = (
    (re.compile(r"یون\s+جئونگان|جئونگان|جیونگان|جئونگهان|جونگان", re.I), "جونگهان"),
    (re.compile(r"سئونگ\s*چئول|سئونگ\s*چول", re.I), "سونگچول"),
    (re.compile(r"\b(?:Yoon\s+)?Jeonghan\b", re.I), "جونگهان"),
    (re.compile(r"\b(?:Choi\s+)?Seungcheol\b|\bS\.Coups\b", re.I), "سونگچول"),
    (re.compile(r"\bJoshua\b|\bHong\s+Jisoo\b", re.I), "جاشوآ"),
    (re.compile(r"\b(?:Kim\s+)?Mingyu\b", re.I), "مینگیو"),
    (re.compile(r"\b(?:Jeon\s+)?Wonwoo\b", re.I), "ونوو"),
    (re.compile(r"\b(?:Kwon\s+Soonyoung|Soonyoung|Hoshi)\b", re.I), "هوشی"),
    (re.compile(r"\b(?:Wen|Moon)\s+Junhui\b|\bJunhui\b", re.I), "جون"),
    (re.compile(r"\b(?:Lee\s+Seokmin|Seokmin|DK)\b", re.I), "دوکیوم"),
    (re.compile(r"\b(?:Xu\s+Minghao|Minghao|The8)\b", re.I), "مینگ‌هائو"),
    (re.compile(r"\b(?:Lee\s+Jihoon|Jihoon|Woozi)\b", re.I), "ووزی"),
    (re.compile(r"\b(?:Boo\s+)?Seungkwan\b", re.I), "سونگکوان"),
    (re.compile(r"\b(?:Lee\s+Chan|Dino)\b", re.I), "دینو"),
    (re.compile(r"\b(?:Chwe\s+Hansol|Hansol|Vernon)\b", re.I), "ورنون"),
)


def _normalize_fic_summary_names(value: str) -> str:
    result = str(value or "").strip()
    for pattern, replacement in _FIC_NAME_REPLACEMENTS:
        result = pattern.sub(replacement, result)
    return result


def _fallback_summaries(fics: list[Fic]) -> dict[str, str]:
    return {fic.url: _translate_summary(fic.summary) for fic in fics}


def _validate_spoiler_mode(spoiler_mode: str) -> str:
    mode = str(spoiler_mode or "").strip().casefold()
    if mode not in SPOILER_MODES:
        raise ValueError(f"unsupported fic spoiler mode: {spoiler_mode!r}")
    return mode


def _summary_for_mode(fic: Fic, fallback: str, spoiler_mode: str) -> str:
    mode = _validate_spoiler_mode(spoiler_mode)
    if mode == SPOILER_NO:
        return fic.summary_fa_nospoiler or fallback
    if mode == SPOILER_FULL:
        return fic.summary_fa_full or fallback
    return fic.summary_fa_medium or fic.summary_fa or fallback


def _fic_response_object(response: object) -> dict[str, Any]:
    parsed = getattr(response, "parsed", None)
    if isinstance(parsed, dict):
        return parsed
    try:
        value = json.loads(str(getattr(response, "text", "") or "{}"))
    except (TypeError, ValueError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def _fic_block_reason(response: object) -> str:
    try:
        feedback = getattr(response, "prompt_feedback", None)
        value = getattr(feedback, "block_reason", "") if feedback is not None else ""
        return str(getattr(value, "name", value) or "")[:80]
    except Exception:
        return ""


def _fic_prompt_prefix() -> str:
    return (
        "برای هر آیتم، فقط بر اساس metadata عمومی AO3 سه خلاصهٔ فارسی دقیق و خوش‌خوان بنویس. ترتیب اولویت قطعی است: معنای منبع، context و metadata، ترجمهٔ دقیق، فارسی طبیعی، و بعد تنظیم اختیاری لحن. "
        "title، summary، relationships، rating، warnings و tags تنها منبع حقیقت‌اند؛ متن داستان در اختیار تو نیست. هیچ پایان، اتفاق، نقش، هویت، ضمیر، رابطه، هشدار، theme یا trope تازه‌ای حدس نزن. "
        "هدف ترجمهٔ کلمه‌به‌کلمه نیست؛ همان معنا را با جمله‌بندی واقعی فارسی منتقل کن. "
        "اول کل summary را بفهم، بعد جمله‌ها را با ترتیب طبیعی فارسی از نو بساز. ساختار انگلیسی، نثر کتابی و عبارت‌های ماشینی مثل «می‌باشد»، «می‌نماید» و «درصدد» ممنوع‌اند. "
        "لحن باید امروزی و مناسب معرفی فن‌فیک باشد؛ کمی محاوره‌ای و صمیمی اشکالی ندارد، اما معنی را عوض نکن و متن را لوس یا اغراق‌آمیز نکن. "
        "اگر summary کوتاه است معمولاً 1 تا 3 جمله کافی است؛ اگر بلند است می‌توانی 2 تا 4 جمله بنویسی. فقط وقتی کوتاه کن که premise، رابطه، تنش، شوخی یا نکتهٔ اصلی از بین نرود. "
        "شوخی، angst، yearning، awkwardness، romance، tension و صراحت جنسی را با همان شدت متن اصلی حفظ کن. محتوای بزرگسالانه را سانسور، ملایم یا حذف نکن. "
        "دیالوگ، نقل‌قول، POV و اینکه چه کسی چه کاری می‌کند باید دقیق بماند. relationships، warnings و tags را برای فهم رابطه و زمینه به کار ببر، اما آن‌ها را به اتفاق قطعی داستان تبدیل نکن. "
        "نام‌ها: Yoon Jeonghan/Jeonghan = جونگهان، Choi Seungcheol/S.Coups = سونگچول، Joshua/Hong Jisoo = جاشوآ، Mingyu = مینگیو، Wonwoo = ونوو، Hoshi/Soonyoung = هوشی، "
        "Jun/Junhui = جون، DK/Seokmin = دوکیوم، The8/Minghao = مینگ‌هائو، Woozi/Jihoon = ووزی، Seungkwan = سونگکوان، Dino/Chan = دینو، Vernon/Hansol = ورنون. "
        "summary_fa_nospoiler فقط premise، رابطه و فضای کلی را نگه دارد و جزئیات بعدی یا پایان را لو ندهد. "
        "summary_fa_medium باید conflict و جزئیات مهمی را که خود metadata گفته نگه دارد، اما پایان را نگوید. "
        "summary_fa_full باید همهٔ اطلاعات و spoilerهای موجود در metadata را نگه دارد؛ چون متن داستان را ندیده‌ای، پایان دیده‌نشده را هرگز نساز. "
        "relationship_dynamic_fa را فقط از summary و relationships توصیف کن. در تگ AO3، `/` رابطهٔ عاشقانه/جنسی و `&` رابطهٔ غیرعاشقانه یا افلاطونی است؛ این دو را هرگز جابه‌جا تفسیر نکن. warnings_fa ترجمهٔ دقیق warnings است؛ حذف یا ملایم‌سازی هشدار ممنوع است. "
        "emotional_tone، themes و tropes فقط از summary/tags استخراج شوند و اگر شاهدی نیست آرایهٔ خالی یا رشتهٔ خالی برگردان. "
        "همچنین why_read بنویس: یک جملهٔ کوتاه فارسی که فقط به premise، رابطه، فضا، theme یا trope موجود در metadata اشاره کند. دربارهٔ کیفیت نثر، اجرای داستان، محبوبیت یا خاص‌بودن ادعایی نکن چون متن فیک را ندیده‌ای. "
        "فقط JSON مطابق schema برگردان؛ هر فیلد summary_fa_* باید خود متن نهایی فارسی باشد و هیچ توضیح اضافی نداشته باشد. "
    )


def summarize_fics_persian(
    settings: Settings,
    fics: list[Fic],
    spoiler_mode: str = SPOILER_MEDIUM,
) -> dict[str, str]:
    spoiler_mode = _validate_spoiler_mode(spoiler_mode)
    if not fics:
        return {}
    if not settings.gemini_api_key:
        return _fallback_summaries(fics)
    try:
        from google import genai
        from google.genai import types

        client = genai.Client(
            api_key=settings.gemini_api_key,
            http_options=types.HttpOptions(timeout=GEMINI_REQUEST_TIMEOUT_MS),
        )
        prompt_prefix = _fic_prompt_prefix()
        candidates = [
            model
            for model in dict.fromkeys([settings.gemini_model, *GEMINI_FREE_FALLBACK_MODELS])
            if model
        ]
        schema = {
            "type": "object",
            "required": ["items"],
            "properties": {
                "items": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "required": [
                            "url", "summary_fa_nospoiler", "summary_fa_medium",
                            "summary_fa_full", "relationship_dynamic_fa", "warnings_fa",
                            "emotional_tone", "themes", "tropes", "why_read",
                        ],
                        "properties": {
                            "url": {"type": "string"},
                            # Accepted for compatibility with an in-flight response
                            # from the pre-spoiler-mode schema; new prompts use medium.
                            "summary_fa": {"type": "string"},
                            "summary_fa_nospoiler": {"type": "string"},
                            "summary_fa_medium": {"type": "string"},
                            "summary_fa_full": {"type": "string"},
                            "relationship_dynamic_fa": {"type": "string"},
                            "warnings_fa": {"type": "array", "items": {"type": "string"}},
                            "emotional_tone": {"type": "string"},
                            "themes": {"type": "array", "items": {"type": "string"}},
                            "tropes": {"type": "array", "items": {"type": "string"}},
                            "why_read": {"type": "string"},
                        },
                    },
                },
            },
        }
        request_state = {
            "calls": 0,
            "max_calls": max(8, (len(fics) + FIC_SUMMARY_BATCH_SIZE - 1) // FIC_SUMMARY_BATCH_SIZE + 8),
            "next_at": 0.0,
        }
        unavailable_models: set[str] = set()

        def pace_request() -> bool:
            if request_state["calls"] >= request_state["max_calls"]:
                return False
            wait = float(request_state["next_at"]) - time.monotonic()
            if wait > 0:
                time.sleep(wait)
            request_state["next_at"] = time.monotonic() + FIC_SUMMARY_PACE_SECONDS
            request_state["calls"] += 1
            return True

        def translate_subset(subset: list[Fic]) -> tuple[dict[str, str], bool]:
            """Translate a batch; isolate blocked items instead of losing all eight.

            The boolean asks the outer loop to stop after project-wide auth,
            transport, or all-model quota failure. Empty/blocked content is split
            recursively within the bounded request budget.
            """
            if not subset or request_state["calls"] >= request_state["max_calls"]:
                return {}, False
            payload = [
                {
                    "url": fic.url,
                    "title": fic.title,
                    "summary": fic.summary,
                    "relationships": fic.relationships,
                    "rating": fic.rating,
                    "warnings": fic.warnings or [],
                    "tags": fic.freeforms or [],
                }
                for fic in subset
            ]
            prompt = prompt_prefix + json.dumps(payload, ensure_ascii=False)
            quota_models = 0
            active_candidates = [model for model in candidates if model not in unavailable_models]
            by_url = {fic.url: fic for fic in subset}
            for model in active_candidates:
                response = None
                final_error: Exception | None = None
                for attempt in range(2):
                    if not pace_request():
                        break
                    try:
                        response = client.models.generate_content(
                            model=model,
                            contents=prompt,
                            config=types.GenerateContentConfig(
                                response_mime_type="application/json",
                                response_json_schema=schema,
                                thinking_config=types.ThinkingConfig(thinking_level="minimal"),
                                safety_settings=translation_safety_settings(types),
                            ),
                        )
                        break
                    except Exception as exc:
                        if attempt == 0 and gemini_retryable_provider_failure(exc):
                            logger.warning(
                                "Fic Gemini model %s was temporarily unavailable; retrying once",
                                model,
                            )
                            continue
                        final_error = exc
                        break

                if final_error is not None:
                    shared_failure = gemini_shared_failure_kind(final_error)
                    if shared_failure == "quota":
                        quota_models += 1
                        # A model-level quota does not recover between adjacent
                        # batches. Retire it for this digest instead of producing
                        # the same 429 for every group before using the fallback.
                        unavailable_models.add(model)
                    logger.warning(
                        "Fic Gemini model %s failed; trying bounded fallback when available: %s",
                        model,
                        type(final_error).__name__,
                    )
                    if shared_failure in {"authentication", "transport"}:
                        return {}, True
                    # Quota is model-specific in Gemini; a supported fallback may
                    # still have capacity. Removed endpoints also fall through.
                    continue

                parsed = _fic_response_object(response) if response is not None else {}
                result: dict[str, str] = {}
                for item in parsed.get("items", []):
                    if not isinstance(item, dict):
                        continue
                    url = str(item.get("url", ""))
                    fic = by_url.get(url)
                    if fic is None:
                        continue
                    generated = {
                        SPOILER_NO: _normalize_fic_summary_names(str(item.get("summary_fa_nospoiler", ""))),
                        SPOILER_MEDIUM: _normalize_fic_summary_names(
                            str(item.get("summary_fa_medium") or item.get("summary_fa", ""))
                        ),
                        SPOILER_FULL: _normalize_fic_summary_names(str(item.get("summary_fa_full", ""))),
                    }
                    issues_by_mode = {
                        mode: fic_summary_quality_issues(
                            fic.summary,
                            candidate,
                            preserve_explicit_content=mode != SPOILER_NO,
                        )
                        for mode, candidate in generated.items()
                    }
                    is_complete_schema = "summary_fa_medium" in item
                    relationship_dynamic = _normalize_fic_summary_names(
                        str(item.get("relationship_dynamic_fa", ""))
                    )
                    warnings_fa = [
                        _normalize_fic_summary_names(str(value))
                        for value in item.get("warnings_fa", [])
                        if str(value).strip()
                    ]
                    if is_complete_schema and (
                        (fic.relationships and not relationship_dynamic)
                        or len(warnings_fa) < len(fic.warnings or [])
                    ):
                        logger.warning(
                            "Fic grounded metadata fields were incomplete for work %s",
                            fic.work_id or "unknown",
                        )
                        continue
                    candidate = generated[spoiler_mode]
                    if issues_by_mode[spoiler_mode]:
                        logger.warning(
                            "Fic %s summary quality gate rejected generated output for work %s: %s",
                            spoiler_mode,
                            fic.work_id or "unknown",
                            ",".join(issues_by_mode[spoiler_mode]),
                        )
                        continue
                    result[url] = candidate
                    if not issues_by_mode[SPOILER_NO]:
                        fic.summary_fa_nospoiler = generated[SPOILER_NO]
                    if not issues_by_mode[SPOILER_MEDIUM]:
                        fic.summary_fa_medium = generated[SPOILER_MEDIUM]
                        fic.summary_fa = generated[SPOILER_MEDIUM]
                    if not issues_by_mode[SPOILER_FULL]:
                        fic.summary_fa_full = generated[SPOILER_FULL]
                    fic.relationship_dynamic_fa = relationship_dynamic
                    fic.warnings_fa = warnings_fa
                    fic.emotional_tone = _normalize_fic_summary_names(str(item.get("emotional_tone", "")))
                    fic.themes = [str(value).strip() for value in item.get("themes", []) if str(value).strip()]
                    fic.tropes = [str(value).strip() for value in item.get("tropes", []) if str(value).strip()]
                    why = _normalize_fic_summary_names(str(item.get("why_read", "")))
                    if why:
                        fic.why_read = why
                if result:
                    missing = [fic for fic in subset if fic.url not in result]
                    if missing and request_state["calls"] < request_state["max_calls"]:
                        extra, stop = translate_subset(missing)
                        result.update(extra)
                        return result, stop
                    return result, False

                block_reason = _fic_block_reason(response) if response is not None else ""
                if block_reason:
                    logger.warning(
                        "Fic translation batch was blocked (%s); isolating affected summaries",
                        block_reason,
                    )
                # A different supported model can handle a content-specific false
                # positive without changing or censoring the AO3 source.

            if active_candidates and quota_models == len(active_candidates) and not [
                model for model in candidates if model not in unavailable_models
            ]:
                return {}, True
            if len(subset) > 1 and request_state["calls"] < request_state["max_calls"]:
                middle = len(subset) // 2
                left, stop_left = translate_subset(subset[:middle])
                if stop_left:
                    return left, True
                right, stop_right = translate_subset(subset[middle:])
                left.update(right)
                return left, stop_right
            return {}, False

        translated: dict[str, str] = {}
        for offset in range(0, len(fics), FIC_SUMMARY_BATCH_SIZE):
            batch = fics[offset : offset + FIC_SUMMARY_BATCH_SIZE]
            batch_result, stop_all_batches = translate_subset(batch)
            missing = [fic for fic in batch if fic.url not in batch_result]
            translated.update(batch_result)
            translated.update(_fallback_summaries(missing))
            if stop_all_batches:
                translated.update(_fallback_summaries(fics[offset + len(batch) :]))
                break

        return {fic.url: translated.get(fic.url, _translate_summary(fic.summary)) for fic in fics}
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


_RATING_FA = {
    "General Audiences": "مناسب همه",
    "Teen And Up Audiences": "نوجوان به بالا",
    "Mature": "بزرگسال",
    "Explicit": "صریح / بزرگسال",
    "Not Rated": "رده‌بندی نشده",
}

_WARNING_FA = {
    "No Archive Warnings Apply": "هشدار اصلی ندارد",
    "Creator Chose Not To Use Archive Warnings": "نویسنده هشدارهای آرشیو را مشخص نکرده",
    "Graphic Depictions Of Violence": "خشونت با توصیف صریح",
    "Major Character Death": "مرگ شخصیت اصلی",
    "Rape/Non-Con": "تجاوز / رابطه بدون رضایت",
    "Underage": "رابطه با فرد زیر سن قانونی",
}

_STATUS_FA = {
    "new": "🆕 تازه پیدا شده",
    "updated": "🔄 تازه آپدیت شده",
    "unchanged": "⭐ انتخاب ثابت",
}


def _rank_digest_fics(fics: list[Fic], source: str) -> list[Fic]:
    status_priority = {"updated": 0, "new": 1, "unchanged": 2, "": 3}

    def quality(fic: Fic) -> tuple[int, int, int, int]:
        primary = fic.x_score if source == "x" else fic.kudos
        return primary, fic.bookmarks, fic.kudos, fic.hits

    return sorted(
        fics,
        key=lambda fic: (status_priority.get(fic.observation_status, 3), *(-x for x in quality(fic))),
    )


def _status_counts(fics: list[Fic]) -> str:
    counts = {status: sum(fic.observation_status == status for fic in fics) for status in _STATUS_FA}
    if not any(counts.values()):
        return ""
    return f"تازه: {counts['new']} · آپدیت‌شده: {counts['updated']} · انتخاب ثابت: {counts['unchanged']}"


def format_digest(
    title: str,
    fics: list[Fic],
    summaries: dict[str, str],
    source: str,
    spoiler_mode: str = SPOILER_MEDIUM,
) -> str:
    spoiler_mode = _validate_spoiler_mode(spoiler_mode)
    if not fics:
        return title + "\n\nاین نوبت نتیجهٔ قابل‌اعتماد پیدا نشد؛ لیست خالی به معنی نبودن فن‌فیک نیست و اجرای بعدی دوباره بررسی می‌کند."
    lines = [title, "", f"تعداد انتخاب‌ها: {len(fics)} · زبان: انگلیسی"]
    counts = _status_counts(fics)
    if counts:
        lines.append(counts)
    lines.append("")
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
            status = _STATUS_FA.get(fic.observation_status, "")
            tier_badge = _TIER_FA.get(fic.quality_tier, "")
            stats = f"کودوس {fic.kudos:,} · بوکمارک {fic.bookmarks:,} · بازدید {fic.hits:,}"
            if source == "x":
                stats += f" · امتیاز X: {fic.x_score:,}"
            if fic.chapters:
                progress = ""
                if fic.completion_status == "complete":
                    progress = " (✅ کامل)"
                elif fic.completion_status == "in_progress":
                    progress = " (✍️ درحال انتشار)"
                stats += f" · فصل {fic.chapters}{progress}"
            jh_relationships = _jeonghan_relationships(fic.relationships)
            rel = "; ".join(jh_relationships[:3]) or ship
            heading = f"{number}) {tier_badge + ' ' if tier_badge else ''}{status + ' · ' if status else ''}{fic.title}"
            lines += [heading, f"نویسنده: {fic.author}", f"رابطه: {rel}"]
            if fic.rating:
                lines.append("رده‌بندی: " + _RATING_FA.get(fic.rating, fic.rating))
            warnings = [_WARNING_FA.get(value, value) for value in (fic.warnings or [])]
            if warnings:
                lines.append("هشدار AO3: " + "؛ ".join(warnings[:3]))
            if fic.freeforms:
                lines.append("تگ‌ها: " + "؛ ".join(fic.freeforms[:3]))
            lines += [
                stats + (f" · کلمه {fic.words}" if fic.words else ""),
                fic.url,
                "خلاصه: " + _summary_for_mode(
                    fic, summaries.get(fic.url, fic.summary), spoiler_mode
                ),
            ]
            if fic.why_read:
                lines.append("چرا بخوانیم: " + fic.why_read)
            lines.append("")
            number += 1
    return "\n".join(lines).strip()


def observe_fics(store: FicStateStore, fics: list[Fic]) -> None:
    for fic in fics:
        if not fic.work_id:
            continue
        fic.observation_status = store.classify(
            FicObservation(work_id=fic.work_id, chapters=fic.chapters, updated=fic.updated)
        )


def _delivery_run_scope(day: str, *, manual_request: bool) -> str:
    """Return an idempotency scope that is stable per deployed revision.

    A new main revision deliberately triggers a fresh validation digest. Reusing
    only the calendar day made Telegram's durable receipt store silently accept
    the previous revision's message as the new delivery. Include GitHub's immutable
    revision so retries of one deployment deduplicate while a real fix is sent once.
    """
    if manual_request:
        return datetime.now(timezone.utc).strftime("manual:%Y%m%dT%H%M%S%fZ")
    revision = re.sub(r"[^0-9a-f]", "", os.getenv("GITHUB_SHA", "").casefold())[:12]
    return f"{day}:{revision}" if revision else day


async def build_digests(
    settings: Settings,
    *,
    fic_store: FicStateStore | None = None,
    spoiler_mode: str = SPOILER_MEDIUM,
) -> tuple[str, str]:
    spoiler_mode = _validate_spoiler_mode(spoiler_mode)
    # Build the authoritative AO3 pool once, then reuse its parsed work metadata
    # for X recommendations. The old order reopened every recommended work page
    # before doing the AO3 search, so a handful of stale/slow links could consume
    # two minutes and still leave the X list empty.
    #
    # Phase isolation: each phase catches its own errors so one failure never
    # prevents the other list from being built.
    ao3_candidates: list[Fic] = []
    try:
        ao3_candidates = await asyncio.to_thread(search_ao3_balanced, 48)
    except Exception as exc:
        logger.warning("AO3 search phase failed: %s", type(exc).__name__)

    x_fics: list[Fic] = []
    try:
        x_fics = await search_x_recommendations(settings, known_fics=ao3_candidates)
    except Exception as exc:
        logger.warning("X fic recommendations phase failed: %s", type(exc).__name__)
    if fic_store is not None:
        try:
            observe_fics(fic_store, x_fics)
        except Exception as exc:
            logger.warning("Fic observation (X) failed: %s", type(exc).__name__)
    x_fics = _rank_digest_fics(x_fics, "x")
    x_summaries: dict[str, str] = {}
    try:
        x_summaries = await asyncio.to_thread(
            summarize_fics_persian, settings, x_fics, spoiler_mode
        )
    except Exception as exc:
        logger.warning("X summary translation phase failed: %s", type(exc).__name__)
    # Compute quality scores relative to the X batch
    for fic in x_fics:
        fic.quality_score, fic.quality_tier = compute_fic_quality_score(fic, "x")
    if x_fics:
        _assign_relative_tiers(x_fics)
    x_text = format_digest(
        "🌙 فن‌فیک‌های پیشنهادی از X", x_fics, x_summaries, "x", spoiler_mode
    )

    x_ids = {fic.work_id for fic in x_fics if fic.work_id}
    ao3_fics = [fic for fic in ao3_candidates if not fic.work_id or fic.work_id not in x_ids][:36]
    if fic_store is not None:
        try:
            observe_fics(fic_store, ao3_fics)
        except Exception as exc:
            logger.warning("Fic observation (AO3) failed: %s", type(exc).__name__)
    ao3_fics = _rank_digest_fics(ao3_fics, "ao3")
    ao3_summaries: dict[str, str] = {}
    try:
        ao3_summaries = await asyncio.to_thread(
            summarize_fics_persian, settings, ao3_fics, spoiler_mode
        )
    except Exception as exc:
        logger.warning("AO3 summary translation phase failed: %s", type(exc).__name__)
    # Compute quality scores relative to the AO3 batch
    for fic in ao3_fics:
        fic.quality_score, fic.quality_tier = compute_fic_quality_score(fic, "ao3")
    if ao3_fics:
        _assign_relative_tiers(ao3_fics)
    ao3_text = format_digest(
        "📚 تازه‌ها و انتخاب‌های محبوب AO3", ao3_fics, ao3_summaries, "ao3", spoiler_mode
    )
    logger.info(
        "Fanfic digest built successfully (x=%s ao3_pool=%s ao3_list=%s)",
        len(x_fics),
        len(ao3_candidates),
        len(ao3_fics),
    )
    return x_text, ao3_text


async def send_digests(settings: Settings, bot: TelegramBot | None = None) -> None:
    db_path = settings.state_path.with_name("private-review.sqlite3")
    fic_store = FicStateStore(db_path)
    manual_request = bot is not None
    owned_message_store: MessageDeliveryStore | None = None
    if bot is None:
        owned_message_store = MessageDeliveryStore(db_path)
        bot = TelegramBot(
            settings.telegram_token,
            settings.admin_user_id,
            settings.review_chat_id,
            message_delivery_store=owned_message_store,
        )
    elif getattr(bot, "message_delivery_store", None) is None:
        owned_message_store = MessageDeliveryStore(db_path)
        bot.message_delivery_store = owned_message_store
    try:
        x_text, ao3_text = await build_digests(settings, fic_store=fic_store)
        day = datetime.now(settings.timezone).strftime("%Y-%m-%d")
        run_scope = _delivery_run_scope(day, manual_request=manual_request)
        # Deliver each list independently so a Telegram error on one never
        # blocks the other.
        try:
            bot.send_message(x_text, delivery_key=f"fic:{run_scope}:x")
        except Exception as exc:
            logger.warning("X fic list delivery failed: %s", type(exc).__name__)
        try:
            bot.send_message(ao3_text, delivery_key=f"fic:{run_scope}:ao3")
        except Exception as exc:
            logger.warning("AO3 fic list delivery failed: %s", type(exc).__name__)
        logger.info("Fanfic digest delivery completed")
    finally:
        fic_store.close()
        if owned_message_store is not None and bot.message_delivery_store is owned_message_store:
            owned_message_store.close()
            bot.message_delivery_store = None


async def main_async() -> int:
    settings = Settings.load(require_secrets=True)
    try:
        await send_digests(settings)
    except Exception as exc:
        logger.error("Fanfic digest failed entirely: %s", type(exc).__name__)
        # Attempt to notify via Telegram even if the build failed.
        try:
            from .telegram import TelegramBot
            bot = TelegramBot(
                settings.telegram_token,
                settings.admin_user_id,
                settings.review_chat_id,
            )
            bot.send_message(
                f"⚠️ فن‌فیک دیلی با خطا مواجه شد ({type(exc).__name__})؛ اجرای بعدی دوباره تلاش می‌کند."
            )
        except Exception:
            pass
        return 1
    return 0


def main() -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    return asyncio.run(main_async())


if __name__ == "__main__":
    raise SystemExit(main())
