# OpenClaude / Ollama adoption decision — 2026-10-05

## User-visible gap

The assistant's production translation path is Gemini-specific. When no Gemini key/capacity is
available it safely preserves the source, but the existing local macOS runtime cannot use a
fully-local no-key language model for translation even when Ollama is already installed.

This is a real optional resilience/privacy capability, but it must not weaken the existing fidelity,
manual-review or production-safety boundaries.

## External research

OpenClaude was reviewed for provider abstraction, local Ollama operation, explicit provider profiles,
context-window configuration, and runtime diagnostics. Its current repository license states that
the tree contains code derived from proprietary Claude Code source and that only contributor
modifications are offered under MIT where legally permissible. No OpenClaude implementation source
was copied here.

The implementation in this repository is clean-room and uses Ollama's public native API contract:

- local `POST /api/chat`;
- `stream: false`;
- JSON Schema passed through `format` for structured output;
- explicit context size (`num_ctx`), default 32,768;
- no API key or Authorization header;
- `GET /api/tags` preflight before model use.

## Decision matrix

### ADAPT — no-key local provider

Adopted only for translation-v2 generation and only when explicitly selected with
`HANI_TRANSLATION_PROVIDER=ollama`. The tracked default remains Gemini.

### ADOPT — translation-specific provider boundary

The local provider does not replace `CaptionWriter._client_or_none`. That is intentional:
archive-search expansion and candidate-title helpers keep their established deterministic/Gemini
behavior. Only the translation-v2 generation method receives the Ollama client.

### ADOPT — structured-output + fail-closed parsing

Ollama receives the existing JSON schema. Invalid HTTP/JSON/structured output returns `None`, which
activates the existing source-preserving/manual-review fallback instead of inventing a caption.

### ADOPT — local provider preflight

The preflight checks the daemon and exact configured model without sending source content. A missing
model is degraded/fallback evidence, not a crash of Telegram or X collection.

### REJECT — automatic Gemini → Ollama failover

Rejected. An arbitrary installed local model is not proven to meet the channel's Persian
naturalness/fidelity requirements. Local/no-key is an explicit operator choice, not a quality claim.

### REJECT — OpenClaude runtime dependency

Rejected. Hani already owns its collection, Telegram, translation validation, state, recovery and
observability. Adding a large coding-agent runtime would duplicate authority and add dependency and
licensing surface without solving the user-visible bottleneck.

## Safety and privacy invariants

- SOURCE stays untrusted translation data; existing indirect-prompt-injection instructions remain.
- No source/prompt text is copied into Ollama failure diagnostics.
- No API key is required or emitted.
- Default endpoint is localhost.
- Existing hard-fact/entity/semantic checks and manual review remain authoritative.
- Ollama cannot change X cursor/checkpoint, Telegram ownership, archive search, delivery authority,
  or provider admission elsewhere.

## Rollback

Remove `app/ollama_structured.py`, the translation-specific client hook, local preflight branch and
LaunchAgent environment passthrough. Default Gemini behavior remains unchanged and no state/schema
migration is involved.
