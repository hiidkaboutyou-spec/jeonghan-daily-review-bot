from __future__ import annotations

from .models import Update, ensure_utc
from .observability import current_retrieval_attempt_id, new_attempt_id, observe
from .x_client import XCollectionError, XCollector, _dedupe, _safe_error, normalize_handle


class XCompletenessError(XCollectionError):
    """The requested bounded source window could not be proven complete."""


class CompleteWindowXCollector(XCollector):
    """X timeline collector that never labels a capped window as complete.

    twscrape paginates its async timeline generators internally, but `limit` is a
    hard result budget. For a requested bounded time window we can prove completeness
    only if the generator naturally exhausts or we actually cross the lower time
    boundary. Reaching the supplied limit while all observed items are still inside
    the window is an explicit incomplete result, not a successful 24-hour fetch.
    """

    async def collect_source(self, handle: str, start, end) -> list[Update]:
        """Return a proven-complete source window or fail explicitly.

        A generic X search is useful for discovery, but it cannot prove that every
        post/reply from a source was returned. Therefore the old search fallback is
        intentionally not accepted as a successful result for the UI's "complete
        24 hours" operation.
        """
        handle = normalize_handle(handle)
        if not handle:
            raise XCollectionError("Source handle is invalid.")
        try:
            return await self._collect_source_timeline(
                handle,
                start,
                end,
                limit=1000,
                include_replies=True,
            )
        except XCompletenessError:
            raise
        except XCollectionError as exc:
            raise XCompletenessError(
                f"Could not prove a complete source timeline for @{handle}; "
                f"search-only fallback was not used. {_safe_error(exc)}"
            ) from exc

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
        attempt_id = current_retrieval_attempt_id() or new_attempt_id()
        observe(
            "source_fetch_start",
            stage="retrieval",
            status="started",
            source=handle,
            retrieval_attempt_id=attempt_id,
            include_replies=include_replies,
            pagination="provider_managed",
            pages_requested="provider_managed",
            cursor_requested="provider_managed",
        )
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
                observe(
                    "source_fetch_end",
                    level="warning",
                    stage="retrieval",
                    status="partial_source_window",
                    source=handle,
                    retrieval_attempt_id=attempt_id,
                    raw_seen=raw_seen,
                    retained=len(updates),
                    cutoff_crossed=False,
                    provider_exhausted=False,
                    complete=False,
                    partial=True,
                    error_class="XCompletenessError",
                )
                raise XCompletenessError(
                    f"X timeline completeness is unproven for @{handle}: "
                    f"the {limit}-item safety limit was reached before the requested start time."
                )
            deduped = _dedupe(updates)
            observe(
                "source_fetch_end",
                stage="retrieval",
                status="complete",
                source=handle,
                retrieval_attempt_id=attempt_id,
                raw_seen=raw_seen,
                retained=len(deduped),
                cutoff_crossed=crossed_lower_boundary,
                provider_exhausted=not crossed_lower_boundary and raw_seen < limit,
                complete=True,
                partial=False,
            )
            return deduped
        except XCompletenessError:
            raise
        except XCollectionError as exc:
            observe(
                "source_fetch_end",
                level="error",
                stage="retrieval",
                status="failed",
                source=handle,
                retrieval_attempt_id=attempt_id,
                raw_seen=raw_seen,
                cutoff_crossed=crossed_lower_boundary,
                complete=False,
                error_class=type(exc).__name__,
            )
            raise
        except Exception as exc:
            observe(
                "source_fetch_end",
                level="error",
                stage="retrieval",
                status="failed",
                source=handle,
                retrieval_attempt_id=attempt_id,
                raw_seen=raw_seen,
                cutoff_crossed=crossed_lower_boundary,
                complete=False,
                error_class=type(exc).__name__,
            )
            raise XCollectionError(
                f"X profile timeline failed for @{handle}: {_safe_error(exc)}"
            ) from exc
