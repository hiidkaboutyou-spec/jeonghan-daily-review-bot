from __future__ import annotations

import unittest

from app.gemini_structured import generate_json_v2


class _Writer:
    def __init__(self):
        self.last_diagnostics = {}

    def _model_candidates(self):
        return ["gemini-test"]


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

    def test_one_transport_failure_does_not_disable_later_translations(self):
        writer = _Writer()
        client = _Client(error=TimeoutError("temporary timeout"))
        self.assertIsNone(_call(writer, client))
        self.assertFalse(hasattr(writer, "_gemini_circuit_open"))


if __name__ == "__main__":
    unittest.main()
