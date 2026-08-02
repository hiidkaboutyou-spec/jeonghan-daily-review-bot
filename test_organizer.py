from __future__ import annotations

import unittest

from src import organizer


def item(tweet_id: str, date: str, text: str, **extra):
    value = {
        "tweet_id": tweet_id,
        "date": date,
        "text": text,
        "source_username": "source",
        "photo_count": 0,
        "video_count": 0,
    }
    value.update(extra)
    return value


class OrganizerTests(unittest.TestCase):
    def test_live_translations_stay_together_and_oldest_live_is_first(self) -> None:
        pairs = [
            (item("2", "2026-07-14T10:30:00+00:00", "Jeonghan new photo", photo_count=1), {}),
            (item("3", "2026-07-14T11:00:00+00:00", "jeonghan's weverse live third translation"), {}),
            (item("1", "2026-07-14T10:00:00+00:00", "jeonghan's weverse live first translation"), {}),
        ]
        ordered = organizer.organize_record_pairs(pairs)
        self.assertEqual([record["tweet_id"] for record, _ in ordered], ["1", "3", "2"])
        self.assertEqual(ordered[0][0]["group_key"], ordered[1][0]["group_key"])
        self.assertEqual(ordered[0][0]["group_index"], 1)
        self.assertEqual(ordered[1][0]["group_index"], 2)

    def test_same_live_gets_exactly_the_same_theme_header(self) -> None:
        first = item("1", "2026-07-14T10:00:00+00:00", "jeonghan's weverse live")
        second = item("2", "2026-07-14T11:00:00+00:00", "jeonghan's weverse live")
        caption_one, theme_one = organizer.apply_post_theme(
            "ترجمهٔ اول", first, "dialogue", {}
        )
        caption_two, theme_two = organizer.apply_post_theme(
            "ترجمهٔ دوم", second, "dialogue", {}
        )
        self.assertEqual(theme_one, theme_two)
        self.assertEqual(caption_one.splitlines()[0], caption_two.splitlines()[0])
        self.assertTrue(caption_one.startswith("\u200f،،"))

    def test_dialogue_only_replies_in_live_thread_stay_with_the_live(self) -> None:
        pairs = [
            (
                item(
                    "1",
                    "2026-07-14T10:00:00+00:00",
                    "jeonghan's weverse live",
                    conversation_id="thread-7",
                ),
                {},
            ),
            (
                item(
                    "photo",
                    "2026-07-14T10:05:00+00:00",
                    "Jeonghan new photo",
                    photo_count=1,
                    conversation_id="photo-thread",
                ),
                {},
            ),
            (
                item(
                    "2",
                    "2026-07-14T10:10:00+00:00",
                    "🪽: الان رامن درست می‌کنم",
                    conversation_id="thread-7",
                ),
                {},
            ),
        ]
        ordered = organizer.organize_record_pairs(pairs)
        self.assertEqual([record["tweet_id"] for record, _ in ordered], ["1", "2", "photo"])
        self.assertEqual(ordered[0][0]["group_key"], ordered[1][0]["group_key"])
        self.assertEqual(ordered[1][0]["group_kind"], "live")

    def test_tumblr_symbol_before_persian_is_forced_rtl(self) -> None:
        fixed = organizer.ensure_rtl_caption("𖥨᩠ׄ݁⠀متن فارسی")
        self.assertTrue(fixed.startswith("\u200f،، "))
        self.assertIn("متن فارسی", fixed)

    def test_jeonghan_instagram_uses_fixed_channel_theme(self) -> None:
        record = item(
            "1",
            "2026-06-24T10:00:00+00:00",
            "jeonghaniyoo_n instagram story update",
        )
        caption, theme = organizer.apply_post_theme(
            "🪽: فقط منو نگاه کن", record, "comment_or_story", {}
        )
        self.assertIn("jeonghaniyoo_n", caption.splitlines()[0])
        self.assertTrue(theme.startswith("instagram:"))


if __name__ == "__main__":
    unittest.main()
