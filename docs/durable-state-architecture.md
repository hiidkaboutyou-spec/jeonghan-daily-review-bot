# Durable Queue / Shared State Architecture

Status: architecture contract for the current free, single-production-runtime system.

This document does **not** introduce a hosted database, distributed queue, fast X detector, Event Fusion, or a public publishing path.

## Decision

The current production topology does **not** need a shared hosted database or distributed queue.

Keep the free production model:

1. GitHub Actions is the single authoritative production runtime.
2. Phase 3 remains the authoritative completeness/backfill path.
3. `data/state.json` (runtime path `.state/state.json` in Actions) remains the canonical correctness ledger for scan cursor, pending logical updates, lifecycle, retry metadata, seen state, Telegram offset, drafts, and Phase 3 checkpoints.
4. `private-review.sqlite3` remains the durable local service ledger/index for Telegram delivery receipts, media reuse receipts, searchable archive/inbox, callback tokens, reminders, source-health history, and related private-review runtime data.
5. GitHub Actions cache plus encrypted recovery artifacts preserve both files between ephemeral runners.
6. `REALTIME_SHADOW_MODE` remains off. No fast detector has delivery authority.

A shared cross-process store becomes necessary only if a second independently running process is later allowed to claim or deliver the same logical updates.

## State ownership map

### Canonical `StateStore` / JSON ownership

`StateStore` owns correctness-sensitive logical state:

- Telegram update offset and bounded Telegram failure metadata.
- `last_auto_run` and `last_auto_attempt`.
- stable `seen` keys by `Update.id`.
- compatibility/raw update archive by `Update.id`.
- persisted review drafts and awaiting/session state.
- `pending_delivery`.
- Phase 2 `update_lifecycle` and quarantined pending rows.
- translation outage/retry metadata.
- Phase 3 `x_retrieval_checkpoints`.
- source-window cursor/completeness consequences expressed through existing scheduling/retrieval semantics.

Writes use a temporary file, `fsync`, and atomic replacement.

### `private-review.sqlite3` ownership

SQLite is intentionally split into durable service ledgers/indexes rather than replacing `StateStore`.

It currently owns or supports:

- exact multipart Telegram delivery plans and confirmed part receipts;
- media-delivery identity/reuse receipts;
- searchable archive and FTS index;
- review-inbox projection;
- media file cache metadata;
- callback token storage;
- reminder jobs and delivery state;
- source health / request history.

SQLite transport receipts are authoritative evidence that a keyed Telegram network part was confirmed. Phase 2 lifecycle is the business-state projection of that fact.

### GitHub Actions persistence ownership

The GitHub workflow restores and checkpoints both state files. Recovery artifacts are validated before restore. This makes the ephemeral runner compatible with durable local state without introducing a paid service.

Repository source code and git history are **not** a storage location for private runtime state.

## Intentional duplication and precedence

Some information exists in more than one place. This is intentional when one copy is canonical and the other is a projection/index.

### Update archive

- JSON `archive`: compatibility/source-of-truth fallback keyed by `Update.id`.
- SQLite `archive_records`: searchable private-review index and richer presentation record.

If the SQLite archive index must be rebuilt, the original logical update remains recoverable from canonical state where retained.

### Delivery state

- SQLite `message_delivery_*`: exact transport plan and receipt authority.
- JSON lifecycle/draft state: logical workflow state and retry projection.

A lifecycle record must never be promoted to `delivered` merely because an attempt was made. Delivery requires the existing confirmed Telegram receipt path.

### Review inbox

SQLite review-inbox rows are a private UI projection. They do not replace `pending_delivery`, `seen`, or Phase 2 lifecycle ownership.

### Processing group vs future Event

The current `EventGroup.key` and Phase 2 lifecycle `event_id` describe processing/organizer grouping. They are **not** the future durable Event Fusion identity.

Future Event Fusion must add a separate relation rather than reinterpreting or overwriting source updates.

## Canonical logical update lifecycle

Do not create a second lifecycle.

Reuse Phase 2 `update_lifecycle` and its existing status/stage semantics.

The conceptual flow is:

`authoritative retrieval`
→ `pending_delivery`
→ processing substates (`pending_translation`, media preparation)
→ Telegram delivery attempt
→ `delivered`

Existing explicit failure/recovery states remain authoritative:

- `retry_pending`
- `pending_translation`
- `pending_media` where currently used
- `quarantined_with_reason`
- `delivered_text_media_failed`

Translation and media fields are orthogonal substate metadata on the same logical lifecycle; they should not become a competing queue.

Retrieval completeness is a source/window property. A partial window may yield valid individual updates, but it must not advance the authoritative cursor or be relabeled complete.

## Stable identity contract

### Logical update identity

`Update.id` is the stable logical post identity.

All detectors, backfills, lifecycle rows, pending delivery, seen state, archive records, and future event membership must refer back to this identity.

### Source identity

Use the normalized configured-source handle. Source authority remains the Phase 1 configured list.

### Retrieval attempt identity

Use the existing retrieval-attempt correlation ID. It is observability/correlation metadata, not the logical update primary key.

### Translation job identity

Keep the existing deterministic translation job identity derived from processing-group identity plus `Update.id`.

A later Event identity must not silently change the identity of already processed translation work.

### Media asset identity

Keep each actual media asset individually addressable.

Existing media lifecycle IDs are stable hashes of media kind + URL, while the media-delivery ledger additionally uses exact source/cache/content/Telegram identifiers as available.

For concerts, event/performance membership must never be used as a media dedupe key. Two different fancams/photos/videos from the same performance remain distinct assets.

### Delivery identity

Keep deterministic delivery keys (for example `draft:<draft_id>`) and the `(delivery_key, part_index)` receipt identity.

This allows a restart to reuse the exact persisted Telegram plan and skip already confirmed parts.

### Future Event identity

When Event Fusion begins, create a new durable `event_id` independent from every `Update.id`.

Event membership is additive:

`Event`
→ `Update A`
→ `Update B`
→ `Update C`

The source updates remain immutable logical records.

A future relation may carry:

- event type;
- timeline ordering;
- update membership;
- confidence/evidence;
- spoken-segment associations;
- media associations.

Do not implement these tables in this architecture task.

## Future Event Fusion contract

Future Event Fusion may group multiple source updates while preserving all source-level evidence.

For Lives, Going Seventeen, reality/variety, interviews, behind-the-scenes, awards and similar time-based content, later fusion can select complementary speech/context/translation into one coherent timeline.

For concerts, grouping is association rather than deduplication:

- preserve distinct performances;
- preserve distinct fancams;
- preserve distinct photos;
- preserve distinct videos;
- preserve interactions and stage moments;
- preserve entrances/exits;
- preserve official/backstage material.

Only genuinely duplicate speech, ment translation, or repeated context may later be fused.

## Future fast-detector coexistence

Fast detection is currently postponed.

While GitHub Actions is the only production process, no distributed claim is needed.

A future compliant detector may observe the same `Update.id` as Phase 3. The required contract is:

1. fast candidate detects `Update.id`;
2. normal configured-source authoritative hydration validates the candidate;
3. both fast path and Phase 3 converge on the same logical `Update.id`;
4. only one canonical update is processed/delivered;
5. Phase 3 remains completeness authority and can recover detector misses.

The current process-local `LogicalUpdateGate` is deliberately insufficient for two independent delivery-authoritative processes.

Before a second process receives delivery authority, introduce a genuinely shared atomic claim/lease with a unique key on `Update.id` (transaction/CAS/unique constraint). Do not claim distributed exactly-once delivery; keep deterministic delivery keys and durable Telegram receipts as the recovery mechanism.

## Storage options

### A. Existing JSON + SQLite + GitHub persistence — selected now

Correctness:
- sufficient for the current single authoritative process;
- stable logical dedupe by `Update.id`;
- durable Phase 3 checkpoints;
- durable Telegram receipts.

Restart durability:
- state and SQLite restored/checkpointed by GitHub Actions;
- recovery artifacts provide an additional validated backup path.

Atomicity:
- JSON writes are atomic file replacements;
- SQLite provides local transactions/unique keys;
- sufficient while only one production process owns mutation.

Cross-process coordination:
- no. This is acceptable because no second delivery-authoritative runtime exists.

Cost:
- no new paid service.

Operational complexity:
- lowest of the available choices.

Migration risk:
- none.

### B. Move more ownership into SQLite — not justified now

SQLite could eventually consolidate some JSON state, but doing so now would create migration and rollback risk without solving a current production requirement.

Do not migrate merely for architectural neatness.

### C. GitHub cache/artifacts as the primary queue — rejected

Cache/artifacts are persistence and recovery mechanisms, not a transactional concurrent queue.

Keep them as checkpoint/backup transport only.

### D. Hosted shared database/queue — postponed

A hosted store only becomes justified when there are independent processes that must atomically claim the same `Update.id`.

Even if a free tier exists, adopting it now adds vendor, network, secret-management, migration, and outage dependencies without a current correctness benefit.

Supabase is therefore **not required** by the current architecture.

## Corruption and recovery policy

Missing state on a true first run may create fresh state.

An **existing** state file that cannot be decoded or is not a JSON object must fail closed and visibly. It may be moved aside as `.broken.json` for forensic recovery, but production must not silently continue with empty seen/pending/cursor/checkpoint state.

Nested malformed legacy fields continue to use existing bounded normalization/quarantine rules where safe:

- malformed pending rows are quarantined;
- malformed Phase 3 checkpoints are discarded visibly and their affected range is re-fetched conservatively;
- partial retrieval never becomes complete merely because a checkpoint is malformed.

No exception message may include private payload contents or secrets.

## Safety invariants

The architecture must preserve all of the following:

- partial retrieval never becomes complete accidentally;
- Phase 3 cursor/checkpoint state remains authoritative;
- lifecycle survives restart;
- retry state survives restart where it affects correctness;
- Telegram delivery receipts remain durable;
- restart does not intentionally create duplicate delivery;
- malformed top-level state fails visibly;
- no credentials/cookies/tokens are written to durable logical records;
- private content is not copied into git or additional hosted services;
- Fanfic/AO3 keeps its independent workflow;
- no public Daily publishing path is introduced;
- `REALTIME_SHADOW_MODE` remains off in production.

## Current limitation

The architecture does not provide distributed atomic claims across independent processes. This is intentional because production currently has one authoritative runtime.

If a future second process is introduced, it must not receive delivery authority until shared atomic claim semantics are designed, tested, and deployed.

## Roadmap gate

This architecture task does not start Event Fusion.

After this contract and its state-integrity hardening are validated and production-verified, the next roadmap task may design Event Fusion on top of:

- immutable `Update.id` source records;
- separate future Event identity;
- additive event membership;
- individually addressable concert media.
