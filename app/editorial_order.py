from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Any

from .event_fusion import build_fingerprint, match_fingerprints
from .models import EventGroup, Update


@dataclass(frozen=True, slots=True)
class GroupGuide:
    index: int
    title: str
    time_label: str
    item_count: int
    sources: tuple[str, ...]
    preview: str = ""
    related_to: int = 0
    relation_decision: str = ""


@dataclass(frozen=True, slots=True)
class EditorialGuide:
    groups: tuple[EventGroup, ...]
    items: tuple[GroupGuide, ...]
    overview: str

    def header_for(self, index: int) -> str:
        item = self.items[index]
        lines = [
            f"🧭 گروه {item.index}/{len(self.items)} · {item.time_label}",
            item.title,
        ]
        if item.related_to:
            if item.relation_decision == "confident_same_event":
                lines.append(f"↳ مربوط به همان رویداد گروه {item.related_to}")
            else:
                lines.append(f"↳ احتمالاً ادامه/پوشش همان رویداد گروه {item.related_to}")
        elif len(self.items) > 1:
            lines.append("↳ موضوع جدا")
        if item.item_count > 1:
            lines.append(
                f"{item.item_count} پست مرتبط دارد؛ همین‌ها را پشت‌سرهم از بخش 1 تا {item.item_count} منتشر کن."
            )
        else:
            lines.append("۱ پست")
        return "\n".join(lines)


MAX_OVERVIEW_GROUPS = 16


_RELATION_RANK = {
    "confident_same_event": 2,
    "probable_same_event": 1,
}


def _local_time_label(group: EventGroup, timezone_info: Any) -> str:
    start = group.started_at.astimezone(timezone_info)
    end = group.ended_at.astimezone(timezone_info)
    prefix = start.strftime("%y%m%d · ")
    if start == end:
        return prefix + start.strftime("%H:%M")
    if start.date() == end.date():
        return prefix + f"{start:%H:%M}–{end:%H:%M}"
    return f"{start:%y%m%d · %H:%M}–{end:%y%m%d · %H:%M}"


def _source_label(update: Update) -> str:
    author = str(update.author or "").strip().lstrip("@")
    if str(update.raw_query or "").startswith("manual_text:") or author == "manual_input":
        return "ورودی دستی"
    return f"@{author}" if author else "منبع نامشخص"


_EVENT_LABELS = {
    "live": "لایو جونگهان",
    "interview": "مصاحبهٔ جونگهان",
    "variety": "برنامهٔ ورایتی با جونگهان",
    "reality": "ریالیتی/برنامه با جونگهان",
    "going_seventeen": "Going Seventeen",
    "fansign_or_video_call": "فنساین/ویدیوکال جونگهان",
    "concert": "کنسرت/اجرای جونگهان",
    "award_show": "مراسم و جوایز",
    "brand_event": "آپدیت برند با جونگهان",
    "airport_or_public_appearance": "فرودگاه/حضور عمومی جونگهان",
    "official_content": "محتوای رسمی جونگهان",
    "social_update": "آپدیت شبکهٔ اجتماعی جونگهان",
}


def _display_title(group: EventGroup, fingerprints: dict[str, Any]) -> str:
    title = str(group.title or "").strip()
    generic = title in {"", "آپدیت جونگهان"}
    if not generic:
        return title
    for update in group.updates:
        fp = fingerprints.get(update.id)
        label = _EVENT_LABELS.get(str(getattr(fp, "event_type", "") or ""))
        if label:
            return label
    return title or "آپدیت جونگهان"


def _preview(group: EventGroup, limit: int = 72) -> str:
    for update in group.updates:
        value = re.sub(r"https?://\S+", " ", str(update.text or ""))
        value = re.sub(r"\s+", " ", value).strip()
        if not value:
            continue
        if len(value) > limit:
            value = value[: limit - 1].rstrip() + "…"
        return value
    return ""


def _group_sources(group: EventGroup) -> tuple[str, ...]:
    values: list[str] = []
    seen: set[str] = set()
    for update in group.updates:
        label = _source_label(update)
        if label in seen:
            continue
        seen.add(label)
        values.append(label)
    return tuple(values)


def _best_relation(
    group: EventGroup,
    previous: list[EventGroup],
    fingerprints: dict[str, Any],
) -> tuple[int, str]:
    best: tuple[int, float, int, str] | None = None
    for previous_index, prior in enumerate(previous, start=1):
        for current_update in group.updates:
            left = fingerprints[current_update.id]
            for prior_update in prior.updates:
                right = fingerprints[prior_update.id]
                match = match_fingerprints(left, right)
                rank = _RELATION_RANK.get(match.decision, 0)
                if not rank:
                    continue
                candidate = (rank, float(match.confidence), previous_index, match.decision)
                if best is None or candidate[:2] > best[:2]:
                    best = candidate
    if best is None:
        return 0, ""
    return best[2], best[3]


def build_editorial_guide(
    groups: list[EventGroup],
    timezone_info: Any,
) -> EditorialGuide:
    ordered = sorted(groups, key=lambda group: (group.started_at, group.key))
    fingerprints = {
        update.id: build_fingerprint(update)
        for group in ordered
        for update in group.updates
    }

    items: list[GroupGuide] = []
    previous: list[EventGroup] = []
    for index, group in enumerate(ordered, start=1):
        related_to, decision = _best_relation(group, previous, fingerprints)
        items.append(
            GroupGuide(
                index=index,
                title=_display_title(group, fingerprints),
                time_label=_local_time_label(group, timezone_info),
                item_count=len(group.updates),
                sources=_group_sources(group),
                preview=_preview(group),
                related_to=related_to,
                relation_decision=decision,
            )
        )
        previous.append(group)

    total_updates = sum(item.item_count for item in items)
    lines = [
        "🧭 نقشهٔ انتشار",
        f"{total_updates} آپدیت · {len(items)} گروه",
        "از بالا به پایین برو؛ ترتیب بر اساس زمان اصلی پست‌هاست و موارد مرتبط کنار هم توضیح داده شده‌اند.",
        "",
    ]
    visible_items = items[:MAX_OVERVIEW_GROUPS]
    for item in visible_items:
        relation = ""
        if item.related_to:
            if item.relation_decision == "confident_same_event":
                relation = f" · همان رویداد گروه {item.related_to}"
            else:
                relation = f" · احتمالاً مرتبط با گروه {item.related_to}"
        elif len(items) > 1:
            relation = " · موضوع جدا"

        count = f" · {item.item_count} پست" if item.item_count > 1 else ""
        sources = "، ".join(item.sources[:3])
        if len(item.sources) > 3:
            sources += f" +{len(item.sources) - 3}"
        source_text = f"\n   منبع: {sources}" if sources else ""
        preview_text = f"\n   موضوع: {item.preview}" if item.preview else ""
        lines.append(
            f"{item.index}) {item.time_label} · {item.title}{count}{relation}"
            f"{source_text}{preview_text}"
        )

    hidden = len(items) - len(visible_items)
    if hidden > 0:
        lines.extend(
            [
                "",
                f"… {hidden} گروه دیگر بعد از این‌ها در همان ترتیب ارسال می‌شوند.",
                "برای دیدن ترتیب موارد باقی‌مانده، «📥 پیش‌نویس‌ها» را باز کن.",
            ]
        )

    overview = "\n".join(lines).strip()
    # Telegram text messages are limited to 4096 characters. Keep headroom for
    # platform/runtime formatting without ever turning navigation into a delivery blocker.
    if len(overview) > 3900:
        overview = overview[:3820].rstrip() + "\n\n… ادامه در «📥 پیش‌نویس‌ها»"

    return EditorialGuide(
        groups=tuple(ordered),
        items=tuple(items),
        overview=overview,
    )
