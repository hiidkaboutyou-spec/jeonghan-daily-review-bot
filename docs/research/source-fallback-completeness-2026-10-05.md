# Source fallback completeness repair — 2026-10-05

## Observed

A date-bundle source scan resets `collector.last_errors`, calls `XCollector.collect_source`, and marks the source `complete` whenever that call returns without leaving an error.

Before this repair, `collect_source` caught a failed profile timeline, silently switched to author-scoped X search, and returned those rows without recording that the authoritative timeline path had failed.

## Evidence

Production code path:

1. `app/date_requests.py` uses `last_errors` as the completeness signal.
2. `app/x_client.py::collect_source` could recover from `_collect_source_timeline` with `_run_queries`.
3. A successful search fallback left `last_errors` empty.
4. Therefore the date job could record `coverage[handle] = "complete"` even though only fallback search observations were available.

This violated the repository's own rule that public/search recovery is useful observation, not completeness authority.

## External comparison

Reviewed `ythx-101/x-tweet-fetcher` (public GitHub, permissive project) as an architectural comparison. It exposes backend routing/fallback rather than pretending every backend is equivalent. No code or dependency is imported.

Decision: **ADAPT** the provenance principle only. Keep Hani's existing collector and state model; explicitly preserve a degraded marker when timeline -> search fallback occurs.

## Change

`XCollector.collect_source` now appends a machine-readable `source_timeline_fallback` entry to `last_errors` after a successful search fallback.

No rows are discarded. The user still receives recovered observations, but date-bundle completeness stays fail-closed.

## Regression

Added a production-path regression that uses the real `XCollector.collect_source` with:
- timeline failure;
- successful author-scoped search fallback;
- a recovered update.

Acceptance:
- source coverage is `partial`;
- final date bundle is `partial`;
- degraded provenance is present.

## Before -> after

Before: timeline fails + search succeeds -> recovered rows + possible `complete` claim.

After: timeline fails + search succeeds -> recovered rows + explicit `partial` claim.

## Risk / rollback

Risk is low: consumers that treat any `last_errors` entry as degraded will now correctly see this fallback as degraded. No schema, secret, dependency, cursor or delivery format changes.

Rollback: revert the focused `app/x_client.py` change and its regression.

## Remaining evidence

Exact-head CI is still authoritative. Real X production proof is required before claiming source completeness itself has improved; this repair improves correctness of the completeness claim.
