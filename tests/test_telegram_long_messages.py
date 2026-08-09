from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from app.message_delivery import MessageDeliveryStore
from app.telegram import TELEGRAM_TEXT_LIMIT, TelegramBot, TelegramError, inline_keyboard, split_telegram_text


class _RecordingBot(TelegramBot):
    def __init__(self, store: MessageDeliveryStore | None = None, *, fail_on_call: int | None = None):
        super().__init__("redacted", 1, -1, message_delivery_store=store)
        self.calls: list[tuple[str, dict]] = []
        self.fail_on_call = fail_on_call

    def api(self, method: str, *, data=None, files=None, timeout=60, attempts=3):
        self.calls.append((method, dict(data or {})))
        if self.fail_on_call is not None and len(self.calls) == self.fail_on_call:
            raise TelegramError("simulated transient failure")
        return {"message_id": len(self.calls)}


class TelegramLongMessageTests(unittest.TestCase):
    def test_exact_boundary_is_one_part(self):
        text = "x" * TELEGRAM_TEXT_LIMIT
        self.assertEqual(split_telegram_text(text), [text])

    def test_boundary_plus_one_is_lossless(self):
        text = "x" * (TELEGRAM_TEXT_LIMIT + 1)
        parts = split_telegram_text(text)
        self.assertEqual("".join(parts), text)
        self.assertTrue(all(1 <= len(part) <= TELEGRAM_TEXT_LIMIT for part in parts))
        self.assertEqual(len(parts), 2)

    def test_realistic_multilingual_text_is_lossless(self):
        unit = "جونگهان امروز گفت hello 정한 こんにちは 🪽😂 #JEONGHAN https://example.com/a?b=1\n"
        text = unit * 140
        parts = split_telegram_text(text)
        self.assertEqual("".join(parts), text)
        self.assertTrue(all(len(part) <= TELEGRAM_TEXT_LIMIT for part in parts))

    def test_keyboard_is_attached_only_to_final_part(self):
        bot = _RecordingBot()
        text = ("سلام دنیا\n" * 600)
        markup = inline_keyboard([[("done", "cmd:done")]])
        bot.send_message(text, reply_markup=markup)
        sends = [data for method, data in bot.calls if method == "sendMessage"]
        self.assertGreater(len(sends), 1)
        for data in sends[:-1]:
            self.assertNotIn("reply_markup", data)
        self.assertIn("reply_markup", sends[-1])
        self.assertEqual(json.loads(sends[-1]["reply_markup"])["inline_keyboard"][0][0]["callback_data"], "cmd:done")
        self.assertEqual("".join(data["text"] for data in sends), text)

    def test_partial_failure_retry_skips_confirmed_parts(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "private-review.sqlite3"
            store1 = MessageDeliveryStore(path)
            text = "الف " * 3000
            first = _RecordingBot(store1, fail_on_call=2)
            with self.assertRaises(TelegramError):
                first.send_message(text, delivery_key="draft:stable")
            self.assertEqual(len(first.calls), 2)
            store1.close()

            store2 = MessageDeliveryStore(path)
            retry = _RecordingBot(store2)
            retry.send_message(text, delivery_key="draft:stable")
            expected_parts = split_telegram_text(text)
            # The first part was already confirmed before the simulated failure.
            self.assertEqual(len(retry.calls), len(expected_parts) - 1)
            self.assertEqual("".join(data["text"] for _, data in retry.calls), "".join(expected_parts[1:]))
            store2.close()

    def test_editing_long_text_uses_edit_then_ordered_tail_messages(self):
        with tempfile.TemporaryDirectory() as temp:
            bot = _RecordingBot(MessageDeliveryStore(Path(temp) / "private-review.sqlite3"))
            text = "日本語 فارسی 한국어 emoji🪽\n" * 300
            bot.edit_message_text(42, text, reply_markup=inline_keyboard([[("back", "cmd:back")]]))
            self.assertEqual(bot.calls[0][0], "editMessageText")
            if len(bot.calls) > 1:
                self.assertTrue(all(method == "sendMessage" for method, _ in bot.calls[1:]))
                self.assertNotIn("reply_markup", bot.calls[0][1])
                self.assertIn("reply_markup", bot.calls[-1][1])
            rebuilt = bot.calls[0][1]["text"] + "".join(data["text"] for _, data in bot.calls[1:])
            self.assertEqual(rebuilt, text)


if __name__ == "__main__":
    unittest.main()
