from __future__ import annotations

import unittest
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import Mock

from app.config import Settings
from app.health_runtime import HealthReviewApplication
from app.models import Update
from app.source_modes import ContentFilter, SourceConfig, SourceMode, SourceModeGate
from app.x_client import XCollector


def _update(author: str, text: str, *, quoted_text: str = "") -> Update:
    return Update(
        id=f"{author}-{len(text)}-{len(quoted_text)}",
        url="",
        author=author,
        author_name=author,
        text=text,
        quoted_text=quoted_text,
        created_at=datetime(2026, 8, 23, tzinfo=timezone.utc),
    )


class SourceModeTests(unittest.TestCase):
    def test_source_config_rejects_unknown_explicit_mode(self):
        with self.assertRaises(ValueError):
            SourceConfig.from_mapping({"handle": "source", "mode": "trust_me"})

    def test_legacy_source_without_mode_remains_full_feed(self):
        source = SourceConfig.from_mapping({"handle": "LegacySource"})
        self.assertEqual(source.handle, "legacysource")
        self.assertIs(source.mode, SourceMode.FULL_FEED)

    def test_content_filter_matches_only_required_names_or_emojis(self):
        content_filter = ContentFilter()
        for text in (
            "JEONGHAN update",
            "Yoon Jeonghan arrived",
            "Hannie posted",
            "Hanie live",
            "Hani update",
            "정한 업데이트",
            "new clip 🪽",
            "new clip 😇",
            "new clip 👼🏻",
            "new clip 👼",
        ):
            with self.subTest(text=text):
                self.assertTrue(content_filter.matches(text))
        for text in ("Joshua update", "seventeen schedule", "notjeonghanaccount"):
            with self.subTest(text=text):
                self.assertFalse(content_filter.matches(text))

    def test_gate_enforces_full_and_filtered_modes_and_quoted_context(self):
        gate = SourceModeGate(
            [
                {"handle": "type_a", "mode": "full_feed"},
                {"handle": "type_b", "mode": "keyword_filter"},
            ]
        )
        self.assertTrue(gate.should_accept_post("@TYPE_A", "unrelated member update"))
        self.assertFalse(gate.should_accept_post("type_b", "unrelated member update"))
        self.assertTrue(gate.should_accept_post("type_b", "new clip 😇"))
        self.assertTrue(gate.should_accept_post("type_b", "quoted", "Yoon Jeonghan update"))
        self.assertFalse(gate.should_accept_post("unknown", "JEONGHAN"))

    def test_collector_boundary_applies_mode_after_configured_source_gate(self):
        collector = XCollector(
            {},
            [
                {"handle": "type_a", "mode": "full_feed"},
                {"handle": "type_b", "mode": "keyword_filter"},
            ],
            [],
        )
        updates = [
            _update("type_a", "other member"),
            _update("type_b", "other member"),
            _update("type_b", "Hannie update"),
            _update("external", "JEONGHAN update"),
        ]
        self.assertEqual(
            [(item.author, item.text) for item in collector._filter_relevant(updates)],
            [("type_a", "other member"), ("type_b", "Hannie update")],
        )

    def test_project_configuration_loads_priority_sources_with_explicit_modes(self):
        settings = Settings.load(require_secrets=False)
        self.assertEqual(len(settings.sources), 33)
        by_handle = {item["handle"]: item for item in settings.sources}
        self.assertEqual(settings.sources[0]["handle"], "hanniezones")
        self.assertEqual(by_handle["hanniezones"]["mode"], "full_feed")
        self.assertTrue(by_handle["hanniezones"]["include_replies"])
        self.assertEqual(by_handle["hanniezones"]["priority"], 1)
        self.assertEqual(by_handle["hani_berry_1004"]["mode"], "full_feed")
        self.assertEqual(by_handle["pledis_17"]["mode"], "keyword_filter")
        self.assertEqual(by_handle["ayecheol"]["mode"], "keyword_filter")
        self.assertEqual(
            by_handle["ayecheol"]["keywords"],
            ["jeonghan", "Hannie", "Hanie", "Hani", "정한", "Yoon Jeonghan"],
        )
        self.assertEqual(settings.validate_files(), [])


class HealthKeyboardRegressionTests(unittest.TestCase):
    def application(self):
        app = object.__new__(HealthReviewApplication)
        app.settings = SimpleNamespace(
            sources=[{"handle": "only", "enabled": True}],
            timezone=timezone.utc,
        )
        app.health = SimpleNamespace(list_all=lambda: [])
        app.telegram = SimpleNamespace(send_message=Mock(), edit_message_text=Mock())
        return app

    def test_single_page_new_message_uses_main_keyboard_not_empty_inline_keyboard(self):
        app = self.application()
        app.show_health()
        markup = app.telegram.send_message.call_args.kwargs["reply_markup"]
        self.assertIn("keyboard", markup)
        self.assertNotIn("inline_keyboard", markup)

    def test_single_page_edit_uses_empty_inline_keyboard_to_remove_old_buttons(self):
        app = self.application()
        app.show_health(message_id=42)
        markup = app.telegram.edit_message_text.call_args.kwargs["reply_markup"]
        self.assertEqual(markup, {"inline_keyboard": []})


if __name__ == "__main__":
    unittest.main()
