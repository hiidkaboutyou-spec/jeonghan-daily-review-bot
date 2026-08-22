from app.source_modes import (
    SourceConfig,
    SourceMode,
    SourceModeGate,
    load_source_modes_from_config,
)


def test_source_mode_config_loading():
    sources, gate = load_source_modes_from_config(
        {
            "sources": [
                {"handle": "trusted", "mode": "full_feed"},
                {"handle": "fan", "mode": "keyword_filter"},
            ]
        }
    )

    assert sources[0].mode == SourceMode.FULL_FEED
    assert sources[1].mode == SourceMode.KEYWORD_FILTER
    assert gate.should_accept_post("trusted", "anything") is True


def test_keyword_or_emoji_matching():
    source = SourceConfig(
        handle="fan",
        label="fan",
        enabled=True,
        priority=1,
        include_replies=True,
        mode=SourceMode.KEYWORD_FILTER,
    )
    gate = SourceModeGate([source])

    assert gate.should_accept_post("fan", "Jeonghan update")
    assert gate.should_accept_post("fan", "윤정한")
    assert gate.should_accept_post("fan", "🪽")
    assert gate.should_accept_post("fan", "😇")
    assert not gate.should_accept_post("fan", "SEVENTEEN general update")
    assert not gate.should_accept_post("fan", "unrelated content")


def test_full_feed_preserves_posts_without_filtering():
    source = SourceConfig(
        handle="trusted",
        label="trusted",
        enabled=True,
        priority=1,
        include_replies=True,
        mode=SourceMode.FULL_FEED,
    )
    gate = SourceModeGate([source])

    assert gate.should_accept_post("trusted", "unrelated content")
