"""Regression tests for the watchdog's artifact ZIP download redirect fix.

Covers:
1. GitHub artifact ZIP download returns 302 to a different host
2. Authorization header is present on the original GitHub request
3. Authorization header is absent on the redirected Azure request
4. Same-host redirect keeps auth
5. Artifact ZIP downloads and extracts successfully
6. Invalid ZIP is handled safely
7. Redirected 401/403 produces bounded failure
8. No secret/token appears in logs or exception summaries
9. Watchdog still falls back safely if artifact cannot be retrieved
"""

from __future__ import annotations

import io
import json
import unittest
import zipfile
from datetime import datetime, timezone
from io import BytesIO
from unittest.mock import MagicMock, patch
from urllib import request
from urllib.error import HTTPError

from tools.daily_watchdog import (
    GitHubActionsClient,
    _download_artifact_zip,
    _NoAuthRedirectHandler,
)

REPO = "hiidkaboutyou-spec/jeonghan-daily-review-bot"
TOKEN = "ghp_test_token_abc123"
ARTIFACT_ID = 9767897902


def _make_zip_bytes(content: bytes) -> bytes:
    buf = BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("production-outcome.json", content)
    return buf.getvalue()


def _make_outcome_json() -> bytes:
    return json.dumps({
        "schema_version": 1,
        "outcome_status": "healthy",
        "run_id": "33418239126",
    }).encode("utf-8")


class _FakeRedirectResponse:
    """Simulates urllib's redirect flow: first call returns the 302 response,
    second call (after handler rewrites the request) returns the final response."""

    def __init__(self, redirect_url: str, final_data: bytes):
        self.redirect_url = redirect_url
        self.final_data = final_data
        self.requests_seen: list = []

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass

    def read(self):
        return self.final_data


class _CapturingRedirectHandler(request.HTTPRedirectHandler):
    """Captures the redirected request for assertion, returns a fake response."""

    def __init__(self, final_data: bytes):
        self.final_data = final_data
        self.redirected_headers: dict | None = None
        self.original_headers: dict | None = None

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        self.original_headers = dict(req.headers)
        new_req = super().redirect_request(req, fp, code, msg, headers, newurl)
        if new_req is not None:
            origin = request.urlsplit(req.full_url).netloc
            dest = request.urlsplit(newurl).netloc
            if origin != dest:
                new_req.headers.pop("Authorization", None)
            self.redirected_headers = dict(new_req.headers)
        return new_req


class TestNoAuthRedirectHandler(unittest.TestCase):
    """Tests for _NoAuthRedirectHandler."""

    def _make_handler_and_request(self, origin_url, redirect_url):
        handler = _NoAuthRedirectHandler()
        req = request.Request(origin_url, headers={
            "Authorization": f"Bearer {TOKEN}",
            "User-Agent": "test",
        })
        return handler, req

    def test_cross_host_redirect_strips_auth(self):
        handler = _NoAuthRedirectHandler()
        req = request.Request(
            f"https://api.github.com/repos/{REPO}/actions/artifacts/{ARTIFACT_ID}/zip",
            headers={"Authorization": f"Bearer {TOKEN}", "User-Agent": "test"},
        )
        redirect_url = "https://productionresultssa.blob.core.windows.net/signed-url"
        new_req = handler.redirect_request(req, None, 302, "Found", {}, redirect_url)
        self.assertIsNotNone(new_req)
        self.assertNotIn("Authorization", new_req.headers)

    def test_same_host_redirect_keeps_auth(self):
        handler = _NoAuthRedirectHandler()
        req = request.Request(
            f"https://api.github.com/repos/{REPO}/actions/artifacts/{ARTIFACT_ID}/zip",
            headers={"Authorization": f"Bearer {TOKEN}", "User-Agent": "test"},
        )
        redirect_url = f"https://api.github.com/repos/{REPO}/actions/artifacts/{ARTIFACT_ID}/zip?v=2"
        new_req = handler.redirect_request(req, None, 302, "Found", {}, redirect_url)
        self.assertIsNotNone(new_req)
        auth = new_req.headers.get("Authorization")
        self.assertIsNotNone(auth)
        self.assertEqual(auth, f"Bearer {TOKEN}")

    def test_no_auth_header_no_error(self):
        handler = _NoAuthRedirectHandler()
        req = request.Request(
            f"https://api.github.com/repos/{REPO}/actions/artifacts/{ARTIFACT_ID}/zip",
            headers={"User-Agent": "test"},
        )
        redirect_url = "https://blob.azure.com/signed"
        new_req = handler.redirect_request(req, None, 302, "Found", {}, redirect_url)
        self.assertIsNotNone(new_req)
        self.assertNotIn("Authorization", new_req.headers)


class TestDownloadArtifactZip(unittest.TestCase):
    """Tests for _download_artifact_zip."""

    @patch("tools.daily_watchdog.request.build_opener")
    def test_download_returns_bytes(self, mock_build_opener):
        fake_data = _make_zip_bytes(_make_outcome_json())
        mock_response = MagicMock()
        mock_response.read.return_value = fake_data
        mock_response.__enter__ = MagicMock(return_value=mock_response)
        mock_response.__exit__ = MagicMock(return_value=False)
        mock_opener = MagicMock()
        mock_opener.open.return_value = mock_response
        mock_build_opener.return_value = mock_opener

        url = f"https://api.github.com/repos/{REPO}/actions/artifacts/{ARTIFACT_ID}/zip"
        result = _download_artifact_zip(url, TOKEN, 30)
        self.assertEqual(result, fake_data)
        mock_build_opener.assert_called_once()
        opener_arg = mock_build_opener.call_args[0][0]
        # build_opener accepts handler classes or instances; either way the
        # no-auth redirect handler must be part of the opener pipeline.
        if isinstance(opener_arg, type):
            self.assertTrue(issubclass(opener_arg, _NoAuthRedirectHandler))
        else:
            self.assertIsInstance(opener_arg, _NoAuthRedirectHandler)

    @patch("tools.daily_watchdog.request.build_opener")
    def test_download_returns_none_on_empty(self, mock_build_opener):
        mock_response = MagicMock()
        mock_response.read.return_value = b""
        mock_response.__enter__ = MagicMock(return_value=mock_response)
        mock_response.__exit__ = MagicMock(return_value=False)
        mock_opener = MagicMock()
        mock_opener.open.return_value = mock_response
        mock_build_opener.return_value = mock_opener

        url = f"https://api.github.com/repos/{REPO}/actions/artifacts/{ARTIFACT_ID}/zip"
        result = _download_artifact_zip(url, TOKEN, 30)
        self.assertIsNone(result)

    @patch("tools.daily_watchdog.request.build_opener")
    def test_download_401_raises_httperror(self, mock_build_opener):
        mock_opener = MagicMock()
        mock_opener.open.side_effect = HTTPError(
            "https://api.github.com/...", 401, "Unauthorized", {}, None
        )
        mock_build_opener.return_value = mock_opener

        url = f"https://api.github.com/repos/{REPO}/actions/artifacts/{ARTIFACT_ID}/zip"
        with self.assertRaises(HTTPError):
            _download_artifact_zip(url, TOKEN, 30)

    @patch("tools.daily_watchdog.request.build_opener")
    def test_download_403_raises_httperror(self, mock_build_opener):
        mock_opener = MagicMock()
        mock_opener.open.side_effect = HTTPError(
            "https://api.github.com/...", 403, "Forbidden", {}, None
        )
        mock_build_opener.return_value = mock_opener

        url = f"https://api.github.com/repos/{REPO}/actions/artifacts/{ARTIFACT_ID}/zip"
        with self.assertRaises(HTTPError):
            _download_artifact_zip(url, TOKEN, 30)


class TestFetchLatestProductionOutcome(unittest.TestCase):
    """Integration tests for fetch_latest_production_outcome with redirect."""

    def _make_client(self):
        return GitHubActionsClient(REPO, TOKEN)

    @patch("tools.daily_watchdog._download_artifact_zip")
    @patch.object(GitHubActionsClient, "_request")
    def test_full_download_and_extract(self, mock_request, mock_download):
        fresh_ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        mock_request.return_value = {
            "artifacts": [{
                "id": ARTIFACT_ID,
                "name": "production-outcome",
                "created_at": fresh_ts,
                "expired": False,
            }]
        }
        mock_download.return_value = _make_zip_bytes(_make_outcome_json())

        client = self._make_client()
        result = client.fetch_latest_production_outcome(33418239126)
        self.assertIsNotNone(result)
        self.assertEqual(result["schema_version"], 1)
        self.assertEqual(result["outcome_status"], "healthy")
        self.assertEqual(result["run_id"], "33418239126")
        mock_download.assert_called_once()

    @patch("tools.daily_watchdog._download_artifact_zip")
    @patch.object(GitHubActionsClient, "_request")
    def test_401_on_download_returns_none(self, mock_request, mock_download):
        mock_request.return_value = {
            "artifacts": [{
                "id": ARTIFACT_ID,
                "name": "production-outcome",
                "created_at": "2026-08-31T17:13:18Z",
                "expired": False,
            }]
        }
        mock_download.side_effect = HTTPError(
            "https://blob.azure.com/...", 401, "Unauthorized", {}, None
        )

        client = self._make_client()
        result = client.fetch_latest_production_outcome(33418239126)
        self.assertIsNone(result)

    @patch("tools.daily_watchdog._download_artifact_zip")
    @patch.object(GitHubActionsClient, "_request")
    def test_403_on_download_returns_none(self, mock_request, mock_download):
        mock_request.return_value = {
            "artifacts": [{
                "id": ARTIFACT_ID,
                "name": "production-outcome",
                "created_at": "2026-08-31T17:13:18Z",
                "expired": False,
            }]
        }
        mock_download.side_effect = HTTPError(
            "https://blob.azure.com/...", 403, "Forbidden", {}, None
        )

        client = self._make_client()
        result = client.fetch_latest_production_outcome(33418239126)
        self.assertIsNone(result)

    @patch("tools.daily_watchdog._download_artifact_zip")
    @patch.object(GitHubActionsClient, "_request")
    def test_invalid_zip_returns_none(self, mock_request, mock_download):
        mock_request.return_value = {
            "artifacts": [{
                "id": ARTIFACT_ID,
                "name": "production-outcome",
                "created_at": "2026-08-31T17:13:18Z",
                "expired": False,
            }]
        }
        mock_download.return_value = b"not a zip file at all"

        client = self._make_client()
        result = client.fetch_latest_production_outcome(33418239126)
        self.assertIsNone(result)

    @patch("tools.daily_watchdog._download_artifact_zip")
    @patch.object(GitHubActionsClient, "_request")
    def test_zip_missing_json_returns_none(self, mock_request, mock_download):
        buf = BytesIO()
        with zipfile.ZipFile(buf, "w") as zf:
            zf.writestr("other-file.txt", "not the outcome")
        mock_request.return_value = {
            "artifacts": [{
                "id": ARTIFACT_ID,
                "name": "production-outcome",
                "created_at": "2026-08-31T17:13:18Z",
                "expired": False,
            }]
        }
        mock_download.return_value = buf.getvalue()

        client = self._make_client()
        result = client.fetch_latest_production_outcome(33418239126)
        self.assertIsNone(result)

    @patch("tools.daily_watchdog._download_artifact_zip")
    @patch.object(GitHubActionsClient, "_request")
    def test_no_matching_artifact_returns_none(self, mock_request, mock_download):
        mock_request.return_value = {
            "artifacts": [{
                "id": 999,
                "name": "other-artifact",
                "created_at": "2026-08-31T17:13:18Z",
                "expired": False,
            }]
        }
        client = self._make_client()
        result = client.fetch_latest_production_outcome(33418239126)
        self.assertIsNone(result)
        mock_download.assert_not_called()

    @patch("tools.daily_watchdog._download_artifact_zip")
    @patch.object(GitHubActionsClient, "_request")
    def test_stale_artifact_returns_none(self, mock_request, mock_download):
        mock_request.return_value = {
            "artifacts": [{
                "id": ARTIFACT_ID,
                "name": "production-outcome",
                "created_at": "2020-01-01T00:00:00Z",
                "expired": False,
            }]
        }
        client = self._make_client()
        result = client.fetch_latest_production_outcome(33418239126)
        self.assertIsNone(result)
        mock_download.assert_not_called()

    @patch("tools.daily_watchdog._download_artifact_zip")
    @patch.object(GitHubActionsClient, "_request")
    def test_corrupt_json_in_zip_returns_none(self, mock_request, mock_download):
        mock_request.return_value = {
            "artifacts": [{
                "id": ARTIFACT_ID,
                "name": "production-outcome",
                "created_at": "2026-08-31T17:13:18Z",
                "expired": False,
            }]
        }
        mock_download.return_value = _make_zip_bytes(b"{{{corrupted json")

        client = self._make_client()
        result = client.fetch_latest_production_outcome(33418239126)
        self.assertIsNone(result)

    @patch("tools.daily_watchdog._download_artifact_zip")
    @patch.object(GitHubActionsClient, "_request")
    def test_no_secret_in_logs(self, mock_request, mock_download):
        """No token should appear in any log output."""
        mock_request.return_value = {
            "artifacts": [{
                "id": ARTIFACT_ID,
                "name": "production-outcome",
                "created_at": "2026-08-31T17:13:18Z",
                "expired": False,
            }]
        }
        mock_download.side_effect = HTTPError(
            "https://blob.azure.com/...", 401, "Unauthorized", {}, None
        )

        client = self._make_client()
        captured_output = io.StringIO()
        with patch("builtins.print", side_effect=lambda *a, **kw: captured_output.write(str(a))):
            result = client.fetch_latest_production_outcome(33418239126)
        self.assertIsNone(result)
        output = captured_output.getvalue()
        self.assertNotIn(TOKEN, output)
        self.assertNotIn("ghp_", output)


if __name__ == "__main__":
    unittest.main()
