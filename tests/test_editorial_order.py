from __future__ import annotations

from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from app.editorial_order import build_editorial_guide
from app.models import EventGroup, Update


def _update(
    update_id: str,
    minute: int,
    text: str,
    *,
    author: str = "source_a",
    raw_query: str = "",
) -> Update:
    return Update(
        id=update_id,
        url=f"https://x.com/{author}/status/{update_id}",
        author=author,
        author_name=author,
        text=text,
        created_at=datetime(2026, 9, 30, 8, minute, tzinfo=timezone.utc),
        raw_query=raw_query,
    )


def test_editorial_guide_is_chronological_even_when_groups_arrive_unsorted():
    early = EventGroup(
        key="early",
        category="general",
        title="آپدیت اول",
        updates=[_update("100", 5, "first item")],
    )
    late = EventGroup(
        key="late",
        category="general",
        title="آپدیت دوم",
        updates=[_update("200", 40, "second item")],
    )

    guide = build_editorial_guide([late, early], ZoneInfo("Asia/Tehran"))

    assert [group.key for group in guide.groups] == ["early", "late"]
    assert "1) 260930 · 11:35 · آپدیت اول" in guide.overview
    assert "2) 260930 · 12:10 · آپدیت دوم" in guide.overview


def test_editorial_guide_marks_probable_cross_source_same_event_without_merging_delivery_groups():
    first = EventGroup(
        key="one",
        category="general",
        title="آپدیت کنسرت",
        updates=[
            _update(
                "101",
                10,
                "JEONGHAN concert #BELLUNA opening stage special unit performance",
                author="source_a",
            )
        ],
    )
    second = EventGroup(
        key="two",
        category="general",
        title="آپدیت فن‌اکانت",
        updates=[
            _update(
                "102",
                15,
                "More JEONGHAN concert #BELLUNA opening stage special unit performance photos",
                author="source_b",
            )
        ],
    )

    guide = build_editorial_guide([first, second], ZoneInfo("Asia/Tehran"))

    assert len(guide.groups) == 2
    assert guide.items[1].related_to == 1
    assert guide.items[1].relation_decision in {
        "confident_same_event",
        "probable_same_event",
    }
    assert "گروه 1" in guide.header_for(1)


def test_editorial_guide_keeps_unrelated_updates_explicitly_separate():
    live = EventGroup(
        key="live",
        category="live",
        title="لایو جونگهان",
        updates=[_update("301", 0, "Weverse live talking about dinner")],
    )
    brand = EventGroup(
        key="brand",
        category="brand",
        title="آپدیت برند با جونگهان",
        updates=[_update("302", 55, "New sponsored fashion campaign photos", author="brand_account")],
    )

    guide = build_editorial_guide([live, brand], ZoneInfo("Asia/Tehran"))

    assert guide.items[1].related_to == 0
    assert "موضوع جدا" in guide.overview
    assert "↳ موضوع جدا" in guide.header_for(1)


def test_editorial_group_header_explains_multi_post_order_and_manual_source():
    group = EventGroup(
        key="thread",
        category="live",
        title="لایو جونگهان",
        updates=[
            _update("401", 20, "part 1 live"),
            _update("402", 21, "part 2 live", author="manual_input", raw_query="manual_text:telegram"),
        ],
    )

    guide = build_editorial_guide([group], ZoneInfo("Asia/Tehran"))

    assert guide.items[0].item_count == 2
    assert guide.items[0].sources == ("@source_a", "ورودی دستی")
    assert "2 پست مرتبط" in guide.header_for(0)
    assert "بخش 1 تا 2" in guide.header_for(0)


def test_editorial_guide_replaces_generic_title_with_event_type_and_shows_preview():
    group = EventGroup(
        key="concert",
        category="general",
        title="آپدیت جونگهان",
        updates=[
            _update(
                "501",
                30,
                "JEONGHAN concert at Belluna Dome opening stage with Joshua",
                author="news_source",
            )
        ],
    )

    guide = build_editorial_guide([group], ZoneInfo("Asia/Tehran"))

    assert guide.items[0].title == "کنسرت/اجرای جونگهان"
    assert "منبع: @news_source" in guide.overview
    assert "موضوع: JEONGHAN concert at Belluna Dome opening stage with Joshua" in guide.overview
