from __future__ import annotations

import unittest

from twscrape import xclid


class TwscrapeXClidCompatibilityTests(unittest.TestCase):
    def test_x_web_webpack_16_hex_hashes_are_supported(self):
        html = (
            '{100:"main",200:"shared~feature"}+'
            '{100:"15e48250ae23af9e",200:"00c0ffee00c0ffee"}'
        )

        self.assertEqual(
            xclid.get_scripts_list(html),
            [
                "https://abs.twimg.com/responsive-web/client-web/main.15e48250ae23af9ea.js",
                "https://abs.twimg.com/responsive-web/client-web/shared~feature.00c0ffee00c0ffeea.js",
            ],
        )


if __name__ == "__main__":
    unittest.main()
