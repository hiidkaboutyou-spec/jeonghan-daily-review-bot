from __future__ import annotations

import asyncio
import io
import unittest
from contextlib import redirect_stdout
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from tools.x_profile_diagnostic import diagnose, summarize_profile_response


class XProfileDiagnosticTests(unittest.TestCase):
    def test_available_profile_reports_only_exact_identity_metadata(self):
        result = summarize_profile_response(
            {
                "data": {
                    "user": {
                        "result": {
                            "__typename": "User",
                            "rest_id": "12345",
                            "legacy": {
                                "screen_name": "FlameHanie",
                                "description": "must not be reported",
                            },
                        }
                    }
                }
            },
            "flamehanie",
        )
        self.assertEqual(
            result,
            {
                "result_type": "User",
                "reason": "",
                "has_numeric_id": True,
                "screen_name": "flamehanie",
                "exact_handle": True,
            },
        )

    def test_unavailable_profile_reports_bounded_reason(self):
        result = summarize_profile_response(
            {
                "data": {
                    "user": {
                        "result": {
                            "__typename": "UserUnavailable",
                            "reason": "Suspended",
                        }
                    }
                }
            },
            "flamehanie",
        )
        self.assertEqual(result["result_type"], "UserUnavailable")
        self.assertEqual(result["reason"], "Suspended")
        self.assertFalse(result["has_numeric_id"])
        self.assertFalse(result["exact_handle"])

    def test_live_diagnostic_assigns_isolated_database_after_collector_init(self):
        response = {
            "data": {
                "user": {
                    "result": {
                        "__typename": "User",
                        "rest_id": "12345",
                        "legacy": {"screen_name": "flamehanie"},
                    }
                }
            }
        }
        api = SimpleNamespace(user_by_login_raw=AsyncMock(return_value=response))
        collector = SimpleNamespace(_get_api=AsyncMock(return_value=api))
        with patch("tools.x_profile_diagnostic.parse_cookie_secret", return_value={"ct0": "x"}), patch(
            "tools.x_profile_diagnostic.XCollector", return_value=collector
        ), redirect_stdout(io.StringIO()):
            code = asyncio.run(diagnose("flamehanie"))

        self.assertEqual(code, 0)
        self.assertEqual(collector.db_path.name, "x.sqlite3")


if __name__ == "__main__":
    unittest.main()
