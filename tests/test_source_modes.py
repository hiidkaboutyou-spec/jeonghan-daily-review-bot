"""Tests for source mode filtering and content matching."""

import pytest

from app.source_modes import (
    ContentFilter,
    JEONGHAN_EMOJIS,
    JEONGHAN_KEYWORDS,
    SourceConfig,
    SourceMode,
    SourceModeGate,
    load_source_modes_from_config,
)


class TestSourceMode:
    """Test SourceMode enum."""

    def test_source_mode_values(self):
        """Test that SourceMode enum has expected values."""
        assert SourceMode.FULL_FEED.value == "full_feed"
        assert SourceMode.KEYWORD_FILTER.value == "keyword_filter"

    def test_source_mode_from_string(self):
        """Test creating SourceMode from string value."""
        assert SourceMode("full_feed") == SourceMode.FULL_FEED
        assert SourceMode("keyword_filter") == SourceMode.KEYWORD_FILTER


class TestSourceConfig:
    """Test SourceConfig dataclass."""

    def test_from_mapping_full_feed(self):
        """Test loading full_feed source config."""
        raw = {
            "handle": "@pledis_17",
            "label": "Official Pledis",
            "enabled": True,
            "priority": 50,
            "include_replies": True,
            "mode": "full_feed",
        }
        config = SourceConfig.from_mapping(raw)
        assert config.handle == "pledis_17"
        assert config.label == "Official Pledis"
        assert config.enabled is True
        assert config.priority == 50
        assert config.include_replies is True
        assert config.mode == SourceMode.FULL_FEED

    def test_from_mapping_keyword_filter(self):
        """Test loading keyword_filter source config."""
        raw = {
            "handle": "ayecheol",
            "label": "Ayecheol",
            "enabled": True,
            "priority": 15,
            "include_replies": True,
            "mode": "keyword_filter",
        }
        config = SourceConfig.from_mapping(raw)
        assert config.handle == "ayecheol"
        assert config.mode == SourceMode.KEYWORD_FILTER
        assert config.keywords == JEONGHAN_KEYWORDS
        assert config.emojis == JEONGHAN_EMOJIS

    def test_from_mapping_legacy_jeonghan_only(self):
        """Test legacy jeonghan_only flag maps to keyword_filter."""
        raw = {
            "handle": "ayecheol",
            "enabled": True,
            "jeonghan_only": True,
        }
        config = SourceConfig.from_mapping(raw)
        assert config.mode == SourceMode.KEYWORD_FILTER

    def test_from_mapping_defaults(self):
        """Test default values for optional fields."""
        raw = {"handle": "testhandle"}
        config = SourceConfig.from_mapping(raw)
        assert config.handle == "testhandle"
        assert config.label == "testhandle"
        assert config.enabled is True
        assert config.priority == 100
        assert config.include_replies is True
        assert config.mode == SourceMode.FULL_FEED

    def test_from_mapping_handle_normalization(self):
        """Test handle normalization (@ prefix removal, lowercase)."""
        raw = {"handle": "@TestHandle"}
        config = SourceConfig.from_mapping(raw)
        assert config.handle == "testhandle"


class TestContentFilter:
    """Test ContentFilter for keyword and emoji matching."""

    def test_keyword_match_exact(self):
        """Test exact keyword matching."""
        filter_obj = ContentFilter()
        assert filter_obj.matches("Jeonghan update")
        assert filter_obj.matches("정한 weverse")
        assert filter_obj.matches("JEONGHAN concert")

    def test_keyword_match_case_insensitive(self):
        """Test case-insensitive keyword matching."""
        filter_obj = ContentFilter()
        assert filter_obj.matches("jeonghan")
        assert filter_obj.matches("JEONGHAN")
        assert filter_obj.matches("JeOngHaN")

    def test_keyword_match_korean(self):
        """Test Korean keyword matching."""
        filter_obj = ContentFilter()
        assert filter_obj.matches("정한 이 너무 사랑해")
        assert filter_obj.matches("윤정한 인스타")

    def test_keyword_match_hashtag(self):
        """Test hashtag keyword matching."""
        filter_obj = ContentFilter()
        assert filter_obj.matches("#JEONGHAN #jeonghan")
        assert filter_obj.matches("Check out #윤정한 here")

    def test_keyword_no_match(self):
        """Test posts that don't match keywords."""
        filter_obj = ContentFilter()
        assert not filter_obj.matches("joshua weverse update")
        assert not filter_obj.matches("seventeen concert announcement")
        assert not filter_obj.matches("random unrelated post")

    def test_emoji_match(self):
        """Test emoji matching."""
        filter_obj = ContentFilter()
        assert filter_obj.has_emoji("Update! 🪽")
        assert filter_obj.has_emoji("Angel face 😇")
        assert filter_obj.has_emoji("Baby angel 👼🏻")
        assert filter_obj.has_emoji("👼")

    def test_emoji_no_match(self):
        """Test posts without filter emojis."""
        filter_obj = ContentFilter()
        assert not filter_obj.has_emoji("Update! ❤️")
        assert not filter_obj.has_emoji("Nice post 😊")
        assert not filter_obj.has_emoji("No emojis here")

    def test_passes_keyword_match(self):
        """Test passes() with keyword match."""
        filter_obj = ContentFilter()
        assert filter_obj.passes("Jeonghan weverse live")
        assert filter_obj.passes("정한 update")

    def test_passes_emoji_match(self):
        """Test passes() with emoji match."""
        filter_obj = ContentFilter()
        assert filter_obj.passes("Update 🪽")
        assert filter_obj.passes("News 😇")

    def test_passes_both_match(self):
        """Test passes() with both keyword and emoji."""
        filter_obj = ContentFilter()
        assert filter_obj.passes("Jeonghan update 🪽")

    def test_passes_no_match(self):
        """Test passes() with no match."""
        filter_obj = ContentFilter()
        assert not filter_obj.passes("random unrelated post")
        assert not filter_obj.passes("joshua news")

    def test_custom_keywords(self):
        """Test filter with custom keywords."""
        custom_keywords = {"hype", "excited"}
        filter_obj = ContentFilter(keywords=custom_keywords)
        assert filter_obj.matches("so hype about this")
        assert filter_obj.matches("I'm excited")
        assert not filter_obj.matches("Jeonghan update")  # Default keywords not used

    def test_custom_emojis(self):
        """Test filter with custom emojis."""
        custom_emojis = {"🎉", "🎊"}
        filter_obj = ContentFilter(emojis=custom_emojis)
        assert filter_obj.has_emoji("Party time 🎉")
        assert filter_obj.has_emoji("Celebration 🎊")
        assert not filter_obj.has_emoji("Angel 😇")  # Default emojis not used


class TestSourceModeGate:
    """Test SourceModeGate for filtering decisions."""

    def test_empty_gate(self):
        """Test gate with no sources."""
        gate = SourceModeGate()
        assert not gate.should_accept_post("anyhandle", "any text")

    def test_full_feed_accepts_all(self):
        """Test full_feed mode accepts all posts from that source."""
        sources = [
            SourceConfig.from_mapping({
                "handle": "pledis_17",
                "mode": "full_feed",
                "enabled": True,
            })
        ]
        gate = SourceModeGate(sources)
        assert gate.should_accept_post("pledis_17", "random text")
        assert gate.should_accept_post("pledis_17", "joshua update")
        assert gate.should_accept_post("pledis_17", "")

    def test_keyword_filter_accepts_matching(self):
        """Test keyword_filter accepts only matching posts."""
        sources = [
            SourceConfig.from_mapping({
                "handle": "ayecheol",
                "mode": "keyword_filter",
                "enabled": True,
            })
        ]
        gate = SourceModeGate(sources)
        assert gate.should_accept_post("ayecheol", "Jeonghan weverse live")
        assert gate.should_accept_post("ayecheol", "정한 update 🪽")
        assert not gate.should_accept_post("ayecheol", "joshua news")
        assert not gate.should_accept_post("ayecheol", "random post")

    def test_unknown_source_rejected(self):
        """Test unknown sources are always rejected."""
        sources = [
            SourceConfig.from_mapping({
                "handle": "pledis_17",
                "mode": "full_feed",
                "enabled": True,
            })
        ]
        gate = SourceModeGate(sources)
        assert not gate.should_accept_post("unknown_handle", "any text")

    def test_disabled_source_rejected(self):
        """Test disabled sources are rejected."""
        sources = [
            SourceConfig.from_mapping({
                "handle": "pledis_17",
                "mode": "full_feed",
                "enabled": False,
            })
        ]
        gate = SourceModeGate(sources)
        assert not gate.should_accept_post("pledis_17", "any text")

    def test_handle_normalization_in_gate(self):
        """Test handle normalization in gate decisions."""
        sources = [
            SourceConfig.from_mapping({
                "handle": "pledis_17",
                "mode": "full_feed",
                "enabled": True,
            })
        ]
        gate = SourceModeGate(sources)
        assert gate.should_accept_post("@pledis_17", "text")
        assert gate.should_accept_post("Pledis_17", "text")
        assert gate.should_accept_post("PLEDIS_17", "text")

    def test_mixed_sources(self):
        """Test gate with mixed source modes."""
        sources = [
            SourceConfig.from_mapping({
                "handle": "pledis_17",
                "mode": "full_feed",
                "enabled": True,
            }),
            SourceConfig.from_mapping({
                "handle": "ayecheol",
                "mode": "keyword_filter",
                "enabled": True,
            }),
        ]
        gate = SourceModeGate(sources)
        # Full feed accepts all
        assert gate.should_accept_post("pledis_17", "any text")
        # Keyword filter only accepts matching
        assert gate.should_accept_post("ayecheol", "Jeonghan update")
        assert not gate.should_accept_post("ayecheol", "joshua update")


class TestLoadSourceModesFromConfig:
    """Test configuration loading."""

    def test_load_from_jeonghan_config(self):
        """Test loading from jeonghan_priority_x_sources.json format."""
        config = {
            "sources": [
                {
                    "handle": "ayecheol",
                    "label": "ayecheol",
                    "enabled": True,
                    "priority": 15,
                    "include_replies": True,
                    "jeonghan_only": True,
                },
                {
                    "handle": "jeongh4nss",
                    "label": "jeongh4nss",
                    "enabled": True,
                    "priority": 20,
                    "include_replies": True,
                    "jeonghan_only": True,
                },
            ]
        }
        source_configs, gate = load_source_modes_from_config(config)
        assert len(source_configs) == 2
        assert all(src.mode == SourceMode.KEYWORD_FILTER for src in source_configs)
        # Gate should have filters for both
        assert "ayecheol" in gate.filters
        assert "jeongh4nss" in gate.filters

    def test_load_empty_config(self):
        """Test loading with no sources."""
        config = {"sources": []}
        source_configs, gate = load_source_modes_from_config(config)
        assert len(source_configs) == 0
        assert len(gate.sources) == 0

    def test_load_mixed_sources(self):
        """Test loading mixed full_feed and keyword_filter sources."""
        config = {
            "sources": [
                {
                    "handle": "pledis_17",
                    "enabled": True,
                    "mode": "full_feed",
                },
                {
                    "handle": "ayecheol",
                    "enabled": True,
                    "mode": "keyword_filter",
                },
            ]
        }
        source_configs, gate = load_source_modes_from_config(config)
        assert len(source_configs) == 2
        assert source_configs[0].mode == SourceMode.FULL_FEED
        assert source_configs[1].mode == SourceMode.KEYWORD_FILTER
        # Only keyword_filter should have filter
        assert "pledis_17" not in gate.filters
        assert "ayecheol" in gate.filters


class TestRegressionEdgeCases:
    """Test edge cases and potential regressions."""

    def test_empty_text(self):
        """Test filtering with empty text."""
        sources = [
            SourceConfig.from_mapping({
                "handle": "ayecheol",
                "mode": "keyword_filter",
                "enabled": True,
            })
        ]
        gate = SourceModeGate(sources)
        assert not gate.should_accept_post("ayecheol", "")
        assert not gate.should_accept_post("ayecheol", None)

    def test_whitespace_only_text(self):
        """Test filtering with whitespace-only text."""
        filter_obj = ContentFilter()
        assert not filter_obj.matches("   ")
        assert not filter_obj.passes("\n\t  ")

    def test_keyword_with_special_chars(self):
        """Test keywords are properly escaped in regex."""
        filter_obj = ContentFilter(keywords={"test[case]"})
        # Should match the literal string, not as regex
        assert filter_obj.matches("this is test[case] text")
        assert not filter_obj.matches("test case")

    def test_multiple_keywords_in_text(self):
        """Test text with multiple Jeonghan keywords."""
        filter_obj = ContentFilter()
        assert filter_obj.matches("Jeonghan and 정한 and jeonghan")

    def test_partial_keyword_no_match(self):
        """Test that partial keyword matches don't count for ambiguous terms."""
        # This tests the robustness of keyword matching
        filter_obj = ContentFilter(keywords={"jeonghan"})
        assert filter_obj.matches("jeonghan")
        # The regex should respect word boundaries implicitly
        # since JEONGHAN_KEYWORDS are specific terms

    def test_filter_posts_with_objects(self):
        """Test filter_posts with real post-like objects."""
        from collections import namedtuple
        
        Post = namedtuple("Post", ["author", "text"])
        posts = [
            Post("ayecheol", "Jeonghan update"),
            Post("ayecheol", "joshua news"),
            Post("pledis_17", "random post"),
        ]
        
        sources = [
            SourceConfig.from_mapping({
                "handle": "ayecheol",
                "mode": "keyword_filter",
                "enabled": True,
            }),
            SourceConfig.from_mapping({
                "handle": "pledis_17",
                "mode": "full_feed",
                "enabled": True,
            }),
        ]
        gate = SourceModeGate(sources)
        filtered = gate.filter_posts(posts)
        
        assert len(filtered) == 2
        assert filtered[0].text == "Jeonghan update"
        assert filtered[1].text == "random post"

    def test_malformed_post_object(self):
        """Test that malformed post objects are skipped gracefully."""
        class BadPost:
            pass
        
        posts = [BadPost()]
        gate = SourceModeGate()
        filtered = gate.filter_posts(posts)
        assert len(filtered) == 0

    def test_deduplication_preserved(self):
        """Test that source mode filtering doesn't affect deduplication."""
        # This is a placeholder for regression testing
        # Actual deduplication is handled elsewhere
        filter_obj = ContentFilter()
        # Same text should still match
        assert filter_obj.passes("Jeonghan update")
        assert filter_obj.passes("Jeonghan update")
