from __future__ import annotations

import logging

from .personal_assistant import PersonalAssistantReviewApplication
from .webhook_runtime_utils import derive_runtime_secret, maintenance_url_from_webhook

logger = logging.getLogger(__name__)


class WebhookAwarePersonalAssistant(PersonalAssistantReviewApplication):
    """Use polling only until a real Telegram webhook runtime takes ownership.

    Scheduled GitHub Actions remain useful as a genuine 15-minute maintenance
    trigger. Once Telegram reports an active webhook, Actions must never call
    deleteWebhook/getUpdates or maintain a second copy of state. Instead it sends
    one authenticated wake/maintenance request to the webhook service and exits.
    """

    async def run(self) -> None:
        try:
            info = self.telegram.api("getWebhookInfo", timeout=30, attempts=2) or {}
        except Exception as exc:
            logger.warning("Could not inspect Telegram webhook ownership; retaining polling fallback (%s)", type(exc).__name__)
            return await super().run()

        webhook_url = str(info.get("url", "") or "").strip() if isinstance(info, dict) else ""
        if not webhook_url:
            return await super().run()

        maintenance_url = maintenance_url_from_webhook(webhook_url)
        if not maintenance_url:
            logger.warning("Telegram has an active webhook with an unusable URL; refusing competing polling runtime")
            return

        secret = derive_runtime_secret(self.settings.telegram_token)
        try:
            response = self.telegram.session.post(
                maintenance_url,
                headers={"X-Assistant-Secret": secret},
                timeout=25,
            )
            if 200 <= response.status_code < 300:
                logger.info("Webhook runtime owns Telegram; maintenance wake accepted with HTTP %s", response.status_code)
            else:
                logger.warning("Webhook runtime owns Telegram but maintenance wake returned HTTP %s", response.status_code)
        except Exception as exc:
            logger.warning("Webhook runtime owns Telegram; maintenance wake failed (%s), polling remains disabled", type(exc).__name__)
