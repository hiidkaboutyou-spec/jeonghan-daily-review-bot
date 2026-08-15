# Durable State Crash / Restart Contract

This note supplements `durable-state-architecture.md`. It documents restart behavior for the current free, single-authoritative GitHub Actions production topology. It does not add Event Fusion, a hosted database, a distributed queue, a fast detector, or any public publishing path.

## Effective durable queue

The repository already has a durable logical work queue:

- `StateStore.pending_delivery` is keyed/deduplicated by stable `Update.id` and remains present until the update is marked seen/delivered.
- Phase 2 `update_lifecycle` records the business processing state for the same `Update.id`.
- drafts and bounded retry metadata are stored in the same canonical JSON correctness ledger.
- `MessageDeliveryStore` persists immutable Telegram text delivery plans plus per-part confirmed receipts in `private-review.sqlite3`.
- `MediaDeliveryLedger` persists successful exact-media receipts in the same private SQLite file.
- Phase 3 checkpoints coexist in the JSON ledger and preserve unresolved retrieval boundaries independently from per-update processing state.

This is sufficient while one GitHub Actions runtime is the only delivery-authoritative process. A second queue technology is not justified.

## Content state vs retrieval-window state

Per-update content/process state and source-window completeness are separate domains.

- `Update.id` lifecycle answers: what processing/delivery work remains for this logical post?
- Phase 3 checkpoints and completeness results answer: has the requested source/time range been proven complete?

A valid update discovered inside a partial source window may enter `pending_delivery`, but that does not make the window complete. A partial/failed retrieval must retain its recovery boundary and must not advance the success cursor merely because some valid updates were found.

## Crash / restart matrix

### 1. Discovered -> crash before processing

If discovery has already been saved into `pending_delivery`, restart resumes from the same stable `Update.id`.

There is a smaller in-memory interval between retrieval and the next state save. A hard process kill in that interval does not persist the newly discovered queue row, but it also does not durably advance the successful retrieval cursor in isolation. The next authoritative scan/backfill must rediscover that range. Stable `Update.id` then prevents a second logical queue item.

### 2. Translated -> crash before media

The private review runtime persists generated drafts and lifecycle state before the first Telegram network delivery. On restart the stored draft can be reused instead of regenerating translation. The update remains logically pending until delivery is confirmed/seen.

### 3. Media prepared -> crash before Telegram

Prepared temporary bytes themselves need not be durable queue state. The logical update, media source identity, lifecycle, and draft remain recoverable. A restart may re-prepare or reuse cached media safely.

### 4. Telegram accepts send -> crash before receipt persistence

This is an unavoidable at-least-once edge in the current Telegram transport.

For text, the immutable delivery plan is stored before `sendMessage`; the confirmed part receipt is committed only after Telegram returns success. If the process dies after Telegram accepted the message but before the SQLite confirmation commits, restart cannot prove that acceptance occurred and may send that part again.

The same fundamental window exists for photo/video/media sends: Telegram does not expose an application-supplied idempotency key, and the local media receipt is written after a successful API response.

Therefore the architecture MUST NOT claim exactly-once Telegram delivery. It provides deterministic identities, durable confirmed receipts, seen-state dedupe, and replay-safe recovery for known receipts, while accepting the narrow accepted-before-receipt duplicate window.

### 5. Retry after restart

`pending_delivery`, Phase 2 lifecycle status/retry metadata, translation cooldown metadata, drafts, and confirmed Telegram receipts survive restart. Confirmed text parts are skipped. Already-seen `Update.id` values are removed from pending processing on the next queue read.

### 6. Phase 3 partial checkpoint + pending update coexist

This is valid and intentional. The same JSON ledger can contain:

- valid logical updates already discovered from the partial range; and
- the unresolved Phase 3 checkpoint/cursor needed to prove the remaining range complete.

Completing or delivering one update must not erase the unresolved retrieval checkpoint.

### 7. Malformed state

A truly missing top-level state file is a valid first-run case.

An existing top-level state file that is unreadable, malformed JSON, or not a JSON object must fail closed. The broken file may be preserved for forensics, but production must not silently continue with empty seen, pending, lifecycle, retry, or checkpoint state.

Nested legacy/malformed structures may still use the existing conservative normalization/quarantine behavior where this does not invent completeness or successful delivery.

### 8. Duplicate `Update.id` discovered after restart

The logical identity remains `Update.id`. Queue insertion is idempotent for an already-pending ID, and seen-state prevents a delivered ID from becoming normal pending work again. Future detectors/backfills must converge on this same identity.

## Identity boundaries

- source identity: normalized configured-source handle;
- logical post: `Update.id`;
- retrieval attempt: existing correlation attempt ID;
- Phase 3 checkpoint: source/window/include-replies checkpoint identity;
- translation job: existing deterministic processing-group + `Update.id` identity;
- media asset: media kind + media URL lifecycle identity, supplemented by exact content/Telegram identities in the media receipt ledger;
- text delivery: deterministic delivery key plus part index;
- future Event: a new independent durable event identity, never a replacement for `Update.id`.

Event membership is derived organization. It must be additive: one future Event can reference many source Updates without deleting or overwriting any of them.

## Concert invariant

Event/performance membership is never a media dedupe key.

One concert Event may contain many source Updates, and every Update may contain multiple independently addressable media assets. Different fancams, photos, videos, stage moments, entrances/exits, official material, and backstage material remain separate unless the exact media identity itself proves they are the same asset under the existing narrow media-reuse rules.

Only future semantic fusion of genuinely duplicate spoken ments, speech translations, or repeated descriptions may reduce repeated text. It must not collapse distinct concert media.

## Privacy and product boundary

Durable queue records must not contain X credentials/cookies, Telegram tokens, API keys, or raw exception/auth payloads. Source health persists sanitized technical status only. Private content stays in the existing private local state/SQLite persistence and encrypted recovery path; it is not copied into git.

Fanfic/AO3 remains independent through its own state/workflow. `REALTIME_SHADOW_MODE` remains off. No new production detector is introduced. All delivery remains private-review-only.

## Future multi-process gate

No distributed lock/lease is required in the current topology.

Before any future independent Fast Detector receives processing or delivery authority, introduce a genuinely shared atomic claim/lease keyed by `Update.id` and test it against the existing Phase 3 backfill path. Until then, Phase 3/GitHub Actions remains the sole authoritative production processing path.
