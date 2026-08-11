from __future__ import annotations

import asyncio
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from app.config import ConfigError
from app.production_preflight import _check_gemini, _check_telegram, _check_x
from app.telegram import TelegramPermanentError
from app.x_client import XCollectionError


def settings(**overrides):
    values = {
        "telegram_token": "token",
        "admin_user_id": 1,
        "review_chat_id": -1001,
        "gemini_api_key": "",
        "gemini_model": "gemini-3.1-flash-lite",
        "x_cookies": {},
        "sources": [],
        "keyword_groups": [],
    }
    values.update(overrides)
    return SimpleNamespace(**values)


class ProductionPreflightTests(unittest.TestCase):
    @patch("app.production_preflight.TelegramBot")
    def test_telegram_is_the_hard_dependency(self, bot_class):
        bot_class.return_value.api.side_effect = TelegramPermanentError("bad token")
        with self.assertRaises(ConfigError):
            _check_telegram(settings())

    @patch("app.production_preflight.TelegramBot")
    def test_telegram_bot_and_chat_are_both_checked(self, bot_class):
        bot_class.return_value.api.side_effect = [
            {"id": 1, "username": "jeonghan_helper_bot"},
            {"id": -1001},
        ]
        self.assertEqual(_check_telegram(settings()), "ok (@jeonghan_helper_bot)")
        self.assertEqual(bot_class.return_value.api.call_count, 2)

    @patch("app.production_preflight.TelegramBot")
    def test_telegram_preflight_does_not_require_a_public_username(self, bot_class):
        bot_class.return_value.api.side_effect = [{"id": 1}, {"id": -1001}]
        self.assertEqual(_check_telegram(settings()), "ok")

    def test_missing_optional_providers_use_degraded_modes(self):
        self.assertIn("fallback", _check_gemini(settings()))
        self.assertIn("offline", asyncio.run(_check_x(settings())))

    @patch("app.production_preflight.XCollector")
    def test_x_failure_is_reported_without_raising(self, collector_class):
        collector_class.return_value.healthcheck = AsyncMock(
            side_effect=XCollectionError("expired")
        )
        result = asyncio.run(
            _check_x(settings(x_cookies={"auth_token": "a", "ct0": "b"}))
        )
        self.assertIn("offline", result)


if __name__ == "__main__":
    unittest.main()
