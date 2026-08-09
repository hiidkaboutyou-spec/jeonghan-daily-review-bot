from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from app.fic_digest import Fic, _get, search_ao3
from app.fic_state import FicObservation, FicStateStore


def _page(work_id: str | None, relationship: str = "Yoon Jeonghan/Choi Seungcheol") -> str:
    if work_id is None:
        # Search page has a work, but not a qualifying Jeonghan ship.
        return """
        <ol><li class='work blurb'>
          <h4 class='heading'><a href='/works/999'>Other</a><a rel='author'>A</a></h4>
          <ul><li class='relationships'><a class='tag'>Choi Seungcheol/Hong Jisoo</a></li></ul>
          <dd class='language'>English</dd>
        </li></ol>
        """
    return f"""
    <ol><li class='work blurb'>
      <h4 class='heading'><a href='/works/{work_id}'>Work {work_id}</a><a rel='author'>A</a></h4>
      <ul><li class='relationships'><a class='tag'>{relationship}</a></li></ul>
      <dd class='language'>English</dd><dd class='chapters'>2/3</dd><dd class='kudos'>10</dd>
      <p class='datetime'>09 Aug 2026</p>
    </li></ol>
    """


class Ao3ReliabilityTests(unittest.TestCase):
    def test_zero_qualifying_middle_page_does_not_hide_later_page(self):
        pages = [
            SimpleNamespace(text=_page("1")),
            SimpleNamespace(text=_page(None)),
            SimpleNamespace(text=_page("3")),
        ]
        with patch("app.fic_digest._get", side_effect=pages), patch("app.fic_digest.time.sleep"):
            result = search_ao3(2, max_pages=3, pace_seconds=1.0)
        self.assertEqual([fic.work_id for fic in result], ["1", "3"])

    def test_empty_actual_search_page_stops_pagination(self):
        with patch(
            "app.fic_digest._get",
            side_effect=[SimpleNamespace(text=_page("1")), SimpleNamespace(text="<ol></ol>")],
        ) as mocked, patch("app.fic_digest.time.sleep"):
            result = search_ao3(5, max_pages=10, pace_seconds=1.0)
        self.assertEqual([fic.work_id for fic in result], ["1"])
        self.assertEqual(mocked.call_count, 2)

    def test_search_blurb_preserves_chapters_and_updated_metadata(self):
        with patch("app.fic_digest._get", side_effect=[SimpleNamespace(text=_page("5"))]):
            result = search_ao3(1, max_pages=1, pace_seconds=0)
        self.assertEqual(result[0].chapters, "2/3")
        self.assertEqual(result[0].updated, "09 Aug 2026")

    def test_retry_after_header_is_honored_for_429(self):
        class Response:
            status_code = 429
            headers = {"Retry-After": "7"}
            def raise_for_status(self):
                return None
        class Ok:
            status_code = 200
            headers = {}
            def raise_for_status(self):
                return None
        with patch("app.fic_digest.requests.get", side_effect=[Response(), Ok()]), patch("app.fic_digest.time.sleep") as sleep:
            response = _get("https://archiveofourown.org/works/search", attempts=2)
        self.assertIsNotNone(response)
        sleep.assert_called_once_with(7.0)


class FicStateTests(unittest.TestCase):
    def test_new_updated_and_unchanged_are_distinguished_durably(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "private-review.sqlite3"
            first = FicStateStore(path)
            now = datetime(2026, 8, 9, tzinfo=timezone.utc)
            self.assertEqual(first.classify(FicObservation("1", "1/3", "2026-08-09"), now=now), "new")
            self.assertEqual(first.classify(FicObservation("1", "1/3", "2026-08-09"), now=now), "unchanged")
            first.close()
            second = FicStateStore(path)
            self.assertEqual(second.classify(FicObservation("1", "2/3", "2026-08-10"), now=now), "updated")
            second.close()

    def test_unrelated_side_ship_does_not_change_jeonghan_ship(self):
        fic = Fic(
            title="x", url="https://archiveofourown.org/works/1", author="a", summary="s",
            relationships=["Yoon Jeonghan/Hong Jisoo", "Choi Seungcheol/Kim Mingyu"],
        )
        self.assertEqual(fic.ship, "Jihan")


if __name__ == "__main__":
    unittest.main()
