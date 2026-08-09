from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import sqlite3
import tempfile
from pathlib import Path

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

FORMAT = "jeonghan-private-state-backup-v1"
AAD = FORMAT.encode("utf-8")
STATE_FILES = ("state.json", "private-review.sqlite3")


class BackupError(RuntimeError):
    pass


def _key_from_env() -> bytes:
    raw = os.getenv("STATE_BACKUP_KEY", "").strip()
    if not raw:
        raise BackupError("STATE_BACKUP_KEY is not configured.")
    try:
        key = base64.b64decode(raw, validate=True)
    except Exception as exc:
        raise BackupError("STATE_BACKUP_KEY must be base64-encoded.") from exc
    if len(key) != 32:
        raise BackupError("STATE_BACKUP_KEY must decode to exactly 32 bytes.")
    return key


def _validate_state_json(data: bytes) -> None:
    try:
        parsed = json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise BackupError("Restored state.json is not valid UTF-8 JSON.") from exc
    if not isinstance(parsed, dict):
        raise BackupError("Restored state.json must contain a JSON object.")


def _validate_sqlite(data: bytes) -> None:
    with tempfile.TemporaryDirectory() as temp:
        path = Path(temp) / "restore.sqlite3"
        path.write_bytes(data)
        try:
            conn = sqlite3.connect(path)
            try:
                result = conn.execute("PRAGMA quick_check").fetchone()
            finally:
                conn.close()
        except sqlite3.DatabaseError as exc:
            raise BackupError("Restored private database is not valid SQLite.") from exc
        if not result or str(result[0]).lower() != "ok":
            raise BackupError("Restored private database failed SQLite quick_check.")


def _payload_for(state_dir: Path) -> bytes:
    files: dict[str, dict[str, str]] = {}
    for name in STATE_FILES:
        path = state_dir / name
        if not path.exists():
            continue
        data = path.read_bytes()
        if name == "state.json":
            _validate_state_json(data)
        else:
            _validate_sqlite(data)
        files[name] = {
            "sha256": hashlib.sha256(data).hexdigest(),
            "data": base64.b64encode(data).decode("ascii"),
        }
    if not files:
        raise BackupError("No state files exist to back up.")
    return json.dumps({"format": FORMAT, "files": files}, sort_keys=True, separators=(",", ":")).encode("utf-8")


def encrypt(state_dir: Path, output: Path) -> None:
    key = _key_from_env()
    payload = _payload_for(state_dir)
    nonce = os.urandom(12)
    ciphertext = AESGCM(key).encrypt(nonce, payload, AAD)
    envelope = {
        "format": FORMAT,
        "algorithm": "AES-256-GCM",
        "nonce": base64.b64encode(nonce).decode("ascii"),
        "ciphertext": base64.b64encode(ciphertext).decode("ascii"),
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    temp = output.with_suffix(output.suffix + ".tmp")
    temp.write_text(json.dumps(envelope, sort_keys=True), encoding="utf-8")
    os.replace(temp, output)


def _decrypt_blob(input_path: Path) -> dict[str, bytes]:
    key = _key_from_env()
    try:
        envelope = json.loads(input_path.read_text(encoding="utf-8"))
        if envelope.get("format") != FORMAT or envelope.get("algorithm") != "AES-256-GCM":
            raise BackupError("Encrypted state backup has an unsupported format.")
        nonce = base64.b64decode(str(envelope["nonce"]), validate=True)
        ciphertext = base64.b64decode(str(envelope["ciphertext"]), validate=True)
        payload = AESGCM(key).decrypt(nonce, ciphertext, AAD)
        decoded = json.loads(payload.decode("utf-8"))
    except BackupError:
        raise
    except Exception as exc:
        raise BackupError("Encrypted state backup failed authentication or parsing.") from exc
    if decoded.get("format") != FORMAT or not isinstance(decoded.get("files"), dict):
        raise BackupError("Decrypted state backup payload is invalid.")
    files: dict[str, bytes] = {}
    for name, record in decoded["files"].items():
        if name not in STATE_FILES or not isinstance(record, dict):
            continue
        try:
            data = base64.b64decode(str(record["data"]), validate=True)
        except Exception as exc:
            raise BackupError(f"Backup entry {name} is malformed.") from exc
        if hashlib.sha256(data).hexdigest() != str(record.get("sha256", "")):
            raise BackupError(f"Backup entry {name} failed SHA-256 verification.")
        if name == "state.json":
            _validate_state_json(data)
        else:
            _validate_sqlite(data)
        files[name] = data
    if not files:
        raise BackupError("Encrypted backup contains no usable state files.")
    return files


def restore(input_path: Path, state_dir: Path, *, only_missing: bool = True) -> list[str]:
    files = _decrypt_blob(input_path)
    state_dir.mkdir(parents=True, exist_ok=True)
    restored: list[str] = []
    # Validate every payload before mutating anything; then atomic replace each file.
    for name, data in files.items():
        destination = state_dir / name
        if only_missing and destination.exists():
            continue
        temp = destination.with_suffix(destination.suffix + ".restore.tmp")
        temp.write_bytes(data)
        os.replace(temp, destination)
        restored.append(name)
    return restored


def main() -> int:
    parser = argparse.ArgumentParser(description="Authenticated encrypted backup for private bot state.")
    sub = parser.add_subparsers(dest="command", required=True)
    enc = sub.add_parser("encrypt")
    enc.add_argument("--state-dir", default=".state")
    enc.add_argument("--output", required=True)
    dec = sub.add_parser("restore")
    dec.add_argument("--input", required=True)
    dec.add_argument("--state-dir", default=".state")
    dec.add_argument("--overwrite", action="store_true")
    args = parser.parse_args()
    try:
        if args.command == "encrypt":
            encrypt(Path(args.state_dir), Path(args.output))
            return 0
        restored = restore(Path(args.input), Path(args.state_dir), only_missing=not args.overwrite)
        print("Restored encrypted state entries:", ", ".join(restored) if restored else "none (cache already present)")
        return 0
    except BackupError as exc:
        print(f"State backup error: {exc}")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
