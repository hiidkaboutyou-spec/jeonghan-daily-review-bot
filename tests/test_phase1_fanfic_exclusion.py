from __future__ import annotations

import asyncio
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from app.fic_digest import Fic, search_x_recommendations


class _ExternalFanficApi:
    async def search(self, _query, limit=120, kv=None):
        del limit, kv
        yield SimpleNamespace(
            rawContent="external fan rec https://archiveofourown.org/works/123",
            links=[],
            likeCount=20,
            retweetCount=5,
            user=SimpleNamespace(username="randomfan"),
        )


class Phase1FanficExclusionTests(unittest.TestCase):
    def test_fanfic_x_recommendations_remain_independent_of_configured_update_sources(self):
        settings = SimpleNamespace(
            x_cookies={},
            sources=[{"handle": "trustedsource", "enabled": True}],
            keyword_groups=[{"name": "english", "terms": ["JEONGHAN"]}],
        )
        fic = Fic(
            title="External recommendation is still valid for Fanfic",
            url="https://archiveofourown.org/works/123",
            author="writer",
            summary="summary",
            relationships=["Yoon Jeonghan/Choi Seungcheol"],
        )

        with patch(
            "app.fic_digest.XCollector._get_api",
            new=AsyncMock(return_value=_ExternalFanficApi()),
        ), patch(
            "app.fic_digest.asyncio.to_thread",
            new=AsyncMock(return_value=fic),
        ), patch(
            "app.fic_digest.asyncio.sleep",
            new=AsyncMock(),
        ):
            result = asyncio.run(search_x_recommendations(settings, limit=1))

        self.assertEqual([item.url for item in result], [fic.url])
        self.assertEqual(result[0].source_note.split()[0], "external")


if __name__ == "__main__":
    unittest.main()
