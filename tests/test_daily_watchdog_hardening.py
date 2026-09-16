from __future__ import annotations

import importlib
import io
import json
import unittest
import zipfile
from datetime import datetime, timezone
from email.message import Message
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, Mock, call, patch
from urllib import error

from tools import daily_watchdog as watchdog

ROOT = Path(__file__).resolve().parents[1]


class DailyWatchdogHardeningTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        # Importing the compatibility transport module intentionally patches the
        # canonical watchdog client. Keep that side effect scoped to this test
        # class so unrelated unit tests continue to exercise normal import state.
        cls._original_fetch = watchdog.GitHubActionsClient.fetch_latest_production_outcome
        cls.hardening = importlib.import_module("tools.daily_watchdog_hardening")

    @classmethod
    def tearDownClass(cls) -> None:
        watchdog.GitHubActionsClient.fetch_latest_production_outcome = cls._original_fetch

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

    def test_import_installs_hardened_fetch_method(self) -> None:
        self.assertIs(
            watchdog.GitHubActionsClient.fetch_latest_production_outcome,
            self.hardening._fetch_latest_production_outcome,
        )

    def test_no_redirect_handler_refuses_automatic_redirect(self) -> None:
        handler = self.hardening._NoRedirect()
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

    def test_direct_artifact_response_keeps_github_auth_on_api_request_only(self) -> None:
        client = SimpleNamespace(
            base="https://api.github.com/repos/example/hani",
            token="super-secret-token",
            timeout=17,
        )
        opener = MagicMock()
        opener.open.return_value = self._response(b"direct-zip")

        with (
            patch.object(self.hardening.request, "build_opener", return_value=opener) as build_opener,
            patch.object(self.hardening.request, "urlopen") as urlopen,
        ):
            result = self.hardening._download_zip_without_cross_origin_auth(client, 123)

        self.assertEqual(result, b"direct-zip")
        self.assertIsInstance(build_opener.call_args.args[0], self.hardening._NoRedirect)
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

    def test_cross_origin_redirect_never_forwards_github_credentials(self) -> None:
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
                redirected_response = self._response(b"redirected-zip")

                with (
                    patch.object(self.hardening.request, "build_opener", return_value=opener),
                    patch.object(
                        self.hardening.request,
                        "urlopen",
                        return_value=redirected_response,
                    ) as urlopen,
                ):
                    result = self.hardening._download_zip_without_cross_origin_auth(
                        client,
                        456,
                    )

                self.assertEqual(result, b"redirected-zip")
                first_request = opener.open.call_args.args[0]
                first_headers = self._headers(first_request)
                self.assertEqual(
                    first_headers.get("authorization"),
                    "Bearer super-secret-token",
                )

                redirected_request = urlopen.call_args.args[0]
                redirected_headers = self._headers(redirected_request)
                self.assertEqual(redirected_request.full_url, signed_url)
                self.assertEqual(redirected_request.get_method(), "GET")
                self.assertEqual(urlopen.call_args.kwargs["timeout"], 23)
                self.assertEqual(
                    redirected_headers,
                    {"user-agent": "jeonghan-daily-watchdog"},
                )
                self.assertNotIn("authorization", redirected_headers)
                self.assertNotIn("accept", redirected_headers)
                self.assertNotIn("x-github-api-version", redirected_headers)

    def test_redirect_without_location_fails_closed(self) -> None:
        client = SimpleNamespace(
            base="https://api.github.com/repos/example/hani",
            token="token",
            timeout=9,
        )
        zip_url = f"{client.base}/actions/artifacts/789/zip"
        opener = MagicMock()
        opener.open.side_effect = self._redirect_error(zip_url, 302, location=None)

        with (
            patch.object(self.hardening.request, "build_opener", return_value=opener),
            patch.object(self.hardening.request, "urlopen") as urlopen,
            self.assertRaises(error.HTTPError),
        ):
            self.hardening._download_zip_without_cross_origin_auth(client, 789)

        urlopen.assert_not_called()

    def test_non_redirect_http_error_is_not_hidden(self) -> None:
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
            patch.object(self.hardening.request, "build_opener", return_value=opener),
            patch.object(self.hardening.request, "urlopen") as urlopen,
            self.assertRaises(error.HTTPError),
        ):
            self.hardening._download_zip_without_cross_origin_auth(client, 790)

        urlopen.assert_not_called()

    def test_fetch_parses_production_outcome_from_in_memory_zip(self) -> None:
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
            self.hardening,
            "_download_zip_without_cross_origin_auth",
            return_value=buffer.getvalue(),
        ) as download:
            result = self.hardening._fetch_latest_production_outcome(client, 77)

        self.assertEqual(result, outcome)
        client._request.assert_called_once_with(
            "GET",
            "https://api.github.com/repos/example/hani/actions/runs/77/artifacts?per_page=30",
        )
        download.assert_called_once_with(client, 991)

    def test_fetch_retries_boundedly_and_reports_final_failure(self) -> None:
        client = SimpleNamespace(
            _request=Mock(side_effect=RuntimeError("boom")),
            base="https://api.github.com/repos/example/hani",
        )

        with (
            patch.object(self.hardening.time, "sleep") as sleep,
            patch.object(self.hardening._watchdog, "log_decision") as log_decision,
        ):
            result = self.hardening._fetch_latest_production_outcome(client, 88)

        self.assertIsNone(result)
        self.assertEqual(client._request.call_count, 3)
        self.assertEqual(sleep.call_args_list, [call(1), call(1)])
        log_decision.assert_called_once_with(
            "outcome_fetch_failed",
            error="RuntimeError",
            http_status="",
            run_id=88,
        )

    def test_production_workflow_uses_semantic_entrypoint(self) -> None:
        workflow = (ROOT / ".github" / "workflows" / "daily-watchdog.yml").read_text(
            encoding="utf-8"
        )
        active_run_lines = [
            line.strip()
            for line in workflow.splitlines()
            if line.lstrip().startswith("run:")
        ]
        self.assertIn("run: python tools/daily_watchdog_runner.py", active_run_lines)
        self.assertNotIn("run: python tools/daily_watchdog_hardening.py", active_run_lines)


if __name__ == "__main__":
    unittest.main()
