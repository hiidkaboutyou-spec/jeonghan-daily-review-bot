from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
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


_RELATION_RANK = {
    "confident_same_event": 2,
    "probable_same_event": 1,
}


def _local_time_label(group: EventGroup, timezone_info: Any) -> str:
    start = group.started_at.astimezone(timezone_info)
    end = group.ended_at.astimezone(timezone_info)
    prefix = start.strftime("%m/%d ")
    if start == end:
        return prefix + start.strftime("%H:%M")
    if start.date() == end.date():
        return prefix + f"{start:%H:%M}–{end:%H:%M}"
    return f"{start:%m/%d %H:%M}–{end:%m/%d %H:%M}"


def _source_label(update: Update) -> str:
    author = str(update.author or "").strip().lstrip("@")
    if str(update.raw_query or "").startswith("manual_text:") or author == "manual_input":
        return "ورودی دستی"
    return f"@{author}" if author else "منبع نامشخص"


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
                title=group.title,
                time_label=_local_time_label(group, timezone_info),
                item_count=len(group.updates),
                sources=_group_sources(group),
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
    for item in items:
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
        source_text = f"\n   {sources}" if sources else ""
        lines.append(
            f"{item.index}) {item.time_label} · {item.title}{count}{relation}{source_text}"
        )

    return EditorialGuide(
        groups=tuple(ordered),
        items=tuple(items),
        overview="\n".join(lines).strip(),
    )
