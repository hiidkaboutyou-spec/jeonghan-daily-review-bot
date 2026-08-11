# Free Koyeb webhook deployment

This repository is designed to cut over without downtime.

## Preferred deployment: one Koyeb token

The repository includes `.github/workflows/deploy-koyeb.yml` so the existing Telegram/X/Gemini secret values never need to be copied out of GitHub by hand.

1. In Koyeb, create one Personal/API access token.
2. Add that value to this GitHub repository as the Actions secret `KOYEB_API_TOKEN`.
3. Run the GitHub Actions workflow **Deploy Personal Assistant to Koyeb Free** once.

The workflow validates the existing required GitHub secrets, creates/updates matching encrypted Koyeb Secrets without printing them, creates the `jeonghan-assistant/telegram` Free Web Service in Frankfurt from `main` using the repository Dockerfile, exposes port 8000, configures `/healthz`, and waits for deployment health.

## Manual service configuration (fallback)

- Source: GitHub repository `hiidkaboutyou-spec/jeonghan-daily-review-bot`
- Branch: `main`
- Builder: Dockerfile
- Type: Web Service
- Instance: `free`
- Region: Frankfurt or Washington, D.C.
- Port: `8000` / HTTP
- Health check: `/healthz`

Koyeb provides `KOYEB_PUBLIC_DOMAIN` automatically. The application uses it to register `https://<domain>/telegram/webhook` with Telegram.

## Required runtime secrets/environment variables

The one-token workflow transfers these existing GitHub runtime values directly into encrypted Koyeb Secrets:

- `TELEGRAM_BOT_TOKEN`
- `TELEGRAM_ADMIN_USER_ID`
- `TELEGRAM_REVIEW_CHAT_ID`
- `X_COOKIE`
- `GEMINI_API_KEY`

Optional:

- `STATE_BACKUP_KEY` — recommended if the original 32-byte base64 key is available. If omitted, webhook mode derives a process-only AES-256 backup key from the Telegram bot token.
- `SENTRY_DSN`
- `PUBLIC_BASE_URL` — only for a custom/non-Koyeb domain. Do not set for the normal Koyeb domain.

## Automatic cutover behavior

1. Before Koyeb is healthy, GitHub Actions continues the current Telegram polling runtime.
2. On Koyeb startup, the web service restores an encrypted pinned Telegram state backup when present, creates the exact production assistant, and calls Telegram `setWebhook` with a derived secret token.
3. On the next scheduled GitHub run, `getWebhookInfo` reports the live webhook. GitHub stops using `getUpdates` and instead sends one authenticated `/maintenance` request to the webhook service, then exits the old live-loop immediately.
4. `/maintenance` queues the normal scheduled scan, pending delivery, and reminders. This is real bot work, not a dummy keepalive.
5. After state changes, `state.json` and `private-review.sqlite3` are AES-256-GCM encrypted and stored as one pinned document in the private Telegram review chat. Later Koyeb instances can restore from that document after ephemeral-disk loss.

## Rollback

If the Koyeb service is intentionally removed, clear the Telegram webhook once (`deleteWebhook` with `drop_pending_updates=false`). The next GitHub scheduled run sees no webhook and automatically resumes the existing polling path.

## Security

- Webhook requests must contain Telegram's `X-Telegram-Bot-Api-Secret-Token` value derived from the bot token.
- Maintenance requests use a separate header name with the same derived runtime secret.
- State backups are encrypted before leaving the process; plaintext SQLite/JSON is never committed to the public repository.
- The Koyeb web service and its stateful assistant execute through one dedicated worker thread so thread-affine SQLite connections are never shared across arbitrary request threads.
