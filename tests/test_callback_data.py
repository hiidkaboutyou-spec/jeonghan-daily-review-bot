from __future__ import annotations

import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from app.callback_store import CALLBACK_MAX_BYTES, CallbackDataError, CallbackStore
from app.telegram import TelegramBot, inline_keyboard


class CallbackDataTests(unittest.TestCase):
    def store(self, root: str, *, ttl: timedelta = timedelta(days=2)) -> CallbackStore:
        return CallbackStore(Path(root) / "private-review.sqlite3", ttl=ttl)

    def test_exactly_64_ascii_bytes_pass_through(self):
        with tempfile.TemporaryDirectory() as temp:
            store = self.store(temp)
            payload = "a" * 64
            self.assertEqual(store.encode(payload), payload)
            store.close()

    def test_65_ascii_bytes_becomes_opaque_token(self):
        with tempfile.TemporaryDirectory() as temp:
            store = self.store(temp)
            payload = "a" * 65
            token = store.encode(payload)
            self.assertLessEqual(len(token.encode("utf-8")), CALLBACK_MAX_BYTES)
            self.assertNotEqual(token, payload)
            self.assertEqual(store.decode(token), payload)
            store.close()

    def test_multibyte_persian_korean_japanese_and_emoji_use_bytes(self):
        samples = ["سلام" * 20, "정한" * 20, "ジョンハン" * 15, "🪽" * 30]
        with tempfile.TemporaryDirectory() as temp:
            store = self.store(temp)
            for payload in samples:
                self.assertGreater(len(payload.encode("utf-8")), 64)
                token = store.encode(payload)
                self.assertLessEqual(len(token.encode("utf-8")), 64)
                self.assertEqual(store.decode(token), payload)
            store.close()

    def test_short_multibyte_payload_passes_without_reencoding(self):
        with tempfile.TemporaryDirectory() as temp:
            store = self.store(temp)
            payload = "draft:soft:جونگهان"
            self.assertLessEqual(len(payload.encode("utf-8")), 64)
            self.assertEqual(store.encode(payload), payload)
            store.close()

    def test_long_draft_identifier_roundtrips_through_bot_markup(self):
        with tempfile.TemporaryDirectory() as temp:
            store = self.store(temp)
            bot = TelegramBot("redacted", 1, -1, callback_store=store)
            raw = "draft:precise:" + ("x" * 100)
            markup = inline_keyboard([[("test", raw)]])
            encoded = bot._encode_reply_markup(markup)
            sent = encoded["inline_keyboard"][0][0]["callback_data"]
            self.assertLessEqual(len(sent.encode("utf-8")), 64)
            self.assertEqual(bot.decode_callback_data(sent), raw)
            store.close()

    def test_collision_extends_token_instead_of_overwriting_other_payload(self):
        with tempfile.TemporaryDirectory() as temp:
            store = self.store(temp)
            payload = "x" * 100
            token = store.encode(payload)
            # Simulate an existing conflicting row for the first deterministic token.
            store.conn.execute(
                "UPDATE callback_tokens SET payload = ? WHERE token = ?",
                ("different", token),
            )
            store.conn.commit()
            replacement = store.encode(payload)
            self.assertNotEqual(replacement, token)
            self.assertEqual(store.decode(replacement), payload)
            store.close()

    def test_expired_and_unknown_tokens_fail_closed(self):
        with tempfile.TemporaryDirectory() as temp:
            store = self.store(temp, ttl=timedelta(seconds=1))
            start = datetime(2026, 8, 9, 12, 0, tzinfo=timezone.utc)
            token = store.encode("x" * 100, now=start)
            with self.assertRaises(CallbackDataError):
                store.decode(token, now=start + timedelta(seconds=2))
            with self.assertRaises(CallbackDataError):
                store.decode("cb:" + ("0" * 24), now=start)
            store.close()

    def test_malformed_empty_and_overlong_incoming_data_fail(self):
        with tempfile.TemporaryDirectory() as temp:
            store = self.store(temp)
            bot = TelegramBot("redacted", 1, -1, callback_store=store)
            with self.assertRaises(CallbackDataError):
                bot.decode_callback_data("")
            with self.assertRaises(CallbackDataError):
                bot.decode_callback_data("z" * 65)
            store.close()

    def test_no_durable_store_never_silently_truncates(self):
        bot = TelegramBot("redacted", 1, -1)
        raw = "draft:soft:" + ("y" * 100)
        with self.assertRaises(CallbackDataError):
            bot._encode_reply_markup(inline_keyboard([[("test", raw)]]))

    def test_encoded_markup_contains_no_private_payload_text(self):
        with tempfile.TemporaryDirectory() as temp:
            store = self.store(temp)
            bot = TelegramBot("redacted", 1, -1, callback_store=store)
            private_payload = "draft:custom:" + ("private-instruction-" * 10)
            encoded = bot._encode_reply_markup(inline_keyboard([[("test", private_payload)]]))
            serialized = json.dumps(encoded, ensure_ascii=False)
            self.assertNotIn("private-instruction", serialized)
            store.close()


if __name__ == "__main__":
    unittest.main()
