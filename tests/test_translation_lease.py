from __future__ import annotations

import asyncio
import base64
import json
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa

from app.translation_lease import (
    ALGORITHM,
    AUDIENCE,
    FORMAT,
    TranslationLeaseError,
    apply_translation_lease,
    build_translation_lease,
    consume_translation_lease,
)
from app.webhook_aware_assistant import (
    WEBHOOK_DELEGATED_EXIT_CODE,
    WEBHOOK_MAINTENANCE_FAILED_EXIT_CODE,
    WebhookAwarePersonalAssistant,
)
from app.webhook_runtime_utils import maintenance_url_from_webhook


class TranslationLeaseTests(unittest.TestCase):
    def setUp(self) -> None:
        self.private_key = rsa.generate_private_key(public_exponent=65537, key_size=3072)
        self.public_pem = self.private_key.public_key().public_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PublicFormat.SubjectPublicKeyInfo,
        )
        self.private_b64 = base64.b64encode(
            self.private_key.private_bytes(
                encoding=serialization.Encoding.PEM,
                format=serialization.PrivateFormat.PKCS8,
                encryption_algorithm=serialization.NoEncryption(),
            )
        ).decode("ascii")

    def _public_path(self) -> Path:
        temp = tempfile.NamedTemporaryFile(delete=False)
        temp.write(self.public_pem)
        temp.close()
        self.addCleanup(lambda: Path(temp.name).unlink(missing_ok=True))
        return Path(temp.name)

    def _custom_envelope(self, payload: dict[str, object]) -> dict[str, str]:
        public_key = self.private_key.public_key()
        kid = __import__("hashlib").sha256(
            public_key.public_bytes(
                encoding=serialization.Encoding.DER,
                format=serialization.PublicFormat.SubjectPublicKeyInfo,
            )
        ).hexdigest()
        payload = dict(payload)
        payload["kid"] = kid
        ciphertext = public_key.encrypt(
            json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8"),
            padding.OAEP(
                mgf=padding.MGF1(algorithm=hashes.SHA256()),
                algorithm=hashes.SHA256(),
                label=FORMAT.encode("ascii"),
            ),
        )
        return {
            "format": FORMAT,
            "alg": ALGORITHM,
            "kid": kid,
            "ciphertext": base64.b64encode(ciphertext).decode("ascii"),
        }

    def test_round_trip_hides_plaintext_and_preserves_provider(self) -> None:
        secret = "gemini-test-secret-value"
        envelope = build_translation_lease(
            secret,
            "gemini-3.5-flash-lite",
            now=1_800_000_000,
            public_key_path=self._public_path(),
        )
        self.assertNotIn(secret, repr(envelope))

        lease = consume_translation_lease(
            envelope,
            now=1_800_000_010,
            private_key_b64=self.private_b64,
        )
        self.assertEqual(lease.provider, "gemini")
        self.assertEqual(lease.model, "gemini-3.5-flash-lite")
        self.assertEqual(lease.api_key, secret)

    def test_expired_lease_fails_closed(self) -> None:
        envelope = build_translation_lease(
            "secret",
            "gemini-test",
            now=1_800_000_000,
            ttl_seconds=60,
            public_key_path=self._public_path(),
        )
        with self.assertRaisesRegex(TranslationLeaseError, "expired"):
            consume_translation_lease(
                envelope,
                now=1_800_000_061,
                private_key_b64=self.private_b64,
            )

    def test_wrong_audience_fails_closed(self) -> None:
        now = 1_800_000_000
        envelope = self._custom_envelope(
            {
                "format": FORMAT,
                "aud": "attacker-controlled-host",
                "provider": "gemini",
                "model": "gemini-test",
                "api_key": "secret",
                "issued_at": now,
                "expires_at": now + 300,
            }
        )
        with self.assertRaisesRegex(TranslationLeaseError, "audience"):
            consume_translation_lease(
                envelope,
                now=now + 1,
                private_key_b64=self.private_b64,
            )

    def test_apply_lease_refreshes_gemini_writers_only_in_memory(self) -> None:
        lease = SimpleNamespace(
            provider="gemini",
            model="gemini-new",
            api_key="leased-secret",
            issued_at=1,
            expires_at=2,
            key_id="kid",
        )
        writer = SimpleNamespace(
            api_key="",
            model="old",
            _client=object(),
            _gemini_circuit_open="quota",
            _translation_provider_name="gemini",
            _translation_model="old",
        )
        legacy = SimpleNamespace(api_key="", model="old", _client=object())
        settings = SimpleNamespace(gemini_api_key="", gemini_model="old")
        app = SimpleNamespace(writer=writer, legacy_writer=legacy, settings=settings)

        self.assertTrue(apply_translation_lease(app, lease))
        self.assertEqual(writer.api_key, "leased-secret")
        self.assertEqual(writer.model, "gemini-new")
        self.assertIsNone(writer._client)
        self.assertEqual(writer._gemini_circuit_open, "")
        self.assertEqual(legacy.api_key, "leased-secret")
        self.assertEqual(settings.gemini_api_key, "leased-secret")

    def test_gemini_lease_never_overrides_explicit_ollama(self) -> None:
        lease = SimpleNamespace(
            provider="gemini",
            model="gemini-new",
            api_key="leased-secret",
            issued_at=1,
            expires_at=2,
            key_id="kid",
        )
        writer = SimpleNamespace(
            api_key="",
            model="local",
            _translation_provider_name="ollama",
            _translation_model="qwen-local",
        )
        settings = SimpleNamespace(gemini_api_key="", gemini_model="old")
        app = SimpleNamespace(writer=writer, legacy_writer=None, settings=settings)

        self.assertFalse(apply_translation_lease(app, lease))
        self.assertEqual(writer._translation_provider_name, "ollama")
        self.assertEqual(settings.gemini_api_key, "")

    def test_maintenance_origin_pinning_rejects_untrusted_webhook(self) -> None:
        trusted = ["https://assistant.example"]
        self.assertEqual(
            maintenance_url_from_webhook(
                "https://assistant.example/telegram/webhook",
                trusted_origins=trusted,
            ),
            "https://assistant.example/maintenance",
        )
        self.assertEqual(
            maintenance_url_from_webhook(
                "https://evil.example/telegram/webhook",
                trusted_origins=trusted,
            ),
            "",
        )

    def test_actions_delegation_encrypts_secret_and_disables_redirects(self) -> None:
        app = object.__new__(WebhookAwarePersonalAssistant)
        session = SimpleNamespace(post=Mock(return_value=SimpleNamespace(status_code=200)))
        app.telegram = SimpleNamespace(
            api=Mock(
                return_value={
                    "url": (
                        "https://jeonghan-personal-assistant-production.up.railway.app"
                        "/telegram/webhook"
                    )
                }
            ),
            session=session,
        )
        app.settings = SimpleNamespace(
            telegram_token="123:abc",
            gemini_api_key="never-send-plaintext",
            gemini_model="gemini-3.5-flash-lite",
            runtime={
                "trusted_webhook_origins": [
                    "https://jeonghan-personal-assistant-production.up.railway.app"
                ]
            },
        )

        with patch.dict(
            os.environ,
            {"ASSISTANT_RUNTIME_MODE": "github_actions_auto"},
            clear=False,
        ):
            code = asyncio.run(app.run())

        self.assertEqual(code, WEBHOOK_DELEGATED_EXIT_CODE)
        session.post.assert_called_once()
        kwargs = session.post.call_args.kwargs
        self.assertFalse(kwargs["allow_redirects"])
        self.assertNotIn("never-send-plaintext", repr(kwargs["json"]))
        self.assertIn("translation_lease", kwargs["json"])

    def test_actions_auto_refuses_credential_handoff_to_unknown_origin(self) -> None:
        app = object.__new__(WebhookAwarePersonalAssistant)
        session = SimpleNamespace(post=Mock())
        app.telegram = SimpleNamespace(
            api=Mock(return_value={"url": "https://evil.example/telegram/webhook"}),
            session=session,
        )
        app.settings = SimpleNamespace(
            telegram_token="123:abc",
            gemini_api_key="never-send-plaintext",
            gemini_model="gemini-3.5-flash-lite",
            runtime={
                "trusted_webhook_origins": [
                    "https://jeonghan-personal-assistant-production.up.railway.app"
                ]
            },
        )

        with patch.dict(
            os.environ,
            {"ASSISTANT_RUNTIME_MODE": "github_actions_auto"},
            clear=False,
        ):
            code = asyncio.run(app.run())

        self.assertEqual(code, WEBHOOK_MAINTENANCE_FAILED_EXIT_CODE)
        session.post.assert_not_called()


if __name__ == "__main__":
    unittest.main()
