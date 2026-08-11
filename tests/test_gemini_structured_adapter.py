from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from app.ai import CaptionWriter
from app.gemini_structured import generate_json_v2


class _Writer:
    def __init__(self, models=None):
        self.last_diagnostics = {}
        self.models = models or ["gemini-test"]

    def _model_candidates(self):
        return self.models


class _Models:
    def __init__(self, response=None, error=None):
        self.response = response
        self.error = error
        self.calls = 0

    def generate_content(self, **kwargs):
        self.calls += 1
        if self.error is not None:
            raise self.error
        return self.response


class _Client:
    def __init__(self, response=None, error=None):
        self.models = _Models(response=response, error=error)


class _Response:
    def __init__(self, *, parsed=None, text=None, candidates=None, prompt_feedback=None):
        self.parsed = parsed
        self.text = text
        self.candidates = candidates or []
        self.prompt_feedback = prompt_feedback


_SCHEMA = {
    "type": "object",
    "required": ["items"],
    "properties": {
        "items": {
            "type": "array",
            "items": {"type": "object"},
        }
    },
}


def _call(writer, client):
    return generate_json_v2(
        writer,
        client,
        "prompt",
        _SCHEMA,
        temperature=0.1,
        purpose="test",
        system_instruction="system",
    )


class GeminiStructuredAdapterTests(unittest.TestCase):
    def test_production_candidates_use_current_stable_lite_with_supported_ga_fallback(self):
        writer = CaptionWriter("key", "gemini-3.5-flash-lite", SimpleNamespace())

        self.assertEqual(
            writer._model_candidates(),
            ["gemini-3.5-flash-lite", "gemini-3.1-flash-lite"],
        )
        self.assertNotIn("gemini-3.1-flash-lite-preview", writer._model_candidates())
        self.assertNotIn("gemini-2.5-flash-lite", writer._model_candidates())
        self.assertNotIn("gemini-2.5-flash", writer._model_candidates())

    def test_translation_requests_disable_adjustable_content_filters(self):
        writer = _Writer()
        client = _Client(_Response(parsed={"items": [{"id": "A", "body": "ترجمه"}]}))

        self.assertIsNotNone(_call(writer, client))

        # _Models does not retain kwargs, so verify the SDK config through a mock.
        generate = Mock(return_value=_Response(parsed={"items": [{"id": "A", "body": "ترجمه"}]}))
        self.assertIsNotNone(
            _call(_Writer(), SimpleNamespace(models=SimpleNamespace(generate_content=generate)))
        )
        config = generate.call_args.kwargs["config"]
        self.assertEqual(len(config.safety_settings), 4)
        self.assertTrue(all(setting.threshold.name == "BLOCK_NONE" for setting in config.safety_settings))

    def test_prefers_sdk_parsed_structured_response(self):
        writer = _Writer()
        expected = {"items": [{"id": "A", "body": "ترجمه"}]}
        result = _call(writer, _Client(_Response(parsed=expected, text="")))
        self.assertEqual(result, expected)
        self.assertEqual(writer.last_diagnostics["structured_response_source"], "response.parsed")

    def test_falls_back_to_json_text(self):
        writer = _Writer()
        result = _call(
            writer,
            _Client(_Response(text='{"items":[{"id":"A","body":"ترجمه"}]}')),
        )
        self.assertEqual(result["items"][0]["id"], "A")
        self.assertEqual(writer.last_diagnostics["structured_response_source"], "response.text")

    def test_empty_structured_response_is_visible_and_fail_closed(self):
        writer = _Writer()
        result = _call(writer, _Client(_Response(parsed=None, text="")))
        self.assertIsNone(result)
        failures = writer.last_diagnostics.get("generation_failures", [])
        self.assertTrue(failures)
        self.assertEqual(failures[-1]["reason"], "empty_structured_response")

    def test_non_object_json_is_not_accepted(self):
        writer = _Writer()
        result = _call(writer, _Client(_Response(text='["not", "an", "object"]')))
        self.assertIsNone(result)
        self.assertEqual(
            writer.last_diagnostics["generation_failures"][-1]["reason"],
            "json_not_nonempty_object",
        )

    def test_quota_failure_opens_process_circuit_and_prevents_request_storm(self):
        writer = _Writer()
        client = _Client(error=RuntimeError("429 RESOURCE_EXHAUSTED quota exceeded"))
        self.assertIsNone(_call(writer, client))
        self.assertEqual(writer._gemini_circuit_open, "quota")
        self.assertEqual(client.models.calls, 1)
        self.assertIsNone(_call(writer, client))
        self.assertEqual(client.models.calls, 1)
        self.assertEqual(writer.last_diagnostics["generation_circuit_open"], "quota")

    def test_quota_retires_one_model_and_uses_one_bounded_free_fallback(self):
        writer = _Writer(["gemini-primary", "gemini-free-fallback"])
        response = _Response(parsed={"items": [{"id": "A", "body": "ترجمه"}]})
        generate = Mock(side_effect=[RuntimeError("429 RESOURCE_EXHAUSTED quota"), response])
        client = SimpleNamespace(models=SimpleNamespace(generate_content=generate))

        result = _call(writer, client)

        self.assertIsNotNone(result)
        self.assertEqual(generate.call_count, 2)
        self.assertEqual(writer._gemini_unavailable_models, {"gemini-primary"})
        self.assertFalse(hasattr(writer, "_gemini_circuit_open"))
        self.assertEqual(
            writer.last_diagnostics["generation_model_failover"]["next"],
            "gemini-free-fallback",
        )

    def test_removed_models_are_retired_once_instead_of_retried_per_item(self):
        writer = _Writer(["removed-primary", "removed-fallback"])
        client = _Client(error=RuntimeError("404 model not found for API version"))

        self.assertIsNone(_call(writer, client))
        self.assertEqual(client.models.calls, 2)
        self.assertEqual(
            writer._gemini_unavailable_models,
            {"removed-primary", "removed-fallback"},
        )
        self.assertEqual(writer._gemini_circuit_open, "models_unavailable")

        self.assertIsNone(_call(writer, client))
        self.assertEqual(client.models.calls, 2)
        self.assertEqual(
            writer.last_diagnostics["generation_circuit_open"],
            "models_unavailable",
        )

    def test_one_transport_failure_does_not_disable_later_translations(self):
        writer = _Writer()
        client = _Client(error=TimeoutError("temporary timeout"))
        self.assertIsNone(_call(writer, client))
        self.assertFalse(hasattr(writer, "_gemini_circuit_open"))

    def test_temporary_503_retries_same_model_once_then_succeeds(self):
        writer = _Writer(["gemini-stable"])
        response = _Response(parsed={"items": [{"id": "A", "body": "ترجمه"}]})
        generate = Mock(side_effect=[RuntimeError("503 UNAVAILABLE high demand"), response])
        client = SimpleNamespace(models=SimpleNamespace(generate_content=generate))

        with patch("app.gemini_structured.time.sleep") as sleep:
            result = _call(writer, client)

        self.assertIsNotNone(result)
        self.assertEqual(generate.call_count, 2)
        sleep.assert_called_once_with(2.0)
        self.assertEqual(
            writer.last_diagnostics["generation_failures"][-1]["reason"],
            "transient_retry:RuntimeError",
        )

    def test_production_pacing_spaces_request_starts_below_provider_rpm(self):
        writer = _Writer()
        writer._gemini_min_request_interval_seconds = 3.5
        response = _Response(parsed={"items": [{"id": "A", "body": "ترجمه"}]})
        client = _Client(response=response)

        with (
            patch("app.gemini_structured.time.monotonic", side_effect=[100.0, 101.0, 103.5]),
            patch("app.gemini_structured.time.sleep") as sleep,
        ):
            self.assertIsNotNone(_call(writer, client))
            self.assertIsNotNone(_call(writer, client))

        sleep.assert_called_once_with(2.5)
        self.assertEqual(client.models.calls, 2)
        self.assertEqual(writer._gemini_next_request_at, 107.0)


if __name__ == "__main__":
    unittest.main()
