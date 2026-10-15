from __future__ import annotations

import asyncio
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import Mock

from app.ai import GroupCopy
from app.config import Settings
from app.sentry_runtime import WebhookAwarePersonalAssistant
from app.telegram import TelegramTransientError


class DeliveryReentrantPollingTests(unittest.TestCase):
    def exercise(self, fail=False):
        with tempfile.TemporaryDirectory() as temp:
            settings = Settings.load(require_secrets=False)
            settings.state_path = Path(temp) / 'state.json'
            app = WebhookAwarePersonalAssistant(settings)
            app.telegram = Mock()
            incoming = {'update_id': 41, 'message': {'text': '/review'}}
            app.telegram.get_updates.return_value = [incoming]
            app.telegram.send_message.return_value = {'message_id': 1}
            from app.models import Update
            updates = [Update(str(i), '', settings.sources[0]['handle'], 'Synthetic',
                              f'Jeonghan synthetic post {i}',
                              datetime(2026, 10, 1, tzinfo=timezone.utc) + timedelta(days=i))
                       for i in range(6)]
            app.writer.write_group = Mock(side_effect=lambda group: GroupCopy(
                group.title, group.category, {u.id: 'متن آزمایشی' for u in group.updates}))
            calls = []

            async def handler(message):
                calls.append(app.state.telegram_offset)
                # Bound the pre-fix reproduction: the nested poll must never
                # redispatch this still-unconfirmed command.
                if len(calls) > 1:
                    raise RecursionError('same in-flight command dispatched again')
                await app.deliver_updates(updates, force=False)
                if fail:
                    raise TelegramTransientError('synthetic temporary failure')

            app.handle_message = handler
            asyncio.run(app.process_telegram_updates())
            self.assertEqual(calls, [0])
            app.telegram.get_updates.assert_called_once_with(0)
            self.assertEqual(app.state.telegram_offset, 0 if fail else 42)
            self.assertEqual(app.state.telegram_failure_count(41), 0)
            self.assertTrue(all(app.state.is_seen(u.id) for u in updates))
            # Guard must be released after both success and transient failure.
            app.telegram.get_updates.return_value = []
            asyncio.run(app.process_telegram_updates(long_poll_seconds=2))
            app.telegram.get_updates.assert_called_with(0 if fail else 42, timeout_seconds=2)

    def test_six_group_delivery_does_not_redispatch_inflight_command(self):
        self.exercise()

    def test_transient_failure_retains_offset_and_releases_poll_guard(self):
        self.exercise(fail=True)
