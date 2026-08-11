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
        self.queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=500)
        self.maintenance_queue: asyncio.Queue[None] = asyncio.Queue(maxsize=1)
        # ArchiveStore, ReviewInboxStore, ReminderStore and related SQLite helpers
        # keep persistent sqlite3.Connection objects. Python's sqlite3 connections
        # are thread-affine by default, so construct and use the entire assistant on
        # one dedicated worker for its full lifetime.
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
        explicit = os.getenv("PUBLIC_BASE_URL", "").strip().rstrip("/")
        domain = os.getenv("KOYEB_PUBLIC_DOMAIN", "").strip()
        self.public_base_url = explicit or (f"https://{domain}" if domain else "")
        if not self.public_base_url:
            raise ConfigError("PUBLIC_BASE_URL or KOYEB_PUBLIC_DOMAIN is required for webhook mode")

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

    def process_update_sync(self, item: dict[str, Any]) -> None:
        with self.lock:
            app = self._require_app()
            try:
                update_id = int(item.get("update_id", 0) or 0)
            except (TypeError, ValueError):
                return
            if update_id < app.state.telegram_offset:
                return

            for attempt in range(1, 4):
                try:
                    asyncio.run(app._process_one_telegram_update(item))
                except TelegramPermanentError:
                    app.state.telegram_offset = max(app.state.telegram_offset, update_id + 1)
                    break
                except TelegramTransientError as exc:
                    if attempt >= 3:
                        logger.warning("Webhook update %s exhausted Telegram retries (%s)", update_id, type(exc).__name__)
                        break
                    continue
                except (XCollectionError, ConfigError) as exc:
                    if attempt >= 3:
                        logger.warning("Webhook update %s exhausted application retries (%s)", update_id, type(exc).__name__)
                        app.state.telegram_offset = max(app.state.telegram_offset, update_id + 1)
                        break
                    continue
                except Exception:
                    logger.exception("Webhook update %s failed", update_id)
                    if attempt >= 3:
                        app.state.telegram_offset = max(app.state.telegram_offset, update_id + 1)
                        break
                else:
                    app.state.clear_telegram_failure(update_id)
                    app.state.telegram_offset = max(app.state.telegram_offset, update_id + 1)
                    break
            app.state.save()
            self._save_and_backup_if_changed()

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
        # Re-fingerprint after WAL checkpoint so the next request does not upload a
        # duplicate backup merely because checkpointing moved bytes into the DB.
        self.last_backup_hash = backup_fingerprint(state_dir)


runtime = WebhookRuntime()


async def _update_worker() -> None:
    while True:
        item = await runtime.queue.get()
        try:
            await runtime.run_state(runtime.process_update_sync, item)
        finally:
            runtime.queue.task_done()


async def _maintenance_worker() -> None:
    while True:
        await runtime.maintenance_queue.get()
        try:
            await runtime.run_state(runtime.maintenance_sync)
        finally:
            runtime.maintenance_queue.task_done()


@asynccontextmanager
async def lifespan(_: FastAPI):
    await runtime.run_state(runtime.startup_sync)
    update_task = asyncio.create_task(_update_worker())
    maintenance_task = asyncio.create_task(_maintenance_worker())
    try:
        yield
    finally:
        update_task.cancel()
        maintenance_task.cancel()
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
        "queue": runtime.queue.qsize(),
        "maintenance_queue": runtime.maintenance_queue.qsize(),
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
    try:
        runtime.queue.put_nowait(payload)
    except asyncio.QueueFull:
        raise HTTPException(status_code=503, detail="update queue full")
    return {"ok": True}


@api.post("/maintenance", status_code=202)
async def maintenance(x_assistant_secret: str | None = Header(default=None)) -> dict[str, bool]:
    supplied = str(x_assistant_secret or "")
    if not runtime.secret or not hmac.compare_digest(supplied, runtime.secret):
        raise HTTPException(status_code=403, detail="invalid maintenance secret")
    if runtime.maintenance_queue.empty():
        runtime.maintenance_queue.put_nowait(None)
    return {"accepted": True}
