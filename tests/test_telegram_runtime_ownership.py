from __future__ import annotations

import asyncio
import os
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from app.webhook_aware_assistant import (
    WEBHOOK_DELEGATED_EXIT_CODE,
    WEBHOOK_MAINTENANCE_FAILED_EXIT_CODE,
    WebhookAwarePersonalAssistant,
)


ROOT = Path(__file__).resolve().parents[1]


class TelegramRuntimeOwnershipTests(unittest.TestCase):
    def test_actions_workflow_uses_auto_transport_ownership(self):
        workflow = (ROOT / ".github" / "workflows" / "main.yml").read_text(encoding="utf-8")

        self.assertIn("ASSISTANT_RUNTIME_MODE: github_actions_auto", workflow)
        self.assertNotIn("ASSISTANT_RUNTIME_MODE: github_actions_polling", workflow)
        self.assertIn('if [ "$code" -eq 3 ]; then', workflow)
        self.assertIn('if [ "$code" -eq 4 ]; then', workflow)

    def test_auto_mode_delegates_to_healthy_webhook_without_polling(self):
        app = object.__new__(WebhookAwarePersonalAssistant)
        app.settings = SimpleNamespace(telegram_token="123:abc")
        app.telegram = SimpleNamespace(
            api=Mock(return_value={"url": "https://assistant.example/telegram/webhook"}),
            session=SimpleNamespace(post=Mock(return_value=SimpleNamespace(status_code=204))),
            ensure_polling_mode=Mock(),
        )

        with patch.dict(
            os.environ,
            {"ASSISTANT_RUNTIME_MODE": "github_actions_auto"},
            clear=False,
        ):
            code = asyncio.run(app.run())

        self.assertEqual(code, WEBHOOK_DELEGATED_EXIT_CODE)
        app.telegram.ensure_polling_mode.assert_not_called()

    def test_auto_mode_keeps_webhook_owner_when_maintenance_is_unavailable(self):
        app = object.__new__(WebhookAwarePersonalAssistant)
        app.settings = SimpleNamespace(telegram_token="123:abc")
        app.telegram = SimpleNamespace(
            api=Mock(return_value={"url": "https://assistant.example/telegram/webhook"}),
            session=SimpleNamespace(post=Mock(side_effect=RuntimeError("offline"))),
            ensure_polling_mode=Mock(),
        )

        with patch.dict(
            os.environ,
            {"ASSISTANT_RUNTIME_MODE": "github_actions_auto"},
            clear=False,
        ):
            code = asyncio.run(app.run())

        self.assertEqual(code, WEBHOOK_MAINTENANCE_FAILED_EXIT_CODE)
        app.telegram.ensure_polling_mode.assert_not_called()

    def test_auto_mode_fails_closed_when_webhook_ownership_cannot_be_inspected(self):
        app = object.__new__(WebhookAwarePersonalAssistant)
        app.telegram = SimpleNamespace(
            api=Mock(side_effect=RuntimeError("telegram unavailable")),
            ensure_polling_mode=Mock(),
        )

        with patch.dict(
            os.environ,
            {"ASSISTANT_RUNTIME_MODE": "github_actions_auto"},
            clear=False,
        ):
            code = asyncio.run(app.run())

        self.assertEqual(code, WEBHOOK_MAINTENANCE_FAILED_EXIT_CODE)
        app.telegram.ensure_polling_mode.assert_not_called()

    def test_auto_mode_keeps_polling_tail_available_for_fallback(self):
        app = object.__new__(WebhookAwarePersonalAssistant)
        app.settings = SimpleNamespace(
            runtime={
                "telegram_interactive_window_seconds": 1,
                "telegram_actions_runtime_cap_seconds": 60,
            }
        )
        app.process_telegram_updates = AsyncMock(return_value=0)
        app.state = SimpleNamespace(save=Mock())

        with (
            patch.dict(
                os.environ,
                {"ASSISTANT_RUNTIME_MODE": "github_actions_auto"},
                clear=False,
            ),
            patch("app.main._monotonic", side_effect=[0.0, 0.0, 0.0, 1.0]),
        ):
            asyncio.run(app._run_interactive_polling_window(0.0))

        app.process_telegram_updates.assert_awaited_once_with(long_poll_seconds=1)
        app.state.save.assert_called_once()


if __name__ == "__main__":
    unittest.main()
