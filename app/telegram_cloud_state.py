from __future__ import annotations

import base64
import hashlib
import json
import logging
import os
import sqlite3
import tempfile
from pathlib import Path

from .telegram import TelegramBot, TelegramError
from tools.state_backup import BackupError, encrypt, restore

logger = logging.getLogger(__name__)
BACKUP_FILENAME = "jeonghan-assistant-state.enc"
BACKUP_CAPTION = "🔐 Jeonghan Assistant encrypted state backup — do not delete or unpin."


def ensure_process_backup_key(token: str) -> None:
    """Reuse STATE_BACKUP_KEY when provided, otherwise derive one from the bot token.

    The derived key never leaves the process. Rotating the bot token also rotates
    this fallback key, so a dedicated STATE_BACKUP_KEY remains preferable when one
    is already available.
    """
    if os.getenv("STATE_BACKUP_KEY", "").strip():
        return
    digest = hashlib.sha256(("jeonghan-assistant-state-v1:" + str(token or "")).encode("utf-8")).digest()
    os.environ["STATE_BACKUP_KEY"] = base64.b64encode(digest).decode("ascii")


def _pinned_backup(telegram: TelegramBot) -> dict | None:
    info = telegram.api("getChat", data={"chat_id": telegram.review_chat_id}, timeout=30, attempts=2) or {}
    if not isinstance(info, dict):
        return None
    message = info.get("pinned_message")
    if not isinstance(message, dict):
        return None
    document = message.get("document")
    if not isinstance(document, dict):
        return None
    if str(document.get("file_name", "")) != BACKUP_FILENAME:
        return None
    return message


def restore_from_telegram(telegram: TelegramBot, state_dir: Path) -> list[str]:
    ensure_process_backup_key(telegram.token)
    message = _pinned_backup(telegram)
    if message is None:
        return []
    document = message.get("document") or {}
    file_id = str(document.get("file_id", ""))
    if not file_id:
        return []
    file_info = telegram.api("getFile", data={"file_id": file_id}, timeout=30, attempts=2) or {}
    file_path = str(file_info.get("file_path", "")) if isinstance(file_info, dict) else ""
    if not file_path:
        return []
    try:
        response = telegram.session.get(
            f"https://api.telegram.org/file/bot{telegram.token}/{file_path}", timeout=60
        )
        response.raise_for_status()
    except Exception as exc:
        raise BackupError(f"Telegram state download failed ({type(exc).__name__}).") from None

    state_dir.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(prefix="jeonghan-state-", suffix=".enc", delete=False) as handle:
        temp_path = Path(handle.name)
        handle.write(response.content)
    try:
        restored = restore(temp_path, state_dir, only_missing=False)
        logger.info("Restored private assistant state from Telegram: %s", ", ".join(restored) or "none")
        return restored
    finally:
        temp_path.unlink(missing_ok=True)


def _checkpoint_sqlite(state_dir: Path) -> None:
    path = state_dir / "private-review.sqlite3"
    if not path.exists():
        return
    conn = sqlite3.connect(path, timeout=15)
    try:
        result = conn.execute("PRAGMA quick_check").fetchone()
        if not result or str(result[0]).lower() != "ok":
            raise BackupError("private-review.sqlite3 failed quick_check before Telegram backup")
        conn.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchall()
    finally:
        conn.close()


def backup_fingerprint(state_dir: Path) -> str:
    digest = hashlib.sha256()
    found = False
    for name in ("state.json", "private-review.sqlite3"):
        path = state_dir / name
        if path.exists():
            found = True
            digest.update(name.encode("utf-8"))
            digest.update(path.read_bytes())
    return digest.hexdigest() if found else ""


def backup_to_telegram(telegram: TelegramBot, state_dir: Path) -> int:
    ensure_process_backup_key(telegram.token)
    _checkpoint_sqlite(state_dir)
    state_dir.mkdir(parents=True, exist_ok=True)
    backup_path = state_dir / BACKUP_FILENAME
    encrypt(state_dir, backup_path)
    pinned = _pinned_backup(telegram)

    try:
        with backup_path.open("rb") as handle:
            files = {"document": (BACKUP_FILENAME, handle, "application/octet-stream")}
            if pinned is not None:
                media = json.dumps(
                    {
                        "type": "document",
                        "media": "attach://document",
                        "caption": BACKUP_CAPTION,
                    },
                    ensure_ascii=False,
                )
                try:
                    sent = telegram.api(
                        "editMessageMedia",
                        data={
                            "chat_id": telegram.review_chat_id,
                            "message_id": int(pinned.get("message_id", 0) or 0),
                            "media": media,
                        },
                        files=files,
                        timeout=90,
                        attempts=2,
                    )
                except TelegramError:
                    handle.seek(0)
                    sent = telegram.api(
                        "sendDocument",
                        data={"chat_id": telegram.review_chat_id, "caption": BACKUP_CAPTION},
                        files=files,
                        timeout=90,
                        attempts=2,
                    )
            else:
                sent = telegram.api(
                    "sendDocument",
                    data={"chat_id": telegram.review_chat_id, "caption": BACKUP_CAPTION},
                    files=files,
                    timeout=90,
                    attempts=2,
                )
        message_id = int((sent or {}).get("message_id", 0) or 0)
        if message_id:
            telegram.api(
                "pinChatMessage",
                data={
                    "chat_id": telegram.review_chat_id,
                    "message_id": message_id,
                    "disable_notification": "true",
                },
                timeout=30,
                attempts=2,
            )
        return message_id
    finally:
        backup_path.unlink(missing_ok=True)
