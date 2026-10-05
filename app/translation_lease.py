from __future__ import annotations

"""Short-lived encrypted translation-provider leases for the webhook runtime."""

import base64
import hashlib
import json
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa

FORMAT = "hani-translation-lease-v1"
ALGORITHM = "RSA-OAEP-SHA256"
AUDIENCE = "jeonghan-webhook-production"
PRIVATE_KEY_ENV = "TRANSLATION_LEASE_PRIVATE_KEY_B64"
PUBLIC_KEY_PATH = Path(__file__).resolve().parents[1] / "config" / "translation_lease_public.pem"
DEFAULT_TTL_SECONDS = 8 * 60
MAX_TTL_SECONDS = 15 * 60
CLOCK_SKEW_SECONDS = 90


class TranslationLeaseError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class TranslationLease:
    provider: str
    model: str
    api_key: str
    issued_at: int
    expires_at: int
    key_id: str


def _public_key_id(public_key: rsa.RSAPublicKey) -> str:
    der = public_key.public_bytes(
        encoding=serialization.Encoding.DER,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    return hashlib.sha256(der).hexdigest()


def _load_public_key(path: Path = PUBLIC_KEY_PATH) -> rsa.RSAPublicKey:
    try:
        raw = path.read_bytes()
        key = serialization.load_pem_public_key(raw)
    except (OSError, ValueError, TypeError) as exc:
        raise TranslationLeaseError("translation lease public key is unavailable") from exc
    if not isinstance(key, rsa.RSAPublicKey):
        raise TranslationLeaseError("translation lease public key must be RSA")
    return key


def _load_private_key(raw_b64: str | None = None) -> rsa.RSAPrivateKey:
    encoded = str(raw_b64 if raw_b64 is not None else os.getenv(PRIVATE_KEY_ENV, "")).strip()
    if not encoded:
        raise TranslationLeaseError("translation lease private key is not configured")
    try:
        pem = base64.b64decode(encoded, validate=True)
        key = serialization.load_pem_private_key(pem, password=None)
    except Exception as exc:
        raise TranslationLeaseError("translation lease private key is invalid") from exc
    if not isinstance(key, rsa.RSAPrivateKey):
        raise TranslationLeaseError("translation lease private key must be RSA")
    return key


def build_translation_lease(
    api_key: str,
    model: str,
    *,
    now: int | None = None,
    ttl_seconds: int = DEFAULT_TTL_SECONDS,
    public_key_path: Path = PUBLIC_KEY_PATH,
) -> dict[str, str]:
    secret = str(api_key or "").strip()
    model_name = str(model or "").strip()
    if not secret:
        raise TranslationLeaseError("translation API key is empty")
    if not model_name or len(model_name) > 160:
        raise TranslationLeaseError("translation model is invalid")
    try:
        ttl = int(ttl_seconds)
    except (TypeError, ValueError) as exc:
        raise TranslationLeaseError("translation lease ttl is invalid") from exc
    if ttl < 60 or ttl > MAX_TTL_SECONDS:
        raise TranslationLeaseError("translation lease ttl is outside the allowed range")

    issued_at = int(time.time() if now is None else now)
    public_key = _load_public_key(public_key_path)
    key_id = _public_key_id(public_key)
    payload = {
        "format": FORMAT,
        "aud": AUDIENCE,
        "provider": "gemini",
        "model": model_name,
        "api_key": secret,
        "issued_at": issued_at,
        "expires_at": issued_at + ttl,
        "kid": key_id,
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    try:
        ciphertext = public_key.encrypt(
            encoded,
            padding.OAEP(
                mgf=padding.MGF1(algorithm=hashes.SHA256()),
                algorithm=hashes.SHA256(),
                label=FORMAT.encode("ascii"),
            ),
        )
    except ValueError as exc:
        raise TranslationLeaseError("translation lease payload is too large") from exc
    return {
        "format": FORMAT,
        "alg": ALGORITHM,
        "kid": key_id,
        "ciphertext": base64.b64encode(ciphertext).decode("ascii"),
    }


def consume_translation_lease(
    envelope: Any,
    *,
    now: int | None = None,
    private_key_b64: str | None = None,
) -> TranslationLease:
    if not isinstance(envelope, dict):
        raise TranslationLeaseError("translation lease envelope must be an object")
    if envelope.get("format") != FORMAT or envelope.get("alg") != ALGORITHM:
        raise TranslationLeaseError("translation lease envelope format is unsupported")
    ciphertext_raw = str(envelope.get("ciphertext") or "").strip()
    if not ciphertext_raw:
        raise TranslationLeaseError("translation lease ciphertext is missing")
    try:
        ciphertext = base64.b64decode(ciphertext_raw, validate=True)
    except Exception as exc:
        raise TranslationLeaseError("translation lease ciphertext is malformed") from exc

    private_key = _load_private_key(private_key_b64)
    expected_kid = _public_key_id(private_key.public_key())
    if str(envelope.get("kid") or "") != expected_kid:
        raise TranslationLeaseError("translation lease key id does not match")
    try:
        plaintext = private_key.decrypt(
            ciphertext,
            padding.OAEP(
                mgf=padding.MGF1(algorithm=hashes.SHA256()),
                algorithm=hashes.SHA256(),
                label=FORMAT.encode("ascii"),
            ),
        )
        payload = json.loads(plaintext.decode("utf-8"))
    except Exception as exc:
        raise TranslationLeaseError("translation lease failed authentication") from exc

    if not isinstance(payload, dict):
        raise TranslationLeaseError("translation lease payload is invalid")
    if payload.get("format") != FORMAT or payload.get("aud") != AUDIENCE:
        raise TranslationLeaseError("translation lease audience is invalid")
    if payload.get("provider") != "gemini":
        raise TranslationLeaseError("translation lease provider is unsupported")
    if str(payload.get("kid") or "") != expected_kid:
        raise TranslationLeaseError("translation lease payload key id does not match")

    try:
        issued_at = int(payload["issued_at"])
        expires_at = int(payload["expires_at"])
    except (KeyError, TypeError, ValueError) as exc:
        raise TranslationLeaseError("translation lease timestamps are invalid") from exc
    current = int(time.time() if now is None else now)
    if issued_at > current + CLOCK_SKEW_SECONDS:
        raise TranslationLeaseError("translation lease is not valid yet")
    if expires_at < current:
        raise TranslationLeaseError("translation lease has expired")
    if expires_at <= issued_at or expires_at - issued_at > MAX_TTL_SECONDS:
        raise TranslationLeaseError("translation lease lifetime is invalid")

    api_key = str(payload.get("api_key") or "").strip()
    model = str(payload.get("model") or "").strip()
    if not api_key or len(api_key) > 1024:
        raise TranslationLeaseError("translation lease API key is invalid")
    if not model or len(model) > 160:
        raise TranslationLeaseError("translation lease model is invalid")

    return TranslationLease(
        provider="gemini",
        model=model,
        api_key=api_key,
        issued_at=issued_at,
        expires_at=expires_at,
        key_id=expected_kid,
    )


def apply_translation_lease(application: Any, lease: TranslationLease) -> bool:
    writer = getattr(application, "writer", None)
    current_provider = str(
        getattr(writer, "_translation_provider_name", "gemini") or "gemini"
    ).strip().casefold()
    if current_provider != "gemini":
        return False

    settings = getattr(application, "settings", None)
    if settings is not None:
        settings.gemini_api_key = lease.api_key
        settings.gemini_model = lease.model

    seen: set[int] = set()
    for name in ("writer", "legacy_writer"):
        target = getattr(application, name, None)
        if target is None or id(target) in seen:
            continue
        seen.add(id(target))
        if hasattr(target, "api_key"):
            target.api_key = lease.api_key
        if hasattr(target, "model"):
            target.model = lease.model
        if hasattr(target, "_client"):
            target._client = None
        if hasattr(target, "_gemini_circuit_open"):
            target._gemini_circuit_open = ""
        if name == "writer":
            setattr(target, "_translation_provider_name", "gemini")
            setattr(target, "_translation_model", lease.model)
    return True
