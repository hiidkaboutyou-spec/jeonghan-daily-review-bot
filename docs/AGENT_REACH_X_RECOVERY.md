# Agent Reach X Recovery

## Purpose

Agent Reach is installed as a bounded tertiary recovery path for configured X sources.
It does not replace the production collector and it does not add requests while the
normal path is healthy.

Runtime order:

1. `twscrape` authenticated production collector (normal authority)
2. public X syndication timeline when production preflight marks X `degraded`
3. Agent Reach's current Twitter backend (`twitter-cli`) only when syndication itself fails

Every result from the degraded chain remains partial. The normal full-success cursor is
therefore retained so Phase 3 can backfill the missed window after X recovers.

## Installation

`requirements.txt` pins both layers used by production:

- official `Panniantong/Agent-Reach` source at a fixed Git commit
- `twitter-cli==0.8.5`, the structured-output X backend selected by Agent Reach

GitHub Actions and the local runtime already install `requirements.txt`; there is no
runtime `agent-reach install --system` step and no system-level mutation is required.

## Authentication and secret boundary

No new credential is required. The adapter reads the existing parsed `X_COOKIE` values:

- `auth_token` -> child-only `TWITTER_AUTH_TOKEN`
- `ct0` -> child-only `TWITTER_CT0`

The child process receives a narrow environment allow-list. Telegram, Gemini, raw
`X_COOKIE`, and unrelated process secrets are not inherited.

`twitter-cli` normally supports browser-cookie fallback. Daily Hani intentionally runs
it with isolated `HOME`, `XDG_CONFIG_HOME`, and `XDG_CACHE_HOME` directories so this
recovery path cannot silently read a developer's local browser session after explicit
credentials fail.

## Safety and scope

- read-only command: `twitter user-posts <handle> --max N --json`
- bounded timeout (default 25 seconds; maximum 60 seconds)
- bounded result request (default 200; code maximum 300)
- exact configured handle is re-checked after parsing
- retweets/promoted rows are dropped
- requested UTC time window is re-applied locally
- malformed or unexpected structured output fails closed
- credentials are redacted from CLI error text
- sources configured to exclude replies do **not** use this fallback because the
  twitter-cli structured user-post schema does not expose enough reply metadata to
  prove that exclusion safely

## Configuration

Optional environment controls:

```bash
X_AGENT_REACH_FALLBACK_ENABLED=1
X_AGENT_REACH_TIMEOUT_SECONDS=25
```

Set `X_AGENT_REACH_FALLBACK_ENABLED=0` for immediate local/runtime rollback of this hop.
The default is enabled because Agent Reach is only reached after both the primary X
provider state is degraded and public syndication fails.

## Validation

CI must remain credential-free and network-independent. Regression tests mock
`twitter-cli` and verify:

- structured payload conversion into `Update`
- source/window authority enforcement
- retweet exclusion
- isolated child environment and secret minimization
- credential redaction on failures
- no reply-exclusion guessing
- syndication remains ahead of Agent Reach in the recovery chain
- manual source recovery can use Agent Reach after syndication failure

Do not add a live X call to pull-request validation.
