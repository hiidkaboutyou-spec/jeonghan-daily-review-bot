# Webhook Gemini credential lease — 2026-10-05

## Production evidence

Railway production was healthy for Telegram webhook delivery and degraded public X recovery, but startup preflight reported:

`Gemini=fallback (GEMINI_API_KEY is not configured)`

The repository's GitHub Actions live job already receives `GEMINI_API_KEY` from the repository secret and delegates maintenance to the Railway webhook runtime. That created a split-runtime failure: Actions had the translation credential, while the always-on process that performed the actual delivery did not.

## Decision

Use the existing authenticated maintenance wake as a narrow, memory-only credential handoff:

1. GitHub Actions derives the existing `X-Assistant-Secret` from the Telegram bot token.
2. For HTTPS webhook origins only, the Actions runtime adds the existing GitHub `GEMINI_API_KEY` as `X-Hani-Gemini-Key`.
3. Credential-bearing maintenance is pinned to the configured production Railway origin.
4. Redirects are disabled so the secret-bearing request cannot be forwarded to another origin.
5. The webhook validates `X-Assistant-Secret` before accepting the provider credential.
6. The key is installed only into the in-process Settings/writer objects. It is not written to repository files, SQLite, Telegram state backups, or Railway variables.
7. If the webhook starts without a translation credential, automatic X collection may queue fresh posts but pending delivery is deferred until a credential lease arrives, preventing avoidable source-only fallback delivery.

The lease is refreshed by normal GitHub Actions live wakes, expires after 10 minutes if refresh stops, and is actively cleared from the in-process writer/client. A process restart also loses the lease by design.

## Security boundaries

- Plain HTTP maintenance URLs are rejected.
- The credential handoff is restricted to the configured production Railway origin.
- Requests do not follow redirects.
- A non-static Gemini lease expires after 10 minutes and is cleared from the in-process writer/client.
- Provider credential values are never logged or returned in API responses.
- The existing maintenance authentication remains mandatory.
- Credential length is bounded.
- A changed credential resets only the in-process Gemini client and circuit state.
- An explicitly configured Ollama translation provider ignores the Gemini lease.

Relevant guidance:
- GitHub Actions secret handling: https://docs.github.com/en/actions/reference/security/secure-use
- GitHub Actions secret reference: https://docs.github.com/en/actions/reference/security/secrets
- OWASP web-service TLS guidance: https://cheatsheetseries.owasp.org/cheatsheets/Web_Service_Security_Cheat_Sheet.html
- OWASP logging guidance: https://cheatsheetseries.owasp.org/cheatsheets/Logging_Cheat_Sheet.html

## Why not Supabase for this fix

The connected Supabase project is currently inactive, and the live reliability gap is credential placement, not missing durable SQL storage. Introducing a second state/queue authority would increase split-brain and operational risk without supplying the missing model credential. Supabase remains a candidate for later shared telemetry/job state only if a multi-worker architecture is intentionally adopted.

## Why not a new public/no-key model gateway

Current public model gateways increasingly require their own API keys or user-wallet authorization. Replacing one missing credential with another third-party dependency would not repair the existing deployment boundary. The repository already has an opt-in local Ollama path for local macOS operation; that is not suitable for the current Railway container without provisioning a model runtime.

## Rollback

Revert the four runtime/test changes in this PR. No schema, persisted state, Telegram webhook ownership, X cursor, or Railway variable migration is involved.


## Post-merge production verification

- PR #178 merged to `main` as `f4781ab933863c3c2b2598265559309d8740e74f`.
- Exact-head validation was green across the Daily workflow, live EN/KO/JA translation smoke, Fanfic, Maintenance, Security, CodeQL, and exact production Docker validation.
- Railway deployment `77aa4ece-dfbc-4297-bb5f-aeda7e9248b3` built exactly `f4781ab9...`, reached `SUCCESS`, restored private state, registered the Telegram webhook, and passed `/healthz`.
- Startup still correctly reports Gemini unavailable before the first Actions lease; the next main live wake is the acceptance event for `translation_ready`.
