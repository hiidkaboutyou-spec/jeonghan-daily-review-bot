from __future__ import annotations

import json
import os
import subprocess
import unittest
from datetime import datetime, timezone
from unittest.mock import patch

from app.x_agent_reach import (
    AgentReachXError,
    _parse_payload,
    collect_agent_reach_timeline,
)


class AgentReachXTests(unittest.TestCase):
    def setUp(self):
        self.start = datetime(2026, 9, 10, 7, 0, tzinfo=timezone.utc)
        self.end = datetime(2026, 9, 10, 9, 0, tzinfo=timezone.utc)
        self.cookies = {"auth_token": "auth-secret", "ct0": "ct0-secret"}

    def _payload(self) -> dict:
        return {
            "ok": True,
            "schema_version": "1",
            "data": [
                {
                    "id": "101",
                    "text": "Jeonghan update",
                    "author": {
                        "id": "1",
                        "name": "Source Zero",
                        "screenName": "source0",
                    },
                    "createdAt": "Wed Sep 10 08:00:00 +0000 2026",
                    "createdAtISO": "2026-09-10T08:00:00Z",
                    "media": [
                        {
                            "type": "photo",
                            "url": "https://pbs.twimg.com/media/test.jpg",
                            "width": 1200,
                            "height": 800,
                        }
                    ],
                    "lang": "en",
                    "isRetweet": False,
                    "isPromoted": False,
                    "quotedTweet": {
                        "id": "99",
                        "text": "quoted text",
                        "author": {"screenName": "quoted", "name": "Quoted"},
                    },
                },
                {
                    "id": "102",
                    "text": "retweet",
                    "author": {"name": "Source Zero", "screenName": "source0"},
                    "createdAtISO": "2026-09-10T08:05:00Z",
                    "isRetweet": True,
                },
                {
                    "id": "103",
                    "text": "outside window",
                    "author": {"name": "Source Zero", "screenName": "source0"},
                    "createdAtISO": "2026-09-10T10:00:00Z",
                    "isRetweet": False,
                },
            ],
        }

    def test_parse_payload_enforces_source_window_and_skips_retweets(self):
        result = _parse_payload(
            self._payload(),
            handle="source0",
            start=self.start,
            end=self.end,
        )

        self.assertEqual(result.raw_seen, 3)
        self.assertEqual([item.id for item in result.updates], ["101"])
        update = result.updates[0]
        self.assertEqual(update.author, "source0")
        self.assertEqual(update.quoted_id, "99")
        self.assertEqual(update.quoted_author, "quoted")
        self.assertEqual(update.media[0].kind, "photo")
        self.assertEqual(update.raw_query, "agent-reach-twitter-cli:@source0")

    @patch("app.x_agent_reach._require_backend", return_value="/venv/bin/twitter")
    @patch("app.x_agent_reach.subprocess.run")
    def test_child_gets_only_explicit_x_credentials_and_isolated_home(self, run, _backend):
        captured = {}

        def fake_run(command, **kwargs):
            captured["command"] = command
            captured["env"] = dict(kwargs["env"])
            return subprocess.CompletedProcess(
                command,
                0,
                stdout=json.dumps(self._payload()),
                stderr="",
            )

        run.side_effect = fake_run
        with patch.dict(
            os.environ,
            {
                "PATH": "/venv/bin:/usr/bin",
                "HOME": "/real/home",
                "X_COOKIE": "auth_token=must-not-leak",
                "TELEGRAM_BOT_TOKEN": "telegram-secret",
                "GEMINI_API_KEY": "gemini-secret",
            },
            clear=True,
        ):
            result = collect_agent_reach_timeline(
                self.cookies,
                "source0",
                self.start,
                self.end,
            )

        self.assertEqual([item.id for item in result.updates], ["101"])
        self.assertEqual(
            captured["command"],
            ["/venv/bin/twitter", "user-posts", "source0", "--max", "200", "--json"],
        )
        child_env = captured["env"]
        self.assertEqual(child_env["TWITTER_AUTH_TOKEN"], "auth-secret")
        self.assertEqual(child_env["TWITTER_CT0"], "ct0-secret")
        self.assertNotEqual(child_env["HOME"], "/real/home")
        self.assertNotIn("X_COOKIE", child_env)
        self.assertNotIn("TELEGRAM_BOT_TOKEN", child_env)
        self.assertNotIn("GEMINI_API_KEY", child_env)

    @patch("app.x_agent_reach._require_backend", return_value="/venv/bin/twitter")
    @patch("app.x_agent_reach.subprocess.run")
    def test_cli_failure_redacts_cookie_values(self, run, _backend):
        run.return_value = subprocess.CompletedProcess(
            ["twitter"],
            3,
            stdout="",
            stderr="invalid auth-secret / ct0-secret",
        )
        with self.assertRaises(AgentReachXError) as caught:
            collect_agent_reach_timeline(
                self.cookies,
                "source0",
                self.start,
                self.end,
            )
        message = str(caught.exception)
        self.assertNotIn("auth-secret", message)
        self.assertNotIn("ct0-secret", message)
        self.assertIn("[redacted]", message)

    @patch("app.x_agent_reach.subprocess.run")
    def test_reply_exclusion_is_not_guessed(self, run):
        with self.assertRaisesRegex(AgentReachXError, "reply exclusion"):
            collect_agent_reach_timeline(
                self.cookies,
                "source0",
                self.start,
                self.end,
                include_replies=False,
            )
        run.assert_not_called()

    @patch("app.x_agent_reach.subprocess.run")
    def test_missing_explicit_cookie_is_rejected_before_subprocess(self, run):
        with patch("app.x_agent_reach._require_backend", return_value="/venv/bin/twitter"):
            with self.assertRaisesRegex(AgentReachXError, "missing auth_token or ct0"):
                collect_agent_reach_timeline(
                    {"auth_token": "only-one"},
                    "source0",
                    self.start,
                    self.end,
                )
        run.assert_not_called()


if __name__ == "__main__":
    unittest.main()
