from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from app.message_delivery import MessageDeliveryStore
from app.telegram import (
    TELEGRAM_SAFE_TEXT_TARGET,
    TELEGRAM_TEXT_LIMIT,
    TelegramBot,
    TelegramError,
    inline_keyboard,
    split_telegram_text,
    strip_part_label,
)


class _RecordingBot(TelegramBot):
    def __init__(self, store: MessageDeliveryStore | None = None, *, fail_on_call: int | None = None, pacing=0, sleeps=None):
        self.sleeps = [] if sleeps is None else sleeps
        super().__init__(
            "redacted", 1, -1,
            message_delivery_store=store,
            send_pacing_seconds=pacing,
            sleep_fn=self.sleeps.append,
        )
        self.calls: list[tuple[str, dict]] = []
        self.fail_on_call = fail_on_call

    def api(self, method: str, *, data=None, files=None, timeout=60, attempts=3):
        self.calls.append((method, dict(data or {})))
        if self.fail_on_call is not None and len(self.calls) == self.fail_on_call:
            raise TelegramError("simulated transient failure")
        return {"message_id": len(self.calls)}


class _FirstCallRateLimitedBot(_RecordingBot):
    def api(self, method: str, *, data=None, files=None, timeout=60, attempts=3):
        result = super().api(method, data=data, files=files, timeout=timeout, attempts=attempts)
        self._last_api_had_rate_limit = len(self.calls) == 1
        return result


class TelegramLongMessageTests(unittest.TestCase):
    @staticmethod
    def rebuild(parts):
        return "".join(strip_part_label(part) for part in parts)

    def test_short_text_remains_one_unlabeled_message(self):
        self.assertEqual(split_telegram_text("سلام جونگهان 🪽"), ["سلام جونگهان 🪽"])

    def test_exact_boundary_is_one_part(self):
        text = "x" * TELEGRAM_SAFE_TEXT_TARGET
        self.assertEqual(split_telegram_text(text), [text])

    def test_boundary_plus_one_is_lossless(self):
        text = "x" * (TELEGRAM_SAFE_TEXT_TARGET + 1)
        parts = split_telegram_text(text)
        self.assertEqual(self.rebuild(parts), text)
        self.assertTrue(all(1 <= len(part) <= TELEGRAM_TEXT_LIMIT for part in parts))
        self.assertEqual(len(parts), 2)
        self.assertEqual(parts[0][:12], "بخش 1 از 2\n\n")
        self.assertEqual(parts[1][:12], "بخش 2 از 2\n\n")

    def test_dynamic_three_and_more_than_three_parts(self):
        three = split_telegram_text("ژ" * 9000)
        many = split_telegram_text("ب" * 18000)
        self.assertEqual(len(three), 3)
        self.assertGreater(len(many), 3)
        self.assertEqual(self.rebuild(three), "ژ" * 9000)
        self.assertEqual(self.rebuild(many), "ب" * 18000)
        for index, part in enumerate(many, 1):
            self.assertTrue(part.startswith(f"بخش {index} از {len(many)}\n\n"))
            self.assertLessEqual(len(part), TELEGRAM_SAFE_TEXT_TARGET)

    def test_realistic_multilingual_text_is_lossless(self):
        unit = "جونگهان امروز گفت hello 정한 こんにちは 🪽😂 #JEONGHAN https://example.com/a?b=1\n"
        text = unit * 140
        parts = split_telegram_text(text)
        self.assertEqual(self.rebuild(parts), text)
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
        self.assertEqual(self.rebuild([data["text"] for data in sends]), text)

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
            self.assertEqual([data["text"] for _, data in retry.calls], expected_parts[1:])
            store2.close()

    def test_exact_plan_survives_retry_without_replacement(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "private-review.sqlite3"
            first_store = MessageDeliveryStore(path)
            first_text = "متن اصلی. " * 900
            first = _RecordingBot(first_store, fail_on_call=2)
            with self.assertRaises(TelegramError):
                first.send_message(first_text, delivery_key="draft:immutable")
            first_store.close()
            second_store = MessageDeliveryStore(path)
            retry = _RecordingBot(second_store)
            retry.send_message("متن تازه‌ای که نباید جایگزین شود", delivery_key="draft:immutable")
            planned_text, planned_parts = second_store.get_plan("draft:immutable")
            self.assertEqual(planned_text, first_text)
            self.assertEqual(self.rebuild(planned_parts), first_text)
            second_store.close()

    def test_short_configured_pacing_only_between_real_sends(self):
        sleeps = []
        bot = _RecordingBot(pacing=1.1, sleeps=sleeps)
        bot.send_message("ژ" * 9000)
        self.assertEqual(sleeps, [1.1] * (len(bot.calls) - 1))

    def test_rate_limit_wait_replaces_ordinary_pacing(self):
        sleeps = []
        bot = _FirstCallRateLimitedBot(pacing=1.1, sleeps=sleeps)
        bot.send_message("ژ" * 9000)
        self.assertEqual(len(bot.calls), 3)
        self.assertEqual(sleeps, [1.1])

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
            self.assertEqual(self.rebuild([bot.calls[0][1]["text"]] + [data["text"] for _, data in bot.calls[1:]]), text)


if __name__ == "__main__":
    unittest.main()
