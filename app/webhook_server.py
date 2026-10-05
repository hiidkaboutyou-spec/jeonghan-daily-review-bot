from __future__ import annotations

import asyncio
import hmac
import logging
import os
import re
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
from . import x_degraded_recovery_runtime as _x_degraded_recovery_runtime
from .webhook_runtime_utils import derive_runtime_secret
from .x_client import XCollectionError

logger = logging.getLogger(__name__)
_T = TypeVar("_T")

DEFAULT_MAINTENANCE_TICK_SECONDS = 60
MIN_MAINTENANCE_TICK_SECONDS = 15
MAX_MAINTENANCE_TICK_SECONDS = 300
MAX_EPHEMERAL_PROVIDER_SECRET_LENGTH = 512
EPHEMERAL_PROVIDER_LEASE_TTL_SECONDS = 10 * 60

_TELEGRAM_API_URL_RE = re.compile(r"https://api\.telegram\.org/bot[^/\s]+")
_TELEGRAM_TOKEN_RE = re.compile(r"\b\d{4,}:[A-Za-z0-9_-]{8,}\b")


def _safe_telegram_error_detail(exc: Exception) -> str:
    """Keep actionable Telegram failures while redacting Bot API credentials."""

    if not isinstance(exc, (TelegramPermanentError, TelegramTransientError)):
        return type(exc).__name__
    detail = str(exc).strip()[:600] or type(exc).__name__
    detail = _TELEGRAM_API_URL_RE.sub("<telegram-api>", detail)
    return _TELEGRAM_TOKEN_RE.sub("<redacted>", detail)


def _maintenance_tick_seconds() -> int:
    raw = os.getenv("WEBHOOK_MAINTENANCE_TICK_SECONDS", "").strip()
    try:
        value = int(raw) if raw else DEFAULT_MAINTENANCE_TICK_SECONDS
    except ValueError:
        logger.warning(
            "Invalid WEBHOOK_MAINTENANCE_TICK_SECONDS=%r; using %ss",
            raw,
            DEFAULT_MAINTENANCE_TICK_SECONDS,
        )
        value = DEFAULT_MAINTENANCE_TICK_SECONDS
    return max(MIN_MAINTENANCE_TICK_SECONDS, min(MAX_MAINTENANCE_TICK_SECONDS, value))


def _configure_webhook_x_recovery(settings: Settings) -> str:
    """Select public X recovery only for this no-cookie webhook process."""
    existing = os.environ.get("X_PROVIDER_PREFLIGHT", "").strip().lower()
    if existing:
        return existing
    cookies = getattr(settings, "x_cookies", {}) or {}
    missing = [name for name in ("auth_token", "ct0") if not str(cookies.get(name) or "").strip()]
    if missing:
        os.environ["X_PROVIDER_PREFLIGHT"] = "degraded"
        logger.warning(
            "Webhook X authentication is unavailable (%s); enabling public recovery.",
            ", ".join(missing),
        )
        return "degraded"
    return ""


def _install_webhook_x_recovery() -> None:
    """Install the same degraded-provider hardening used by the Daily runtime."""
    _x_degraded_recovery_runtime.install(WebhookAwarePersonalAssistant)


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
        self.last_maintenance_at = datetime.min.replace(tzinfo=timezone.utc)
        self.last_maintenance_error = ""
        self.last_provider_lease_at = datetime.min.replace(tzinfo=timezone.utc)
        self.static_gemini_configured = False
        self.maintenance_tick_seconds = _maintenance_tick_seconds()

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
        northflank_hosts = os.getenv("NF_HOSTS", "").strip()
        if northflank_hosts:
            first_host = next(
                (item.strip().strip("/") for item in northflank_hosts.split(",") if item.strip()),
                "",
            )
            if first_host:
                if first_host.startswith(("http://", "https://")):
                    return first_host.rstrip("/")
                return f"https://{first_host}"
        return ""

    def startup_sync(self) -> None:
        settings = Settings.load(require_secrets=True)
        errors = settings.validate_files()
        if errors:
            raise ConfigError("; ".join(errors))
        self.settings = settings
        self.static_gemini_configured = bool(
            str(getattr(settings, "gemini_api_key", "") or "").strip()
        )
        _configure_webhook_x_recovery(settings)
        _install_webhook_x_recovery()
        bootstrap_telegram = TelegramBot(
            settings.telegram_token,
            settings.admin_user_id,
            settings.review_chat_id,
        )
        state_dir = settings.state_path.parent
        try:
            restore_from_telegram(bootstrap_telegram, state_dir)
        except Exception as exc:
            logger.warning(
                "Telegram cloud-state restore unavailable (%s); using local state if present",
                _safe_telegram_error_detail(exc),
            )

        self.application = WebhookAwarePersonalAssistant(settings)
        # This process owns Telegram through setWebhook. Delivery code must never
        # interleave getUpdates while this flag is set; Telegram rejects polling
        # whenever a webhook is active.
        self.application.telegram_webhook_owned = True
        self.secret = derive_runtime_secret(settings.telegram_token)
        self.public_base_url = self._public_url_from_environment()
        if not self.public_base_url:
            raise ConfigError("A public webhook URL is required (PUBLIC_BASE_URL, Render URL, or Northflank NF_HOSTS)")

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

        Platform failures retain the update for retry. Application/provider faults
        use the same persisted poison budget as polling, across HTTP deliveries and
        restarts, so a broken command cannot replay forever or block the queue.
        """
        with self.lock:
            app = self._require_app()
            self._expire_gemini_credential_lease_if_needed(app)
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
                except TelegramPermanentError as exc:
                    logger.error(
                        "Webhook update %s permanent Telegram failure (%s); consuming update",
                        update_id,
                        _safe_telegram_error_detail(exc),
                    )
                    app.state.telegram_offset = max(app.state.telegram_offset, update_id + 1)
                    break
                except TelegramTransientError as exc:
                    if attempt >= 3:
                        logger.warning(
                            "Webhook update %s exhausted Telegram retries (%s)",
                            update_id,
                            _safe_telegram_error_detail(exc),
                        )
                        handled = False
                        break
                    continue
                except XCollectionError as exc:
                    handled = app._handle_telegram_update_failure(update_id, exc)
                    break
                except ConfigError as exc:
                    # Configuration faults are deterministic for this running
                    # instance. A Telegram retry would loop forever, so consume the
                    # update after logging it clearly for operator action.
                    logger.error("Webhook update %s configuration failure (%s)", update_id, type(exc).__name__)
                    app.state.telegram_offset = max(app.state.telegram_offset, update_id + 1)
                    break
                except Exception as exc:
                    logger.exception("Webhook update %s failed (%s)", update_id, type(exc).__name__)
                    handled = app._handle_telegram_update_failure(update_id, exc)
                    break
                else:
                    app.state.clear_telegram_failure(update_id)
                    app.state.telegram_offset = max(app.state.telegram_offset, update_id + 1)
                    break
            app.state.save()
            self._save_and_backup_if_changed()
            return handled

    def _provider_lease_fresh(self, now: datetime | None = None) -> bool:
        if self.last_provider_lease_at == datetime.min.replace(tzinfo=timezone.utc):
            return False
        current = now or datetime.now(timezone.utc)
        return current - self.last_provider_lease_at <= timedelta(
            seconds=EPHEMERAL_PROVIDER_LEASE_TTL_SECONDS
        )

    def _expire_gemini_credential_lease_if_needed(
        self,
        app: WebhookAwarePersonalAssistant,
        *,
        now: datetime | None = None,
    ) -> None:
        if self.static_gemini_configured or self._provider_lease_fresh(now):
            return
        if self.last_provider_lease_at == datetime.min.replace(tzinfo=timezone.utc):
            return

        if self.settings is not None:
            self.settings.gemini_api_key = ""
        writer = getattr(app, "writer", None)
        for candidate in (writer, getattr(app, "legacy_writer", None)):
            if candidate is None or not hasattr(candidate, "api_key"):
                continue
            candidate.api_key = ""
            if hasattr(candidate, "_client"):
                candidate._client = None
            if hasattr(candidate, "_gemini_circuit_open"):
                candidate._gemini_circuit_open = ""
        self.last_provider_lease_at = datetime.min.replace(tzinfo=timezone.utc)
        logger.info("Expired in-memory Gemini provider credential lease.")

    def translation_ready(self) -> bool:
        app = self.application
        if app is None:
            return False
        writer = getattr(app, "writer", None)
        provider = str(getattr(writer, "_translation_provider_name", "gemini") or "gemini").casefold()
        if provider == "ollama":
            return getattr(writer, "_translation_client_or_none", None) is not None
        has_key = bool(str(getattr(writer, "api_key", "") or "").strip())
        if self.static_gemini_configured:
            return has_key
        return has_key and self._provider_lease_fresh()

    def install_gemini_credential_lease_sync(self, api_key: str) -> bool:
        """Install a Gemini key in process memory only after authenticated handoff."""
        value = str(api_key or "").strip()
        if not value:
            return False
        if len(value) > MAX_EPHEMERAL_PROVIDER_SECRET_LENGTH:
            raise ConfigError("Provider credential lease is invalid.")

        with self.lock:
            app = self._require_app()
            if self.static_gemini_configured:
                return False
            writer = getattr(app, "writer", None)
            provider = str(getattr(writer, "_translation_provider_name", "gemini") or "gemini").casefold()
            if provider != "gemini":
                return False

            if self.settings is not None:
                self.settings.gemini_api_key = value
            for candidate in (writer, getattr(app, "legacy_writer", None)):
                if candidate is None or not hasattr(candidate, "api_key"):
                    continue
                previous = str(getattr(candidate, "api_key", "") or "")
                changed = previous != value
                candidate.api_key = value
                if changed and hasattr(candidate, "_client"):
                    candidate._client = None
                if changed and hasattr(candidate, "_gemini_circuit_open"):
                    candidate._gemini_circuit_open = ""

            self.last_provider_lease_at = datetime.now(timezone.utc)
            return True

    def maintenance_sync(self) -> None:
        with self.lock:
            app = self._require_app()
            now = datetime.now(timezone.utc)
            self._expire_gemini_credential_lease_if_needed(app, now=now)
            try:
                asyncio.run(app.process_due_reminders())
                from .date_requests import process_date_requests
                asyncio.run(process_date_requests(app))
                if now - self.last_scan_at >= timedelta(minutes=12):
                    asyncio.run(app.run_scheduled_scan())
                    if self.translation_ready():
                        asyncio.run(app.deliver_pending())
                    else:
                        logger.warning(
                            "Pending delivery deferred until a translation credential is available."
                        )
                    self.last_scan_at = now
                self.last_maintenance_error = ""
            except Exception as exc:
                self.last_maintenance_error = type(exc).__name__
                raise
            finally:
                self.last_maintenance_at = datetime.now(timezone.utc)
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
            logger.warning(
                "Telegram cloud-state backup failed (%s)",
                _safe_telegram_error_detail(exc),
            )
            return
        self.last_backup_hash = backup_fingerprint(state_dir)


runtime = WebhookRuntime()


async def _autonomous_maintenance_loop(active_runtime: WebhookRuntime) -> None:
    """Keep scheduled collection alive without depending on GitHub cron delivery."""
    # Let the HTTP server become healthy before the first potentially expensive scan.
    await asyncio.sleep(min(15, active_runtime.maintenance_tick_seconds))
    while True:
        try:
            await active_runtime.run_state(active_runtime.maintenance_sync)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            active_runtime.last_maintenance_error = type(exc).__name__
            logger.exception(
                "Autonomous webhook maintenance failed (%s); next tick will retry",
                type(exc).__name__,
            )
        await asyncio.sleep(active_runtime.maintenance_tick_seconds)


@asynccontextmanager
async def lifespan(_: FastAPI):
    await runtime.run_state(runtime.startup_sync)
    maintenance_task = asyncio.create_task(
        _autonomous_maintenance_loop(runtime),
        name="jeonghan-autonomous-maintenance",
    )
    try:
        yield
    finally:
        maintenance_task.cancel()
        try:
            await maintenance_task
        except asyncio.CancelledError:
            pass
        if runtime.application is not None:
            await runtime.run_state(runtime._save_and_backup_if_changed, force=True)
        runtime.executor.shutdown(wait=True, cancel_futures=True)


api = FastAPI(title="Jeonghan Personal Assistant", lifespan=lifespan)


@api.get("/")
def root() -> dict[str, Any]:
    return {"ok": True, "service": "jeonghan-personal-assistant", "mode": "telegram-webhook"}


@api.get("/healthz")
def healthz() -> dict[str, Any]:
    last_maintenance = (
        None
        if runtime.last_maintenance_at == datetime.min.replace(tzinfo=timezone.utc)
        else runtime.last_maintenance_at.isoformat()
    )
    last_provider_lease = (
        None
        if runtime.last_provider_lease_at == datetime.min.replace(tzinfo=timezone.utc)
        else runtime.last_provider_lease_at.isoformat()
    )
    writer = getattr(runtime.application, "writer", None) if runtime.application is not None else None
    translation_provider = str(
        getattr(writer, "_translation_provider_name", "gemini") or "gemini"
    )
    return {
        "ok": runtime.application is not None,
        "mode": "telegram-webhook",
        "public_base_url": bool(runtime.public_base_url),
        "autonomous_maintenance": True,
        "maintenance_tick_seconds": runtime.maintenance_tick_seconds,
        "translation_provider": translation_provider,
        "translation_ready": runtime.translation_ready(),
        "last_provider_lease_at": last_provider_lease,
        "last_maintenance_at": last_maintenance,
        "last_maintenance_error": runtime.last_maintenance_error,
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
async def maintenance(
    x_assistant_secret: str | None = Header(default=None),
    x_hani_gemini_key: str | None = Header(default=None),
) -> dict[str, bool]:
    supplied = str(x_assistant_secret or "")
    if not runtime.secret or not hmac.compare_digest(supplied, runtime.secret):
        raise HTTPException(status_code=403, detail="invalid maintenance secret")
    if x_hani_gemini_key:
        try:
            await runtime.run_state(
                runtime.install_gemini_credential_lease_sync,
                x_hani_gemini_key,
            )
        except ConfigError as exc:
            raise HTTPException(status_code=400, detail="invalid provider credential lease") from exc
    # Run before acknowledging so a free host cannot spin down after a 202 while the
    # work exists only in volatile memory.
    await runtime.run_state(runtime.maintenance_sync)
    return {"ok": True}
