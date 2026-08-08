from __future__ import annotations

import importlib.util
import json
import tempfile
import unittest
from pathlib import Path

SPEC = importlib.util.spec_from_file_location("builder", Path(__file__).parents[1] / "tools" / "build_channel_style_corpus.py")
builder = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(builder)


class CorpusBuilderTests(unittest.TestCase):
    def test_plain_string_and_entity_array(self):
        self.assertEqual(builder.visible_text("abc"), "abc")
        self.assertEqual(builder.visible_text(["a", {"type": "bold", "text": "b"}, "c"]), "abc")

    def test_truncated_export_recovers_complete_objects_only(self):
        raw = '{"name":"x","type":"public_channel","id":1,"messages":[{"id":1,"type":"message","text":"ok"},{"id":2,"type":"message","text":["a",{"type":"bold","text":"b"}]},{"id":3,"type":"message","text":"broken"'
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "result 4.json"
            p.write_text(raw, encoding="utf-8")
            meta, msgs, truncated = builder.load_export(p)
            self.assertTrue(truncated)
            self.assertEqual(meta["id"], 1)
            self.assertEqual([m["id"] for m in msgs], [1, 2])

    def test_runtime_row_carries_authority_fields(self):
        row = builder.make_row("1", {"id": 10, "type": "message", "text": "hello", "date": "2026-01-01"}, "result 3")
        self.assertEqual(row["example_id"], "1:10")
        self.assertEqual(row["source_export"], "result 3")
        self.assertEqual(row["base_style_weight"], 1.0)
        self.assertEqual(row["text"], "hello")

    def test_manifest_shards_are_plain_utf8_and_deterministic(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            export = root / "result 3.json"
            payload = {"name": "x", "id": 1, "messages": [
                {"id": 2, "type": "message", "text": "second", "date": "2026-01-02"},
                {"id": 1, "type": "message", "text": "first", "date": "2026-01-01"},
            ]}
            export.write_text(json.dumps(payload), encoding="utf-8")
            self.assertEqual(builder.source_label(export), "result 3")
            rows = [builder.make_row("1", m, "result 3") for m in payload["messages"]]
            rows.sort(key=lambda r: int(r["message_id"]))
            text = "".join(json.dumps(r, ensure_ascii=False, separators=(",", ":")) + "\n" for r in rows)
            self.assertTrue(text.startswith('{"version":1,"example_id":"1:1"'))
            self.assertNotIn("base64", text.lower())


if __name__ == "__main__":
    unittest.main()
