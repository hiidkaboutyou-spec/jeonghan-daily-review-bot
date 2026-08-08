from __future__ import annotations

import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from app.ai import CaptionWriter, GroupCopy
from app.channel_style_application import ChannelStyleReviewApplication
from app.channel_style_safety import validate_production_style_memory
from app.channel_translation import ChannelStyleCaptionWriter
from app.models import EventGroup, Update
from app.style import StyleMemory


ROOT = Path(__file__).parents[1]


class _FakeMemory:
    def __init__(self):
        self.profile = {"register": {}}
        self.glossary = {
            "categories": {
                "brands": [{"canonical_form": "GUCCI", "aliases": ["Gucci", "گوچی"]}],
                "member_names": [{"canonical_form": "Joshua", "aliases": ["جاشوآ"]}],
            }
        }

    def retrieve_examples(self, neutral, analysis, limit=8, **kwargs):
        return []

    def relevant_glossary(self, source, neutral):
        return []


def _group(text: str = "جونگهان امروز اومد.", *, update_id: str = "1") -> EventGroup:
    update = Update(
        id=update_id,
        url=f"https://x.com/source/status/{update_id}",
        author="source",
        author_name="source",
        text=text,
        created_at=datetime(2026, 8, 8, tzinfo=timezone.utc),
        lang="fa",
    )
    return EventGroup(key="event", category="general", title="title", updates=[update])


class ProductionWriterWiringTests(unittest.TestCase):
    def test_real_production_application_primary_writer_is_channel_style_writer(self):
        memory = _FakeMemory()

        def fake_parent_init(app, settings):
            app.memory = memory
            app.writer = CaptionWriter(settings.gemini_api_key, settings.gemini_model, memory)

        settings = SimpleNamespace(gemini_api_key="", gemini_model="test")
        with patch("app.channel_style_application.ReminderReviewApplication.__init__", fake_parent_init), patch(
            "app.channel_style_application.validate_production_style_memory",
            return_value=(True, "", 16306),
        ):
            app = ChannelStyleReviewApplication(settings)

        self.assertIs(type(app.writer), ChannelStyleCaptionWriter)
        self.assertIs(type(app.legacy_writer), CaptionWriter)
        self.assertIsNot(app.writer, app.legacy_writer)
        self.assertTrue(app.channel_style_enabled)
        self.assertEqual(app.channel_style_indexed_examples, 16306)

    def test_invalid_style_artifacts_keep_legacy_writer(self):
        memory = _FakeMemory()

        def fake_parent_init(app, settings):
            app.memory = memory
            app.writer = CaptionWriter(settings.gemini_api_key, settings.gemini_model, memory)

        settings = SimpleNamespace(gemini_api_key="", gemini_model="test")
        with patch("app.channel_style_application.ReminderReviewApplication.__init__", fake_parent_init), patch(
            "app.channel_style_application.validate_production_style_memory",
            return_value=(False, "style_shard_hash_mismatch", 0),
        ):
            app = ChannelStyleReviewApplication(settings)

        self.assertIs(type(app.writer), CaptionWriter)
        self.assertFalse(app.channel_style_enabled)
        self.assertEqual(app.channel_style_error, "style_shard_hash_mismatch")

    def test_production_pipeline_order(self):
        memory = _FakeMemory()
        events = []

        class Probe(ChannelStyleCaptionWriter):
            def _client_or_none(self):
                return object()

            def _neutral_group(self, group, analysis, client):
                events.append("neutral")
                return GroupCopy(group.title, group.category, {group.updates[0].id: "ترجمه خنثی"})

            def _style_group(self, group, neutral, analysis, examples, glossary, mode, client):
                events.append("style")
                return GroupCopy(group.title, group.category, {group.updates[0].id: "ترجمه خنثی"})

            def _verify_and_repair(self, group, neutral, styled, analysis, client):
                events.append("verify")
                return styled

        writer = Probe("key", "model", memory)
        import app.channel_translation as module
        real_analyze = module.analyze_source

        def traced_analyze(*args, **kwargs):
            if "classify" not in events:
                events.append("classify")
            return real_analyze(*args, **kwargs)

        def retrieve(*args, **kwargs):
            events.append("retrieve")
            return []

        def glossary(*args, **kwargs):
            events.append("glossary")
            return []

        memory.retrieve_examples = retrieve
        memory.relevant_glossary = glossary
        with patch("app.channel_translation.analyze_source", side_effect=traced_analyze):
            writer.write_group(_group())

        self.assertEqual(events[:6], ["neutral", "classify", "retrieve", "glossary", "style", "verify"])


class SafeFallbackTests(unittest.TestCase):
    def test_gemini_unavailable_returns_neutral(self):
        memory = _FakeMemory()

        class Probe(ChannelStyleCaptionWriter):
            def _client_or_none(self):
                return None

            def _neutral_group(self, group, analysis, client):
                return GroupCopy(group.title, group.category, {"1": "ترجمه خنثی"})

        output = Probe("", "model", memory).write_group(_group())
        self.assertEqual(output.bodies["1"], "ترجمه خنثی")

    def test_style_transfer_or_gemini_error_returns_neutral(self):
        memory = _FakeMemory()

        class Probe(ChannelStyleCaptionWriter):
            def _client_or_none(self):
                return object()

            def _neutral_group(self, group, analysis, client):
                return GroupCopy(group.title, group.category, {"1": "ترجمه خنثی"})

            def _style_group(self, *args, **kwargs):
                return None

        output = Probe("key", "model", memory).write_group(_group())
        self.assertEqual(output.bodies["1"], "ترجمه خنثی")

    def test_neutral_translation_exception_preserves_source(self):
        memory = _FakeMemory()

        class Probe(ChannelStyleCaptionWriter):
            def _client_or_none(self):
                return None

            def _neutral_group(self, *args, **kwargs):
                raise RuntimeError("token=secret")

        source = "SOURCE MUST SURVIVE"
        output = Probe("", "model", memory).write_group(_group(source))
        self.assertEqual(output.bodies["1"], source)

    def test_verifier_exception_does_not_crash(self):
        memory = _FakeMemory()

        class Probe(ChannelStyleCaptionWriter):
            def _client_or_none(self):
                return object()

            def _neutral_group(self, group, analysis, client):
                return GroupCopy(group.title, group.category, {"1": "جونگهان امروز اومد."})

            def _style_group(self, group, neutral, analysis, examples, glossary, mode, client):
                return GroupCopy(group.title, group.category, {"1": "جونگهان امروز اومد."})

            def _verify_and_repair(self, *args, **kwargs):
                raise RuntimeError("verifier exploded")

        output = Probe("key", "model", memory).write_group(_group())
        self.assertEqual(output.bodies["1"], "جونگهان امروز اومد.")

    def test_historical_facts_cannot_leak_from_style_output(self):
        memory = _FakeMemory()

        class Probe(ChannelStyleCaptionWriter):
            def _client_or_none(self):
                return object()

            def _neutral_group(self, group, analysis, client):
                return GroupCopy(group.title, group.category, {"1": "جونگهان امروز اومد."})

            def _style_group(self, group, neutral, analysis, examples, glossary, mode, client):
                return GroupCopy(group.title, group.category, {"1": "جونگهان امروز اومد؛ GUCCI 2030 با جاشوآ."})

            def _verify_and_repair(self, *args, **kwargs):
                raise AssertionError("fact leak must be rejected before verifier")

        writer = Probe("key", "model", memory)
        output = writer.write_group(_group())
        self.assertEqual(output.bodies["1"], "جونگهان امروز اومد.")
        self.assertEqual(writer.last_diagnostics.get("fallback"), "fact_leak_guard_neutral")


class CorpusAndFtsTests(unittest.TestCase):
    def test_fresh_fts_initializes_all_16306_and_rebuild_recovers(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "style.sqlite3"
            memory = StyleMemory(ROOT, db_path=db)
            count = memory.conn.execute("SELECT count(*) FROM channel_style_examples").fetchone()[0]
            fts = memory.conn.execute("SELECT count(*) FROM channel_style_fts").fetchone()[0]
            self.assertEqual((count, fts), (16306, 16306))

            memory.conn.execute("DROP TABLE channel_style_fts")
            memory.conn.commit()
            rebuilt = memory.rebuild_from_derived_corpus()
            self.assertEqual(rebuilt, 16306)
            self.assertEqual(memory.conn.execute("SELECT count(*) FROM channel_style_fts").fetchone()[0], 16306)
            memory.conn.close()

    def test_missing_or_corrupt_fts_is_rebuilt_on_next_initialization(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "style.sqlite3"
            first = StyleMemory(ROOT, db_path=db)
            first.conn.execute("DELETE FROM channel_style_fts WHERE rowid IN (SELECT rowid FROM channel_style_fts LIMIT 5)")
            first.conn.commit()
            first.conn.close()

            second = StyleMemory(ROOT, db_path=db)
            self.assertEqual(second.conn.execute("SELECT count(*) FROM channel_style_fts").fetchone()[0], 16306)
            second.conn.close()

    def test_runtime_manifest_hash_validation_and_malformed_manifest_fail_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "data" / "channel_style").mkdir(parents=True)
            (root / "config").mkdir()
            (root / "data" / "channel_style" / "manifest.json").write_text("{bad json", encoding="utf-8")
            ok, reason, count = validate_production_style_memory(SimpleNamespace(), root)
            self.assertFalse(ok)
            self.assertEqual(count, 0)
            self.assertTrue(reason.startswith("style_artifact_"))

    def test_date_is_not_used_in_ranking_contract(self):
        manifest = json.loads((ROOT / "data" / "channel_style" / "manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(manifest["recency_weighting"], "NONE")
        self.assertEqual(manifest["date_score_contribution"], 0.0)


class PrivateReviewBoundaryTests(unittest.TestCase):
    def test_part2_adds_no_public_publishing_path(self):
        paths = [
            ROOT / "app" / "channel_style_application.py",
            ROOT / "app" / "channel_translation.py",
            ROOT / "app" / "channel_style_safety.py",
        ]
        text = "\n".join(path.read_text(encoding="utf-8") for path in paths)
        for forbidden in ("send_to_channel", "autopublish", "publish_button", "channel_admin_permissions"):
            self.assertNotIn(forbidden, text)


if __name__ == "__main__":
    unittest.main()
