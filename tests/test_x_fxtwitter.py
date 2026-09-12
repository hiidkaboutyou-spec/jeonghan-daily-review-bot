from __future__ import annotations

import unittest
from datetime import datetime, timezone
from unittest.mock import Mock, patch

from app.x_fxtwitter import FxTwitterError, collect_fxtwitter_timeline


def _status(
    identifier: str,
    *,
    author: str = "source",
    created_timestamp: float = 1786608000,
    reply_to: str = "",
    reposted: bool = False,
):
    return {
        "type": "status",
        "id": identifier,
        "url": f"https://x.com/{author}/status/{identifier}",
        "text": "Jeonghan update",
        "created_at": datetime.fromtimestamp(
            created_timestamp, tz=timezone.utc
        ).isoformat(),
        "created_timestamp": created_timestamp,
        "author": {"screen_name": author, "name": "Configured Source"},
        "media": {
            "all": [
                {
                    "type": "photo",
                    "url": "https://pbs.twimg.com/media/test.jpg",
                    "width": 1200,
                    "height": 800,
                }
            ]
        },
        "lang": "en",
        "replying_to": (
            {"screen_name": "other", "status": reply_to} if reply_to else None
        ),
        "reposted_by": {"screen_name": "reposter"} if reposted else None,
    }


class FxTwitterRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.start = datetime(2026, 8, 13, 7, 0, tzinfo=timezone.utc)
        self.end = datetime(2026, 8, 13, 9, 0, tzinfo=timezone.utc)

    @patch("app.x_fxtwitter.requests.get")
    def test_collects_authorized_status_and_media(self, get):
        response = Mock(status_code=200)
        response.json.return_value = {
            "code": 200,
            "results": [_status("123", created_timestamp=self.start.timestamp() + 1800)],
            "cursor": {"top": None, "bottom": None},
        }
        get.return_value = response

        result = collect_fxtwitter_timeline(
            "source",
            self.start,
            self.end,
            include_replies=True,
        )

        self.assertEqual(result.raw_seen, 1)
        self.assertEqual([item.id for item in result.updates], ["123"])
        self.assertEqual(result.updates[0].raw_query, "fxtwitter:@source")
        self.assertEqual(result.updates[0].media[0].kind, "photo")

    @patch("app.x_fxtwitter.requests.get")
    def test_filters_wrong_author_reposts_and_replies(self, get):
        created = self.start.timestamp() + 1800
        response = Mock(status_code=200)
        response.json.return_value = {
            "code": 200,
            "results": [
                _status("wrong", author="attacker", created_timestamp=created),
                _status("repost", created_timestamp=created, reposted=True),
                _status("reply", created_timestamp=created, reply_to="parent"),
            ],
            "cursor": {"top": None, "bottom": None},
        }
        get.return_value = response

        result = collect_fxtwitter_timeline(
            "source",
            self.start,
            self.end,
            include_replies=False,
        )
        self.assertEqual(result.updates, [])

    @patch("app.x_fxtwitter.requests.get")
    def test_204_is_successful_empty_window(self, get):
        get.return_value = Mock(status_code=204)
        result = collect_fxtwitter_timeline("source", self.start, self.end)
        self.assertEqual(result.updates, [])
        self.assertEqual(result.pages, 1)

    @patch("app.x_fxtwitter.requests.get")
    def test_http_failure_is_closed(self, get):
        get.return_value = Mock(status_code=503)
        with self.assertRaises(FxTwitterError):
            collect_fxtwitter_timeline("source", self.start, self.end)


if __name__ == "__main__":
    unittest.main()
