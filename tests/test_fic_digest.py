from __future__ import annotations

import unittest

from app.fic_digest import Fic, _chunks, format_digest


class FanficDigestTests(unittest.TestCase):
    def test_chunks_never_truncate_long_block(self):
        text = "عنوان\n\n" + ("الف" * 9000) + "\n\nپایان"
        chunks = _chunks(text, max_len=3800)
        self.assertTrue(chunks)
        self.assertTrue(all(0 < len(chunk) <= 3800 for chunk in chunks))
        reconstructed = "\n\n".join(chunks)
        self.assertIn("الف" * 9000, reconstructed.replace("\n\n", ""))
        self.assertTrue(reconstructed.endswith("پایان"))

    def test_digest_keeps_separate_ship_sections(self):
        fics = [
            Fic(
                title="A",
                url="https://archiveofourown.org/works/1",
                author="one",
                summary="summary A",
                relationships=["Yoon Jeonghan/Choi Seungcheol"],
            ),
            Fic(
                title="B",
                url="https://archiveofourown.org/works/2",
                author="two",
                summary="summary B",
                relationships=["Yoon Jeonghan/Hong Jisoo"],
            ),
        ]
        text = format_digest("title", fics, {f.url: f.summary for f in fics}, "ao3")
        self.assertIn("━━ Jeongcheol ━━", text)
        self.assertIn("━━ Jihan ━━", text)
        self.assertIn("https://archiveofourown.org/works/1", text)
        self.assertIn("https://archiveofourown.org/works/2", text)


if __name__ == "__main__":
    unittest.main()
