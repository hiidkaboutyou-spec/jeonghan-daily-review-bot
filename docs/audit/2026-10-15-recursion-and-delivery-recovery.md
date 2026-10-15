# 2026-10-15 — Telegram delivery recursion and post-deploy recovery

## Incident
The live Railway runtime logged a `RecursionError` cycle through:
- `app/channel_style_rewrite_state_compat.py`
- `app/forward_ready_state_compat.py`
- `app/user_voice_calibration_state_compat.py`

The wrappers only checked the top-level function for their installation marker. Repeated/interleaved installation could wrap an already-installed layer again. The wrappers also relied on closure-bound predecessor functions.

## Fix shipped
- PR #197: https://github.com/hiidkaboutyou-spec/jeonghan-daily-review-bot/pull/197
- Main commit: `8269527096fefda305b8af681be1521e15ecde70`
- Bound each wrapper's predecessor explicitly.
- Added `__wrapped__` links and chain-aware idempotence checks for sanitize/prune wrappers.
- Added `tests/test_state_compat_idempotence.py` to repeat all three installs and exercise both prune and sanitize.
- Validation on the PR head passed the full project test suite; production smoke, security checks, and fanfic validation passed.

## Deployment verification
Railway production service `jeonghan-personal-assistant` deployed commit `8269527096fefda305b8af681be1521e15ecde70` as deployment `a021233d-1639-4ea3-abce-b3d03bf6f63b`.
- Deployment status: `SUCCESS`
- Application startup completed.
- `GET /healthz` returned `200 OK`.
- No `RecursionError` appeared in the new deployment's runtime logs.

## Remaining delivery recovery
The service intentionally has no persistent `GEMINI_API_KEY`; GitHub Actions leases the key to the webhook runtime in memory over the authenticated HTTPS maintenance endpoint. Deploying restarts the process and clears that in-memory lease. Immediately after this deployment, the webhook logged 29 queued items deferred until a translation credential became available. This is a credential-lease timing issue, separate from the recursion bug.

A fresh GitHub Actions live pass must re-lease the key and wake the durable delivery queue. Do not add a static Gemini key to Railway, delete the Telegram webhook, start a competing poller, or publish drafts publicly. Verify the queue's delivery receipts and absence of recursion errors after the wake.
