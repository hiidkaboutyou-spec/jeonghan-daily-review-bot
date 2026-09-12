from __future__ import annotations

import fcntl
import logging
import os
import sqlite3
import subprocess
import sys
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from app.config import ROOT, ConfigError, Settings
from app.production_preflight import run_preflight
from app.telegram import TelegramBot
from app.telegram_cloud_state import backup_fingerprint, backup_to_telegram, restore_from_telegram

SERVICE = "jeonghan-daily-review-bot"
REQUIRED_ACCOUNTS = (
    "TELEGRAM_BOT_TOKEN",
    "TELEGRAM_ADMIN_USER_ID",
    "TELEGRAM_REVIEW_CHAT_ID",
    "X_COOKIE",
)
OPTIONAL_ACCOUNTS = ("GEMINI_API_KEY", "STATE_BACKUP_KEY", "SENTRY_DSN")
LOCK_PATH = Path.home() / "Library" / "Application Support" / SERVICE / "runtime.lock"
logger = logging.getLogger(__name__)


class LocalRuntimeError(RuntimeError):
    pass


def apply_provider_report(report: dict[str, str]) -> None:
    """Expose preflight state to runtime fallbacks and reject only unusable X."""
    x_status = str(report.get("x", "offline (missing status)")).strip()
    if x_status == "ok":
        os.environ["X_PROVIDER_PREFLIGHT"] = "online"
        return
    if x_status.startswith("degraded"):
        os.environ["X_PROVIDER_PREFLIGHT"] = "degraded"
        return
    os.environ["X_PROVIDER_PREFLIGHT"] = "offline"
    raise LocalRuntimeError("X provider preflight is offline; state was not advanced.")


def _git(*args: str) -> str:
    result = subprocess.run(
        ("git", "-C", str(ROOT), *args),
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        text=True,
        timeout=15,
    )
    if result.returncode:
        raise LocalRuntimeError("Trusted checkout verification failed.")
    return result.stdout.strip()


def verify_checkout() -> None:
    if Path(_git("rev-parse", "--show-toplevel")).resolve() != ROOT.resolve():
        raise LocalRuntimeError("Runtime repository root is not trusted.")
    if _git("status", "--porcelain", "--untracked-files=no"):
        raise LocalRuntimeError("Production checkout contains tracked modifications.")
    branch = _git("symbolic-ref", "--short", "HEAD")
    if branch != "main":
        raise LocalRuntimeError("Production runtime must execute the main branch.")
    head = _git("rev-parse", "HEAD")
    upstream = _git("rev-parse", "origin/main")
    if head != upstream:
        raise LocalRuntimeError("Production checkout is not exactly origin/main.")


def _read_keychain(account: str, *, required: bool) -> str:
    result = subprocess.run(
        ("security", "find-generic-password", "-s", SERVICE, "-a", account, "-w"),
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        text=True,
        timeout=15,
    )
    value = result.stdout.rstrip("\n") if result.returncode == 0 else ""
    if required and not value:
        raise LocalRuntimeError(f"Required Keychain item is missing or empty: {account}")
    return value


def load_keychain_environment() -> None:
    for account in REQUIRED_ACCOUNTS:
        os.environ[account] = _read_keychain(account, required=True)
    for account in OPTIONAL_ACCOUNTS:
        value = _read_keychain(account, required=False)
        if value:
            os.environ[account] = value
        else:
            os.environ.pop(account, None)
    os.environ["ASSISTANT_RUNTIME_MODE"] = "github_actions_polling"


@contextmanager
def single_writer_lock() -> Iterator[None]:
    LOCK_PATH.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    descriptor = os.open(LOCK_PATH, os.O_CREAT | os.O_RDWR, 0o600)
    try:
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise LocalRuntimeError("Another production monitor pass is already running.") from exc
        yield
    finally:
        os.close(descriptor)


def validate_state(settings: Settings) -> None:
    state_path = settings.state_path
    if state_path.exists():
        import json

        try:
            value = json.loads(state_path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise LocalRuntimeError("state.json is unreadable or invalid.") from exc
        if not isinstance(value, dict):
            raise LocalRuntimeError("state.json must contain an object.")
    database = state_path.with_name("private-review.sqlite3")
    if database.exists():
        try:
            with sqlite3.connect(database, timeout=15) as connection:
                result = connection.execute("PRAGMA quick_check").fetchone()
        except sqlite3.DatabaseError as exc:
            raise LocalRuntimeError("Private review database validation failed.") from exc
        if not result or str(result[0]).lower() != "ok":
            raise LocalRuntimeError("Private review database failed quick_check.")


async def execute() -> int:
    verify_checkout()
    load_keychain_environment()
    settings = Settings.load(require_secrets=True)
    telegram = TelegramBot(settings.telegram_token, settings.admin_user_id, settings.review_chat_id)
    state_dir = settings.state_path.parent
    if not settings.state_path.exists() or not settings.state_path.with_name("private-review.sqlite3").exists():
        restore_from_telegram(telegram, state_dir)
    validate_state(settings)
    report = await run_preflight()
    for provider, status in report.items():
        logger.info("Production preflight: %s=%s", provider, status)
    apply_provider_report(report)

    before = backup_fingerprint(state_dir)
    from app.sentry_runtime import async_main

    code = int(await async_main())
    validate_state(settings)
    after = backup_fingerprint(state_dir)
    if after and after != before:
        backup_to_telegram(telegram, state_dir)
    return code


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    try:
        with single_writer_lock():
            import asyncio

            return asyncio.run(execute())
    except (ConfigError, LocalRuntimeError) as exc:
        logger.error("Local production pass refused: %s", exc)
        return 2
    except Exception as exc:
        logger.exception("Local production pass failed (%s).", type(exc).__name__)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
