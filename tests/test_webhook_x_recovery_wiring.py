from __future__ import annotations

import os
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from app import webhook_server


class WebhookXRecoveryWiringTests(unittest.TestCase):
    def test_missing_x_cookies_select_degraded_recovery_for_webhook_only(self):
        settings = SimpleNamespace(x_cookies={})
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("X_PROVIDER_PREFLIGHT", None)
            state = webhook_server._configure_webhook_x_recovery(settings)
            self.assertEqual(state, "degraded")
            self.assertEqual(os.environ.get("X_PROVIDER_PREFLIGHT"), "degraded")

    def test_valid_x_cookies_do_not_force_degraded_state(self):
        settings = SimpleNamespace(
            x_cookies={"auth_token": "auth", "ct0": "csrf"}
        )
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("X_PROVIDER_PREFLIGHT", None)
            state = webhook_server._configure_webhook_x_recovery(settings)
            self.assertEqual(state, "")
            self.assertNotIn("X_PROVIDER_PREFLIGHT", os.environ)

    def test_explicit_provider_state_is_preserved(self):
        settings = SimpleNamespace(x_cookies={})
        with patch.dict(
            os.environ,
            {"X_PROVIDER_PREFLIGHT": "online"},
            clear=False,
        ):
            state = webhook_server._configure_webhook_x_recovery(settings)
            self.assertEqual(state, "online")
            self.assertEqual(os.environ["X_PROVIDER_PREFLIGHT"], "online")

    def test_webhook_runtime_installs_live_x_recovery_hardening(self):
        with patch.object(
            webhook_server._x_degraded_recovery_runtime,
            "install",
        ) as install:
            webhook_server._install_webhook_x_recovery()

        install.assert_called_once_with(webhook_server.WebhookAwarePersonalAssistant)


if __name__ == "__main__":
    unittest.main()
