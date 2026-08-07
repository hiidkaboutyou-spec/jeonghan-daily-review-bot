from __future__ import annotations

from .review_inbox import ReviewItem
from .telegram import inline_keyboard

_STATUS_LABELS = {"pending": "⏳ در انتظار", "ready": "✅ آماده", "rejected": "🗑 ردشده", "all": "همه"}


def inbox_list_keyboard(items: list[ReviewItem], status: str, page: int, pages: int):
    rows = []
    for item in items:
        label = f"{_STATUS_LABELS.get(item.status, item.status)} · @{item.source or 'unknown'} · {item.category}"
        rows.append([(label[:50], f"inbox:open:{item.draft_id}:{status}:{page}")])
    nav = []
    if page > 0:
        nav.append(("◀️ قبلی", f"inbox:page:{status}:{page - 1}"))
    nav.append((f"{page + 1}/{pages}", "noop:inbox-page"))
    if page + 1 < pages:
        nav.append(("بعدی ▶️", f"inbox:page:{status}:{page + 1}"))
    rows.append(nav)
    rows.append([("⏳ pending", "inbox:page:pending:0"), ("✅ ready", "inbox:page:ready:0"), ("🗑 rejected", "inbox:page:rejected:0")])
    rows.append([("همه", "inbox:page:all:0")])
    return inline_keyboard(rows)


def inbox_draft_keyboard(draft_id: str, status: str, page: int):
    return inline_keyboard([
        [("😂 بامزه‌تر", f"draft:fun:{draft_id}"), ("🪽 نرم‌تر", f"draft:soft:{draft_id}")],
        [("📰 دقیق‌تر", f"draft:precise:{draft_id}"), ("📋 متن تمیز", f"draft:copy:{draft_id}")],
        [("⏰ ۱ ساعت", f"remind:1h:{draft_id}"), ("🌙 امشب", f"remind:tonight:{draft_id}")],
        [("🌅 فردا صبح", f"remind:tomorrow:{draft_id}"), ("📌 نگه دار", f"remind:pin:{draft_id}")],
        [("🗑 رد", f"draft:reject:{draft_id}")],
        [("◀️ برگشت به صندوق", f"inbox:page:{status}:{page}")],
    ])
