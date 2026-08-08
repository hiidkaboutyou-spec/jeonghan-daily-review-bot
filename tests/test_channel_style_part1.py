from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path


class ShardedCorpusArtifactTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.root = Path(__file__).parents[1]
        cls.corpus_dir = cls.root / "data" / "channel_style"
        cls.manifest = json.loads((cls.corpus_dir / "manifest.json").read_text(encoding="utf-8"))

    def test_manifest_authority_contract(self):
        self.assertEqual(self.manifest["authority_message_count"], 16306)
        self.assertEqual(self.manifest["result_3_contribution"], 15206)
        self.assertEqual(self.manifest["result_4_contribution"], 1100)
        self.assertEqual(self.manifest["chronological_base_weight"], 1.0)
        self.assertEqual(self.manifest["recency_weighting"], "NONE")
        self.assertEqual(self.manifest["date_score_contribution"], 0.0)
        self.assertFalse(self.manifest["text_similarity_deduplication"])

    def test_every_manifest_hash_and_record_contract(self):
        seen = set()
        source_counts = {"result 3": 0, "result 4": 0}
        total = 0
        for shard in self.manifest["shards"]:
            path = self.corpus_dir / shard["filename"]
            raw = path.read_bytes()
            self.assertEqual(hashlib.sha256(raw).hexdigest(), shard["sha256"])
            local = 0
            for line in raw.decode("utf-8").splitlines():
                if not line:
                    continue
                row = json.loads(line)
                self.assertTrue(row["example_id"])
                self.assertNotIn(row["example_id"], seen)
                seen.add(row["example_id"])
                self.assertIn(row["source_export"], source_counts)
                source_counts[row["source_export"]] += 1
                self.assertEqual(row["base_style_weight"], 1.0)
                self.assertTrue(row["text"].strip())
                local += 1
            self.assertEqual(local, shard["example_count"])
            total += local
        self.assertEqual(total, 16306)
        self.assertEqual(source_counts, {"result 3": 15206, "result 4": 1100})

    def test_glossary_exposes_authority(self):
        glossary = json.loads((self.root / "config" / "channel_glossary.json").read_text(encoding="utf-8"))
        self.assertEqual(glossary["channel_style_version"], 1)
        self.assertEqual(glossary["authority_message_count"], 16306)
        self.assertTrue(glossary["categories"])


if __name__ == "__main__":
    unittest.main()
