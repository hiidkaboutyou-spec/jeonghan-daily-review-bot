from __future__ import annotations

import json
import os
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from zoneinfo import ZoneInfo

from app.config import ROOT, Settings, parse_cookie_secret
from app.main import parse_date_query
from app.models import Update
from app.organizer import organize_updates
from app.state import StateStore
from app.style import RLM, ThemeEngine, apply_rtl, ensure_rtl_line
from app.telegram import main_keyboard
from app.x_client import XCollector


class CookieTests(unittest.TestCase):
    def test_cookie_header(self):
        value = parse_cookie_secret("auth_token=abc; ct0=xyz")
        self.assertEqual(value["auth_token"], "abc")
        self.assertEqual(value["ct0"], "xyz")

    def test_cookie_json(self):
        value = parse_cookie_secret('{"auth_token":"abc","ct0":"xyz"}')
        self.assertEqual(value, {"auth_token": "abc", "ct0": "xyz"})

    def test_netscape_cookie(self):
        raw = ".x.com\tTRUE\t/\tTRUE\t2147483647\tauth_token\tabc\n.x.com\tTRUE\t/\tTRUE\t2147483647\tct0\txyz\n"
        value = parse_cookie_secret(raw)
        self.assertEqual(value["ct0"], "xyz")


class RtlTests(unittest.TestCase):
    def test_header_starts_with_rlm_and_persian_commas(self):
        result = ensure_rtl_line("🐹⌕໋  ִ˒˒ متن فارسی", header=True)
        self.assertTrue(result.startswith(RLM + "،، "))

    def test_multiline_rtl(self):
        result = apply_rtl("𖥨 متن فارسی\nEnglish line")
        self.assertTrue(result.splitlines()[0].startswith(RLM + "،، "))
        self.assertEqual(result.splitlines()[1], "English line")


class OrderingTests(unittest.TestCase):
    def make_update(self, id_: str, minute: int, text: str, conversation: str = "live-1") -> Update:
        return Update(
            id=id_,
            url=f"https://x.com/source/status/{id_}",
            author="source",
            author_name="Source",
            text=text,
            created_at=datetime(2026, 7, 14, 12, minute, tzinfo=timezone.utc),
            conversation_id=conversation,
        )

    def test_oldest_to_newest_and_live_kept_together(self):
        updates = [
            self.make_update("3", 3, "weverse live part 3"),
            self.make_update("1", 1, "weverse live part 1"),
            self.make_update("2", 2, "weverse live part 2"),
        ]
        groups = organize_updates(updates)
        self.assertEqual(len(groups), 1)
        self.assertEqual([item.id for item in groups[0].updates], ["1", "2", "3"])
        self.assertEqual(groups[0].category, "live")

    def test_unrelated_update_is_separate(self):
        live = self.make_update("1", 1, "weverse live part 1", "live-1")
        other = self.make_update("2", 2, "airport photos", "airport-2")
        groups = organize_updates([other, live])
        self.assertEqual(len(groups), 2)
        self.assertEqual(groups[0].updates[0].id, "1")
        self.assertEqual(groups[1].updates[0].id, "2")


class StateTests(unittest.TestCase):
    def test_pending_force_field_is_not_passed_into_update(self):
        with tempfile.TemporaryDirectory() as temp:
            store = StateStore(Path(temp) / "state.json")
            update = Update(
                id="10",
                url="https://x.com/a/status/10",
                author="a",
                author_name="A",
                text="test",
                created_at=datetime.now(timezone.utc),
            )
            store.queue_updates([update], force=True)
            pending = store.pop_pending(1)
            self.assertEqual(pending[0][0].id, "10")
            self.assertTrue(pending[0][1])

    def test_seen_does_not_block_explicit_force_logic(self):
        with tempfile.TemporaryDirectory() as temp:
            store = StateStore(Path(temp) / "state.json")
            update = Update(
                id="11",
                url="https://x.com/a/status/11",
                author="a",
                author_name="A",
                text="test",
                created_at=datetime.now(timezone.utc),
            )
            store.mark_seen(update)
            self.assertTrue(store.is_seen("11"))
            # Explicit replay calls deliver_updates(..., force=True), so the item remains usable.
            self.assertEqual(store.get_update("11").id, "11")


class XConversionTests(unittest.TestCase):
    def test_twscrape_tweet_and_best_video_are_converted(self):
        media = SimpleNamespace(
            photos=[SimpleNamespace(url="https://pbs.twimg.com/media/photo")],
            videos=[
                SimpleNamespace(
                    thumbnailUrl="https://pbs.twimg.com/thumb",
                    duration=1200,
                    variants=[
                        SimpleNamespace(url="https://video/low.mp4", contentType="video/mp4", bitrate=256000),
                        SimpleNamespace(url="https://video/high.mp4", contentType="video/mp4", bitrate=1024000),
                    ],
                )
            ],
            animated=[],
        )
        tweet = SimpleNamespace(
            id_str="123",
            url="https://x.com/source/status/123",
            date=datetime(2026, 7, 14, 12, 0, tzinfo=timezone.utc),
            user=SimpleNamespace(username="source", displayname="Source"),
            rawContent="JEONGHAN update",
            conversationIdStr="100",
            inReplyToTweetIdStr="99",
            quotedTweet=None,
            lang="en",
            media=media,
        )
        collector = XCollector({}, [], [])
        update = collector._convert_tweet(tweet, raw_query="test")
        self.assertEqual(update.id, "123")
        self.assertEqual(update.conversation_id, "100")
        self.assertTrue(update.is_reply)
        self.assertIn("name=orig", update.media[0].url)
        self.assertEqual(update.media[1].url, "https://video/high.mp4")


class DateAndConfigTests(unittest.TestCase):
    def test_compact_date_uses_tehran_day(self):
        start, end = parse_date_query("260714", ZoneInfo("Asia/Tehran"))
        self.assertEqual(start.isoformat(), "2026-07-13T20:30:00+00:00")
        self.assertEqual(end.isoformat(), "2026-07-14T20:30:00+00:00")

    def test_sources_have_couphanfiles_and_multilingual_terms(self):
        data = json.loads((ROOT / "config" / "sources.json").read_text(encoding="utf-8"))
        handles = {item["handle"] for item in data["sources"]}
        terms = {term for group in data["keyword_groups"] for term in group["terms"]}
        self.assertIn("couphanfiles", handles)
        self.assertIn("윤정한", terms)
        self.assertIn("ジョンハン", terms)
        self.assertIn("JEONGHAN", terms)

    def test_project_config_validates(self):
        settings = Settings.load(require_secrets=False)
        self.assertEqual(settings.validate_files(), [])

    def test_empty_gemini_model_variable_uses_config_default(self):
        with patch.dict(os.environ, {"GEMINI_MODEL": ""}, clear=False):
            settings = Settings.load(require_secrets=False)
        self.assertEqual(settings.gemini_model, "gemini-2.5-flash-lite")

    def test_main_menu_contains_interactive_callbacks(self):
        keyboard = main_keyboard()["inline_keyboard"]
        callbacks = {button["callback_data"] for row in keyboard for button in row}
        self.assertIn("cmd:recent2h", callbacks)
        self.assertIn("cmd:search", callbacks)
        self.assertIn("cmd:sources", callbacks)


if __name__ == "__main__":
    unittest.main()
