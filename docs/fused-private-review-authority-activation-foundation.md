# Fused Private-Review Authority Activation Foundation

Status: **foundation only; production authority remains LEGACY/OFF**.

This layer supplies the deterministic, private-review-only authority decision and execution plumbing needed for a later isolated activation. It does not activate fused Telegram delivery in production, style authority, user-voice learning, public publishing, forwarding, retrieval changes, or new storage.

## Hard production default

Environment controls:

- `FUSED_PRIVATE_REVIEW_AUTHORITY` — accepted values `off|false|0|legacy`, `shadow`, `canary`, `on|true|1`. Missing or malformed values resolve to `LEGACY`.
- `FUSED_PRIVATE_REVIEW_CANARY_PERCENT` — integer `0..100`; missing/malformed/out-of-range resolves to `0`.
- `FUSED_PRIVATE_REVIEW_KILL_SWITCH` — strict true values `1|true|yes|on`; missing/malformed resolves to false.

This PR does not set any of these variables in workflows, config or hosting. Current Update-oriented delivery therefore remains authoritative.

## Authority model

`FusedPrivateReviewAuthorityController` returns one deterministic decision for a plan:

- `LEGACY`: existing private-review delivery.
- `SHADOW`: evaluate eligibility but still use legacy.
- `CANARY`: deterministic SHA-256 selection using plan/package identity and a bounded percentage.
- `ON`: may select fused only when all safety gates pass.

There is no random selection, calendar activation or benchmark-score activation.

## Important runtime boundary in this foundation

The current canonical Draft is created inside the legacy private-review delivery path. ForwardReadyPackage and FusedPrivateReviewDeliveryPlan therefore remain post-legacy shadow planners in production.

To avoid a dangerous refactor or double-send, this PR leaves the runtime order unchanged:

```
existing authoritative private-review delivery
        ↓ completes first
ForwardReadyPackage — SHADOW
        ↓
FusedPrivateReviewDeliveryPlan — SHADOW
        ↓
Fused authority decision metadata — OBSERVATION ONLY
```

`network_execution_enabled=false` is emitted with the runtime authority observation. The controller/executor are fully testable plumbing for a later isolated activation, but this PR deliberately does not move fused execution before legacy transport.

## Eligibility

Fused authority rejects before send when any of the following is true:

- plan missing or unknown plan version
- private review chat missing
- receipt stores unavailable
- `BLOCKED`, `NEEDS_REVIEW`, `PARTIAL_COVERAGE`, or `MEDIA_INCOMPLETE`
- unresolved conflict/fidelity/partial/media warning
- unsupported delivery unit
- invalid unit order
- album outside Telegram `2..10`
- invalid media-unit cardinality
- unresolved canonical text body
- caption body above 1024
- text splitter output above Telegram limit
- duplicated review-control unit
- future Forward/public/target contract enabled

`PARTIAL_COVERAGE` and `MEDIA_INCOMPLETE` intentionally fall back to legacy rather than pretending completeness.

## Pre-send vs post-receipt behavior

Before any fused receipt:

- controller errors/ineligibility/kill switch → legacy fallback is allowed.

After any fused text or media receipt:

- full legacy fallback is forbidden.
- normal mode → `FUSED_RESUME`.
- kill switch → `FUSED_RESUME_REQUIRED`, meaning receipt-aware/manual recovery, not a duplicate legacy resend.

This is duplicate avoidance, **not** an exactly-once Telegram claim.

## Receipt ownership

No third receipt store exists.

- text receipts: `MessageDeliveryStore`
- media receipts: `MediaDeliveryLedger`

`FusedReceiptProbe` reads those owners. `FusedUnitExecutor` uses the existing Telegram text method and existing private media-delivery callable. It neither creates tables nor marks a plan delivered merely because planning succeeded.

## Body resolution

`FusedBodyResolver` never learns or reconstructs a shadow style body as authority.

Resolution in this foundation is intentionally conservative:

1. a genuine active confirmed Final Edit, only when Final Edit ID, Draft ID/update linkage, authoritative Draft fingerprint, provenance, active/revoked state, and final-body fingerprint all match;
2. otherwise the current canonical authoritative review Draft.

Direct User Style and Channel Style references remain metadata-only and non-authoritative. Historical corpus text is not used as a body source. Private Final Edit bodies remain in the existing private-review SQLite owner.

## Executor

`FusedUnitExecutor` supports the existing plan unit family:

- `media_album`
- `single_photo`
- `single_video`
- `standalone_media`
- `caption`
- `text`
- `continuation_text`
- `review_controls`

It accepts no `chat_id`/target argument. Telegram sends therefore remain bound to the existing configured private review chat.

Media units resolve only canonical existing media references and are handed to the existing private-media path. No new dedupe rule is introduced. Exact-media identity remains owned by `MediaDeliveryLedger`.

Caption units are deliberately sent as separate receipt-owned text in this foundation rather than silently attaching a caption through a path with no dedicated text receipt owner. No factual truncation occurs.

Long text continues to use `split_telegram_text`.

Review controls may occur once only and reuse the existing Draft callback family. No public Forward button or second review system is added.

## Canary and rollback

Canary selection is stable for the same `plan_id + package_id`; restart does not re-roll it.

Rollback for untouched work is immediate: set authority OFF/LEGACY and the next eligible untouched logical delivery remains legacy. No database migration is required.

Already partially receipted fused work is different: the controller refuses a legacy full resend and requires fused receipt-aware recovery.

## Kill switch

Kill switch behavior:

- no fused receipt → do not start fused delivery; use legacy.
- fused receipt already exists → do not start legacy duplicate; return `FUSED_RESUME_REQUIRED`.

No paid/external infrastructure is needed.

## Privacy-safe decision observation

Permitted metadata includes:

- authority version/mode
- selected path
- eligible/reason
- plan ID
- package ID
- deterministic canary selection
- fallback reason
- whether receipts are present
- execution-start/completion booleans

Private bodies, tokens, cookies, authorization headers and secret URLs are not emitted.

## Safety boundaries preserved

- 24-source configured-source authority: unchanged
- retrieval/lifecycle/cursor/completeness: unchanged
- Event/Segment semantics: unchanged
- Translation Fusion factual authority: unchanged
- Channel Style: shadow/non-authoritative
- Direct User Style: shadow/non-authoritative
- User Voice: non-authoritative; `AUTO_LEARN=false`
- Final Edit Capture: canonical private SQLite owner unchanged
- ForwardReadyPackage: shadow
- FusedPrivateReviewDeliveryPlan: shadow
- current legacy Telegram private-review delivery: authoritative
- public auto-publish: absent
- Fanfic/AO3: independent
- paid infrastructure: none added

## Activation prerequisite after this foundation

A later isolated phase must separate canonical Draft preparation from legacy network sending (or otherwise provide safe pre-send canonical plans) before moving the authority decision ahead of transport. That future change must retain the receipt-aware pre-send/post-receipt boundary proven here. This PR intentionally does not perform that authority activation.
