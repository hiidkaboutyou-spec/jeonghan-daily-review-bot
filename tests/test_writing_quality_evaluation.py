from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.fic_digest import Fic
from tools import run_writing_quality_evaluation as evaluation


class WritingQualityEvaluationTests(unittest.TestCase):
    def test_real_fic_loader_retries_transient_ao3_failures(self):
        fic = Fic(
            title="Test",
            url="https://archiveofourown.org/works/123",
            author="author",
            summary="summary",
            relationships=["Yoon Jeonghan/Choi Seungcheol"],
        )
        with tempfile.TemporaryDirectory() as directory:
            cohort = Path(directory) / "works.json"
            cohort.write_text(
                json.dumps([{"work_id": "123", "coverage": ["transient"]}]),
                encoding="utf-8",
            )
            with (
                patch.object(evaluation, "FIC_WORKS_PATH", cohort),
                patch.object(evaluation, "fetch_ao3_work", side_effect=[None, fic]) as fetch,
                patch.object(evaluation.time, "sleep") as sleep,
            ):
                fics, coverage = evaluation._load_real_fics()

        self.assertEqual(fics, [fic])
        self.assertEqual(coverage, {"123": ["transient"]})
        self.assertEqual(fetch.call_count, 2)
        sleep.assert_called_once_with(5.0)


if __name__ == "__main__":
    unittest.main()
