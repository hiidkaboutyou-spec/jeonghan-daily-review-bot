from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from hypothesis import given, settings
from hypothesis import strategies as st

from app.callback_store import CALLBACK_MAX_BYTES, CallbackStore
from app.telegram import (
    TELEGRAM_TEXT_LIMIT,
    split_telegram_text,
    strip_part_label,
)


UNICODE_TEXT = st.text(
    alphabet=st.characters(blacklist_categories=("Cs",)),
    min_size=1,
    max_size=12_000,
)

LONG_CALLBACK_TEXT = st.text(
    alphabet=st.characters(blacklist_categories=("Cs",)),
    min_size=65,
    max_size=320,
)


class TelegramPropertyInvariantTests(unittest.TestCase):
    @given(
        text=UNICODE_TEXT,
        limit=st.integers(min_value=64, max_value=TELEGRAM_TEXT_LIMIT),
    )
    @settings(max_examples=100, deadline=None)
    def test_split_telegram_text_is_lossless_and_bounded(self, text: str, limit: int) -> None:
        parts = split_telegram_text(text, limit=limit)

        self.assertTrue(parts)
        self.assertTrue(all(1 <= len(part) <= limit for part in parts))

        if len(parts) == 1:
            self.assertEqual(parts[0], text)
            return

        rebuilt = "".join(strip_part_label(part) for part in parts)
        self.assertEqual(rebuilt, text)
        for index, part in enumerate(parts, start=1):
            self.assertTrue(part.startswith(f"بخش {index} از {len(parts)}\n\n"))

    @given(payload=LONG_CALLBACK_TEXT)
    @settings(max_examples=80, deadline=None)
    def test_long_callback_payload_roundtrips_through_durable_store(self, payload: str) -> None:
        self.assertGreater(len(payload.encode("utf-8")), CALLBACK_MAX_BYTES)
        with tempfile.TemporaryDirectory() as temp:
            store = CallbackStore(Path(temp) / "private-review.sqlite3")
            try:
                token = store.encode(payload)
                self.assertNotEqual(token, payload)
                self.assertLessEqual(len(token.encode("utf-8")), CALLBACK_MAX_BYTES)
                self.assertEqual(store.decode(token), payload)
            finally:
                store.close()


if __name__ == "__main__":
    unittest.main()
