from __future__ import annotations

import asyncio
import os
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from app.fic_digest import (
    Fic,
    _delivery_run_scope,
    _get,
    build_digests,
    search_ao3,
    search_ao3_balanced,
    search_x_recommendations,
    send_digests,
)
from app.fic_state import FicObservation, FicStateStore


def _page(work_id: str | None, relationship: str = "Yoon Jeonghan/Choi Seungcheol") -> str:
    if work_id is None:
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
      <ul><li class='warnings'><a class='tag'>Major Character Death</a></li>
      <li class='freeforms'><a class='tag'>Slow Burn</a></li></ul>
      <dd class='language'>English</dd><dd class='chapters'>2/3</dd><dd class='kudos'>10</dd>
      <p class='datetime'>09 Aug 2026</p>
    </li></ol>
    """


class Ao3ReliabilityTests(unittest.TestCase):
    def test_zero_qualifying_middle_page_does_not_hide_later_page(self):
        pages = [SimpleNamespace(text=_page("1")), SimpleNamespace(text=_page(None)), SimpleNamespace(text=_page("3"))]
        with patch("app.fic_digest._get", side_effect=pages), patch("app.fic_digest.time.sleep"):
            result = search_ao3(2, max_pages=3, pace_seconds=1.0)
        self.assertEqual([fic.work_id for fic in result], ["1", "3"])

    def test_empty_actual_search_page_stops_pagination(self):
        with patch("app.fic_digest._get", side_effect=[SimpleNamespace(text=_page("1")), SimpleNamespace(text="<ol></ol>")]) as mocked, patch("app.fic_digest.time.sleep"):
            result = search_ao3(5, max_pages=10, pace_seconds=1.0)
        self.assertEqual([fic.work_id for fic in result], ["1"])
        self.assertEqual(mocked.call_count, 2)

    def test_page_safety_cap_is_enforced(self):
        with patch("app.fic_digest._get", return_value=SimpleNamespace(text=_page(None))) as mocked, patch("app.fic_digest.time.sleep"):
            self.assertEqual(search_ao3(99, max_pages=25, pace_seconds=1), [])
        self.assertEqual(mocked.call_count, 25)

    def test_request_failure_stops_without_skipping_gap(self):
        with patch("app.fic_digest._get", side_effect=[SimpleNamespace(text=_page("1")), None, SimpleNamespace(text=_page("3"))]) as mocked, patch("app.fic_digest.time.sleep"):
            result = search_ao3(3, max_pages=5, pace_seconds=1)
        self.assertEqual([fic.work_id for fic in result], ["1"])
        self.assertEqual(mocked.call_count, 2)

    def test_requested_result_count_is_respected(self):
        with patch("app.fic_digest._get", side_effect=[SimpleNamespace(text=_page("1")), SimpleNamespace(text=_page("2"))]), patch("app.fic_digest.time.sleep"):
            result = search_ao3(1, max_pages=5, pace_seconds=1)
        self.assertEqual([fic.work_id for fic in result], ["1"])

    def test_search_blurb_preserves_chapters_and_updated_metadata(self):
        with patch("app.fic_digest._get", side_effect=[SimpleNamespace(text=_page("5"))]):
            result = search_ao3(1, max_pages=1, pace_seconds=0)
        self.assertEqual(result[0].chapters, "2/3")
        self.assertEqual(result[0].updated, "09 Aug 2026")
        self.assertEqual(result[0].warnings, ["Major Character Death"])
        self.assertEqual(result[0].freeforms, ["Slow Burn"])

    def test_balanced_search_combines_recent_and_popular_without_duplicates(self):
        recent = [
            Fic("recent 1", "https://archiveofourown.org/works/1", "a", "s", ["Yoon Jeonghan/Choi Seungcheol"]),
            Fic("recent 2", "https://archiveofourown.org/works/2", "a", "s", ["Yoon Jeonghan/Choi Seungcheol"]),
        ]
        popular = [
            Fic("duplicate", "https://archiveofourown.org/works/2", "a", "s", ["Yoon Jeonghan/Choi Seungcheol"]),
            Fic("popular", "https://archiveofourown.org/works/3", "a", "s", ["Yoon Jeonghan/Choi Seungcheol"]),
        ]
        with patch("app.fic_digest.search_ao3", side_effect=[recent, popular]) as search, patch("app.fic_digest.time.sleep"):
            result = search_ao3_balanced(3, max_pages_each=4, pace_seconds=1)
        self.assertEqual([fic.work_id for fic in result], ["1", "2", "3"])
        self.assertEqual(search.call_args_list[0].kwargs["sort_column"], "revised_at")
        self.assertEqual(search.call_args_list[1].kwargs["sort_column"], "kudos_count")

    def test_retry_after_header_is_honored_for_429(self):
        class Response:
            status_code = 429
            headers = {"Retry-After": "7"}
            def raise_for_status(self): return None
        class Ok:
            status_code = 200
            headers = {}
            def raise_for_status(self): return None
        with patch("app.fic_digest.requests.get", side_effect=[Response(), Ok()]), patch("app.fic_digest.time.sleep") as sleep:
            response = _get("https://archiveofourown.org/works/search", attempts=2)
        self.assertIsNotNone(response)
        sleep.assert_called_once_with(7.0)

    def test_deleted_ao3_work_is_not_retried(self):
        class Missing:
            status_code = 404
            headers = {}

        with patch("app.fic_digest.requests.get", return_value=Missing()) as get, patch(
            "app.fic_digest.time.sleep"
        ) as sleep:
            response = _get("https://archiveofourown.org/works/404", attempts=3)

        self.assertIsNone(response)
        get.assert_called_once()
        sleep.assert_not_called()

    def test_search_uses_ao3_character_filter_not_broad_any_field_query(self):
        with patch("app.fic_digest._get", return_value=SimpleNamespace(text=_page("1"))) as get:
            result = search_ao3(1, max_pages=1, pace_seconds=0)

        self.assertEqual([fic.work_id for fic in result], ["1"])
        url = get.call_args.args[0]
        self.assertIn("work_search%5Bcharacter_names%5D=Yoon+Jeonghan", url)
        self.assertNotIn("work_search%5Bquery%5D", url)
        self.assertEqual(get.call_args.kwargs["attempts"], 2)

    def test_x_detail_lookups_are_serial_and_paced(self):
        settings = SimpleNamespace(x_cookies={}, sources=[], keyword_groups=[])
        tweet = SimpleNamespace(
            rawContent="https://archiveofourown.org/works/1 https://archiveofourown.org/works/2",
            links=[], likeCount=10, retweetCount=0,
        )
        class API:
            async def search(self, *args, **kwargs):
                yield tweet
        collector = SimpleNamespace(_get_api=AsyncMock(return_value=API()))
        loaded = []
        def fetch(url):
            loaded.append(url)
            return Fic("t", url, "a", "s", ["Yoon Jeonghan/Choi Seungcheol"])
        with patch("app.fic_digest.XCollector", return_value=collector), patch("app.fic_digest.fetch_ao3_work", side_effect=fetch), patch("app.fic_digest.asyncio.sleep", new=AsyncMock()) as sleep:
            result = asyncio.run(search_x_recommendations(settings, limit=2))
        self.assertEqual(len(result), 2)
        self.assertEqual(len(loaded), 2)
        self.assertGreaterEqual(sleep.await_count, 1)

    def test_x_recommendation_reuses_ao3_search_metadata_without_refetching_work(self):
        settings = SimpleNamespace(x_cookies={}, sources=[], keyword_groups=[])
        tweet = SimpleNamespace(
            rawContent="https://archiveofourown.org/works/7",
            links=[], likeCount=25, retweetCount=3,
        )

        class API:
            async def search(self, *args, **kwargs):
                yield tweet

        collector = SimpleNamespace(_get_api=AsyncMock(return_value=API()))
        known = Fic(
            "known", "https://archiveofourown.org/works/7", "a", "summary",
            ["Yoon Jeonghan/Choi Seungcheol"], kudos=99,
        )
        with patch("app.fic_digest.XCollector", return_value=collector), patch(
            "app.fic_digest.fetch_ao3_work"
        ) as fetch, patch("app.fic_digest.asyncio.sleep", new=AsyncMock()):
            result = asyncio.run(
                search_x_recommendations(settings, limit=1, known_fics=[known])
            )

        fetch.assert_not_called()
        self.assertEqual(result[0].work_id, "7")
        self.assertEqual(result[0].x_score, 31)
        self.assertEqual(result[0].kudos, 99)


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
        fic = Fic(title="x", url="https://archiveofourown.org/works/1", author="a", summary="s", relationships=["Yoon Jeonghan/Hong Jisoo", "Choi Seungcheol/Kim Mingyu"])
        self.assertEqual(fic.ship, "Jihan")


class FicDeliveryTests(unittest.TestCase):
    def test_automatic_delivery_scope_is_stable_per_deployed_revision(self):
        with patch.dict(os.environ, {"GITHUB_SHA": "abcdef1234567890"}, clear=False):
            first = _delivery_run_scope("2026-08-11", manual_request=False)
            retry = _delivery_run_scope("2026-08-11", manual_request=False)
        with patch.dict(os.environ, {"GITHUB_SHA": "9999991234567890"}, clear=False):
            next_deploy = _delivery_run_scope("2026-08-11", manual_request=False)

        self.assertEqual(first, retry)
        self.assertEqual(first, "2026-08-11:abcdef123456")
        self.assertNotEqual(first, next_deploy)

    def test_send_uses_stable_delivery_keys_for_resume(self):
        with tempfile.TemporaryDirectory() as temp:
            settings = SimpleNamespace(state_path=Path(temp) / "state.json", telegram_token="x", admin_user_id=1, review_chat_id=2, timezone=timezone.utc)
            bot = SimpleNamespace(message_delivery_store=None, send_message=Mock())
            with patch("app.fic_digest.build_digests", new=AsyncMock(return_value=("x digest", "ao3 digest"))):
                asyncio.run(send_digests(settings, bot=bot))
            keys = [call.kwargs["delivery_key"] for call in bot.send_message.call_args_list]
            self.assertEqual(len(keys), 2)
            self.assertTrue(keys[0].endswith(":x"))
            self.assertTrue(keys[1].endswith(":ao3"))
            self.assertIn(":manual:", keys[0])

    def test_x_and_ao3_digests_do_not_repeat_the_same_work(self):
        settings = SimpleNamespace(gemini_api_key="", gemini_model="")
        duplicate_x = Fic("from x", "https://archiveofourown.org/works/1", "a", "x", ["Yoon Jeonghan/Choi Seungcheol"])
        duplicate_ao3 = Fic("same work", "https://archiveofourown.org/works/1", "a", "a", ["Yoon Jeonghan/Choi Seungcheol"])
        unique_ao3 = Fic("unique work", "https://archiveofourown.org/works/2", "a", "b", ["Yoon Jeonghan/Choi Seungcheol"])
        with patch("app.fic_digest.search_x_recommendations", new=AsyncMock(return_value=[duplicate_x])), patch(
            "app.fic_digest.search_ao3_balanced", return_value=[duplicate_ao3, unique_ao3]
        ):
            x_text, ao3_text = asyncio.run(build_digests(settings))
        self.assertIn("/works/1", x_text)
        self.assertNotIn("/works/1", ao3_text)
        self.assertIn("/works/2", ao3_text)


if __name__ == "__main__":
    unittest.main()
