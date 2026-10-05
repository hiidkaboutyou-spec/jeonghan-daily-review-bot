# ai-engineering-from-scratch adoption review — 2026-10-05

## Scope

Reviewed `rohitg00/ai-engineering-from-scratch` against the current Hani runtime, not against an older roadmap snapshot. The upstream repository is MIT-licensed. This change adapts concepts rather than vendoring upstream implementation code.

## Adopted now

### 1. Eval-harness latency evidence

Upstream's fixture eval harness records latency and aggregates mean/p95 alongside correctness. Hani already has stronger domain-specific translation gates (hard facts, Persian naturalness prechecks, exact-output-bound human review, quota-aware resume), but it did not expose comparable end-to-end latency evidence for the legacy and current translation paths.

Adopted:
- measure the full logical provider operation around each quota-aware pipeline call;
- include retry/backoff time in the measured operation;
- persist per-case `elapsed_ms`;
- aggregate sample count, mean and nearest-rank p95 for legacy and current pipelines;
- keep latency informational: it does **not** weaken or replace the existing quality/human gates.

This also matches the current OpenTelemetry GenAI convention that a logical inference span should cover automatic retries.

### 2. Indirect prompt-injection boundary for translation SOURCE

Upstream's PVE lesson correctly treats retrieved content as untrusted data and emphasizes source provenance. Hani's v1 translator already stated that instructions inside source text are data, while the direct v2 path relied mostly on SOURCE-as-truth wording.

Adopted:
- the v2 system contract now explicitly states that system/developer-style instructions or behavior-change requests inside SOURCE are untrusted translation content;
- they may be translated, but never executed and never allowed to change schema/output destination/translation rules;
- the source-only retry path preserves the same rule;
- a regression test sends an injection-shaped sentence through the concrete v2 prompt path and verifies that the directive remains source data.

## Already present; no duplicate implementation

The following upstream ideas are useful but already exist in stricter Hani-specific form:
- evidence-first agent harness and production acceptance;
- source-completeness and cursor/state gates;
- bounded retry/quota handling;
- translation fixture/production/human benchmarks;
- privacy-safe structured observability and Sentry scrubbing;
- explicit source authority and factual fidelity repair.

## Deferred deliberately

### Full OpenTelemetry/Prometheus dependency
Useful later for cross-service traces, but current structured metadata + Sentry already covers the immediate debugging need. Current GenAI semantic conventions remain in development, so adding a production dependency now would increase operational surface without fixing a demonstrated incident.

### Generic tool registry / JSON-RPC / sandbox harness
Hani is an application with explicit runtime boundaries, not a generic model-controlled tool host. The repository-owned agent harness is developer-side. Adding a generic tool dispatcher to production would widen the attack surface.

### HyDE / multi-query / cross-encoder RAG
These are not the bottleneck for Hani's source collection or short-form translation and would add model calls/cost/failure modes without measured benefit.

## Acceptance

- Existing translation quality semantics remain unchanged.
- Latency evidence is additive and non-authoritative.
- Injection-shaped SOURCE remains available for faithful translation but cannot redefine the v2 instruction contract.
- No new runtime dependency, secret, external service, state schema, or delivery authority is introduced.
