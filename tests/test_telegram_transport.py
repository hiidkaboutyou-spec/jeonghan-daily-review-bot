from __future__ import annotations

import asyncio
import unittest
from types import SimpleNamespace
from unittest.mock import patch

import requests

from app.telegram import (
    TelegramBot,
    TelegramPermanentError,
    TelegramRateLimitError,
    TelegramTransientError,
)
from app.telegram_update_runtime import TelegramSafeReviewApplication


class _Response:
    def __init__(self, status_code: int, payload=None, *, json_error: bool = False):
        self.status_code = status_code
        self._payload = payload
        self._json_error = json_error
        self.ok = 200 <= status_code < 300

    def json(self):
        if self._json_error:
            raise ValueError("bad json")
        return self._payload


class _Session:
    def __init__(self, outcomes):
        self.outcomes = list(outcomes)
        self.calls = 0

    def post(self, *args, **kwargs):
        self.calls += 1
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


class TelegramTransportTests(unittest.TestCase):
    def bot_with(self, outcomes):
        bot = TelegramBot("super-secret-token", 1, -1)
        bot.session = _Session(outcomes)
        return bot

    def test_429_honors_retry_after_then_succeeds(self):
        bot = self.bot_with([
            _Response(429, {"ok": False, "error_code": 429, "parameters": {"retry_after": 7}}),
            _Response(200, {"ok": True, "result": {"message_id": 9}}),
        ])
        with patch("app.telegram.time.sleep") as sleep, patch("app.telegram.random.uniform", return_value=0.0):
            result = bot.api("sendMessage", attempts=2)
        self.assertEqual(result["message_id"], 9)
        sleep.assert_called_once_with(7.0)

    def test_final_429_is_typed_transient_with_retry_after(self):
        bot = self.bot_with([
            _Response(429, {"ok": False, "error_code": 429, "parameters": {"retry_after": 33}}),
        ])
        with self.assertRaises(TelegramRateLimitError) as caught:
            bot.api("sendMessage", attempts=1)
        self.assertEqual(caught.exception.retry_after, 33)

    def test_5xx_retries_then_becomes_transient(self):
        bot = self.bot_with([
            _Response(503, {"ok": False, "error_code": 503}),
            _Response(503, {"ok": False, "error_code": 503}),
        ])
        with patch("app.telegram.time.sleep"), patch("app.telegram.random.uniform", return_value=0.0):
            with self.assertRaises(TelegramTransientError):
                bot.api("sendMessage", attempts=2)

    def test_network_reset_is_transient_and_token_is_not_exposed(self):
        bot = self.bot_with([requests.ConnectionError("https://api.telegram.org/botsuper-secret-token/sendMessage")])
        with self.assertRaises(TelegramTransientError) as caught:
            bot.api("sendMessage", attempts=1)
        self.assertNotIn("super-secret-token", str(caught.exception))

    def test_non_5xx_invalid_json_is_permanent(self):
        bot = self.bot_with([_Response(400, json_error=True)])
        with self.assertRaises(TelegramPermanentError):
            bot.api("sendMessage", attempts=1)

    def test_400_description_is_redacted(self):
        bot = self.bot_with([
            _Response(400, {"ok": False, "error_code": 400, "description": "bad https://api.telegram.org/botsuper-secret-token/path"})
        ])
        with self.assertRaises(TelegramPermanentError) as caught:
            bot.api("sendMessage", attempts=1)
        self.assertNotIn("super-secret-token", str(caught.exception))

    def test_malformed_error_code_is_a_typed_permanent_error(self):
        bot = self.bot_with([
            _Response(400, {"ok": False, "error_code": {"unexpected": "object"}}),
        ])
        with self.assertRaises(TelegramPermanentError):
            bot.api("sendMessage", attempts=1)


class _State:
    def __init__(self):
        self.telegram_offset = 0
        self.failures = {}

    def record_telegram_failure(self, update_id, error_type):
        self.failures[update_id] = self.failures.get(update_id, 0) + 1
        return self.failures[update_id]

    def clear_telegram_failure(self, update_id):
        self.failures.pop(update_id, None)


class _Telegram:
    def __init__(self):
        self.callback_store = None

    def get_updates(self, offset):
        return [{"update_id": 10, "message": {}}]


class TelegramUpdateTransientTests(unittest.TestCase):
    def app(self, exc):
        app = object.__new__(TelegramSafeReviewApplication)
        app.state = _State()
        app.telegram = _Telegram()
        app._safe_send = lambda text: None

        async def handler(item):
            raise exc

        app._process_one_telegram_update = handler
        return app

    def test_transient_platform_failure_does_not_increment_poison_counter_or_offset(self):
        app = self.app(TelegramTransientError("outage"))
        asyncio.run(app.process_telegram_updates())
        self.assertEqual(app.state.telegram_offset, 0)
        self.assertEqual(app.state.failures, {})

    def test_permanent_bot_api_failure_is_not_retried_forever(self):
        app = self.app(TelegramPermanentError("bad request"))
        asyncio.run(app.process_telegram_updates())
        self.assertEqual(app.state.telegram_offset, 11)
        self.assertEqual(app.state.failures, {})


if __name__ == "__main__":
    unittest.main()
