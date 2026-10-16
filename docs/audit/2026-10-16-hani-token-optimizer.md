# HANI repair — 2026-10-16 token-budget pass

## Evidence

The active production path is the installed translation-v2 writer. Each normal group uses one source-grounded generation call, with a bounded repair call only when deterministic fidelity checks fail. The prompt previously repeated media metadata, speaker scaffolding, large style/profile context, glossary entries, paired demonstrations, and historical excerpts for every group.

The repository already contains the recursion guard and its regression coverage. This change does not add a second Telegram consumer, change offsets, delete queued updates, or weaken delivery receipts.

## Changes

- Added the context-budget optimizer to the active v2 translation path.
- Kept complete authoritative source text; removed duplicated media URL payloads from the model context because media delivery owns those URLs.
- Sent speaker scaffolding only for dialogue groups instead of every item.
- Bounded style profile, voice guidance, glossary fields, paired demonstrations, and historical style excerpts.
- Recorded bounded-context diagnostics without storing source text or credentials.
- Added regression tests for glossary bounds and malformed-entry handling.

## Expected effect

Each group keeps the source needed for faithful translation while reducing repeated input context. Metadata-only and Persian-source groups still make no generation call. Fidelity failures can still trigger the existing repair path; the optimizer does not bypass validation.

## Validation required

Run the repository's canonical compile/check/unit suite and inspect the exact-head CI result before merging. A production smoke/wake is required to confirm the durable queue drains after the latest deployed recursion repair.
