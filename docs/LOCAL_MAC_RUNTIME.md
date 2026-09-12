# Local macOS production monitor

The live X-dependent monitor runs as one bounded pass every five minutes through a
per-user LaunchAgent. Secrets stay in macOS Keychain and are never written to the plist.

Required generic-password items use service `jeonghan-daily-review-bot` and accounts
`TELEGRAM_BOT_TOKEN`, `TELEGRAM_ADMIN_USER_ID`, `TELEGRAM_REVIEW_CHAT_ID`, and `X_COOKIE`.
`GEMINI_API_KEY`, `STATE_BACKUP_KEY`, and `SENTRY_DSN` are optional. When no dedicated
backup key exists, the established Telegram-token-derived key is used in process.

The wrapper refuses dirty, non-`main`, or out-of-date checkouts, takes a non-blocking
single-writer lock, validates state, runs provider preflight, executes one monitor pass,
and uploads an encrypted pinned Telegram recovery snapshot only when durable state changed.
An authenticated-X outage marked `degraded` activates the bounded public-syndication
fallback while retaining the authenticated success cursor. A genuinely `offline` result
(for example missing required cookie fields) still fails closed without advancing state.

After the feature is merged and the required Keychain values are populated:

```bash
python3.11 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python -m tools.local_runtime.install_launch_agent
launchctl bootstrap gui/$(id -u) "$HOME/Library/LaunchAgents/com.hiidkaboutyou.jeonghan-daily-review-bot.plist"
```

The generated plist intentionally keeps the `.venv/bin/python` entrypoint rather than
resolving its symlink to the base interpreter, so scheduled runs use the installed project
dependencies after reboot as well as immediately after setup.

Do not disable the GitHub live schedule until one local pass has succeeded, state recovery
has been verified, and the local single-writer boundary is active. CI/check workflows and
the nightly fanfic workflow remain on GitHub Actions.
