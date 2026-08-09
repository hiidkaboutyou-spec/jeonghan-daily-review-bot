from __future__ import annotations

from .models import Update, ensure_utc
from .x_client import XCollectionError, XCollector, _dedupe, _safe_error


class CompleteWindowXCollector(XCollector):
    """X timeline collector that never labels a capped window as complete.

    twscrape paginates its async timeline generators internally, but `limit` is a
    hard result budget. For a requested bounded time window we can prove completeness
    only if the generator naturally exhausts or we actually cross the lower time
    boundary. Reaching the supplied limit while all observed items are still inside
    the window is an explicit incomplete result, not a successful 24-hour fetch.
    """

    async def _collect_source_timeline(
        self,
        handle: str,
        start,
        end,
        *,
        limit: int,
        include_replies: bool,
    ) -> list[Update]:
        api = await self._get_api()
        start = ensure_utc(start)
        end = ensure_utc(end)
        limit = max(1, int(limit))
        raw_seen = 0
        crossed_lower_boundary = False
        try:
            user = await api.user_by_login(handle)
            if user is None:
                raise RuntimeError("profile was not found")
            generator = (
                api.user_tweets_and_replies(user.id, limit=limit)
                if include_replies
                else api.user_tweets(user.id, limit=limit)
            )
            updates: list[Update] = []
            async for tweet in generator:
                raw_seen += 1
                update = self._convert_tweet(tweet, raw_query=f"timeline:@{handle}")
                if update is None:
                    continue
                if update.created_at < start:
                    crossed_lower_boundary = True
                    break
                if getattr(tweet, "retweetedTweet", None) is not None:
                    continue
                if update.created_at < end:
                    updates.append(update)

            if raw_seen >= limit and not crossed_lower_boundary:
                raise XCollectionError(
                    f"X timeline completeness is unproven for @{handle}: "
                    f"the {limit}-item safety limit was reached before the requested start time."
                )
            return _dedupe(updates)
        except XCollectionError:
            raise
        except Exception as exc:
            raise XCollectionError(
                f"X profile timeline failed for @{handle}: {_safe_error(exc)}"
            ) from exc
