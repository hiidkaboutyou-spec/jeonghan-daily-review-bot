from __future__ import annotations

import json
import unittest
from datetime import datetime, timezone

from app.x_syndication import SyndicationError, parse_syndication_html


def _document(*tweets: dict) -> str:
    entries = [
        {"type": "tweet", "content": {"tweet": tweet}}
        for tweet in tweets
    ]
    payload = {"props": {"pageProps": {"timeline": {"entries": entries}}}}
    return '<script id="__NEXT_DATA__" type="application/json">' + json.dumps(payload) + "</script>"


def _tweet(identifier: str, *, author: str = "source", created: str = "Tue Aug 25 10:00:00 +0000 2026") -> dict:
    return {
        "id_str": identifier,
        "conversation_id_str": identifier,
        "created_at": created,
        "full_text": "Jeonghan update https://t.co/link",
        "lang": "en",
        "user": {"screen_name": author, "name": "Configured Source"},
        "entities": {
            "urls": [{"url": "https://t.co/link", "expanded_url": "https://example.com/update"}]
        },
        "extended_entities": {
            "media": [{
                "type": "photo",
                "media_url_https": "https://pbs.twimg.com/media/test.jpg",
                "original_info": {"width": 1200, "height": 800},
            }]
        },
    }


class SyndicationParserTests(unittest.TestCase):
    def setUp(self):
        self.start = datetime(2026, 8, 25, 9, 0, tzinfo=timezone.utc)
        self.end = datetime(2026, 8, 25, 11, 0, tzinfo=timezone.utc)

    def test_parses_authorized_window_and_media(self):
        result = parse_syndication_html(
            _document(_tweet("123")), handle="source", start=self.start, end=self.end
        )
        self.assertEqual(result.raw_seen, 1)
        self.assertEqual([item.id for item in result.updates], ["123"])
        update = result.updates[0]
        self.assertEqual(update.author, "source")
        self.assertIn("https://example.com/update", update.text)
        self.assertEqual(update.media[0].kind, "photo")
        self.assertEqual(update.media[0].width, 1200)
        self.assertEqual(update.raw_query, "syndication:@source")

    def test_rejects_external_author_and_out_of_window_items(self):
        result = parse_syndication_html(
            _document(
                _tweet("external", author="attacker"),
                _tweet("old", created="Tue Aug 25 08:59:59 +0000 2026"),
            ),
            handle="source",
            start=self.start,
            end=self.end,
        )
        self.assertEqual(result.raw_seen, 2)
        self.assertEqual(result.updates, [])

    def test_excludes_retweets(self):
        tweet = _tweet("retweet")
        tweet["retweeted_status"] = {"id_str": "original"}
        result = parse_syndication_html(
            _document(tweet), handle="source", start=self.start, end=self.end
        )
        self.assertEqual(result.updates, [])

    def test_respects_source_reply_mode(self):
        tweet = _tweet("reply")
        tweet["in_reply_to_status_id_str"] = "parent"
        result = parse_syndication_html(
            _document(tweet),
            handle="source",
            start=self.start,
            end=self.end,
            include_replies=False,
        )
        self.assertEqual(result.updates, [])

    def test_malformed_or_missing_structured_data_fails_closed(self):
        for document in ("", '<script id="__NEXT_DATA__">not-json</script>'):
            with self.subTest(document=document):
                with self.assertRaises(SyndicationError):
                    parse_syndication_html(
                        document, handle="source", start=self.start, end=self.end
                    )


if __name__ == "__main__":
    unittest.main()
