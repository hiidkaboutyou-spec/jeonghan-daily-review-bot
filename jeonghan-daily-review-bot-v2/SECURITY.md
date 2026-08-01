# Security

- Never commit Telegram tokens, Gemini keys, or X cookies into files.
- Store all credentials only in GitHub Actions Secrets.
- Use a separate throwaway X account. Do not use your main/personal account.
- An X `auth_token` cookie grants session access. Treat it like a password.
- If a secret is ever exposed, revoke/rotate it immediately.
- Keep the repository workflow limited to `schedule` and `workflow_dispatch`; do not add untrusted pull-request workflows that access secrets.
