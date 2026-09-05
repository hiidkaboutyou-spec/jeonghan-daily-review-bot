from __future__ import annotations

from .source_first_queue import SourceQueueSnapshot
from .telegram import inline_keyboard

_STATE_ICON = {
    "active": "▶️",
    "pending": "⏳",
    "deferred": "⏸",
    "complete": "✅",
}
_PROOF_ICON = {
    "complete": "✓",
    "partial": "~",
    "unproven": "?",
    "unknown": "·",
}


def source_first_text(snapshot: SourceQueueSnapshot) -> str:
    if not snapshot.session_id:
        return (
            "📥 صندوق بررسی منبع‌به‌منبع\n\n"
            "فعلاً پیش‌نویسِ در انتظار بررسی نداریم."
        )

    lines = ["📥 صندوق بررسی منبع‌به‌منبع", ""]
    if snapshot.active_source:
        lines.extend(
            [
                f"منبع {snapshot.active_position}/{snapshot.total_sources}: @{snapshot.active_source}",
                f"پست {snapshot.current_item_number}/{snapshot.current_item_total}",
                "این منبع تا تمام‌شدن پست‌هایش فعال می‌ماند؛ برای رفتن به منبع بعدی باید آن را صریحاً عقب بیندازی.",
                "",
            ]
        )
    elif snapshot.deferred_sources:
        lines.extend(["همهٔ موارد باقی‌مانده فعلاً عقب انداخته شده‌اند.", ""])
    else:
        lines.extend(["این دور بررسی تمام شده است.", ""])

    proof_complete = sum(item.completeness_status == "complete" for item in snapshot.sources)
    lines.append(f"اثبات پوشش Phase 4 (shadow): {proof_complete}/{snapshot.total_sources} منبع COMPLETE")
    lines.append("وضعیت منابع:")
    for item in snapshot.sources:
        state = _STATE_ICON.get(item.state, "•")
        proof = _PROOF_ICON.get(item.completeness_status, "·")
        progress = f"{item.done_items}/{item.total_items}" if item.total_items else "0/0"
        retry = " · retry failed" if item.last_retry_status == "failed" else ""
        lines.append(f"{state} @{item.source} · {progress} · proof {proof}{retry}")
    return "\n".join(lines)


def source_first_keyboard(snapshot: SourceQueueSnapshot):
    rows: list[list[tuple[str, str]]] = []
    if snapshot.active_source and snapshot.current_draft_id:
        rows.append([("📝 باز کردن پست فعلی", "sq:open")])
        rows.append(
            [
                ("⏸ عقب انداختن این منبع", f"sq:defer:{snapshot.active_source}"),
                ("🔁 retry همین منبع", f"sq:retry:{snapshot.active_source}"),
            ]
        )
    for source in snapshot.deferred_sources[:8]:
        rows.append([(f"▶️ ادامه @{source}", f"sq:resume:{source}")])
    rows.append([("🕘 نمای قدیمیِ زمانی", "sq:legacy:pending:0")])
    return inline_keyboard(rows)


def source_first_draft_keyboard(draft_id: str, source: str):
    return inline_keyboard(
        [
            [("😂 بامزه‌تر", f"draft:fun:{draft_id}"), ("🪽 نرم‌تر", f"draft:soft:{draft_id}")],
            [("📰 دقیق‌تر", f"draft:precise:{draft_id}"), ("📋 متن تمیز", f"draft:copy:{draft_id}")],
            [("🗑 رد", f"draft:reject:{draft_id}")],
            [("⏸ عقب انداختن منبع", f"sq:defer:{source}"), ("🔁 retry منبع", f"sq:retry:{source}")],
            [("◀️ برگشت به صف منبعی", "sq:home")],
        ]
    )
