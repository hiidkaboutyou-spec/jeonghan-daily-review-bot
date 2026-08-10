# Operations Runbook

This runbook is for the repository owner. It assumes no code editing.

## What the bot does

The bot collects Jeonghan-related source updates, organizes them, prepares Persian ChannelStyle captions/translations and sends them to a **private Telegram review chat**. It also supports recent replay, source-window retrieval, archive search, inbox/reminders, media delivery and a nightly fic digest.

It does **not** automatically publish to a public Telegram channel.

## Workflows

### Jeonghan Daily Review Bot

File: `.github/workflows/main.yml`

- validation on relevant push/PR changes;
- scheduled runtime: `7,22,37,52 * * * *`;
- manual dispatch has `check` and `live` modes.

### Nightly Jeonghan Fanfic Digest

File: `.github/workflows/fic-digest.yml`

- scheduled at `30 18 * * *` UTC;
- runtime shares the private SQLite store and runtime concurrency group with main.

### Channel Style Translation Benchmark

File: `.github/workflows/translation-benchmark.yml`

- separate from fast runtime validation;
- checkpoint/resume is intentional;
- a 429 is an external quota result, not a reason to edit benchmark output manually.

GitHub schedules use the default branch and can be delayed: <https://docs.github.com/en/actions/using-workflows/events-that-trigger-workflows#schedule>.

## Required GitHub Actions Secrets

Open the repository → **Settings** → **Secrets and variables** → **Actions** → **New repository secret**.

Required for real Telegram/X runtime:

- `TELEGRAM_BOT_TOKEN`
- `TELEGRAM_ADMIN_USER_ID`
- `TELEGRAM_REVIEW_CHAT_ID`
- `X_COOKIE`

Optional:

- `GEMINI_API_KEY` — ChannelStyle/model features; fallback exists if absent.
- `STATE_BACKUP_KEY` — enables encrypted recovery backup.
- `SENTRY_DSN` — optional scrubbed technical observability.

`GEMINI_MODEL` can be supplied as an environment/Actions variable where the workflow supports it; code default is `gemini-2.5-flash-lite`.

Never paste real tokens, cookies, chat/user IDs, backup keys, private messages or private SQLite content into GitHub issues, PR comments, public logs or tracked files.

## Generate `STATE_BACKUP_KEY`

Run locally on a trusted machine:

```bash
python -c "import base64,secrets; print(base64.b64encode(secrets.token_bytes(32)).decode())"
```

Copy the one-line result directly into a GitHub Actions secret named exactly:

`STATE_BACKUP_KEY`

Do not add quotes, spaces or a trailing comment. Do not save it in the repository.

The decoded key must be exactly 32 bytes. `tools/state_backup.py` rejects malformed base64 or any other decoded length.

## What happens if `STATE_BACKUP_KEY` is missing

Normal bot validation/runtime is not intentionally broken by absence of this optional secret. Runtime emits a warning that encrypted recovery is disabled; Actions Cache remains best-effort only.

Do **not** interpret this as durable persistence.

## How cache works

The workflows cache:

- `.state/state.json`
- `.state/private-review.sqlite3`

Cache is an optimization and continuity mechanism. GitHub can evict caches. GitHub documentation: <https://docs.github.com/en/actions/reference/workflows-and-actions/dependency-caching>.

## How encrypted recovery works

When `STATE_BACKUP_KEY` is configured:

1. cache restore happens first;
2. if required state is missing, workflow lists non-expired `private-state-backup` artifacts newest→oldest;
3. each artifact is downloaded to `/tmp`, unpacked and authenticated/validated with `python -m tools.state_backup validate`;
4. the first valid candidate is restored;
5. JSON and SQLite are validated before replacement;
6. after runtime/checkpoint, a new ciphertext-only backup can be uploaded for 90 days.

The uploaded artifact contains `.state/private-state-backup.enc`, not plaintext JSON or SQLite.

Because this repository is public, encryption is essential. GitHub documents artifact read access here: <https://docs.github.com/en/rest/actions/artifacts>.

## Verify backup creation after merge

Do this only after `STATE_BACKUP_KEY` has been added and the branch has been human-approved/merged.

1. Open **Actions**.
2. Open a completed scheduled/live run of `Jeonghan Daily Review Bot` or `Nightly Jeonghan Fanfic Digest`.
3. Confirm `Create authenticated encrypted recovery backup` succeeded.
4. Confirm `Upload encrypted recovery backup` succeeded.
5. At the run bottom, verify an artifact named `private-state-backup` exists.
6. Do not post/download/decrypt it on an untrusted computer.
7. Confirm workflow logs contain no message bodies, cookie/token values or SQLite plaintext.

This is production verification, not unit-test evidence.

## Test restore safely

Do **not** intentionally delete production state just to test recovery.

A safe owner verification is:

1. confirm at least two successful encrypted backup artifacts exist;
2. use a disposable local directory or temporary test branch/workflow only if separately authorized;
3. provide the same `STATE_BACKUP_KEY` through the environment, never command-line history if your shell records it;
4. run `python -m tools.state_backup validate --input <encrypted-file>`;
5. restore into an empty disposable directory;
6. verify `state.json` parses and SQLite returns `PRAGMA quick_check = ok`;
7. delete the disposable plaintext copy securely according to your machine policy.

The normal workflow itself never prints decrypted data.

## Rotate `STATE_BACKUP_KEY`

Key rotation makes old artifacts unreadable with the new key.

Safe process:

1. Do not delete the old key immediately if recovery continuity matters.
2. Confirm current production state is healthy and cached/local state exists.
3. Generate a fresh 32-byte base64 key.
4. Replace the GitHub secret `STATE_BACKUP_KEY` with the new value.
5. Allow a successful runtime to create a new backup using the new key.
6. Verify the new artifact exists and its workflow succeeded.
7. Old artifacts encrypted with the old key will fail authentication and the workflow will try older/newer candidates; once only new-key backups are needed, remove obsolete old artifacts if desired.

If you lose the old key before creating a new-key backup, old ciphertext cannot be recovered by repository code.

## Rotate the X cookie

When X authentication expires or becomes invalid:

1. obtain a fresh cookie through your normal authorized account/browser process;
2. replace only the GitHub Actions secret `X_COOKIE`;
3. do not commit a cookie file/database;
4. run/observe validation; live X access must be tested in an authorized runtime, not by exposing the cookie;
5. if logs show configuration parsing failure, check that required `auth_token` and `ct0` fields exist in the supported format.

Do not paste X cookies into issues or chat screenshots.

## Verify Telegram configuration

Without revealing values:

- `TELEGRAM_BOT_TOKEN` must be present;
- `TELEGRAM_ADMIN_USER_ID` must parse as an integer > 0;
- `TELEGRAM_REVIEW_CHAT_ID` must be a non-zero integer;
- bot output must arrive only in the intended private review chat;
- unauthorized users/chats must not gain review actions.

After merge, perform a small smoke check with `/start`, `/menu`, `/status` and one non-destructive archive/recent command.

## Recognize Telegram 429

A Telegram flood-control response is HTTP/API 429 with `retry_after`. The transport waits according to the bounded retry policy and treats it as transient. Do not repeatedly restart workflows to fight a Telegram 429.

Bot API reference: <https://core.telegram.org/bots/api#responseparameters>.

## Recognize Gemini 429

The benchmark/runtime may report `429 RESOURCE_EXHAUSTED`. Google documents request/token/day/spend-related rate limits: <https://ai.google.dev/gemini-api/docs/rate-limits>.

Do not change the approved model merely to make the audit green and do not handwrite benchmark outputs.

## Resume the Gemini benchmark

When quota becomes available:

1. open `Channel Style Translation Benchmark` in Actions;
2. allow the normal PR/workflow trigger or use manual dispatch only when owner-authorized;
3. keep the existing checkpoint/resume behavior;
4. verify completed cases are restored rather than regenerated;
5. a complete machine result still requires human SOURCE→OLD→NEW review before Part 4 can pass.

Expected blocked state while quota is unavailable:

- Benchmark harness: VERIFIED WORKING
- Live Gemini generation: BLOCKED BY EXTERNAL QUOTA
- Human quality gate: NOT PASSED
- Merge authorization: NOT GRANTED

The benchmark workflow treats only exit code 3 backed by an `INCOMPLETE`
checkpoint containing explicit 429/`RESOURCE_EXHAUSTED` evidence as
`BLOCKED_BY_EXTERNAL_QUOTA`. It uploads that incomplete artifact and writes the
warning to the job summary. Exit 3 without that evidence, malformed checkpoints,
verifier defects, and all other non-zero exits remain genuine workflow failures.

## Interpret common workflow failures

- **Compile failure:** syntax/import defect; do not run live.
- **`app --check` failure:** tracked config/corpus validation defect; inspect exact message.
- **Unit-test failure:** repository gate failed; fix before merge.
- **`pip check` failure:** installed dependency incompatibility; inspect manually before changing versions.
- **Encrypted backup validation failure:** workflow tries older artifacts. If artifacts exist but none validate, runtime fails closed.
- **No recovery artifact exists:** first-run/cache behavior is allowed; create a backup after a healthy runtime if key is configured.
- **Telegram transient failure:** bounded retry; do not delete state/offset manually.
- **Gemini 429 in benchmark:** external quota; checkpoint should survive.

## State corruption recovery

If `private-review.sqlite3` fails `PRAGMA quick_check`:

1. stop destructive/manual edits;
2. preserve the corrupt file privately if forensic analysis is needed;
3. prefer a previously authenticated encrypted backup;
4. validate candidate before restore;
5. do not replace a healthy state file with an unvalidated copy;
6. if no valid recovery exists, use the repository's rebuild/first-run behavior only with explicit awareness of what historical state may be lost.

If `state.json` is malformed, the backup tool rejects it rather than encrypting/restoring it as valid state.

## Rollback guidance

Before merge, rollback means moving/closing PR work — never force-push/reset production main as part of normal operation.

After merge, if a severe regression occurs:

1. identify the exact production commit used by the failed scheduled run;
2. preserve logs/artifacts privately;
3. revert through a normal reviewed commit/PR rather than rewriting main history;
4. keep state/recovery backups untouched until the failure is understood;
5. never restore plaintext private state into a public branch.

## Production verification after merge

Repository CI cannot prove scheduled default-branch external behavior before merge. After human merge approval and merge:

1. wait for the next scheduled main run; do not trigger live solely for audit unless authorized;
2. open the workflow run;
3. in Checkout/log metadata, record the commit SHA actually checked out;
4. verify it descends from/equals the merged commit expected on `main`;
5. confirm validation succeeded before live steps;
6. confirm at least one private bot pass completed successfully;
7. confirm no public channel output occurred;
8. confirm encrypted backup steps if key is configured;
9. verify nightly fic runtime similarly on its next schedule;
10. only then upgrade those matrix rows from implemented/CI-verified to production-verified.

## Exact commit used by a scheduled run

GitHub scheduled workflows run from the default branch. To confirm the exact commit:

1. open **Actions** → workflow → scheduled run;
2. inspect the run's commit/Checkout step;
3. copy the SHA shown by `git log -1 --format=%H`/checkout metadata if present;
4. compare it with repository `main` history at that time;
5. remember a scheduled workflow can be delayed and therefore may not start at the exact cron minute.

Never infer the production SHA from the time alone.
