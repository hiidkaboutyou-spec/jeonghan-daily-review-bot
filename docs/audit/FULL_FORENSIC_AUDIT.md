# Full Forensic Audit

> **Production launch update — 2026-08-11:** The historical audit baseline below has been superseded by the production `main` deployment. Live default-branch runs verified Telegram, X, `gemini-3.1-flash-lite`, the primary ChannelStyle writer, encrypted artifact creation/upload, and the full nightly fanfic delivery. Current evidence is maintained in [`../LAUNCH_STATUS.md`](../LAUNCH_STATUS.md) and the feature matrix.

## Scope and evidence boundary

Repository: `hiidkaboutyou-spec/jeonghan-daily-review-bot`

Audited branch: `agent/channel-style-translation-v1`

Protected production baseline: `main = 7ebcc7425cd65e47186145c5b43fa4784d531289`.

The final closure started from branch HEAD `cbe035ff37a84b0c1d16df2373e447d34a53fff0`. The exact final HEAD is intentionally recorded by the final GitHub Actions/PR closure report rather than hard-coded here, because committing this document changes the branch SHA.

This audit distinguishes four evidence levels:

- **Code verified** — implementation and deterministic regression tests were inspected.
- **CI verified** — the repository validation workflow executed compile/check/tests successfully for the cited candidate.
- **Production verified** — a real scheduled/runtime execution on the default branch exercised the external service path.
- **External/human verification required** — repository work is complete but owner configuration, quota, scheduled default-branch execution, or human judgment is required.

No merge, main update, reset, rebase or force-push was part of this audit.

## Final architecture and production call path

`python -m app` loads validated `Settings`, initializes optional scrubbed observability and enters `ChannelStyleReviewApplication`. The ChannelStyle application is layered on the private review runtime; the primary caption/translation writer is the ChannelStyle writer when the corpus validates, with hardened legacy/neutral fallbacks when the style layer or Gemini is unavailable.

The application is private-review-only. Telegram delivery targets the configured review chat, authorization is restricted to the configured admin/review context, and no public-channel publish action was added by this branch.

Core runtime flow:

1. GitHub Actions restores best-effort state cache.
2. If required state is missing and `STATE_BACKUP_KEY` is configured, authenticated encrypted recovery artifacts are tried newest-to-oldest until one validates.
3. `Settings` and tracked configuration are validated.
4. X retrieval/search gathers source material with bounded completeness rules.
5. Updates are normalized, filtered, grouped and ordered.
6. The translation/caption pipeline creates private review drafts.
7. Media and text delivery use persistent dedup/multipart receipts.
8. Telegram commands, callbacks, inbox actions and reminders are processed in the private review chat.
9. Runtime state is checkpointed; caches are saved; if enabled, an encrypted recovery artifact is produced.

## Workflows

### `.github/workflows/main.yml`

- Push/PR: compile, project check and complete unit suite only; live runtime steps are skipped.
- Schedule: approximately every five minutes, with a short quiet window around the nightly fic runtime.
- Manual dispatch: explicit `check` or `live` mode.
- Runtime concurrency resolves to `jeonghan-daily-review-bot-runtime` and does not intentionally overlap the Nightly fic runtime.
- Production Gemini model defaults to `gemini-3.1-flash-lite`; configured fallbacks remain bounded.

### `.github/workflows/fic-digest.yml`

- Schedule: `30 18 * * *` (intended around 22:00 Tehran; GitHub scheduling is not an exact-time guarantee).
- Uses the same private SQLite persistence location and the same runtime concurrency group as main runtime.
- CI does not require live AO3 access.

### `.github/workflows/translation-benchmark.yml`

- Expensive ChannelStyle benchmark is isolated from normal validation.
- Uses checkpoint/resume and bounded quota failure behavior.
- A live `429 RESOURCE_EXHAUSTED` is preserved as external evidence, not converted into a fake PASS.

GitHub documents that scheduled workflows run from the latest commit on the default branch and may be delayed under high load: <https://docs.github.com/en/actions/using-workflows/events-that-trigger-workflows#schedule>.

## State and persistence architecture

### JSON state

`.state/state.json` stores runtime state such as Telegram offset and other non-SQLite state. `StateStore.path` is the canonical runtime location.

### Private SQLite

`.state/private-review.sqlite3` is shared private persistence for archive/index data, callback-token mappings, message/multipart receipts, media-delivery identity and fic observations. Production wiring derives the path from the real state object, with a compatibility fallback only when a settings state path exists; intentional no-state test doubles can construct safely without silently changing production persistence.

### Cache

Actions Cache is treated as acceleration/best-effort continuity, not as a database or authoritative backup. GitHub documents eviction/retention behavior, including removal of caches that are not accessed within the configured retention period/default policy: <https://docs.github.com/en/actions/reference/workflows-and-actions/dependency-caching>.

### Encrypted recovery artifact

When `STATE_BACKUP_KEY` exists, `tools/state_backup.py` creates a ciphertext-only `private-state-backup.enc` artifact using AES-256-GCM. The key must decode from base64 to exactly 32 bytes. Each encryption uses `os.urandom(12)` for a fresh 96-bit nonce. The format string is authenticated as AAD.

On restore, authentication/decryption occurs before any target mutation. The decrypted payload has per-file SHA-256 checks, then JSON validation and SQLite `PRAGMA quick_check`. Target files are staged and atomically replaced; if a later replace fails, already-replaced targets are rolled back. Temporary files are cleaned in `finally`/workflow traps.

The workflow lists non-expired artifacts newest-to-oldest, validates each candidate without state mutation, and restores the first valid candidate. If artifacts exist but none authenticate, the runtime fails closed rather than silently starting with apparently lost private state. If no recovery artifact exists at all, first-run/cache behavior remains allowed.

`cryptography` documents AESGCM key sizes, authentication-tag failure semantics and nonce non-reuse requirements: <https://cryptography.io/en/stable/hazmat/primitives/aead/#cryptography.hazmat.primitives.ciphers.aead.AESGCM>.

The repository is public, and GitHub documents that repository-read access is sufficient to retrieve workflow artifacts. Therefore plaintext private state is never intentionally uploaded: <https://docs.github.com/en/rest/actions/artifacts>.

## Source retrieval and X completeness

The configured source set and multilingual terms are validated by `python -m app --check`. Complete-source mode does not silently substitute search results for a timeline. A requested time window is considered complete only when collection naturally exhausts or crosses the lower time boundary; hitting a cap before the boundary fails instead of claiming completeness. Marketplace/trade noise and malformed manual handles have regression coverage. Partial source failure is recorded rather than hidden by successful results from other sources.

## Telegram polling and command processing

Polling advances the Telegram offset only after successful handling. Transient Telegram platform failures do not increment the poison-update counter or advance the offset. Permanent malformed/unsupported application updates have bounded handling; unsupported queued types are acknowledged rather than poisoning the queue.

Admin/review-chat authorization remains part of the private-review boundary. Callback queries are acknowledged through the Telegram transport and callback routing is centralized before feature-specific handlers.

## Callback routing

Telegram `callback_data` is limited by UTF-8 bytes, not Python character count. The repository now centrally validates the byte length. Payloads within the limit remain direct; oversized payloads are replaced by short opaque tokens mapped durably in the private SQLite store. The token mapping has collision handling and expiry; malformed, expired and unknown tokens fail closed. Private callback payload text is not exposed in the token.

Telegram Bot API reference: <https://core.telegram.org/bots/api>.

## Media delivery and duplicate prevention

Media retrieval prefers direct/high-quality variants and bounded fallbacks. Telegram file IDs are cached where safe. Exact-media dedup uses durable delivery identity including stable Telegram `file_unique_id` when available and content hashing for downloaded bytes. A failed Telegram send does not create a false delivery receipt.

No claim is made that Telegram offers exactly-once delivery; the residual acceptance-before-receipt crash window is documented in `KNOWN_LIMITATIONS.md`.

## Multipart/long-message delivery

Long private text is split losslessly at safe boundaries without silent truncation. Ordering is deterministic, and the inline keyboard is attached only to the intended final part. Durable multipart receipts allow retry to skip confirmed earlier parts. Editing a long message edits the first part and sends ordered tail parts.

The logic preserves Unicode code points, including Persian/Korean/Japanese/emoji and mixed-language content. No parse-mode entity splitting is introduced because the relevant current private-review sends use plain text unless explicitly handled by a specific method.

## Telegram 429/5xx/failure handling

The transport distinguishes:

- `429` with `retry_after`;
- transient 5xx;
- timeout/connection reset;
- malformed JSON/protocol response;
- Telegram `ok: false` responses;
- permanent 4xx.

Retries are bounded. Temporary Telegram unavailability does not quarantine a valid update. Permanent failures are not retried forever. Error construction/logging redacts the bot token and avoids forwarding private message bodies.

Telegram documents `ResponseParameters.retry_after` as seconds to wait after flood control: <https://core.telegram.org/bots/api#responseparameters>.

## Translation pipeline

The branch carries the ChannelStyle corpus, retrieval and translation pipeline. The corpus validation currently reports 16,306 examples and explicitly no recency weighting. Date is not a style-authority signal. Retrieved history can influence style but is not authorized to inject historical facts.

Hard fidelity checks cover names/identity, numbers/dates, URLs, hashtags, laughter, speaker/quote structure and source-authorized canonical forms. Explicit confirmed feedback can be learned; generated/rejected drafts are not automatically promoted into training memory.

The production writer is ChannelStyle when artifacts validate; fallback behavior preserves safe neutral/source behavior when Gemini/style transfer is unavailable.

### Part 4 evidence boundary

Deterministic benchmark harness behavior is verified: checkpoint/resume, model-change invalidation, bounded 429 exit and artifact preservation are tested. Live benchmark attempts continue to receive `429 RESOURCE_EXHAUSTED`; Google documents that Gemini rate/spend limits can produce this response: <https://ai.google.dev/gemini-api/docs/rate-limits>.

A prior machine benchmark pass was rejected by human review for Persian naturalness/faithfulness issues; subsequent hardening was implemented, but a fresh complete model-generated benchmark plus human gate has not completed because quota remains unavailable. Therefore:

- Benchmark harness: **VERIFIED WORKING**
- Live Gemini generation: **BLOCKED BY EXTERNAL QUOTA**
- Human quality gate: **NOT PASSED**

No handwritten output is represented as Gemini output.

## AO3/fanfic digest

The AO3 path reads public HTML, not a live API dependency in CI. Search pagination:

- stops on requested result count;
- stops on a truly empty search result page;
- does **not** stop merely because one page has zero qualifying Jeonghan relationships;
- has a hard maximum of 25 pages;
- is paced between pages;
- retries only bounded transient HTTP failures and honors numeric `Retry-After` for 429.

Detail lookups driven by X recommendations are serial and paced. Relationship classification requires a Jeonghan relationship tag and ignores unrelated side relationships when determining the Jeonghan ship.

`FicStateStore` tracks work ID/chapter/update metadata in private SQLite and classifies observations as new/updated/unchanged. Digest sends use stable daily delivery keys so partial multipart failure can resume without repeating confirmed parts.

Operational risk remains because the repository relies on AO3 public HTML structure; live AO3 is intentionally not mandatory in CI.

## Confirmed defects and root causes closed

Major forensic defects closed include:

- production constructor coupled to `settings.state_path` despite the canonical path belonging to the real `StateStore`;
- repeated exact media without a durable delivery ledger;
- callback length measured/truncated as characters rather than validated as UTF-8 bytes;
- long Telegram text previously failing/truncating instead of losslessly splitting;
- Telegram transient failures previously feeding poison-update behavior;
- incomplete source timeline evidence/ordering edge cases;
- AO3 pagination stopping on a non-qualifying page and aggressive detail concurrency;
- shared fic/main state lacking coordinated runtime serialization;
- Actions cache previously treated as stronger persistence than GitHub guarantees;
- encrypted recovery initially selected only the newest artifact and lacked rollback if a multi-file replacement failed halfway;
- benchmark freshness/resume wiring and multiple deterministic fidelity false positives/false negatives.

Detailed issue/commit mapping is in `REPAIR_LOG.md`.

## Test and CI evidence

The earlier pre-AO3/recovery green checkpoint `ef6ce7eab472e31cff38b1dc9a33f407e6fee98b` executed 264 tests with zero failures/errors.

After AO3/recovery implementation, Nightly validation on candidate `b8a233d5ed488de7130c01828ecb396f82df4036` executed:

- `python -m pip check` — no broken requirements;
- `python -m compileall -q app tests tools` — success;
- `python -m app --check` — `CHECK OK: 22 sources, 16306 channel-style examples, recency weighting NONE`;
- complete unittest discovery — 282 tests, all successful.

Additional final AO3/recovery workflow tests were committed after that candidate. The final closure verdict must use the exact final-head CI run after all audit documentation commits; this document does not substitute for that final CI evidence.

No separate Ruff/Black/Mypy/Pyright configuration is present in the audited repository, so no unconfigured lint/type command is falsely reported as a project gate.

## Security review

Evidence-backed controls verified in code/tests:

- admin/review-chat boundary retained;
- callback oversized payloads opaque, persistent and expiring;
- collision handling prevents token overwrite;
- invalid/unknown/expired callbacks fail closed;
- untrusted manual X handles are constrained;
- untrusted media URLs are checked before external extractor fallback;
- subprocess construction does not embed the X cookie in the logged gallery command;
- media download/retry paths are bounded;
- AO3 pagination/retries are bounded;
- Telegram retry behavior is bounded;
- Sentry event scrubbing removes message bodies, cookies, headers and PII-rich contexts;
- backup key is read from environment only and never printed;
- encrypted recovery authenticates before mutation;
- public artifact contains ciphertext only;
- private SQLite/state files remain ignored and are not tracked.

No new public-autopublish capability was found or introduced.

The final secret-pattern and dependency-advisory scan is a closure gate and must be recorded in the final report; scanner hits must be manually distinguished from intentional fake test placeholders.

## Configuration inventory

| Name/value | Required | Secret? | Consumer | Validation / fallback | Failure mode / safe example |
|---|---|---:|---|---|---|
| `TELEGRAM_BOT_TOKEN` | Live yes | yes | `Settings`, Telegram transport; main/fic workflows | non-empty for live | configuration error; `000000:REPLACE_ME` |
| `TELEGRAM_ADMIN_USER_ID` | Live yes | treat private | `Settings`, authorization | integer > 0 | configuration error; `123456789` |
| `TELEGRAM_REVIEW_CHAT_ID` | Live yes | treat private | `Settings`, Telegram target | non-zero integer | configuration error; `-1001234567890` |
| `X_COOKIE` | Live yes | yes | `Settings`, X collector | parser accepts supported formats and requires `auth_token` + `ct0` | configuration error; placeholder only |
| `GEMINI_API_KEY` | no | yes | ChannelStyle/fic summary model | empty allowed | neutral/legacy/fallback behavior |
| `GEMINI_MODEL` | no | no | Settings/benchmark/fic workflow | empty uses configured default | default `gemini-3.1-flash-lite` |
| `STATE_BACKUP_KEY` | no | yes | `tools.state_backup`, main/fic workflows | strict base64, decoded length 32 | absent value is replaced by a masked stable key derived from the bot token; malformed configured key fails backup/restore |
| `SENTRY_DSN` | no | yes-ish endpoint credential | `app.observability` / main workflow | empty disables | no observability; safe placeholder empty |
| `Settings.state_path` | repository-derived | private path | `StateStore` and derived stores | canonical `.state/state.json` | runtime state path; private DB sibling |
| private DB path | derived | private file | archive/callback/delivery/fic stores | `.state/private-review.sqlite3` | cache/recovery/first-run behavior |
| `config/settings.json` timezone | tracked | no | Settings | timezone name | validation error on bad config |
| `default_scan_hours`, `menu_recent_hours` | tracked | no | retrieval/UI | numeric config | settings validation/default file |
| `max_download_bytes`, `telegram_upload_limit_bytes`, `download_chunk_bytes` | tracked | no | media | numeric bounded limits | media failure/fallback |
| AO3 base/user-agent | code constant | no | `fic_digest.py` | HTTPS AO3 public HTML | transient failure produces no mandatory CI failure |
| AO3 `max_pages`/pace | code defaults | no | `search_ao3` | cap clamps to max 25 | bounded incomplete result rather than infinite pagination |
| benchmark output | workflow/CLI | no | benchmark tool | `.state/part4-real-benchmark.json` | checkpoint artifact |
| benchmark pacing/batch/cooldown/retry | workflow/CLI | no | benchmark tool | current workflow uses bounded values | unresolved 429 exits with checkpoint |

No actual secret values are required or inspected by this inventory.

## Authoritative research sources

- Telegram Bot API: <https://core.telegram.org/bots/api>
- GitHub dependency caching/eviction: <https://docs.github.com/en/actions/reference/workflows-and-actions/dependency-caching>
- GitHub workflow artifacts API/access: <https://docs.github.com/en/rest/actions/artifacts>
- GitHub artifact retention: <https://docs.github.com/en/actions/how-tos/manage-workflow-runs/download-workflow-artifacts>
- GitHub scheduled workflow behavior: <https://docs.github.com/en/actions/using-workflows/events-that-trigger-workflows#schedule>
- `cryptography` AESGCM: <https://cryptography.io/en/stable/hazmat/primitives/aead/#cryptography.hazmat.primitives.ciphers.aead.AESGCM>
- Gemini rate limits: <https://ai.google.dev/gemini-api/docs/rate-limits>
- Gemini API errors: <https://ai.google.dev/gemini-api/docs/api-errors>

## Remaining external verification

Repository code cannot itself complete these gates:

1. A dedicated valid `STATE_BACKUP_KEY` is recommended before rotating the Telegram bot token; production can otherwise use its masked derived key.
2. A disposable controlled recovery drill is still needed to promote F40 from partial; live default-branch backup creation/upload is already proven.
3. Gemini project/account quota must become available for completion of the fresh live benchmark.
4. A human must inspect the required real SOURCE → OLD → NEW outputs and approve Persian quality.

## Final forensic verdict rule

If exact final-head validation, documentation, secret/dependency scan and full diff review are green, the technically correct merge-readiness status is **SUBSTANTIAL REPAIRS COMPLETE — EXTERNAL VERIFICATION REMAINS**. That status means the branch can enter human merge review; it does **not** authorize merge and does not claim the translation human-quality gate has passed.
