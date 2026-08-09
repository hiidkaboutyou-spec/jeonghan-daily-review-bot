from __future__ import annotations

import asyncio
import tempfile
import unittest
from pathlib import Path

from app.channel_style_application import ChannelStyleReviewApplication
from app.state import SCHEMA_VERSION, StateStore
from app.telegram import TelegramError
from app.telegram_update_runtime import (
    MAX_TELEGRAM_UPDATE_ATTEMPTS,
    TelegramSafeReviewApplication,
)


class _Telegram:
    def __init__(self, updates):
        self.updates = list(updates)

    def get_updates(self, offset):
        return [item for item in self.updates if int(item.get("update_id", 0)) >= offset]


class _Probe(TelegramSafeReviewApplication):
    def __init__(self, state, updates, fail_ids=None):
        self.state = state
        self.telegram = _Telegram(updates)
        self.fail_ids = set(fail_ids or [])
        self.handled = []
        self.notices = []

    async def handle_message(self, message):
        update_id = int(message["probe_update_id"])
        self.handled.append(update_id)
        if update_id in self.fail_ids:
            raise TelegramError("redacted simulated failure")

    async def handle_callback(self, callback):
        return None

    def _safe_send(self, text):
        self.notices.append(text)


class TelegramUpdateSafetyTests(unittest.TestCase):
    def make_state(self, temp):
        return StateStore(Path(temp) / "state.json")

    def update(self, update_id):
        return {"update_id": update_id, "message": {"probe_update_id": update_id}}

    def test_production_application_uses_safe_polling_runtime(self):
        self.assertTrue(issubclass(ChannelStyleReviewApplication, TelegramSafeReviewApplication))

    def test_state_schema_migrates_and_tracks_failure_without_error_text(self):
        with tempfile.TemporaryDirectory() as temp:
            state = self.make_state(temp)
            self.assertEqual(SCHEMA_VERSION, 3)
            count = state.record_telegram_failure(12, "TelegramError")
            self.assertEqual(count, 1)
            self.assertEqual(state.telegram_failure_count(12), 1)
            self.assertEqual(state.data["telegram_failures"]["12"]["error_type"], "TelegramError")
            self.assertNotIn("message", state.data["telegram_failures"]["12"])

    def test_offset_advances_only_after_successful_handler(self):
        with tempfile.TemporaryDirectory() as temp:
            state = self.make_state(temp)
            app = _Probe(state, [self.update(10), self.update(11)], fail_ids={10})
            asyncio.run(app.process_telegram_updates())
            self.assertEqual(state.telegram_offset, 0)
            self.assertEqual(state.telegram_failure_count(10), 1)
            self.assertEqual(app.handled, [10])

    def test_success_clears_failure_and_advances_in_order(self):
        with tempfile.TemporaryDirectory() as temp:
            state = self.make_state(temp)
            state.record_telegram_failure(10, "TelegramError")
            app = _Probe(state, [self.update(10), self.update(11)])
            asyncio.run(app.process_telegram_updates())
            self.assertEqual(state.telegram_offset, 12)
            self.assertEqual(state.telegram_failure_count(10), 0)
            self.assertEqual(app.handled, [10, 11])

    def test_failure_stops_later_updates_until_retry(self):
        with tempfile.TemporaryDirectory() as temp:
            state = self.make_state(temp)
            app = _Probe(state, [self.update(20), self.update(21)], fail_ids={20})
            asyncio.run(app.process_telegram_updates())
            self.assertEqual(app.handled, [20])
            self.assertEqual(state.telegram_offset, 0)

    def test_poison_update_is_quarantined_after_bounded_attempts(self):
        with tempfile.TemporaryDirectory() as temp:
            state = self.make_state(temp)
            updates = [self.update(30), self.update(31)]
            for _ in range(MAX_TELEGRAM_UPDATE_ATTEMPTS - 1):
                app = _Probe(state, updates, fail_ids={30})
                asyncio.run(app.process_telegram_updates())
                self.assertEqual(state.telegram_offset, 0)
            final = _Probe(state, updates, fail_ids={30})
            asyncio.run(final.process_telegram_updates())
            self.assertEqual(final.handled, [30, 31])
            self.assertEqual(state.telegram_offset, 32)
            self.assertEqual(state.telegram_failure_count(30), 0)

    def test_unsupported_queued_update_type_is_acknowledged_not_poisoned(self):
        with tempfile.TemporaryDirectory() as temp:
            state = self.make_state(temp)
            app = _Probe(state, [{"update_id": 40, "edited_message": {}}])
            asyncio.run(app.process_telegram_updates())
            self.assertEqual(state.telegram_offset, 41)


if __name__ == "__main__":
    unittest.main()
