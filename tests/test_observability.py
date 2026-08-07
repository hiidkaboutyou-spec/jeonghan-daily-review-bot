from __future__ import annotations

import os
import unittest
from unittest.mock import patch

from app.observability import init_optional_sentry, scrub_event


class OptionalSentryTests(unittest.TestCase):
    def test_no_dsn_means_disabled(self):
        with patch.dict(os.environ, {}, clear=True):
            self.assertFalse(init_optional_sentry())

    def test_scrubber_removes_private_content_and_secrets(self):
        event = {
            "level": "error",
            "message": "private caption secret-token",
            "request": {"headers": {"Authorization": "Bearer secret"}, "data": "private chat"},
            "user": {"id": "123"},
            "breadcrumbs": [{"message": "draft body"}],
            "extra": {"TELEGRAM_BOT_TOKEN": "secret", "X_COOKIE": "secret"},
            "contexts": {"private": {"caption": "secret"}},
            "exception": {"values": [{
                "type": "RuntimeError",
                "value": "auth_token=secret private draft",
                "stacktrace": {"frames": [{
                    "filename": "/repo/app/main.py", "function": "run", "lineno": 10,
                    "vars": {"X_COOKIE": "secret"}, "context_line": "private caption"
                }]},
            }]},
        }
        safe = scrub_event(event, {})
        rendered = repr(safe)
        self.assertNotIn("secret-token", rendered)
        self.assertNotIn("auth_token", rendered)
        self.assertNotIn("private draft", rendered)
        self.assertNotIn("X_COOKIE", rendered)
        self.assertNotIn("Authorization", rendered)
        self.assertNotIn("context_line", rendered)
        self.assertIn("RuntimeError", rendered)
        self.assertIn("main.py", rendered)

    def test_scrubber_does_not_forward_message_body_without_exception(self):
        safe = scrub_event({"message": "private review text", "extra": {"cookie": "secret"}}, {})
        rendered = repr(safe)
        self.assertNotIn("private review text", rendered)
        self.assertNotIn("secret", rendered)
        self.assertIn("SanitizedTechnicalEvent", rendered)


if __name__ == "__main__":
    unittest.main()
