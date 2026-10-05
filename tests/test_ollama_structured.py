from __future__ import annotations

import os
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from app.ollama_structured import (
    OllamaStructuredClient,
    configured_translation_provider,
    generate_json_v2,
    install_ollama_translation,
)


class FakeResponse:
    def __init__(self, status_code=200, payload=None):
        self.status_code = status_code
        self._payload = payload if payload is not None else {}

    def json(self):
        return self._payload


class FakeSession:
    def __init__(self, *, post_response=None, get_response=None):
        self.post_response = post_response
        self.get_response = get_response
        self.posts = []
        self.gets = []

    def post(self, url, *, json, timeout):
        self.posts.append((url, json, timeout))
        return self.post_response

    def get(self, url, *, timeout):
        self.gets.append((url, timeout))
        return self.get_response


class OllamaStructuredTests(unittest.TestCase):
    def test_native_structured_request_is_no_key_and_schema_constrained(self):
        response = FakeResponse(
            payload={
                "message": {
                    "role": "assistant",
                    "content": '{"title":"t","category":"general","items":[]}',
                }
            }
        )
        session = FakeSession(post_response=response)
        client = OllamaStructuredClient(
            model="qwen-local:latest",
            num_ctx=32768,
            session=session,
        )
        writer = SimpleNamespace(last_diagnostics={}, _translation_model=client.model)
        schema = {
            "type": "object",
            "required": ["title", "category", "items"],
            "properties": {
                "title": {"type": "string"},
                "category": {"type": "string"},
                "items": {"type": "array"},
            },
        }

        parsed = generate_json_v2(
            writer,
            client,
            "SOURCE",
            schema,
            temperature=0.1,
            purpose="test",
            system_instruction="SYSTEM",
        )

        self.assertEqual(parsed["title"], "t")
        self.assertEqual(len(session.posts), 1)
        url, payload, timeout = session.posts[0]
        self.assertEqual(url, "http://127.0.0.1:11434/api/chat")
        self.assertEqual(payload["model"], "qwen-local:latest")
        self.assertFalse(payload["stream"])
        self.assertEqual(payload["format"], schema)
        self.assertEqual(payload["options"]["num_ctx"], 32768)
        self.assertEqual(payload["messages"][0], {"role": "system", "content": "SYSTEM"})
        self.assertEqual(payload["messages"][1], {"role": "user", "content": "SOURCE"})
        self.assertNotIn("api_key", payload)
        self.assertNotIn("authorization", payload)
        self.assertGreater(timeout, 0)
        self.assertEqual(writer.last_diagnostics["generation_provider"], "ollama")

    def test_invalid_structured_output_fails_closed_without_content_in_diagnostics(self):
        session = FakeSession(
            post_response=FakeResponse(
                payload={"message": {"role": "assistant", "content": "not-json"}}
            )
        )
        client = OllamaStructuredClient(model="local:latest", session=session)
        writer = SimpleNamespace(last_diagnostics={}, _translation_model=client.model)

        parsed = generate_json_v2(
            writer,
            client,
            "secret source text",
            {"type": "object"},
            temperature=0,
            purpose="translation",
            system_instruction="system",
        )

        self.assertIsNone(parsed)
        failures = writer.last_diagnostics["generation_failures"]
        self.assertEqual(failures[-1]["reason"], "invalid_structured_json")
        self.assertNotIn("secret source text", repr(failures))

    def test_healthcheck_requires_exact_installed_model_and_sends_no_source(self):
        session = FakeSession(
            get_response=FakeResponse(
                payload={"models": [{"name": "qwen-local:latest"}]}
            )
        )
        client = OllamaStructuredClient(model="qwen-local:latest", session=session)
        self.assertIn("ok", client.healthcheck())
        self.assertEqual(session.gets[0][0], "http://127.0.0.1:11434/api/tags")

        missing = OllamaStructuredClient(
            model="missing:latest",
            session=FakeSession(
                get_response=FakeResponse(
                    payload={"models": [{"name": "qwen-local:latest"}]}
                )
            ),
        )
        self.assertIn("not installed", missing.healthcheck())

    def test_ollama_is_explicit_opt_in_and_does_not_replace_general_gemini_client(self):
        settings = SimpleNamespace(runtime={}, gemini_model="gemini-test")
        writer = SimpleNamespace(_client_or_none=lambda: "gemini-client")
        with patch.dict(
            os.environ,
            {
                "HANI_TRANSLATION_PROVIDER": "ollama",
                "OLLAMA_MODEL": "local:latest",
            },
            clear=False,
        ):
            install_ollama_translation(writer, settings)

        self.assertEqual(writer._client_or_none(), "gemini-client")
        self.assertEqual(writer._translation_client_or_none().model, "local:latest")
        self.assertEqual(writer._translation_provider_name, "ollama")

    def test_default_provider_remains_gemini(self):
        settings = SimpleNamespace(runtime={})
        with patch.dict(os.environ, {}, clear=True):
            self.assertEqual(configured_translation_provider(settings), "gemini")


if __name__ == "__main__":
    unittest.main()
