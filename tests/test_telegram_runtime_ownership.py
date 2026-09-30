from __future__ import annotations

import asyncio
import os
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from app.webhook_aware_assistant import WebhookAwarePersonalAssistant


ROOT = Path(__file__).resolve().parents[1]


class TelegramRuntimeOwnershipTests(unittest.TestCase):
    def test_actions_workflow_uses_auto_transport_ownership(self):
        workflow = (ROOT / ".github" / "workflows" / "main.yml").read_text(encoding="utf-8")

        self.assertIn("ASSISTANT_RUNTIME_MODE: github_actions_auto", workflow)
        self.assertNotIn("ASSISTANT_RUNTIME_MODE: github_actions_polling", workflow)

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
