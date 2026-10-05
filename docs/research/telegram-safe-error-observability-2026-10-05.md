# Telegram safe error observability — 2026-10-05

## Run checkpoint

- Run identifier: `automation-2026-10-05T03:26Z-telegram-observability`
- Base: `main@ae97e8b9c04e4757bbf752264497d9ec9a47edbe`
- Head branch: `fix/telegram-safe-error-observability-164`
- Implementation head before this note: `9a10f005244caa822221371fb8c60bca760cf443`
- Related issue: #151

## Problem and acceptance

Production Telegram failures were reduced to exception class names in the concrete Railway webhook runtime. The useful Bot API description created by `TelegramBot.api()` was therefore lost in cloud-state restore/backup, permanent update failure, and exhausted transient retry logs.

Acceptance:

1. Known `TelegramPermanentError` and `TelegramTransientError` logs retain a bounded actionable reason.
2. Bot API URLs and token-shaped values remain redacted.
3. Unknown exceptions remain class-name-only.
4. Permanent failures keep the existing consume/offset behavior.
5. Exhausted transient failures keep the existing no-ack/retry behavior.

## Implementation

Changed:

- `app/webhook_server.py`
  - adds a bounded Telegram-only error formatter;
  - redacts Bot API URLs and token-shaped values again at the logging boundary;
  - preserves safe reasons for restore, backup, permanent failure, and exhausted transient retry.
- `tests/test_webhook_runtime.py`
  - proves transient reason visibility without offset advance;
  - proves permanent reason visibility, credential redaction, and existing offset advance.

Commits:

- `56b652d163c046961512b24f6601deff6f352083` — regression contract
- `9a10f005244caa822221371fb8c60bca760cf443` — implementation

## Validation actually run

Local isolated clone of the exact branch:

- `python -m unittest discover -s tests -p 'test_webhook_runtime.py' -v`: 18 passed.
- `python -m compileall -q app tests tools`: passed.
- `python -m app --check`: passed; 33 sources and 16,306 channel-style examples.
- `python -m pip check`: no broken requirements.
- `python -m unittest discover -s tests -p 'test_*.py' -q`: 1,321 passed, 6 skipped.

No live Telegram message was sent and no production state was mutated during validation.

## Design decision

No Redis, Celery, Postgres, new logger, or new runtime dependency was added. Telegram Bot API already returns a human-readable `description`, and the existing client already converts it to typed, sanitized exceptions. The missing behavior was solely at the webhook logging boundary. Official reference: https://core.telegram.org/bots/api#making-requests

## Risk and rollback

Risk is limited to log content. Delivery ownership, retry count, Telegram offset, cursor/checkpoint, X ingestion, scheduling, and cloud-state semantics are unchanged.

Rollback: revert the focused commits above. This restores class-name-only logs without requiring a state or configuration migration.

## Proof boundary and next step

Implemented and locally verified only. Exact-head CI, review, merge, Railway deployment, and extraction of the real production failure reason remain unproven. After CI is green, merge only after a fresh diff/review check; then inspect the exact deployed main logs. Use the revealed reason to repair the specific cloud-state/chat/delivery fault instead of changing storage architecture speculatively.
