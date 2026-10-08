"""Private, resumable date bundles on the existing single runtime/state owner.

Provider completeness stays authoritative. Public recovery and local archives
are useful observations, never evidence that the whole day was retrieved.
"""
from __future__ import annotations

import asyncio
import hashlib
import logging
import re
import time
from collections import Counter
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import requests

from .main import parse_date_query, short_id
from .models import Update
from .organizer import detect_category
from .source_authority_hardening import _configured_only
from .x_client import normalize_handle, _date_suffix

DIGITS = str.maketrans("۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩", "01234567890123456789")
DATE_TOKEN = re.compile(r"\b(?:20\d{2}[-/.]\d{1,2}[-/.]\d{1,2}|20\d{6}|\d{6})\b")
BRIDGE_URL = "https://raw.githubusercontent.com/hiidkaboutyou-spec/jeonghan-daily-review-bot/main/config/content_requests.json"
LIVE = re.compile(r"(?i)(\blive\b|라이브|생방|ライブ|لایو)")
CATEGORIES = {"live": "لایو", "weverse": "ویورس", "instagram": "اینستاگرام", "general": "سایر آپدیت‌ها",
              "brand": "برند", "airport": "فرودگاه", "concert": "اجرا", "birthday": "تولد"}
CATEGORIES.update({"jeonghan_instagram": "اینستاگرام جونگهان", "member_instagram": "اینستاگرام اعضا", "fansign": "فنساین"})
logger = logging.getLogger(__name__)
STATUSES = {"collecting": "در حال جمع‌آوری", "delivering": "در حال ارسال", "complete": "کامل",
            "partial": "ناقص", "translation_pending": "منتظر ترجمه", "disabled": "غیرفعال"}


def status_label(status):
    return "ناموفق" if status.startswith("failed:") else STATUSES.get(status, status)


def request_timezone(app):
    return ZoneInfo((getattr(app.settings, "runtime", {}) or {}).get("content_date_timezone", "Asia/Seoul"))


def route_date_request(app, text: str) -> bool:
    if not text.strip():
        return False
    normalized = text.translate(DIGITS)
    command = normalized.split(maxsplit=1)[0].split("@")[0].lower()
    if command == "/date_status":
        jobs = app.state.data.get("date_requests", {}).get("jobs", {})
        lines = [f"{j['day']} · {status_label(j['status'])} · {len(j.get('delivered', []))}/{len(j.get('selected', []))}" for j in jobs.values()]
        app.telegram.send_message("\n".join(lines[-20:]) or "درخواست تاریخ‌داری ثبت نشده.")
        return True
    if normalized.startswith("/") and command != "/date":
        return False
    token = DATE_TOKEN.search(normalized)
    if not token and command != "/date":
        return False
    window = parse_date_query(normalized, request_timezone(app))
    if window is None:
        app.telegram.send_message("تاریخ معتبر نیست. مثال: /date 261004 لایو تولد")
        return True
    topic = "live" if LIVE.search(normalized) else "all"
    try:
        enqueue(app, window[0].astimezone(request_timezone(app)).date().isoformat(), topic)
    except ValueError:
        app.telegram.send_message("این تاریخ هنوز نرسیده؛ محتوای آینده در دسترس نیست.")
    return True


def enqueue(app, day: str, topic: str, *, external_id: str = "") -> str:
    tz = request_timezone(app)
    window = parse_date_query(day, tz)
    if window is None or topic not in {"all", "live"}:
        raise ValueError("Invalid date request")
    start, end = window
    if start > datetime.now(timezone.utc):
        raise ValueError("Future content is unavailable")
    request_id = hashlib.sha256(f"{day}:{tz.key}:{topic}:{external_id}".encode()).hexdigest()[:20]
    namespace = app.state.data.setdefault("date_requests", {})
    jobs = namespace.setdefault("jobs", {})
    handles = list(dict.fromkeys(
        normalize_handle(s["handle"]) for s in app.settings.sources if s.get("enabled", True)
    ))
    if request_id in jobs:
        job = jobs[request_id]
        # Explicit repeats preserve delivery receipts. An in-progress calendar
        # day must be fetched again even if every source was previously reachable:
        # posts written since the last request are not in that old snapshot.
        if job["status"] in {"partial", "translation_pending"} or (
            job["status"] == "complete" and end > datetime.now(timezone.utc)
        ):
            # A formerly in-progress day still needs a full refresh after midnight;
            # the earlier "complete" source receipts cover only a snapshot.
            if end > datetime.now(timezone.utc) or job.get("provisional_day", False):
                job["pending_sources"] = handles
                job["coverage"] = {}
                job["local_loaded"] = False
            else:
                job["pending_sources"] = [
                    h for h in handles if job.get("coverage", {}).get(h) != "complete"
                ]
            job["status"] = "collecting" if job["pending_sources"] else "delivering"
            job["retry_after"] = ""
            job.pop("reported", None)
    else:
        jobs[request_id] = {
            "id": request_id, "day": day, "timezone": tz.key, "topic": topic,
            "start": start.isoformat(), "end": end.isoformat(), "status": "collecting",
            "pending_sources": handles, "coverage": {}, "observed": [],
            "selected": [], "delivered": [], "translation_attempts": {},
        }
    app.state.save()
    app.telegram.send_message(
        f"درخواست {day} ({tz.key}) ثبت شد؛ همهٔ منابع فعال را بررسی می‌کنم. "
        "پست‌ها با ترجمه و لینک منبع، به ترتیب انتشار می‌آیند. وضعیت: /date_status",
        delivery_key=f"date-request:{request_id}:accepted",
    )
    return request_id


def sync_repository_requests(app):
    """An opt-in, fixed trusted-main queue; no arbitrary destinations or URLs."""
    if not (getattr(app.settings, "runtime", {}) or {}).get("repository_content_requests", False):
        return
    namespace = app.state.data.setdefault("date_requests", {})
    now = datetime.now(timezone.utc)
    previous = namespace.get("bridge_checked_at")
    if previous and now - datetime.fromisoformat(previous) < timedelta(minutes=5):
        return
    namespace["bridge_checked_at"] = now.isoformat()
    try:
        response = requests.get(BRIDGE_URL, timeout=8, stream=True)
        try:
            response.raise_for_status()
            body = bytearray()
            for chunk in response.iter_content(4096):
                body.extend(chunk)
                if len(body) > 65536:
                    raise ValueError("Repository queue too large")
            import json
            entries = json.loads(body)
        finally:
            response.close()
        if not isinstance(entries, list) or len(entries) > 50:
            raise ValueError("Invalid queue")
        accepted = namespace.setdefault("bridge_accepted", [])
        for entry in entries:
            if not isinstance(entry, dict) or set(entry) != {"id", "date", "topic"}:
                continue
            identifier = entry["id"]
            if not isinstance(identifier, str) or not re.fullmatch(r"[a-zA-Z0-9_-]{1,80}", identifier) or identifier in accepted:
                continue
            if not isinstance(entry["date"], str) or not re.fullmatch(r"20\d{2}-\d{2}-\d{2}", entry["date"]):
                continue
            if not isinstance(entry["topic"], str) or entry["topic"] not in {"all", "live"}:
                continue
            try:
                enqueue(app, entry["date"], entry["topic"], external_id=identifier)
            except ValueError:
                continue  # One invalid/future request must not block later entries.
            accepted.append(identifier)
            app.state.save()
        namespace.pop("bridge_error", None)
    except (requests.RequestException, ValueError, TypeError) as exc:
        namespace["bridge_error"] = type(exc).__name__


def select_updates(updates: list[Update], topic: str) -> list[Update]:
    for update in updates:
        update.category = detect_category(update)
    if topic == "live":
        seeds = [u for u in updates if LIVE.search(u.translation_source())]
        threads = {(u.author.casefold(), u.conversation_id) for u in seeds}
        updates = [u for u in updates if (u.author.casefold(), u.conversation_id) in threads]
    return sorted({u.id: u for u in updates}.values(), key=lambda u: (u.created_at, u.id))


def _job_update(app, identifier):
    return app.state.get_update(identifier) or app.archive_db.get_update(identifier)


async def process_date_requests(app):
    """Bound each wake; a date-job failure cannot stop ordinary Daily monitoring."""
    try:
        sync_repository_requests(app)
        started = time.monotonic()
        steps = max(1, min(5, int((getattr(app.settings, "runtime", {}) or {}).get("date_request_steps_per_tick", 5))))
        for _ in range(steps):
            if time.monotonic() - started >= 25:
                break
            await _process_date_step(app)
    except Exception as exc:
        logger.warning("Date request step deferred (%s)", type(exc).__name__)
        app.state.data.setdefault("date_requests", {})["last_error"] = type(exc).__name__
        app.state.save()


async def _process_date_step(app):
    jobs = app.state.data.get("date_requests", {}).get("jobs", {})
    for job in list(jobs.values()):
        if job["status"] not in {"collecting", "delivering"}:
            continue
        retry = job.get("retry_after")
        if retry and datetime.fromisoformat(retry) > datetime.now(timezone.utc):
            continue
        start, end = datetime.fromisoformat(job["start"]), datetime.fromisoformat(job["end"])
        if not job.get("local_loaded"):
            local = list(app.archive_db.date_window(start, end))
            local.extend(u for raw in app.state.data.get("archive", {}).values()
                         if (u := Update.from_dict(raw)) and start <= u.created_at < end)
            for update in _configured_only(app.collector, local):
                app.archive_db.index_update(update)
                app.state.archive_update(update)
                if update.id not in job["observed"]:
                    job["observed"].append(update.id)
            job["local_loaded"] = True
            app.state.save()
        if job["pending_sources"]:
            handle = job["pending_sources"][0]
            enabled = {normalize_handle(s["handle"]).casefold() for s in app.settings.sources if s.get("enabled", True)}
            try:
                if handle.casefold() not in enabled:
                    job["coverage"][handle] = "disabled"
                else:
                    app.collector.last_errors = []
                    updates = await asyncio.wait_for(app.collector.collect_source(handle, start, end), timeout=25)
                    for update in _configured_only(app.collector, updates):
                        if update.author.casefold() == handle.casefold() and start <= update.created_at < end:
                            app.archive_db.index_update(update)
                            app.state.archive_update(update)
                            if update.id not in job["observed"]:
                                job["observed"].append(update.id)
                    job["coverage"][handle] = "partial" if app.collector.last_errors else "complete"
            except Exception as exc:
                job["coverage"][handle] = f"failed:{type(exc).__name__}"
                # A failed complete timeline may still have useful author-scoped
                # search observations. They remain PARTIAL and cannot move a daily
                # cursor or authorize a completeness claim.
                if getattr(app.collector, "cookies", None) and handle.casefold() in enabled:
                    try:
                        recovered = await asyncio.wait_for(app.collector._run_queries(
                            [f"from:{handle} -filter:retweets {_date_suffix(start, end)}"],
                            start, end, max_per_query=1000), timeout=20)
                        for update in _configured_only(app.collector, recovered):
                            if update.author.casefold() == handle.casefold() and start <= update.created_at < end:
                                app.archive_db.index_update(update)
                                app.state.archive_update(update)
                                if update.id not in job["observed"]:
                                    job["observed"].append(update.id)
                        job["coverage"][handle] = "partial"
                    except Exception:
                        pass  # Keep the original bounded failure classification.
            job["pending_sources"].pop(0)
            app.state.save()
            return  # Bound work on the existing webhook worker; resume next tick.
        if job["status"] == "collecting":
            updates = [u for identifier in job["observed"] if (u := _job_update(app, identifier))]
            selected = select_updates(_configured_only(app.collector, updates), job["topic"])
            job["selected"] = [u.id for u in selected]
            job["status"] = "delivering"
            counts = Counter(u.category for u in selected)
            app.telegram.send_message(
                f"بستهٔ {job['day']} · {len(selected)} پست\n"
                + " · ".join(f"{CATEGORIES.get(category, category)}: {count}" for category, count in counts.items())
                + "\nترتیب: زمان انتشار پست‌ها، نه ترتیب لحظه‌های لایو."
                + "\nپوشش منابع: " + "، ".join(f"@{h}: {status_label(s)}" for h, s in job["coverage"].items()),
                delivery_key=f"date-request:{job['id']}:overview",
            )
            app.state.save()
        remaining = [i for i in job["selected"] if i not in job["delivered"]]
        if remaining:
            identifier = remaining[0]
            update = _job_update(app, identifier)
            if update is None:
                job["status"] = "partial"
                app.state.save()
                return
            if not _configured_only(app.collector, [update]):
                job["selected"].remove(identifier)
                job["coverage"][update.author] = "disabled"
                app.state.save()
                return
            app.content_request_id = job["id"]
            try:
                await app.deliver_updates(_configured_only(app.collector, [update]), force=True)
            finally:
                app.content_request_id = ""
            draft = app.state.get_draft(short_id(f"request:{job['id']}:{identifier}"))
            if draft and draft.telegram_message_id and draft.mode != "manual_review":
                job["delivered"].append(identifier)
                job["retry_after"] = ""
            else:
                attempts = job["translation_attempts"].get(identifier, 0) + 1
                job["translation_attempts"][identifier] = attempts
                job["retry_after"] = (datetime.now(timezone.utc) + timedelta(minutes=min(30, 2 ** attempts))).isoformat()
                if attempts >= 3:
                    job["status"] = "translation_pending"
                    app.telegram.send_message("ترجمهٔ این بسته هنوز تأیید نشده؛ متن ناقص را به‌عنوان ترجمه نمی‌فرستم. "
                                              "برای تلاش دوباره همان درخواست تاریخ را بفرست.",
                                              delivery_key=f"date-request:{job['id']}:translation-pending")
            app.state.save()
            return
        day_still_open = end > datetime.now(timezone.utc)
        # Persist this distinction so re-asking tomorrow fetches posts that
        # appeared after the first snapshot instead of trusting earlier receipts.
        job["provisional_day"] = day_still_open
        source_coverage_complete = (
            bool(job["coverage"])
            and all(status == "complete" for status in job["coverage"].values())
        )
        job["status"] = "complete" if source_coverage_complete and not day_still_open else "partial"
        if day_still_open:
            summary = (
                "این روز هنوز تمام نشده و پست‌های بعدی ممکن است اضافه شوند؛ "
                "برای دریافت موارد جدید، همان درخواست روز را دوباره بفرست."
            )
        elif source_coverage_complete:
            summary = "همهٔ منابع فعال برای این روز بررسی شدند."
        else:
            summary = (
                "پوشش بعضی منابع ناقص است؛ این نتیجه را همهٔ پست‌های روز حساب نکن. "
                "برای تلاش دوباره همان تاریخ را بفرست."
            )
        app.telegram.send_message(
            f"{len(job['delivered'])} پستِ بستهٔ {job['day']} فرستاده شد. " + summary,
            delivery_key=f"date-request:{job['id']}:finished:{len(job['delivered'])}",
        )
        app.state.save()
        return
