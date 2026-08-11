from __future__ import annotations

import asyncio
import os
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import requests

from app.config import ConfigError, ROOT, Settings, parse_cookie_secret
from app.fic_digest import Fic, _is_jeonghan, search_ao3
from app.models import EventGroup, Update
from app.organizer import organize_updates
from app.state import SCHEMA_VERSION, StateStore
from app.style import ThemeEngine
from app.telegram import TelegramBot, TelegramError
from app.x_client import (
    XCollectionError,
    XCollector,
    _keyword_queries,
    is_relevant_jeonghan_update,
    normalize_handle,
)


class ConfigAuditTests(unittest.TestCase):
    def test_malformed_cookie_json_becomes_config_error(self):
        with self.assertRaises(ConfigError):
            parse_cookie_secret('{"auth_token":')

    def test_gemini_key_is_optional_because_fallback_exists(self):
        env = {
            "TELEGRAM_BOT_TOKEN": "fake-token",
            "TELEGRAM_ADMIN_USER_ID": "1",
            "TELEGRAM_REVIEW_CHAT_ID": "-100123",
            "X_COOKIE": "auth_token=a; ct0=b",
            "GEMINI_API_KEY": "",
        }
        with patch.dict(os.environ, env, clear=False):
            settings = Settings.load(require_secrets=True)
        self.assertEqual(settings.gemini_api_key, "")

    def test_x_cookie_is_optional_so_telegram_assistant_still_starts(self):
        env = {
            "TELEGRAM_BOT_TOKEN": "fake-token",
            "TELEGRAM_ADMIN_USER_ID": "1",
            "TELEGRAM_REVIEW_CHAT_ID": "-100123",
            "X_COOKIE": "",
        }
        with patch.dict(os.environ, env, clear=False):
            settings = Settings.load(require_secrets=True)
        self.assertEqual(settings.x_cookies, {})

    def test_malformed_x_cookie_degrades_instead_of_killing_assistant(self):
        env = {
            "TELEGRAM_BOT_TOKEN": "fake-token",
            "TELEGRAM_ADMIN_USER_ID": "1",
            "TELEGRAM_REVIEW_CHAT_ID": "-100123",
            "X_COOKIE": '{"auth_token":',
        }
        with patch.dict(os.environ, env, clear=False):
            settings = Settings.load(require_secrets=True)
        self.assertEqual(settings.x_cookies, {})


class StateAuditTests(unittest.TestCase):
    def test_old_or_malformed_nested_state_is_normalized(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "state.json"
            path.write_text(
                '{"schema":0,"telegram_offset":"bad","seen":[],"archive":"bad",'
                '"sessions":null,"drafts":[],"awaiting":3,"pending_delivery":[null,{"id":"x"}]}',
                encoding="utf-8",
            )
            store = StateStore(path)
            self.assertEqual(store.data["schema"], SCHEMA_VERSION)
            self.assertEqual(store.telegram_offset, 0)
            self.assertIsInstance(store.data["seen"], dict)
            self.assertIsInstance(store.data["archive"], dict)
            self.assertEqual(store.data["pending_delivery"], [{"id": "x"}])


class OrganizerAuditTests(unittest.TestCase):
    @staticmethod
    def _update(id_: str, minute: int, text: str, conversation: str) -> Update:
        return Update(
            id=id_,
            url=f"https://x.com/source/status/{id_}",
            author="source",
            author_name="Source",
            text=text,
            created_at=datetime(2026, 7, 14, 12, minute, tzinfo=timezone.utc),
            conversation_id=conversation,
            reply_to_id="root" if id_ != conversation else "",
        )

    def test_two_live_threads_close_in_time_are_not_merged(self):
        first = self._update("101", 1, "weverse live part 1", "100")
        second = self._update("201", 2, "weverse live part 1", "200")
        groups = organize_updates([first, second])
        self.assertEqual(len(groups), 2)
        self.assertNotEqual(groups[0].key, groups[1].key)

    def test_explicit_live_part_numbers_override_bad_timestamps(self):
        part2 = self._update("102", 1, "weverse live part 2", "100")
        part1 = self._update("101", 2, "weverse live part 1", "100")
        groups = organize_updates([part2, part1])
        self.assertEqual([item.id for item in groups[0].updates], ["101", "102"])


class XAuditTests(unittest.TestCase):
    def test_keyword_synonyms_are_combined_into_one_query_per_group(self):
        queries = _keyword_queries(
            [
                {"name": "english", "terms": ["JEONGHAN", "Yoon Jeonghan", "#JEONGHAN"]},
                {"name": "korean", "terms": ["윤정한", "정한"]},
            ],
            "since:2026-08-10 until:2026-08-12",
        )

        self.assertEqual(len(queries), 2)
        self.assertIn('(JEONGHAN OR "Yoon Jeonghan" OR #JEONGHAN)', queries[0])
        self.assertIn("(윤정한 OR 정한)", queries[1])
        self.assertTrue(all("since:2026-08-10" in query for query in queries))

    def test_wts_jeonghan_without_price_is_still_marketplace_noise(self):
        update = Update(
            id="sale",
            url="https://x.com/seller/status/sale",
            author="seller",
            author_name="Seller",
            text="WTS JEONGHAN photocard, dm to claim",
            created_at=datetime.now(timezone.utc),
        )
        self.assertFalse(is_relevant_jeonghan_update(update, trusted_source=False))

    def test_manual_source_handle_rejects_query_injection(self):
        self.assertEqual(normalize_handle("good_handle"), "good_handle")
        self.assertEqual(normalize_handle("@good_handle"), "good_handle")
        self.assertEqual(normalize_handle("https://x.com/good_handle?s=21"), "good_handle")
        self.assertEqual(normalize_handle("name from:other"), "")
        self.assertEqual(normalize_handle("bad/extra"), "")

    def test_partial_source_failure_is_recorded_even_when_other_results_exist(self):
        collector = XCollector(
            {},
            [{"handle": "source", "enabled": True, "include_replies": True}],
            [{"name": "en", "terms": ["JEONGHAN"]}],
        )
        collector._collect_source_timeline = AsyncMock(side_effect=XCollectionError("timeline failed"))
        good = Update(
            id="1",
            url="https://x.com/fan/status/1",
            author="fan",
            author_name="Fan",
            text="JEONGHAN update",
            created_at=datetime(2026, 8, 7, 10, 0, tzinfo=timezone.utc),
        )
        collector._run_queries = AsyncMock(return_value=[good])
        result = asyncio.run(
            collector.collect_window(
                datetime(2026, 8, 7, 9, 0, tzinfo=timezone.utc),
                datetime(2026, 8, 7, 11, 0, tzinfo=timezone.utc),
            )
        )
        self.assertEqual([item.id for item in result], ["1"])
        self.assertTrue(collector.last_errors)


class TelegramAuditTests(unittest.TestCase):
    def test_network_error_does_not_expose_bot_token_or_chain_original_exception(self):
        secret = "123456:VERY_SECRET_TOKEN"
        bot = TelegramBot(secret, 1, -100)
        bot.session.post = Mock(
            side_effect=requests.ConnectionError(
                f"boom https://api.telegram.org/bot{secret}/getUpdates"
            )
        )
        with self.assertRaises(TelegramError) as ctx:
            bot.api("getUpdates", attempts=1)
        self.assertNotIn(secret, str(ctx.exception))
        self.assertIsNone(ctx.exception.__cause__)

    def test_over_limit_messages_fail_explicitly_instead_of_truncating(self):
        bot = TelegramBot("fake", 1, -100)
        with self.assertRaises(TelegramError):
            bot.send_message("x" * 4097)


class StyleAuditTests(unittest.TestCase):
    def test_theme_engine_no_longer_silently_truncates_caption(self):
        settings = Settings.load(require_secrets=False)
        engine = ThemeEngine(settings.themes, settings.timezone)
        update = Update(
            id="long",
            url="https://x.com/a/status/long",
            author="a",
            author_name="A",
            text="JEONGHAN",
            created_at=datetime.now(timezone.utc),
        )
        group = EventGroup(key="single:long", category="general", title="test", updates=[update])
        caption = engine.caption(group, update, "الف" * 5000, 1, 1)
        self.assertGreater(len(caption), 4096)
        self.assertTrue(caption.endswith(update.url))


class FanficAuditTests(unittest.TestCase):
    def test_summary_mention_without_jeonghan_relationship_is_rejected(self):
        relationships = ["Choi Seungcheol/Hong Jisoo"]
        self.assertFalse(_is_jeonghan(relationships, "Jeonghan appears in the summary"))

    def test_ship_classification_ignores_unrelated_side_relationships(self):
        fic = Fic(
            title="x",
            url="https://archiveofourown.org/works/1",
            author="a",
            summary="s",
            relationships=[
                "Yoon Jeonghan/Hong Jisoo",
                "Choi Seungcheol/Kim Mingyu",
            ],
        )
        self.assertEqual(fic.ship, "Jihan")

    def test_ao3_search_paginates_to_reach_requested_limit(self):
        page1 = """
        <ol><li class='work blurb'>
          <h4 class='heading'><a href='/works/1'>One</a><a rel='author'>A</a></h4>
          <ul><li class='relationships'><a class='tag'>Yoon Jeonghan/Choi Seungcheol</a></li></ul>
          <dd class='language'>English</dd><dd class='kudos'>10</dd>
        </li></ol>
        """
        page2 = """
        <ol><li class='work blurb'>
          <h4 class='heading'><a href='/works/2'>Two</a><a rel='author'>B</a></h4>
          <ul><li class='relationships'><a class='tag'>Yoon Jeonghan/Hong Jisoo</a></li></ul>
          <dd class='language'>English</dd><dd class='kudos'>9</dd>
        </li></ol>
        """
        with patch(
            "app.fic_digest._get",
            side_effect=[SimpleNamespace(text=page1), SimpleNamespace(text=page2)],
        ) as mocked_get:
            result = search_ao3(2)
        self.assertEqual([fic.url for fic in result], [
            "https://archiveofourown.org/works/1",
            "https://archiveofourown.org/works/2",
        ])
        self.assertIn("page=1", mocked_get.call_args_list[0].args[0])
        self.assertIn("page=2", mocked_get.call_args_list[1].args[0])


class WorkflowSecurityTests(unittest.TestCase):
    def test_main_workflow_caches_only_state_json_not_twscrape_cookie_db(self):
        text = (ROOT / ".github" / "workflows" / "main.yml").read_text(encoding="utf-8")
        self.assertIn("path: .state/state.json", text)
        self.assertNotIn("path: .state\n", text)
        self.assertNotIn("jeonghan-state-${{ runner.os }}-", text)


if __name__ == "__main__":
    unittest.main()
