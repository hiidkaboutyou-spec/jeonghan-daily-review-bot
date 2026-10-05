# Telegram cloud-state size repair — 2026-10-05

Run identifier: `2026-10-05T12:00+03:30-telegram-state-size`

Base: `main@c3aec24598ab5e274d9cfc3d1bb3bfc78c9b9757`

Branch: `fix/telegram-compressed-cloud-state-166`

Implementation head before this evidence-only update: `5f7a05212f90cf05b700ba7db9f7181194815a15`

## Problem and acceptance criteria

The first production deployment containing safe Telegram error details logged:

`Telegram getFile failed: Bad Request: file is too big`

Railway has no attached volume, so a restart that cannot restore the pinned Telegram backup can lose cursor, deduplication, delivery-ledger, and Telegram offset state. A green workflow does not prove this restart path is safe.

Acceptance criteria:

1. New encrypted backups are small enough to download through Telegram's hosted Bot API in the normal state shape.
2. Existing version-1 encrypted backups remain restorable.
3. Authentication, SHA-256 verification, SQLite `quick_check`, atomic replacement, and fresh nonces remain intact.
4. A backup above a conservative downloadable limit is rejected before any Telegram upload/edit/pin call, preserving the previous pinned backup.

## Evidence and options

Telegram's official Bot API documentation states that `getFile` downloads are limited to 20 MB: <https://core.telegram.org/bots/api#getfile>.

The previous format Base64-encoded each state file inside a JSON payload, encrypted that payload, and Base64-encoded the ciphertext again. That makes binary state grow by roughly 1.78x before JSON overhead. The production failure is therefore consistent with a private SQLite database that is well below 20 MB on disk but above 20 MB in the encrypted envelope.

Options considered:

- **Compressed authenticated envelope (chosen):** zlib from the Python standard library before AES-256-GCM. No service, credential, cost, license, or dependency change. A bounded decompressor prevents unbounded expansion. The reader remains compatible with v1.
- **Multiple Telegram documents:** avoids the per-file limit but requires a manifest, multiple pinned-message references, atomic multi-part publication, cleanup, and recovery behavior. Higher state-corruption and duplicate risks.
- **Railway persistent volume/database:** a reasonable future authority, but changes hosting topology and recovery ownership. It is not required for this narrowly proven size fault.
- **Local Telegram Bot API server:** removes the hosted download limit, but adds another stateful service and operational/security burden solely to carry one backup.

## Implementation

- `tools/state_backup.py`: emits compressed v2 envelopes, restores both v1 and v2, and caps decompressed payloads at 128 MiB.
- `app/telegram_cloud_state.py`: refuses to replace/upload a backup above 19 MiB, leaving a 1 MiB margin below Telegram's documented ceiling.
- `tests/test_state_backup.py`: covers v2 round-trip/format, actual size reduction, and v1 compatibility in addition to existing authentication and rollback tests.
- `tests/test_telegram_cloud_state.py`: proves an oversized backup causes zero Telegram API calls and is removed locally.

## Verification

Local focused tests:

`python -m unittest -v tests.test_state_backup tests.test_telegram_cloud_state`

Result: 17 passed.

Local full suite:

`python -m unittest discover -s tests -p 'test_*.py'`

Result: 1341 passed, 6 skipped.

CI and production verification remain separate and must be added to the PR/issue after the exact branch head is available. Production acceptance requires: deploy the exact merge SHA, observe a successful compressed backup replacing the oversized v1 document, then restart/redeploy once and observe `Restored private assistant state from Telegram` without repeated deliveries.

## Risk, rollback, and next step

Risk is limited to backup serialization and restore. The writer changes to v2, while the reader supports both formats. Rollback is reverting the PR; an already-created v2 backup requires retaining the v2 reader, so rollback after production publication should revert the writer/guard only or restore code from the parent while first retaining a v1-compatible state copy.

Next: open the focused PR, require the exact-head checks, merge only if green and conflict-free, verify Railway auto-deploys the merge SHA, then perform one controlled restart to prove restore, deduplication, cursor, and Telegram offset continuity.
