# Phase 1 — Project-wide configured-sources-only audit

Applies to branch `audit/configured-sources-only-phase1-v1` based on production main `41191ca2fe602df161db1628c56b17d8a6e14532`.

Fanfic/AO3 is explicitly excluded from this source-authority policy. The assistant remains private-review-only; this phase adds no public Telegram publishing path.

## Authority invariant

Every normal, non-Fanfic update entering retrieval, replay, reconstruction, media recovery, queueing, or private review must be authored by an enabled handle in `config/sources.json`. Keyword/search queries may discover or recover posts, but only configured authors may survive. Configured posts do not need a Jeonghan keyword. Per-source `include_replies`, media metadata, stable post identity, completeness state and deterministic oldest-to-newest ordering remain authoritative.

## Retrieval inventory

| Entry point | Trigger / path | Collector / state path | Source authority | Completeness / cursor | Media / dedupe / order |
| --- | --- | --- | --- | --- | --- |
| Automatic Daily | GitHub Actions production pass → `WebhookAwarePersonalAssistant.run_scheduled_scan` | `XCollector.collect_window` | Enabled configured sources only; source-scoped recovery; final runtime guard | Timeline must exhaust/cross lower bound; `last_auto_run` advances only with no collector errors | `Update.media` preserved; ID dedupe; sorted `(created_at,id)` before queue |
| Webhook maintenance Daily | `/maintenance` → `WebhookRuntime.maintenance_sync` → same scheduled scan | Same as Automatic Daily | Same invariant | Same invariant | Same invariant |
| Polling fallback Daily | stale/no webhook → `WebhookAwarePersonalAssistant.run` → normal app pass | Same collector/runtime | Same invariant | Same invariant | Same invariant |
| Recent updates / 2h | `/recent2h`, `/fetch2h`, button or natural-language intent → `run_recent2h` | `collect_window` | Source-only at collector and final delivery boundary | Partial paths are explicitly warned and are not represented as complete | No post-delivery collection cap in production; deterministic order; media preserved |
| 24h source | source callback / command / natural-language source request → `run_source24` | `collect_source` | Requested handle must be enabled/configured; custom arbitrary source UI removed; collector also rejects unconfigured handles | Completeness-aware timeline; search fallback cannot certify a complete 24h window | `include_replies` read from source config; media metadata preserved; oldest-first |
| Manual/archive search | `/search`, date picker, today/yesterday, ordinary search text → `run_search` | local `ArchiveStore.search` + `XCollector.search_archive` | Local legacy rows and remote X candidates are filtered to configured authors before session creation/indexing | Candidate search is not mislabeled as a complete source window | Stable ID combine/dedupe, organizer ordering, media fields survive `Update` serialization |
| Search-assisted X retrieval | `XCollector.search_archive` | X Latest/Top search | `_filter_relevant` is now strict configured-author filtering | Discovery only; no completeness claim | Stable ID dedupe; configured no-keyword posts survive if returned |
| Timeline failure recovery | `collect_window` timeline exception | `from:<configured-handle>` search | Recovery query is source-scoped and post-filtered to that same configured author | Original timeline error remains in `last_errors`; successful cursor retained for retry | Recovery duplicates collapse by post ID; richer media variant wins |
| Selected-event reconstruction | candidate `pick:*` callback → `run_selected_event` → `collect_event` | X thread + selected-author/event queries | stale external sessions are blocked before reconstruction; collector independently rejects external selected author and re-filters results | Event search is reconstruction, not a completeness claim | Stable ID dedupe; organizer deterministic order |
| Pending replay/resend | scheduled/webhook `deliver_pending` | persisted `pending_delivery` | stale pre-policy external queue rows are removed before replay; final delivery guard rechecks authors | Queue remains durable for allowed updates | Same update/media objects retained; delivery batching does not change source identity |
| Draft rewrite/copy replay | inbox/reply action → `handle_draft_action` | persisted draft + archived `Update` | external-author legacy draft is blocked from resend/rewrite | N/A | No media recollection unless source-authorized update reaches normal delivery |
| Review inbox | inbox list/open callbacks | `ReviewInboxStore` | listing supports `allowed_sources`; production passes configured handle set and blocks direct open of legacy external draft | N/A | Private review only |
| Media download/recovery | authorized `deliver_updates` → `MediaDedupReviewApplication._deliver_private_media` → `MediaManager.prepare` | original `Update.media`, Telegram file cache, yt-dlp/gallery-dl fallback for trusted URLs | final source-authority boundary runs before media preparation; media layer cannot independently introduce an update author | N/A | direct metadata, cache identity, SHA-256 media dedupe and Telegram file identity retained |
| Telegram webhook commands | `/telegram/webhook` → `process_update_sync` → normal message/callback handlers | same application methods above | same invariant because webhook and polling share `WebhookAwarePersonalAssistant` | Telegram offset advances only after handled/persisted update | same private review path |
| Legacy `Application` collector calls | compatibility/direct module methods | same monkey-patched `XCollector` methods | collector-level source-only rule still applies, including source24 and event reconstruction | complete-window collector remains installed; tracked legacy ceiling is raised above the maximum provable complete-window result budget | collector ID dedupe/media serialization unchanged |
| Fanfic/AO3 | `/fic`, nightly fic workflow, AO3 search, X fic recommendations | `fic_digest` direct AO3 + low-level X API search | **OUT OF SCOPE intentionally**; broad recommendation authors remain allowed | existing Fanfic behavior unchanged | independent fic state/work identity |

## Confirmed violations found in the released baseline

1. Manual `search_archive` deliberately allowed relevant external keyword hits.
2. Local historical archive rows from external authors could reappear in manual search candidates.
3. 24h retrieval accepted arbitrary/custom X handles even when not configured.
4. A stale external search session could reconstruct an external event.
5. Stale pre-policy external rows in `pending_delivery` could be replayed.
6. Old external drafts could remain visible/actionable in the private inbox.
7. Media recovery had no independent author check before downloading bytes; it relied entirely on upstream correctness.
8. The legacy compatibility collection ceiling was lower than the aggregate maximum of a proven-complete multi-source window.

## Phase 1 fixes

- Made normal `XCollector` relevance filtering strict configured-author filtering.
- Kept automatic recovery source-scoped and retained partial errors/cursor behavior.
- Rejected unconfigured `collect_source` calls before timeline lookup.
- Rejected external selected-event reconstruction and re-filtered event results.
- Removed the arbitrary/custom source button from private UI.
- Added production runtime guards for 2h, 24h, scheduled scan, manual local+remote search, stale sessions, pending queue, draft actions, inbox listing/open and final media/text delivery.
- Raised the legacy compatibility collection ceiling above the aggregate maximum that can be returned by a collector window that has actually proven completeness; production paths still do not use that ceiling for collection completeness.
- Added deterministic regressions for source-only entrypoints, media-only posts, stale recovery state, duplicate identity, ordering, and the Fanfic exclusion.

## Non-goals preserved

No Event Fusion, Rust migration, Supabase, new Sentry instrumentation, hosting/deployment redesign, translation redesign, or public Telegram publishing was added in this phase.
