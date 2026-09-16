from __future__ import annotations

import io
import json
import unittest
import zipfile
from datetime import datetime, timezone
from email.message import Message
from types import SimpleNamespace
from unittest.mock import MagicMock, Mock, call, patch
from urllib import error

from tools import daily_watchdog as watchdog


class DailyWatchdogCanonicalTransportContractTests(unittest.TestCase):
    """Lock the credential-safe artifact transport to the canonical module."""

    @staticmethod
    def _response(body: bytes) -> MagicMock:
        response = MagicMock()
        response.__enter__.return_value.read.return_value = body
        return response

    @staticmethod
    def _headers(req) -> dict[str, str]:
        return {str(key).lower(): str(value) for key, value in req.header_items()}

    @staticmethod
    def _redirect_error(url: str, code: int, *, location: str | None) -> error.HTTPError:
        headers = Message()
        if location is not None:
            headers["Location"] = location
        return error.HTTPError(url, code, "redirect", headers, None)

    def test_canonical_client_owns_hardened_fetch_method(self) -> None:
        self.assertIs(
            watchdog.GitHubActionsClient.fetch_latest_production_outcome,
            watchdog._fetch_latest_production_outcome,
        )

    def test_canonical_no_redirect_handler_refuses_automatic_redirect(self) -> None:
        handler = watchdog._NoRedirect()
        self.assertIsNone(
            handler.redirect_request(
                object(),
                object(),
                302,
                "Found",
                Message(),
                "https://example.invalid/signed",
            )
        )

    def test_canonical_direct_download_keeps_api_auth_on_initial_request_only(self) -> None:
        client = SimpleNamespace(
            base="https://api.github.com/repos/example/hani",
            token="super-secret-token",
            timeout=17,
        )
        opener = MagicMock()
        opener.open.return_value = self._response(b"direct-zip")

        with (
            patch.object(watchdog.request, "build_opener", return_value=opener) as build_opener,
            patch.object(watchdog.request, "urlopen") as urlopen,
        ):
            result = watchdog._download_zip_without_cross_origin_auth(client, 123)

        self.assertEqual(result, b"direct-zip")
        self.assertIsInstance(build_opener.call_args.args[0], watchdog._NoRedirect)
        request_obj = opener.open.call_args.args[0]
        self.assertEqual(
            request_obj.full_url,
            "https://api.github.com/repos/example/hani/actions/artifacts/123/zip",
        )
        self.assertEqual(request_obj.get_method(), "GET")
        self.assertEqual(opener.open.call_args.kwargs["timeout"], 17)
        headers = self._headers(request_obj)
        self.assertEqual(headers["authorization"], "Bearer super-secret-token")
        self.assertEqual(headers["accept"], "application/vnd.github+json")
        self.assertEqual(headers["x-github-api-version"], "2022-11-28")
        self.assertEqual(headers["user-agent"], "jeonghan-daily-watchdog")
        urlopen.assert_not_called()

    def test_canonical_redirect_never_forwards_github_credentials(self) -> None:
        client = SimpleNamespace(
            base="https://api.github.com/repos/example/hani",
            token="super-secret-token",
            timeout=23,
        )
        signed_url = "https://pipelines.actions.githubusercontent.com/signed-artifact"

        for status in (301, 302, 303, 307, 308):
            with self.subTest(status=status):
                zip_url = f"{client.base}/actions/artifacts/456/zip"
                opener = MagicMock()
                opener.open.side_effect = self._redirect_error(
                    zip_url,
                    status,
                    location=signed_url,
                )

                with (
                    patch.object(watchdog.request, "build_opener", return_value=opener),
                    patch.object(
                        watchdog.request,
                        "urlopen",
                        return_value=self._response(b"redirected-zip"),
                    ) as urlopen,
                ):
                    result = watchdog._download_zip_without_cross_origin_auth(client, 456)

                self.assertEqual(result, b"redirected-zip")
                first_headers = self._headers(opener.open.call_args.args[0])
                self.assertEqual(
                    first_headers.get("authorization"),
                    "Bearer super-secret-token",
                )

                redirected_request = urlopen.call_args.args[0]
                redirected_headers = self._headers(redirected_request)
                self.assertEqual(redirected_request.full_url, signed_url)
                self.assertEqual(urlopen.call_args.kwargs["timeout"], 23)
                self.assertEqual(
                    redirected_headers,
                    {"user-agent": "jeonghan-daily-watchdog"},
                )
                self.assertNotIn("authorization", redirected_headers)
                self.assertNotIn("accept", redirected_headers)
                self.assertNotIn("x-github-api-version", redirected_headers)

    def test_canonical_redirect_without_location_fails_closed(self) -> None:
        client = SimpleNamespace(
            base="https://api.github.com/repos/example/hani",
            token="token",
            timeout=9,
        )
        zip_url = f"{client.base}/actions/artifacts/789/zip"
        opener = MagicMock()
        opener.open.side_effect = self._redirect_error(zip_url, 302, location=None)

        with (
            patch.object(watchdog.request, "build_opener", return_value=opener),
            patch.object(watchdog.request, "urlopen") as urlopen,
            self.assertRaises(error.HTTPError),
        ):
            watchdog._download_zip_without_cross_origin_auth(client, 789)

        urlopen.assert_not_called()

    def test_canonical_non_redirect_http_error_is_not_hidden(self) -> None:
        client = SimpleNamespace(
            base="https://api.github.com/repos/example/hani",
            token="token",
            timeout=9,
        )
        zip_url = f"{client.base}/actions/artifacts/790/zip"
        opener = MagicMock()
        opener.open.side_effect = error.HTTPError(
            zip_url,
            410,
            "Gone",
            Message(),
            None,
        )

        with (
            patch.object(watchdog.request, "build_opener", return_value=opener),
            patch.object(watchdog.request, "urlopen") as urlopen,
            self.assertRaises(error.HTTPError),
        ):
            watchdog._download_zip_without_cross_origin_auth(client, 790)

        urlopen.assert_not_called()

    def test_canonical_fetch_parses_outcome_and_retries_boundedly(self) -> None:
        outcome = {
            "schema_version": 1,
            "outcome_status": "healthy",
            "useful_work_performed": True,
        }
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w") as archive:
            archive.writestr("production-outcome.json", json.dumps(outcome))

        client = SimpleNamespace(
            base="https://api.github.com/repos/example/hani",
            _request=Mock(
                return_value={
                    "artifacts": [
                        {
                            "name": "production-outcome",
                            "id": 991,
                            "created_at": datetime.now(timezone.utc).isoformat(),
                        }
                    ]
                }
            ),
        )
        with patch.object(
            watchdog,
            "_download_zip_without_cross_origin_auth",
            return_value=buffer.getvalue(),
        ) as download:
            result = watchdog._fetch_latest_production_outcome(client, 77)

        self.assertEqual(result, outcome)
        download.assert_called_once_with(client, 991)

        failing_client = SimpleNamespace(
            _request=Mock(side_effect=RuntimeError("boom")),
            base="https://api.github.com/repos/example/hani",
        )
        with (
            patch.object(watchdog.time, "sleep") as sleep,
            patch.object(watchdog, "log_decision") as log_decision,
        ):
            result = watchdog._fetch_latest_production_outcome(failing_client, 88)

        self.assertIsNone(result)
        self.assertEqual(failing_client._request.call_count, 3)
        self.assertEqual(sleep.call_args_list, [call(1), call(1)])
        log_decision.assert_called_once_with(
            "outcome_fetch_failed",
            error="RuntimeError",
            http_status="",
            run_id=88,
        )


if __name__ == "__main__":
    unittest.main()
