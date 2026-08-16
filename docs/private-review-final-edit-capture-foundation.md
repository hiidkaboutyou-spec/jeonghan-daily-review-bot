# Private-Review Final-Edit Capture Foundation

Status: isolated development foundation; capture-only; not authoritative for Telegram.

## Purpose

Close one missing evidence loop without changing retrieval or delivery authority:

`Update → faithful factual shadow → Channel Style shadow → existing private-review Draft → user-confirmed final edit`

A final edit is real evidence only when the admin supplies/changes the text and explicitly confirms that exact version. Copy, Reject, Funnier, Softer, Precise, retry/regenerate, Style Rewrite output, and synthetic benchmark fixtures are not real edits.

## Existing private-review audit

The production review path already owns the right UX and storage boundary:

- `StateStore.drafts` owns the currently authoritative review Draft and `Draft.id` / `Update.id` linkage.
- `ReviewInboxStore` owns private inbox status in `private-review.sqlite3`.
- `ArchiveStore` owns searchable private review/archive content in the same SQLite database.
- `draft:<action>:<draft_id>` callbacks already correlate Copy/Reject/Funnier/Softer/Precise to an exact Draft.
- reply-based review actions already exist in `PersonalAssistantReviewApplication`.
- Telegram long outgoing Drafts are split safely and the keyboard is attached to the final part; `Draft.telegram_message_id` refers to that final Telegram part, while `Draft.caption` remains the whole logical Draft.
- no production path previously preserved a distinct, confirmed, free-form final user-edited body. `copy` reuses the current Draft; bot rewrite modes generate another bot Draft.

Therefore this phase extends the existing review system instead of creating a second one.

## Selected UX

1. Existing Draft keyboards gain `✏️ ویرایش نهایی`.
2. `draft:edit:<draft_id>` creates one bounded private edit session for that exact Draft/Update and sends a dedicated prompt.
3. The user's text is accepted only as a reply to that session's exact prompt message ID in the configured private review chat.
4. The bot shows a private preview with `Confirm / Replace / Cancel`.
5. Only `Confirm` creates a canonical `FinalEditRecord` with provenance `user_confirmed`.

A newly arriving Draft cannot steal an existing edit session because prompt correlation, Draft ID, Update ID, review-chat fingerprint, and authoritative Draft fingerprint all have to agree. A Draft regenerated after edit-start becomes stale and cannot be confirmed under the old session.

## Multi-part safety

The semantic target is the **whole logical Draft**, never an arbitrary Telegram fragment. This foundation intentionally refuses final-edit capture when the logical `Draft.caption` exceeds Telegram's single user-text message limit. The existing Draft remains untouched. Supporting assembled multi-message user input would be a separate UX expansion; silently treating one fragment as the whole final body is forbidden.

## Storage ownership

Full private user text does **not** enter generic Durable State, Event state, Translation Fusion metadata, Style Rewrite metadata, or User-Voice Calibration metadata.

The existing private-review SQLite database is the canonical owner:

- `final_edit_sessions` temporarily holds a candidate body while awaiting confirmation; Cancel/expiry clears it.
- `final_edits` owns the confirmed final body and revision history.

Generic/calibration metadata uses IDs, fingerprints, labels, confidence, eligibility, and version/status fields only. `text_persisted_in_generic_state=false`.

Persisted relationship fields include:

- Final Edit ID
- Draft ID
- Update ID
- Event ID when available
- Segment ID when available
- privacy-safe review chat reference
- factual draft fingerprint when the existing shadow layer supplied it
- Style Rewrite candidate fingerprint when the existing shadow layer supplied it
- authoritative review Draft fingerprint
- final user-edit fingerprint
- content type
- timestamps/status/provenance
- supersession/revocation state
- calibration eligibility (`undecided` initially unless a matching transient shadow reconstruction can classify it)

The historical 16,306-example Channel Style corpus is never rewritten.

## Calibration boundary

`AUTO_LEARN` remains false. Confirmation may transiently reconstruct Translation Fusion and Channel Style shadow text from the same canonical Update/state. Calibration classification is accepted only when the reconstructed factual/candidate fingerprints match the fingerprints captured for the edit session. The resulting `VoiceCalibrationRecord.metadata()` contains no text.

Possible labels include factual correction, style/format/tone preference, shortening/expansion, category-specific or one-off wording, ambiguous, and unclassified. A factual/mistranslation/conflict correction remains valuable feedback but is not style-learning evidence.

This phase does not derive/apply preference signals, modify example ranking, change category/global weights, retrain anything, change Translation Fusion, or change Style Rewrite output.

## Privacy and failure isolation

- private review chat only
- private SQLite only for message bodies
- no full edit body in logs, Sentry, GitHub Actions output, or artifacts
- telemetry/reporting may use only IDs/fingerprints/status/counts
- `confirmed_real_final_edit_count` and per-content-type counts expose no text
- capture errors are handled independently and leave the original Draft available
- starting/cancelling/confirming an edit does not mark an Update seen/delivered or mutate receipts/cursors

## Revision and retention

A newer confirmed edit for the same Draft supersedes the previous active record. History remains auditable; only the newest non-revoked active version counts toward the privacy-safe real-edit counter, so repeated revisions do not become multiple independent preferences. Revocation is supported at the storage layer.

Pending sessions have a bounded TTL. Expiry/cancel clears unconfirmed candidate text and creates no calibration evidence.

## Authority invariants

Final Edit Capture is review data only. It does not:

- publish to a public channel
- activate Style Rewrite for Telegram
- change current private-review Draft text
- collapse/combine Updates
- change ordering
- mutate seen/delivered state
- alter Telegram message/media receipts
- move Event/Segment membership
- change Translation Fusion factual output
- alter Phase 3 retrieval/completeness/cursors
- alter media identity/grouping/delivery or concert coverage

The project remains review-only/private-review-only and free.

## Fanfic/AO3 independence

Capture is installed from `app.sentry_runtime`, the normal Daily/private-review entrypoint. It is deliberately not imported from `app.__init__`. Running `python -m app.fic_digest` therefore does not install or require Final Edit Capture, User-Voice Calibration runtime, or the new private tables.

## Direct user style rules

Explicit user rules (Instagram/Weverse/event headers, Persian prefix families, natural colloquial translation, future local symbol rotation) are intentionally not implemented here. The future authority hierarchy remains compatible with:

`FACTUAL FIDELITY > DIRECT USER RULES > STABLE REPEATED REAL USER-EDIT PREFERENCES > HISTORICAL STYLE EXAMPLES > GENERIC HEURISTICS`

Final Edit Capture cannot override that future hierarchy.

## Real-data gate

No synthetic benchmark fixture is inserted into the production SQLite database. Immediately after code deployment, before the user performs and confirms an actual edit:

`REAL USER EDIT TRIPLETS = 0`

`USER-VOICE QUALITY = INSUFFICIENT REAL EDIT DATA`

Only confirmed `user_confirmed` records count.
