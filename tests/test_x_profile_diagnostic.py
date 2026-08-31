from __future__ import annotations

import unittest

from tools.x_profile_diagnostic import summarize_profile_response


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


if __name__ == "__main__":
    unittest.main()
