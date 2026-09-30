from __future__ import annotations

import asyncio
import os
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from app.main import Application
from app.telegram import TelegramBot


class TelegramInteractivePollingTests(unittest.TestCase):
    def test_transport_uses_requested_long_poll_timeout(self):
        bot = TelegramBot("token", 1, 2)
        bot.api = Mock(return_value=[])

        result = bot.get_updates(41, timeout_seconds=23)

        self.assertEqual(result, [])
        bot.api.assert_called_once()
        call = bot.api.call_args
        self.assertEqual(call.args[0], "getUpdates")
        self.assertEqual(call.kwargs["data"]["offset"], 41)
        self.assertEqual(call.kwargs["data"]["timeout"], 23)
        self.assertEqual(call.kwargs["timeout"], 33)

    def test_transport_preserves_existing_one_shot_polling_default(self):
        bot = TelegramBot("token", 1, 2)
        bot.api = Mock(return_value=[])

        bot.get_updates(7)

        call = bot.api.call_args
        self.assertEqual(call.kwargs["data"]["timeout"], 0)
        self.assertEqual(call.kwargs["timeout"], 30)

    def test_process_updates_requests_long_poll_only_when_asked(self):
        app = Application.__new__(Application)
        app.state = SimpleNamespace(telegram_offset=9)
        app.telegram = Mock()
        app.telegram.get_updates.return_value = []

        count = asyncio.run(app.process_telegram_updates(long_poll_seconds=12))

        self.assertEqual(count, 0)
        app.telegram.get_updates.assert_called_once_with(9, timeout_seconds=12)

    def test_actions_interactive_window_long_polls_and_checkpoints(self):
        app = Application.__new__(Application)
        app.settings = SimpleNamespace(
            runtime={
                "telegram_interactive_window_seconds": 3,
                "telegram_actions_runtime_cap_seconds": 60,
            }
        )
        app.state = Mock()
        app.process_telegram_updates = AsyncMock(return_value=0)

        with patch.dict(os.environ, {"ASSISTANT_RUNTIME_MODE": "github_actions_polling"}, clear=False):
            with patch("app.main._monotonic", side_effect=[100.0, 100.0, 100.0, 104.0]):
                asyncio.run(app._run_interactive_polling_window(100.0))

        app.process_telegram_updates.assert_awaited_once_with(long_poll_seconds=3)
        app.state.save.assert_called_once_with()

    def test_interactive_window_is_disabled_outside_actions_runtime(self):
        app = Application.__new__(Application)
        app.settings = SimpleNamespace(runtime={})
        app.state = Mock()
        app.process_telegram_updates = AsyncMock(return_value=0)

        with patch.dict(os.environ, {"ASSISTANT_RUNTIME_MODE": "local"}, clear=False):
            asyncio.run(app._run_interactive_polling_window(0.0))

        app.process_telegram_updates.assert_not_awaited()
        app.state.save.assert_not_called()

    def test_runtime_cap_prevents_polling_after_a_long_monitor_pass(self):
        app = Application.__new__(Application)
        app.settings = SimpleNamespace(
            runtime={
                "telegram_interactive_window_seconds": 240,
                "telegram_actions_runtime_cap_seconds": 60,
            }
        )
        app.state = Mock()
        app.process_telegram_updates = AsyncMock(return_value=0)

        with patch.dict(os.environ, {"ASSISTANT_RUNTIME_MODE": "github_actions_polling"}, clear=False):
            with patch("app.main._monotonic", side_effect=[100.0, 100.0]):
                asyncio.run(app._run_interactive_polling_window(0.0))

        app.process_telegram_updates.assert_not_awaited()
        app.state.save.assert_not_called()


if __name__ == "__main__":
    unittest.main()
