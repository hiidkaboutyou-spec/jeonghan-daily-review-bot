from __future__ import annotations

import asyncio
import hmac
import logging
import os
import threading
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, TypeVar

from fastapi import FastAPI, Header, HTTPException, Request

from .config import ConfigError, Settings
from .telegram import TelegramBot, TelegramPermanentError, TelegramTransientError
from .telegram_cloud_state import backup_fingerprint, backup_to_telegram, restore_from_telegram
from .webhook_aware_assistant import WebhookAwarePersonalAssistant
from .webhook_runtime_utils import derive_runtime_secret
from .x_client import XCollectionError

logger = logging.getLogger(__name__)
_T = TypeVar("_T")


class WebhookRuntime:
    def __init__(self) -> None:
        self.settings: Settings | None = None
        self.application: WebhookAwarePersonalAssistant | None = None
        # ArchiveStore, ReviewInboxStore, ReminderStore and related SQLite helpers
        # keep persistent sqlite3.Connection objects. Construct and use the entire
        # assistant on one dedicated worker for its full lifetime.
        self.executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="assistant-state")
        self.lock = threading.RLock()
        self.secret = ""
        self.public_base_url = ""
        self.last_backup_hash = ""
        self.last_scan_at = datetime.min.replace(tzinfo=timezone.utc)

    async def run_state(self, fn: Callable[..., _T], *args: Any, **kwargs: Any) -> _T:
        loop = asyncio.get_running_loop()
        if kwargs:
            return await loop.run_in_executor(self.executor, lambda: fn(*args, **kwargs))
        return await loop.run_in_executor(self.executor, fn, *args)

    @staticmethod
    def _public_url_from_environment() -> str:
        explicit = os.getenv("PUBLIC_BASE_URL", "").strip().rstrip("/")
        if explicit:
            return explicit
        render_url = os.getenv("RENDER_EXTERNAL_URL", "").strip().rstrip("/")
        if render_url:
            return render_url
        render_host = os.getenv("RENDER_EXTERNAL_HOSTNAME", "").strip().strip("/")
        if render_host:
            return f"https://{render_host}"
        return ""

    def startup_sync(self) -> None:
        settings = Settings.load(require_secrets=True)
        errors = settings.validate_files()
        if errors:
            raise ConfigError("; ".join(errors))
        self.settings = settings
        bootstrap_telegram = TelegramBot(
            settings.telegram_token,
            settings.admin_user_id,
            settings.review_chat_id,
        )
        state_dir = settings.state_path.parent
        try:
            restore_from_telegram(bootstrap_telegram, state_dir)
        except Exception as exc:
            logger.warning("Telegram cloud-state restore unavailable (%s); using local state if present", type(exc).__name__)

        self.application = WebhookAwarePersonalAssistant(settings)
        self.secret = derive_runtime_secret(settings.telegram_token)
        self.public_base_url = self._public_url_from_environment()
        if not self.public_base_url:
            raise ConfigError("PUBLIC_BASE_URL or Render public URL environment is required for webhook mode")

        webhook_url = self.public_base_url + "/telegram/webhook"
        self.application.telegram.api(
            "setWebhook",
            data={
                "url": webhook_url,
                "secret_token": self.secret,
                "allowed_updates": '["message","callback_query"]',
                "drop_pending_updates": "false",
                "max_connections": "1",
            },
            timeout=45,
            attempts=3,
        )
        logger.info("Telegram webhook registered for %s", self.public_base_url)
        self._save_and_backup_if_changed(force=True)

    def _require_app(self) -> WebhookAwarePersonalAssistant:
        if self.application is None:
            raise RuntimeError("Webhook runtime is not initialized")
        return self.application

    def process_update_sync(self, item: dict[str, Any]) -> bool:
        """Process and durably save one Telegram update before acknowledging it.

        Return False for exhausted transient failures. The HTTP layer then sends a
        non-2xx response so Telegram retries the same update. Duplicate retries are
        harmless because telegram_offset is persisted and checked here.
        """
        with self.lock:
            app = self._require_app()
            try:
                update_id = int(item.get("update_id", 0) or 0)
            except (TypeError, ValueError):
                return True
            if update_id < app.state.telegram_offset:
                return True

            handled = True
            for attempt in range(1, 4):
                try:
                    asyncio.run(app._process_one_telegram_update(item))
                except TelegramPermanentError:
                    app.state.telegram_offset = max(app.state.telegram_offset, update_id + 1)
                    break
                except TelegramTransientError as exc:
                    if attempt >= 3:
                        logger.warning("Webhook update %s exhausted Telegram retries (%s)", update_id, type(exc).__name__)
                        handled = False
                        break
                    continue
                except XCollectionError as exc:
                    if attempt >= 3:
                        logger.warning("Webhook update %s exhausted X retries (%s)", update_id, type(exc).__name__)
                        handled = False
                        break
                    continue
                except ConfigError as exc:
                    # Configuration faults are deterministic for this running
                    # instance. A Telegram retry would loop forever, so consume the
                    # update after logging it clearly for operator action.
                    logger.error("Webhook update %s configuration failure (%s)", update_id, type(exc).__name__)
                    app.state.telegram_offset = max(app.state.telegram_offset, update_id + 1)
                    break
                except Exception as exc:
                    logger.exception("Webhook update %s failed (%s)", update_id, type(exc).__name__)
                    if attempt >= 3:
                        handled = False
                        break
                else:
                    app.state.clear_telegram_failure(update_id)
                    app.state.telegram_offset = max(app.state.telegram_offset, update_id + 1)
                    break
            app.state.save()
            self._save_and_backup_if_changed()
            return handled

    def maintenance_sync(self) -> None:
        with self.lock:
            app = self._require_app()
            now = datetime.now(timezone.utc)
            try:
                asyncio.run(app.process_due_reminders())
                if now - self.last_scan_at >= timedelta(minutes=12):
                    asyncio.run(app.run_scheduled_scan())
                    asyncio.run(app.deliver_pending())
                    self.last_scan_at = now
            finally:
                app.state.save()
                self._save_and_backup_if_changed()

    def _save_and_backup_if_changed(self, *, force: bool = False) -> None:
        app = self._require_app()
        app.state.save()
        state_dir = app.settings.state_path.parent
        fingerprint = backup_fingerprint(state_dir)
        if not fingerprint or (not force and fingerprint == self.last_backup_hash):
            return
        try:
            backup_to_telegram(app.telegram, state_dir)
        except Exception as exc:
            logger.warning("Telegram cloud-state backup failed (%s)", type(exc).__name__)
            return
        self.last_backup_hash = backup_fingerprint(state_dir)


runtime = WebhookRuntime()


@asynccontextmanager
async def lifespan(_: FastAPI):
    await runtime.run_state(runtime.startup_sync)
    try:
        yield
    finally:
        if runtime.application is not None:
            await runtime.run_state(runtime._save_and_backup_if_changed, force=True)
        runtime.executor.shutdown(wait=True, cancel_futures=True)


api = FastAPI(title="Jeonghan Personal Assistant", lifespan=lifespan)


@api.get("/")
def root() -> dict[str, Any]:
    return {"ok": True, "service": "jeonghan-personal-assistant", "mode": "telegram-webhook"}


@api.get("/healthz")
def healthz() -> dict[str, Any]:
    return {
        "ok": runtime.application is not None,
        "mode": "telegram-webhook",
        "public_base_url": bool(runtime.public_base_url),
    }


@api.post("/telegram/webhook")
async def telegram_webhook(
    request: Request,
    x_telegram_bot_api_secret_token: str | None = Header(default=None),
) -> dict[str, bool]:
    supplied = str(x_telegram_bot_api_secret_token or "")
    if not runtime.secret or not hmac.compare_digest(supplied, runtime.secret):
        raise HTTPException(status_code=403, detail="invalid webhook secret")
    payload = await request.json()
    if not isinstance(payload, dict):
        raise HTTPException(status_code=400, detail="invalid update")

    # Do not acknowledge Telegram until the update is processed and state is saved.
    # Telegram retries non-2xx deliveries; the persisted telegram_offset makes those
    # retries idempotent if a response is lost after successful processing.
    handled = await runtime.run_state(runtime.process_update_sync, payload)
    if not handled:
        raise HTTPException(status_code=503, detail="temporary processing failure; retry update")
    return {"ok": True}


@api.post("/maintenance")
async def maintenance(x_assistant_secret: str | None = Header(default=None)) -> dict[str, bool]:
    supplied = str(x_assistant_secret or "")
    if not runtime.secret or not hmac.compare_digest(supplied, runtime.secret):
        raise HTTPException(status_code=403, detail="invalid maintenance secret")
    # Run before acknowledging so a free host cannot spin down after a 202 while the
    # work exists only in volatile memory.
    await runtime.run_state(runtime.maintenance_sync)
    return {"ok": True}
