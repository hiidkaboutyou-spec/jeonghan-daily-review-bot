from __future__ import annotations

import unittest
from datetime import timezone
from types import SimpleNamespace
from unittest.mock import patch

from app.models import EventGroup, Update
from app.style import ThemeEngine
from app.x_link_ingest import (
    SharedStatusRef,
    XLinkIngestError,
    _update_from_payload,
    collect_shared_statuses,
    extract_status_links,
)


def _payload(identifier: str = "2100000000000000001", handle: str = "source_one") -> dict:
    return {
        "id_str": identifier,
        "text": "JEONGHAN https://t.co/abc",
        "created_at": "Wed Sep 30 08:00:00 +0000 2026",
        "lang": "en",
        "user": {"screen_name": handle, "name": "Source One"},
        "entities": {
            "urls": [
                {
                    "url": "https://t.co/abc",
                    "expanded_url": "https://example.com/full",
                }
            ]
        },
        "mediaDetails": [
            {
                "type": "photo",
                "media_url_https": "https://pbs.twimg.com/media/photo.jpg",
                "original_info": {"width": 1200, "height": 900},
            },
            {
                "type": "video",
                "media_url_https": "https://pbs.twimg.com/media/video.jpg",
                "original_info": {"width": 1080, "height": 1920},
                "video_info": {
                    "variants": [
                        {
                            "content_type": "video/mp4",
                            "bitrate": 256000,
                            "url": "https://video.twimg.com/low.mp4",
                        },
                        {
                            "content_type": "video/mp4",
                            "bitrate": 832000,
                            "url": "https://video.twimg.com/high.mp4",
                        },
                    ]
                },
            },
        ],
        "quoted_tweet": {
            "id_str": "2099999999999999999",
            "text": "quoted post",
            "created_at": "Wed Sep 30 07:50:00 +0000 2026",
            "user": {"screen_name": "quoted", "name": "Quoted"},
        },
    }


class XLinkIngestTests(unittest.TestCase):
    def test_extracts_common_share_domains_and_dedupes(self):
        text = (
            "https://x.com/source_one/status/2100000000000000001?s=46\n"
            "https://twitter.com/source_one/status/2100000000000000001\n"
            "https://fixupx.com/source_two/status/2100000000000000002),\n"
            "https://fxtwitter.com/source_three/status/2100000000000000003"
        )
        refs = extract_status_links(text)
        self.assertEqual([item.status_id for item in refs], [
            "2100000000000000001",
            "2100000000000000002",
            "2100000000000000003",
        ])
        self.assertEqual([item.expected_handle for item in refs], [
            "source_one",
            "source_two",
            "source_three",
        ])

    def test_accepts_i_web_status_without_inventing_author(self):
        refs = extract_status_links("https://x.com/i/web/status/2100000000000000004")
        self.assertEqual(len(refs), 1)
        self.assertEqual(refs[0].expected_handle, "")

    def test_ignores_non_status_and_untrusted_hosts(self):
        text = (
            "https://x.com/source_one\n"
            "https://example.com/source_one/status/2100000000000000001"
        )
        self.assertEqual(extract_status_links(text), [])

    def test_payload_conversion_keeps_media_quote_and_expands_url(self):
        ref = SharedStatusRef(
            status_id="2100000000000000001",
            expected_handle="source_one",
            original_url="https://x.com/source_one/status/2100000000000000001",
        )
        update = _update_from_payload(_payload(), ref)
        self.assertEqual(update.id, ref.status_id)
        self.assertEqual(update.author, "source_one")
        self.assertIn("https://example.com/full", update.text)
        self.assertEqual(update.quoted_id, "2099999999999999999")
        self.assertEqual(update.quoted_author, "quoted")
        self.assertEqual(len(update.media), 2)
        self.assertEqual(update.media[1].url, "https://video.twimg.com/high.mp4")
        self.assertEqual(update.raw_query, "manual_link:x_syndication")

    def test_payload_rejects_author_mismatch(self):
        ref = SharedStatusRef(
            status_id="2100000000000000001",
            expected_handle="different",
            original_url="https://x.com/different/status/2100000000000000001",
        )
        with self.assertRaises(XLinkIngestError):
            _update_from_payload(_payload(), ref)

    @patch("app.x_link_ingest.fetch_shared_status")
    def test_collection_isolates_one_bad_link(self, fetch):
        first = Update(
            id="2100000000000000001",
            url="https://x.com/a/status/2100000000000000001",
            author="a",
            author_name="A",
            text="one",
            created_at="2026-09-30T08:00:00Z",
        )

        def side_effect(ref):
            if ref.status_id.endswith("2"):
                raise XLinkIngestError("failed")
            return first

        fetch.side_effect = side_effect
        result = collect_shared_statuses(
            "https://x.com/a/status/2100000000000000001 "
            "https://x.com/b/status/2100000000000000002"
        )
        self.assertEqual([item.id for item in result.updates], [first.id])
        self.assertEqual(result.failed_ids, ["2100000000000000002"])
        self.assertEqual(result.requested_count, 2)

    def test_manual_caption_uses_neutral_source_label(self):
        engine = ThemeEngine(
            {"themes": {"general": {"variants": [{"prefix": "،، 🪽", "label": "آپدیت"}]}}},
            timezone.utc,
        )
        update = Update(
            id="manual-test",
            url="",
            author="manual_input",
            author_name="Manual input",
            text="source",
            created_at="2026-09-30T08:00:00Z",
            raw_query="manual_text:telegram",
        )
        group = EventGroup(key="manual", category="general", title="manual", updates=[update])
        caption = engine.caption(group, update, "ترجمه", 1, 1)
        self.assertIn("⌕ ورودی دستی", caption)
        self.assertNotIn("@manual_input", caption)


if __name__ == "__main__":
    unittest.main()
