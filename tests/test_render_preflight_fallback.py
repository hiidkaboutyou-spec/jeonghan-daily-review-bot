from __future__ import annotations

import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from app.render_preflight import run_preflight


class RenderPreflightFallbackTests(unittest.TestCase):
    def _settings(self, *, gemini_api_key: str = ""):
        return SimpleNamespace(
            telegram_token="token",
            admin_user_id=1823582217,
            review_chat_id=1823582217,
            gemini_api_key=gemini_api_key,
            gemini_model="gemini-test",
            x_cookies={},
            validate_files=lambda: [],
        )

    def test_missing_gemini_key_keeps_webhook_runtime_deployable(self):
        settings = self._settings(gemini_api_key="")
        bot = MagicMock()
        bot.api.side_effect = [
            {"id": 8776046411, "username": "hani_test_bot"},
            {"id": 1823582217, "type": "private"},
        ]

        with patch("app.render_preflight.Settings.load", return_value=settings), \
             patch("app.render_preflight.TelegramBot", return_value=bot):
            report = run_preflight()

        self.assertEqual(report["telegram"], "ok")
        self.assertEqual(report["review_chat"], "ok")
        self.assertTrue(report["gemini"].startswith("fallback"))
        self.assertEqual(bot.api.call_args_list[0].args[0], "getMe")
        self.assertEqual(bot.api.call_args_list[1].args[0], "getChat")


if __name__ == "__main__":
    unittest.main()
