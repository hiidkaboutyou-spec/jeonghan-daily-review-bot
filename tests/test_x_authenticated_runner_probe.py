import asyncio
import os
import unittest
from unittest.mock import patch

from tools import x_authenticated_runner_probe as diagnostic


class AuthenticatedRunnerProbeTests(unittest.TestCase):
    def test_coarse_failures_do_not_contain_private_messages(self):
        secret = "auth_token=private-token; ct0=private-csrf"
        for error, kind in (
            (f"HttpStatusError 403; {secret}", "http_403"),
            (f"HttpStatusError 401; {secret}", "http_401"),
            (f"HttpStatusError 429; {secret}", "rate_limited"),
            (f"XClIdParseError; {secret}", "transaction_id_unavailable"),
            (f"unexpected; {secret}", "other_failure"),
        ):
            with self.subTest(kind=kind):
                self.assertEqual(diagnostic.failure_kind(RuntimeError(error)), kind)
                self.assertNotIn("private-token", kind)

    def test_missing_credentials_do_not_call_provider(self):
        with patch.dict(os.environ, {"X_COOKIE": ""}), patch.object(
            diagnostic, "_read_profile"
        ) as collector:
            self.assertEqual(
                asyncio.run(diagnostic.probe()),
                {"authenticated_profile": "missing_credentials"},
            )
            collector.assert_not_called()

    def test_success_and_403_use_disposable_account_database(self):
        for error, expected in ((None, "verified"), (RuntimeError("403 secret"), "http_403")):
            with self.subTest(expected=expected), patch.dict(
                os.environ, {"X_COOKIE": "auth_token=sample; ct0=sample"}
            ), patch.object(diagnostic, "_read_profile") as collector:
                async def healthcheck(_cookies, db_path, _observed):
                    self.assertFalse(db_path.exists())
                    if error:
                        raise error

                collector.side_effect = healthcheck
                self.assertEqual(asyncio.run(diagnostic.probe())["authenticated_profile"], expected)
                self.assertEqual(collector.call_count, 1)

    def test_cookie_formats(self):
        self.assertEqual(diagnostic.parse_cookies('{"auth_token":"a", "ct0":"b"}')["ct0"], "b")
        self.assertEqual(diagnostic.parse_cookies("auth_token=a; ct0=b")["auth_token"], "a")

    def test_upstream_403_log_survives_generic_account_lock_error(self):
        async def locked(_cookies, _db_path, observed):
            observed.add("http_403")
            raise RuntimeError("No account available")

        with patch.dict(os.environ, {"X_COOKIE": "auth_token=a; ct0=b"}), patch.object(
            diagnostic, "_read_profile", side_effect=locked
        ):
            self.assertEqual(asyncio.run(diagnostic.probe()), {"authenticated_profile": "http_403"})
