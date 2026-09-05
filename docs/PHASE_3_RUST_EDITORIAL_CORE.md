# Phase 3 — Rust Editorial Core Foundation

## Evidence-backed scope

Phase 3 introduces Rust only for deterministic truth/state. Python remains authoritative for volatile integrations (X/twscrape, media, AI and Telegram). This is deliberately not a rewrite.

The initial boundary is newline-delimited, versioned JSON over stdin/stdout. It is inspectable, language-neutral and keeps Python and Rust independently testable. PyO3 is intentionally deferred until profiling proves subprocess IPC is a real bottleneck.

SQLite remains the application persistence format established by Phases 1–2. The Rust foundation does not create a second database or fork source truth.

The existing Python recovery/checkpoint modules (`phase3_recovery` and its hardening layer) remain compatibility-critical collection infrastructure. The Rust core does not replace, rename, or bypass them in this phase.

## Rust invariants introduced

- only `COMPLETE` source windows can advance a complete-through cursor;
- cursor advancement is monotonic;
- source/post idempotency keys are deterministic and normalized;
- duplicate queue identities are rejected;
- source-first queue ordering is deterministic;
- invalid editorial queue transitions are rejected;
- JSON contract version is explicit (`1`).

## Rollout

This phase is shadow/foundation only. Existing Python production behavior remains usable and no public/Telegram delivery path is replaced. Later phases may call the JSONL executable behind a feature flag after contract parity tests exist.

## Validation gate

`cargo fmt --all -- --check`, `cargo clippy --workspace --all-targets --all-features -- -D warnings`, and `cargo test --workspace --all-features` must pass in CI before merge. Existing Python runtime audit/tests must also remain green. Phase 3 does not close merely because the Rust crate compiles: tests must demonstrate the invalid transitions above are impossible through the core API.
