# Delivery credential starvation health — 2026-10-07

Run: `2026-10-07T13:58+03:30-delivery-starvation-health`

Rebased on current `main` after cadence fix #184 on 2026-10-08.

## Production problem and acceptance target

The Railway webhook runtime continued its autonomous degraded X scans while no
credential-bearing GitHub Actions maintenance wake reached the service. The
last observed wake was `2026-10-07T06:09:06Z`; scans continued every roughly
12 minutes through `2026-10-07T10:20:41Z`. The latest inspected scan reached
33/33 selected sources, retained the authoritative success cursor, and queued
updates from both `@haniwadda` and `@anshelhan`. The bounded log query returned
at least 136 unique pending IDs, so that count is a lower bound rather than a
claim about the complete durable queue.

The existing public `/healthz` response reported `translation_ready=false` but
could not distinguish an idle service from a service with real updates blocked
behind the missing credential. The acceptance target for this slice is:

- report whether a delivery backlog exists without exposing its size or content;
- report whether that backlog is specifically blocked on translation readiness;
- keep HTTP health successful so Railway does not restart a state owner merely
  because an external credential lease is absent;
- include the private backlog count only in operator logs;
- do not change queue, cursor, deduplication, translation, Telegram or credential
  lifetime semantics.

## Evidence and options

Repository inspection shows that the Actions workflow is scheduled every five
minutes and hands the Gemini key to `/maintenance`; the runtime lease expires
after ten minutes. GitHub documents that scheduled workflows can be delayed
under load and queued jobs can be dropped:

https://docs.github.com/en/actions/reference/workflows-and-actions/events-that-trigger-workflows#schedule

Options considered:

1. **Persistent Railway provider credential** — removes Actions scheduling from
   translation availability, but requires an explicit secret-placement decision
   and cannot be performed by code without the credential. This remains the
   strongest operational fix.
2. **Longer in-memory lease** — code-only and would bridge some missed wakes, but
   expands secret lifetime and still fails after a restart. Deferred pending an
   explicit security/availability decision.
3. **Deliver untranslated content after a timeout** — improves freshness but
   changes owner-visible editorial semantics and can create later correction or
   duplicate-delivery problems. Rejected for this slice.
4. **Content-free starvation health signal** — no new dependency, service or
   secret; makes the proven failure state machine-readable and improves the
   existing warning. Selected as the safe independent slice while the credential
   placement decision remains blocked.

No external implementation is copied or installed. There is no new license,
dependency, network, cost or private-data surface.

## Implementation and validation

Changed surfaces:

- `app/webhook_server.py`: add a defensive pending queue count helper, add
  `delivery_backlog_present`, `delivery_blocked` and
  `delivery_blocked_reason` to `/healthz`, and include only the aggregate count
  in the existing private warning.
- `tests/test_webhook_runtime.py`: exercise the concrete production `healthz()`
  route with blocked and ready states and prove queue item IDs are absent.

Regression-first evidence: both new tests failed on the base with missing health
keys, then passed after implementation. Focused `tests.test_webhook_runtime`
passes 27/27.

The health response intentionally exposes booleans, not item counts, source names,
post IDs, chat IDs, text or credentials. Existing clients remain compatible
because only additive JSON keys are introduced.

## Risk, rollback and next proof

Risk is limited to health/log observability. A malformed state object returns an
empty backlog rather than raising. The endpoint remains HTTP 200 and cannot take
delivery authority. Rollback is a normal revert of this two-file runtime/test
change plus this note.

This does **not** fix the missing credential or prove timely delivery. After merge
and exact-SHA deployment, production acceptance requires `/healthz` to show
`delivery_backlog_present=true`, `delivery_blocked=true`, and reason
`translation_credential_unavailable` during a real starvation window, then clear
the block after a credential-bearing wake drains the queue. The next architecture
decision remains persistent Railway credential versus a deliberately longer
ephemeral lease; do not infer that a green Actions run proves continuous health.
