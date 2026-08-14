from __future__ import annotations

import unittest
from datetime import date

from app.private_ui import callback_data_lengths, date_picker_keyboard, source_page_keyboard


class PrivateUiTests(unittest.TestCase):
    def test_source_menu_is_paginated_and_has_no_custom_source_escape_hatch(self):
        sources = [{"handle": f"source{i}", "enabled": True} for i in range(14)]
        first, page, pages = source_page_keyboard(sources, 0, page_size=6)
        self.assertEqual((page, pages), (0, 3))
        labels = [button["text"] for row in first["inline_keyboard"] for button in row]
        callbacks = [button["callback_data"] for row in first["inline_keyboard"] for button in row]
        self.assertIn("@source0", labels)
        self.assertNotIn("@source8", labels)
        self.assertIn("بعدی ▶️", labels)
        self.assertNotIn("➕ وارد کردن منبع دیگر", labels)
        self.assertNotIn("source:24:custom", callbacks)

        last, page, pages = source_page_keyboard(sources, 99, page_size=6)
        self.assertEqual(page, 2)
        labels = [button["text"] for row in last["inline_keyboard"] for button in row]
        self.assertIn("@source13", labels)
        self.assertIn("◀️ قبلی", labels)
        self.assertNotIn("➕ وارد کردن منبع دیگر", labels)

    def test_disabled_sources_are_not_offered_for_retrieval(self):
        markup, _, _ = source_page_keyboard(
            [
                {"handle": "enabledsource", "enabled": True},
                {"handle": "disabledsource", "enabled": False},
            ],
            0,
        )
        labels = [button["text"] for row in markup["inline_keyboard"] for button in row]
        self.assertIn("@enabledsource", labels)
        self.assertNotIn("@disabledsource", labels)

    def test_date_picker_has_recent_days_and_navigation(self):
        markup = date_picker_keyboard(date(2026, 8, 7))
        callbacks = [button["callback_data"] for row in markup["inline_keyboard"] for button in row]
        self.assertIn("datepick:20260807", callbacks)
        self.assertIn("datepick:20260801", callbacks)
        self.assertIn("datepage:-7", callbacks)
        self.assertIn("noop:typed-search", callbacks)

    def test_older_date_page_can_navigate_forward(self):
        markup = date_picker_keyboard(date(2026, 8, 7), -14)
        callbacks = [button["callback_data"] for row in markup["inline_keyboard"] for button in row]
        self.assertIn("datepage:-21", callbacks)
        self.assertIn("datepage:-7", callbacks)

    def test_all_callback_data_fit_telegram_limit(self):
        sources = [{"handle": "a" * 15, "enabled": True} for _ in range(9)]
        source_markup, _, _ = source_page_keyboard(sources, 0)
        date_markup = date_picker_keyboard(date(2026, 8, 7))
        self.assertTrue(all(length <= 64 for length in callback_data_lengths(source_markup)))
        self.assertTrue(all(length <= 64 for length in callback_data_lengths(date_markup)))


if __name__ == "__main__":
    unittest.main()
