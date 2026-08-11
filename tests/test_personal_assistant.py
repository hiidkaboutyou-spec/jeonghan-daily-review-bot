from __future__ import annotations

import unittest

from app.personal_assistant import assistant_main_keyboard, parse_assistant_intent


class PersonalAssistantIntentTests(unittest.TestCase):
    def test_assistant_routes_recent_updates_without_commands(self):
        self.assertEqual(parse_assistant_intent("چه خبر؟").kind, "recent2h")
        self.assertEqual(parse_assistant_intent("آپدیت جدید چی اومده").kind, "recent2h")

    def test_assistant_routes_24_hour_source_with_persian_digits(self):
        intent = parse_assistant_intent("۲۴ ساعت @girlsupermodel")
        self.assertEqual(intent.kind, "source24")
        self.assertEqual(intent.argument, "@girlsupermodel")

    def test_assistant_routes_archive_search_and_dates(self):
        intent = parse_assistant_intent("پیدا کن لایوی که داشت بازی می‌کرد")
        self.assertEqual(intent.kind, "search")
        self.assertIn("لایوی", intent.argument)
        self.assertEqual(parse_assistant_intent("2026-07-14").kind, "search")

    def test_assistant_routes_private_workflows(self):
        self.assertEqual(parse_assistant_intent("پیش‌نویس‌ها").kind, "inbox")
        self.assertEqual(parse_assistant_intent("یادآورها").kind, "reminders")
        self.assertEqual(parse_assistant_intent("AO3 رو بیار").kind, "fic")
        self.assertEqual(parse_assistant_intent("بات سالمه؟").kind, "dashboard")

    def test_unknown_plain_text_becomes_safe_archive_search(self):
        intent = parse_assistant_intent("جونگهان با فودن")
        self.assertEqual(intent.kind, "search")
        self.assertEqual(intent.argument, "جونگهان با فودن")

    def test_slash_commands_and_existing_buttons_still_delegate(self):
        self.assertEqual(parse_assistant_intent("/recent2h").kind, "delegate")
        self.assertEqual(parse_assistant_intent("🔎 سرچ آرشیو").kind, "delegate")

    def test_assistant_keyboard_exposes_core_private_actions(self):
        keyboard = assistant_main_keyboard()
        labels = [button["text"] for row in keyboard["keyboard"] for button in row]
        self.assertIn("✨ دستیار من", labels)
        self.assertIn("📥 پیش‌نویس‌ها", labels)
        self.assertIn("🕑 ۲ ساعت اخیر", labels)
        self.assertIn("🗂 ۲۴ ساعت منبع", labels)
        self.assertIn("🔎 سرچ آرشیو", labels)
        self.assertIn("📚 فن‌فیک", labels)
        self.assertIn("⏰ یادآورها", labels)
        self.assertTrue(keyboard["is_persistent"])


if __name__ == "__main__":
    unittest.main()
