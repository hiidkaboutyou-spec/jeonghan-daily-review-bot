# Structured Production Outcome Contract

## Overview

Every production run produces a deterministic, redacted, machine-readable summary
of the actual run outcome stored as `production-outcome.json`. The watchdog
consumes this structured outcome instead of relying primarily on GitHub Actions
workflow conclusion.

**Core principle:**
```
WORKFLOW SUCCESS IS NOT PRODUCTION SUCCESS
```

## Outcome Schema (v1)

The outcome is a JSON object with the following structure:

### Identity
| Field | Type | Description |
|-------|------|-------------|
| `schema_version` | int | Schema version (currently 1) |
| `run_id` | string | Run identifier (GitHub run ID or local identifier) |
| `run_started_at` | string | ISO 8601 UTC timestamp |
| `run_finished_at` | string | ISO 8601 UTC timestamp |
| `trigger_event` | string | `schedule`, `push`, `workflow_dispatch` |
| `commit_sha` | string | Git commit SHA |

### Source Collection
| Field | Type | Description |
|-------|------|-------------|
| `configured_source_count` | int | Total sources in config |
| `active_source_count` | int | Enabled sources |
| `attempted_source_count` | int | Sources actually attempted |
| `complete_source_count` | int | Fully successful sources |
| `partial_source_count` | int | Partially completed sources |
| `failed_source_count` | int | Completely failed sources |
| `disabled_source_count` | int | Disabled sources (excluded from completeness) |
| `failed_source_handles` | list[str] | Privacy-safe source identifiers |
| `failed_source_reasons` | list[str] | Truncated error class names only |
| `fallback_source_count` | int | Sources using fallback recovery |
| `collection_complete` | bool | All active sources fully collected |

### Discovery
| Field | Type | Description |
|-------|------|-------------|
| `discovered_record_count` | int | Raw records found |
| `retained_candidate_count` | int | After dedup/filter |
| `duplicate_drop_count` | int | Deduplicated |
| `filtered_drop_count` | int | Filtered out |

### AI / Translation
| Field | Type | Description |
|-------|------|-------------|
| `ai_jobs_attempted` | int | AI translation attempts |
| `ai_jobs_successful` | int | Successful translations |
| `ai_jobs_failed` | int | Failed translations |
| `ai_timeout_count` | int | Timeout failures |
| `fallback_writer_count` | int | Fallback translations used |
| `translation_deferral_count` | int | Deferred due to provider outage |
| `manual_review_count` | int | Flagged for manual review |

### Telegram Delivery
| Field | Type | Description |
|-------|------|-------------|
| `delivery_attempt_count` | int | Message sends attempted |
| `delivery_success_count` | int | Successful sends |
| `delivery_failure_count` | int | Failed sends |
| `media_delivery_success_count` | int | Media sends succeeded |
| `media_delivery_failure_count` | int | Media sends failed |

### State
| Field | Type | Description |
|-------|------|-------------|
| `state_checkpoint_success` | bool | State save succeeded |
| `database_checkpoint_success` | bool | SQLite checkpoint succeeded |
| `cursor_advanced` | bool | Production cursor moved forward |
| `cursor_reason` | string | `complete_window`, `partial_window`, etc. |
| `backlog_count` | int | Pending delivery items |
| `backlog_present` | bool | Whether backlog exists |
| `recovery_artifact_created` | bool | Encrypted backup created |

### Recovery
| Field | Type | Description |
|-------|------|-------------|
| `recovery_required` | bool | Run needs recovery |
| `recovery_reason` | string | Why recovery is needed |
| `recovery_dispatch_recommended` | bool | Watchdog should dispatch |
| `previous_recovery_context` | string | Previous recovery info |

### Classification
| Field | Type | Description |
|-------|------|-------------|
| `outcome_status` | enum | `healthy`, `degraded`, `recovery_required`, `failed` |
| `outcome_reasons` | list[str] | Why this status was assigned |
| `useful_work_performed` | bool | Whether the run did useful work |

## Status Classification Rules

### HEALTHY
- All active sources collected completely
- No critical delivery failure
- No state persistence failure
- Cursor decision is valid
- AI failures are zero or safely handled
- No unresolved recovery condition
- A complete collection with zero new posts remains HEALTHY

### DEGRADED
- One or more source paths partial but recoverable
- AI fallback used materially
- Limited Telegram/media failure with preserved retry state
- Backlog exists but production remains safe
- Translation deferred due to provider outage
- Useful work happened, but the run was not fully complete

### RECOVERY_REQUIRED
- Collection incomplete AND cursor held
- Recovery explicitly recommended by upstream logic
- Large backlog requiring replay (not alone, combined with collection issues)

### FAILED
- All active sources failed
- State/database checkpoint fails in a way that risks correctness
- Outcome contract itself cannot be produced safely

## Zero Useful Work Detection

Distinguishes between:
- **Valid zero-update run**: Collection completes successfully but nothing new exists → HEALTHY
- **Suspicious zero-useful-work run**: Nothing processed because upstream collection or processing failed

A fully complete collection with zero eligible posts must remain HEALTHY.

## Privacy / Redaction

The serialized outcome is safe for GitHub Actions artifacts/logs.

**Never exposed:**
- Telegram bot tokens
- Telegram chat IDs
- Gemini API keys
- X cookies
- Auth headers
- Session credentials
- Raw environment variables
- Private message contents
- Full generated captions
- Sensitive database contents

**Safe to include:**
- Source handles (already public via X/Twitter)
- Truncated error class names (e.g., `TimeoutError`, not full text)
- Numerical counters
- Boolean flags
- ISO timestamps

## Watchdog Decision Order

1. Was the expected production run present?
2. Is a valid outcome contract available?
3. What does the contract classify the run as?
4. Does it request/recommend recovery?
5. Has bounded recovery already been attempted?
6. Is another retry allowed?
7. Should human attention be surfaced?

Workflow conclusion remains a useful secondary signal.

## Recovery Rules

| Status | Action |
|--------|--------|
| HEALTHY | No recovery |
| DEGRADED | Record status, no immediate recovery |
| RECOVERY_REQUIRED + dispatch recommended | Dispatch bounded recovery |
| FAILED (all sources) | Allow one bounded retry |
| FAILED (state checkpoint) | Do not retry; requires manual intervention |

### Safety Guards
- Failed automated recovery chains are hard-stopped
- Newer runs prevent duplicate dispatches
- Duplicate Telegram sends are prevented by deduplication
- Infinite recovery loops are bounded

## Schema Versioning

- Schema version starts at 1
- Watchdog validates required fields on every read
- Unknown future fields are tolerated (forward-compatible)
- Unknown/incompatible schema versions are rejected explicitly
- Corrupted JSON is rejected with clear error classification

## Debugging

### Check outcome artifact
```bash
cat production-outcome.json | python -m json.tool
```

### Manual classification
```python
from app.production_outcome import load_outcome, classify_outcome
outcome = load_outcome()
status, reasons = classify_outcome(outcome)
print(f"Status: {status}, Reasons: {reasons}")
```

### Watchdog log analysis
```
daily_watchdog: outcome_consumed source_run_id=12345 status=healthy useful_work=True
daily_watchdog: outcome_skip_recovery source_run_id=12345
```

### Common patterns
- `all_active_sources_failed` → Check X provider status or cookies
- `partial_collection_cursor_held` → Cursor retained for retry; next run will resume
- `state_checkpoint_failed` → Critical; investigate state file permissions/disk
- `ai_fallback_used` → Gemini quota or model issue; fallback translation active
- `partial_telegram_failure` → Telegram transient error; preserved in retry queue
