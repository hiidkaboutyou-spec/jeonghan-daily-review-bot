from __future__ import annotations

import unittest

from app.webhook_server import WebhookAwarePersonalAssistant


class WebhookXRecoveryWiringTests(unittest.TestCase):
    def test_webhook_runtime_installs_live_x_recovery_hardening(self):
        self.assertTrue(
            getattr(WebhookAwarePersonalAssistant, "_hani_live_recovery_hardening", False)
        )


if __name__ == "__main__":
    unittest.main()
