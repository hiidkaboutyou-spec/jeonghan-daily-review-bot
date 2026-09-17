from __future__ import annotations

import json
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from types import ModuleType, SimpleNamespace
from unittest.mock import Mock, patch

from app import channel_human_quality_gate_runtime as humanfix
from app.ai import GroupCopy
from app.models import EventGroup, Update
from tools import run_translation_benchmark as benchmark


class HumanGateRuntimeCoverageTests(unittest.TestCase):
    @staticmethod
    def _group(*texts: str) -> EventGroup:
        updates = [
            Update(
                id=f"u{index}",
                url="",
                author="jeonghan",
                author_name="Jeonghan",
                text=text,
                created_at=datetime(2026, 9, 17, 12, index, tzinfo=timezone.utc),
            )
            for index, text in enumerate(texts, start=1)
        ]
        return EventGroup(key="human-gate", category="general", title="Human gate", updates=updates)

    def test_human_polish_returns_current_when_client_is_unavailable(self) -> None:
        group = self._group("Did you eat yet?")
        current = GroupCopy(group.title, group.category, {"u1": "چیزی خوردی؟"})
        writer = SimpleNamespace(_client_or_none=lambda: None, last_diagnostics={})

        result = humanfix.ChannelStyleCaptionWriter._human_polish(writer, group, current)

        self.assertIs(result, current)
        self.assertEqual(writer.last_diagnostics, {})

    def test_human_polish_skips_generator_when_no_item_needs_polish(self) -> None:
        group = self._group("Jeonghan posted a photo.")
        current = GroupCopy(group.title, group.category, {"u1": "جونگهان یه عکس گذاشت."})
        generate = Mock()
        writer = SimpleNamespace(
            _client_or_none=lambda: object(),
            _generate_json_v2=generate,
            last_diagnostics={},
        )

        with patch.object(humanfix, "_needs_human_polish", return_value=False):
            result = humanfix.ChannelStyleCaptionWriter._human_polish(writer, group, current)

        self.assertIs(result, current)
        generate.assert_not_called()

    def test_human_polish_v2_accepts_safe_candidate_and_ignores_low_information_only(self) -> None:
        group = self._group("Did you eat yet?")
        current = GroupCopy(
            group.title,
            group.category,
            {"u1": "⚠️ نیاز به بازبینی دستی\n\nتا حالا خوردی؟"},
        )
        generate = Mock(return_value={"items": [{"id": "u1", "body": "unused"}]})
        writer = SimpleNamespace(
            _client_or_none=lambda: object(),
            _generate_json_v2=generate,
            last_diagnostics={},
        )

        with (
            patch.object(humanfix, "_needs_human_polish", return_value=True),
            patch.object(humanfix.translation, "_parse_bodies", return_value={"u1": "⚠️ نیاز به بازبینی دستی\n\nچیزی خوردی؟"}),
            patch.object(humanfix, "verify_hard_facts", return_value=[]),
            patch.object(
                humanfix,
                "semantic_quality_failures",
                return_value=["low-information source needs editorial judgment"],
            ),
        ):
            result = humanfix.ChannelStyleCaptionWriter._human_polish(writer, group, current)

        self.assertEqual(result.bodies["u1"], "چیزی خوردی؟")
        self.assertEqual(writer.last_diagnostics["human_quality_polish"], "applied")
        generate.assert_called_once()
        kwargs = generate.call_args.kwargs
        self.assertEqual(kwargs["purpose"], "human quality polish")
        self.assertEqual(kwargs["temperature"], 0.08)
        self.assertIn("SOURCE تنها مرجع حقیقت است", kwargs["system_instruction"])

    def test_human_polish_rejects_hard_fact_and_semantic_quality_failures(self) -> None:
        group = self._group("First source", "Second source")
        current = GroupCopy(
            group.title,
            group.category,
            {"u1": "فعلی اول", "u2": "فعلی دوم"},
        )
        writer = SimpleNamespace(
            _client_or_none=lambda: object(),
            _generate_json_v2=Mock(return_value={"items": []}),
            last_diagnostics={},
        )

        def fact_failures(_source: str, candidate: str, _analysis) -> list[str]:
            return ["fact drift"] if candidate == "کاندید اول" else []

        def quality_failures(_item, candidate: str) -> list[str]:
            return ["meaning loss"] if candidate == "کاندید دوم" else []

        with (
            patch.object(humanfix, "_needs_human_polish", return_value=True),
            patch.object(
                humanfix.translation,
                "_parse_bodies",
                return_value={"u1": "کاندید اول", "u2": "کاندید دوم"},
            ),
            patch.object(humanfix, "verify_hard_facts", side_effect=fact_failures),
            patch.object(humanfix, "semantic_quality_failures", side_effect=quality_failures),
        ):
            result = humanfix.ChannelStyleCaptionWriter._human_polish(writer, group, current)

        self.assertEqual(result.bodies, current.bodies)
        self.assertEqual(writer.last_diagnostics["human_quality_polish"], "applied")

    def test_human_polish_legacy_generator_and_parse_failure_keep_current(self) -> None:
        group = self._group("Did you eat yet?")
        current = GroupCopy(group.title, group.category, {"u1": "تا حالا خوردی؟"})
        generate = Mock(return_value={"items": []})
        writer = SimpleNamespace(
            _client_or_none=lambda: object(),
            _generate_json=generate,
            last_diagnostics={},
        )

        with (
            patch.object(humanfix, "_needs_human_polish", return_value=True),
            patch.object(humanfix.translation, "_parse_bodies", return_value=None),
        ):
            result = humanfix.ChannelStyleCaptionWriter._human_polish(writer, group, current)

        self.assertIs(result, current)
        generate.assert_called_once()
        self.assertEqual(generate.call_args.kwargs["purpose"], "human quality polish")

    def test_invalidated_resume_cursor_retains_only_latest_case_identity(self) -> None:
        self.assertEqual(humanfix.invalidated_resume_cursor({}), [])
        self.assertEqual(humanfix.invalidated_resume_cursor({"cases": ["bad"]}), [])
        self.assertEqual(humanfix.invalidated_resume_cursor({"cases": [{}]}), [])

        result = humanfix.invalidated_resume_cursor(
            {
                "cases": [
                    {"case_id": "B01", "verifier_result": "PASS", "body": "stale"},
                    {"case_id": "B02", "verifier_result": "PASS", "body": "also stale"},
                ]
            }
        )

        self.assertEqual(
            result,
            [
                {
                    "case_id": "B02",
                    "output_mode": "invalidated_resume_cursor",
                    "verifier_result": "INVALIDATED",
                    "verifier_failures": ["production_writer_fingerprint_changed"],
                }
            ],
        )

    def test_cached_benchmark_patch_is_fingerprint_aware_and_idempotent(self) -> None:
        cached_name = "tools.run_translation_benchmark_cached"
        old_cached = sys.modules.get(cached_name)
        old_load = benchmark._load_resume
        old_write = benchmark._write_checkpoint
        had_marker = hasattr(benchmark, "_human_gate_fingerprint_patch")
        old_marker = getattr(benchmark, "_human_gate_fingerprint_patch", None)

        original_load = Mock(return_value=[{"case_id": "fresh"}])

        def original_write(_output_path: Path, **kwargs):
            return {"cases": kwargs.get("cases", [])}

        try:
            sys.modules[cached_name] = ModuleType(cached_name)
            benchmark._load_resume = original_load
            benchmark._write_checkpoint = original_write
            if hasattr(benchmark, "_human_gate_fingerprint_patch"):
                delattr(benchmark, "_human_gate_fingerprint_patch")

            humanfix._patch_cached_benchmark_resume()
            wrapped_load = benchmark._load_resume
            wrapped_write = benchmark._write_checkpoint
            self.assertTrue(benchmark._human_gate_fingerprint_patch)

            with tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                missing = root / "missing.json"
                self.assertEqual(wrapped_load(missing), [])

                invalid = root / "invalid.json"
                invalid.write_text("{", encoding="utf-8")
                self.assertEqual(wrapped_load(invalid), [])

                stale = root / "stale.json"
                stale.write_text(
                    json.dumps(
                        {
                            "production_writer_fingerprint": "old-fingerprint",
                            "cases": [{"case_id": "B01", "verifier_result": "PASS"}],
                        }
                    ),
                    encoding="utf-8",
                )
                stale_result = wrapped_load(stale)
                self.assertEqual(stale_result[0]["case_id"], "B01")
                self.assertEqual(stale_result[0]["verifier_result"], "INVALIDATED")

                fresh = root / "fresh.json"
                fresh.write_text(
                    json.dumps(
                        {
                            "production_writer_fingerprint": humanfix.HUMAN_GATE_FINGERPRINT,
                            "cases": [],
                        }
                    ),
                    encoding="utf-8",
                )
                self.assertEqual(wrapped_load(fresh), [{"case_id": "fresh"}])
                original_load.assert_called_once_with(fresh)

                checkpoint = root / "checkpoint.json"
                payload = wrapped_write(checkpoint, cases=[{"case_id": "B02"}])
                self.assertEqual(
                    payload["production_writer_fingerprint"],
                    humanfix.HUMAN_GATE_FINGERPRINT,
                )
                persisted = json.loads(checkpoint.read_text(encoding="utf-8"))
                self.assertEqual(
                    persisted["production_writer_fingerprint"],
                    humanfix.HUMAN_GATE_FINGERPRINT,
                )

            humanfix._patch_cached_benchmark_resume()
            self.assertIs(benchmark._load_resume, wrapped_load)
            self.assertIs(benchmark._write_checkpoint, wrapped_write)
        finally:
            benchmark._load_resume = old_load
            benchmark._write_checkpoint = old_write
            if had_marker:
                benchmark._human_gate_fingerprint_patch = old_marker
            elif hasattr(benchmark, "_human_gate_fingerprint_patch"):
                delattr(benchmark, "_human_gate_fingerprint_patch")
            if old_cached is None:
                sys.modules.pop(cached_name, None)
            else:
                sys.modules[cached_name] = old_cached


if __name__ == "__main__":
    unittest.main()
