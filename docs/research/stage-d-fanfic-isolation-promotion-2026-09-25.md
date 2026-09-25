# Stage D Fanfic isolation: promotion evidence — 2026-09-25

## Decision and scope

Promote the previously observed `app.fic_digest` isolation contract to a blocking maintenance gate on a new branch from `main@0e3384bee94043d159c158bd7dbcdcdc86db4fdd`. PR #119 remains a stale, report-only pilot and is not the promotion vehicle. The static Forbidden contract retains all ten exact forbidden modules, indirect-chain checking, `as_packages=False`, and no ignore rules. Its source-scoped literal dynamic-import scan and clean-process package-initialization probe also become blocking. Both existing recovery-family protected contracts and dynamic-import audits remain blocking and unchanged.

There is no runtime, state/schema, provider, source, Telegram, AO3, schedule, credential, production dependency, or delivery change. Maintenance can be rolled back by reverting this promotion commit. Reverting it does not undo any runtime data or require a state migration.

## Preflight and independent evidence

- `main@0e3384b` consists of a tooling-roadmap documentation merge on top of `06ca181`, itself a documentation-only frontier refresh on top of runtime commit `0d7209e`. There have been no runtime, maintenance config, or workflow changes since `0d7209e` in `main`.
- Independent real-main Hani Maintenance Diagnostics run [#164](https://github.com/hiidkaboutyou-spec/jeonghan-daily-review-bot/actions/runs/35952186074) ran on `0d7209eb7e9d593b34d690b216fadbe8a369ca92` and passed. Its artifact `10788079517` (SHA-256 digest `cc3536ca4f7e6f6c3c4a4332c7e942ec5c701db8fcab41e0f6678e8ded4f6783`) reports recovery-integrity and resumable-recovery static contracts both `1 kept / 0 broken`, their companion literal dynamic-import audits both with `0 violations / 0 parse errors`, and successful tool self-tests. This is evidence on the still-current main **runtime and maintenance semantics**, not a claim that the run executed at the latest docs-only SHA.
- Report-only #119 head `4a863a1` passed Maintenance #156 with Fanfic static Forbidden contract kept and clean-process/dynamic audit clean. Its branch is now behind main; do not merge it.
- Combined-tree validation-only PR #128 head `65a0b33` against canonical `06ca181` passed [Maintenance #165](https://github.com/hiidkaboutyou-spec/jeonghan-daily-review-bot/actions/runs/35976221504), plus all eight other triggered workflows (Daily, Fanfic, Translation Benchmark, Render, CodeQL, Security, Workflow Safety, Workflow Security Lint). Maintenance artifact `10798222303` (SHA-256 digest `1a51edfb1bbbf8d00e4d50d3ad2fb1fb5b21274f10387c6ee74a311cec50deed`) reports both recovery contracts kept, no recovery bypasses, Fanfic Forbidden contract `1 kept / 0 broken`, no Fanfic literal dynamic bypasses, no forbidden modules loaded, package-initialization witness loaded, and no parse/probe errors.
- The later `main@0e3384b` change compared with #128's base is only `docs/EXECUTION_ROADMAP_V2.md`; it does not change the measured contract, application, or maintenance semantics. This promotion still needs fresh CI on **its own exact PR head** before merging.

## Exit gate

Before merge, inspect all triggered PR-head checks, including Hani Maintenance Diagnostics, Security Diagnostics, Workflow Safety, Daily, Nightly Fanfic, Translation Benchmark, Render and CodeQL. Read the Maintenance artifact to confirm all three contracts kept, both recovery audits clean, Fanfic runtime/literal audit clean with a loaded package-init witness, and all tool self-tests green. Do not merge a missing or failing gate. After merge, inspect real-main Maintenance, Fanfic, Daily, and Watchdog production evidence before declaring the Stage D promotion complete or starting a fourth architecture candidate.

## External tool decision

Import Linter 2.15 is already pinned as a maintenance-only dependency; the existing native audit is retained. No new installation is needed for this boundary. `codebase-memory-mcp` remains a separate developer-side benchmark in draft PR #131; normal installation can change agent configuration and start a watcher, so this promotion does not install it or index private production data.
