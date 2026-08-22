from app.source_modes import (
    filter_posts_for_source_mode,
    matches_jeonghan_filter,
    should_collect_post,
)


def test_keyword_matcher_passes_terms():
    assert matches_jeonghan_filter("JEONGHAN airport photos")
    assert matches_jeonghan_filter("윤정한 오늘")
    assert matches_jeonghan_filter("Yoon Jeonghan")
    assert matches_jeonghan_filter("Hannie update")


def test_keyword_matcher_rejects_unrelated():
    assert not matches_jeonghan_filter("SEVENTEEN general update")


def test_emoji_matcher():
    assert matches_jeonghan_filter("🪽")
    assert matches_jeonghan_filter("😇")
    assert matches_jeonghan_filter("👼🏻")
    assert matches_jeonghan_filter("👼")
    assert not matches_jeonghan_filter("🎸")


def test_source_modes():
    assert should_collect_post({"mode": "full_feed"}, "random update")
    assert should_collect_post({"mode": "keyword_filter"}, "😇")
    assert not should_collect_post({"mode": "keyword_filter"}, "random member update")


def test_filter_preserves_full_feed_and_filters_keyword_feed():
    posts = [{"text": "random"}, {"text": "Jeonghan photo"}]
    assert len(filter_posts_for_source_mode({"mode": "full_feed"}, posts)) == 2
    assert len(filter_posts_for_source_mode({"mode": "keyword_filter"}, posts)) == 1
