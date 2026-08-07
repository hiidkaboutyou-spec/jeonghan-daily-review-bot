from __future__ import annotations

import unittest

from app.media import _photo_variants


class MediaFallbackTests(unittest.TestCase):
    def test_photo_variants_try_orig_then_all_smaller_sizes_without_duplicates(self):
        variants = _photo_variants("https://pbs.twimg.com/media/ABC?format=jpg&name=orig")
        self.assertEqual(len(variants), 5)
        self.assertIn("name=orig", variants[0])
        self.assertIn("name=4096x4096", variants[1])
        self.assertIn("name=large", variants[2])
        self.assertIn("name=medium", variants[3])
        self.assertIn("name=small", variants[4])
        self.assertEqual(len(variants), len(set(variants)))
        self.assertTrue(all("format=jpg" in item for item in variants))


if __name__ == "__main__":
    unittest.main()
