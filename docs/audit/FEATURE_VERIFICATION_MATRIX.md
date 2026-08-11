# Feature Verification Matrix

Production evidence below was refreshed after the 2026-08-11 default-branch launch. The exact launch commit and workflow links are recorded in [`../LAUNCH_STATUS.md`](../LAUNCH_STATUS.md).

Classification is deliberately conservative. Mocked/CI evidence is not promoted to production evidence when a real external platform or scheduled default-branch run is required.

| ID | Capability / expected behavior | Implementation path | Automated / CI evidence | Production evidence | Classification | Residual limitation / next action |
|---|---|---|---|---|---|---|
| F01 | Validate configured source inventory and files | `app/config.py`, `config/sources.json`, `app/channel_style_validation.py` | `python -m app --check`; config/source tests | tracked config only | VERIFIED WORKING | None actionable in repo |
| F02 | Complete source-window retrieval does not falsely claim completeness | `app/x_client.py` | `test_x_completeness.py` | External X conditions vary | VERIFIED WORKING | Live credentials/platform still required at runtime |
| F03 | Multilingual EN/KR/JP Jeonghan retrieval terms | `config/sources.json`, `app/x_client.py` | source/config tests | Historical runtime architecture exists | VERIFIED WORKING | X search availability external |
| F04 | Filter marketplace/trade noise | `app/x_client.py` | `test_audit_regressions.py`, `test_bot.py` | deterministic content filter | VERIFIED WORKING | Heuristic false positives/negatives remain possible |
| F05 | Record partial source failures without hiding good results | `app/x_client.py`, source health | regression tests | no exact external failure required for code verification | VERIFIED WORKING | External X outage semantics cannot be guaranteed |
| F06 | Replay all relevant updates from last two hours | command/retrieval runtime | command/retrieval tests | no post-merge live production proof for final branch | IMPLEMENTED BUT NOT VERIFIED | Verify `/recent2h` after merge on a real review chat |
| F07 | Complete 24h per-source retrieval, oldest→newest | command/X completeness path | completeness/order tests | no post-merge live X proof | IMPLEMENTED BUT NOT VERIFIED | Verify `/source24` with a real source after merge |
| F08 | Search private archive by date/text and return candidates | `app/archive_store.py`, `app/private_features.py` | archive search/rebuild/restart tests | local persistent behavior deterministic | VERIFIED WORKING | Recovery of remote state still external |
| F09 | Group events/live threads and preserve logical order | `app/organizer.py` | ordering/audit edge tests | deterministic | VERIFIED WORKING | Source metadata can be incomplete |
| F10 | Normalize/migrate persistent JSON state safely | `app/state.py` | state migration/normalization tests | deterministic | VERIFIED WORKING | Remote durability depends on cache/recovery |
| F11 | Keep all bot behavior private-review-only with admin/chat authorization | application/Telegram command runtimes | private UI/boundary tests; no publish action | actual configured IDs are owner/runtime inputs | VERIFIED WORKING | Owner must keep secrets/IDs correct |
| F12 | Persistent Telegram menu/private UI | `app/private_features.py`, UI runtime | menu/date/source pagination tests | no post-merge live visual proof | IMPLEMENTED BUT NOT VERIFIED | Human smoke-test menu after merge |
| F13 | Enforce callback-data UTF-8 1–64 byte limit | `app/telegram.py`, callback runtime/store | exact 64/65 ASCII and multibyte tests | deterministic | VERIFIED WORKING | Telegram may evolve protocol limits |
| F14 | Opaque long callback tokens: durable mapping, collision handling, expiry | callback token store/runtime | collision/expiry/unknown/malformed tests | deterministic SQLite path | VERIFIED WORKING | Expired tokens intentionally stop working |
| F15 | Losslessly split long Telegram text | Telegram/message delivery | exact-boundary, Persian/KR/JP/emoji tests | deterministic | VERIFIED WORKING | Telegram platform can still reject a request for unrelated reasons |
| F16 | Resume multipart delivery without repeating confirmed parts | `app/message_delivery.py` | partial-failure retry tests | deterministic receipt semantics | VERIFIED WORKING | Tiny acceptance-before-receipt crash window remains |
| F17 | Prefer original/high-quality photo variants | `app/media.py` | media quality/fallback tests | platform URLs/extractors external | VERIFIED WORKING | External formats can change |
| F18 | Video fallback via yt-dlp/FFmpeg/gallery-dl as configured | `app/media.py` | extractor order/FFmpeg/unavailable tests | real platform downloads not final-branch CI dependency | IMPLEMENTED BUT NOT VERIFIED | Smoke-test representative live media after merge |
| F19 | Reuse valid Telegram file IDs and refresh invalid cache | `app/media_file_cache.py` | file cache/group/refresh tests | Telegram validity external | VERIFIED WORKING | Telegram can invalidate cached file IDs |
| F20 | Prevent repeat exact media via durable delivery identity | media delivery ledger/runtime | `file_unique_id`, content hash, failed-send tests | no exactly-once guarantee from Telegram | VERIFIED WORKING | Residual crash window documented |
| F21 | Handle Telegram 429 using `retry_after` and bounded retries | `app/telegram.py` | mocked 429 tests | Bot API semantics external | VERIFIED WORKING | Flood limits controlled by Telegram |
| F22 | Handle Telegram 5xx/timeouts/resets/malformed JSON/permanent 4xx safely | Telegram transport | deterministic transport tests | external outage behavior can vary | VERIFIED WORKING | No infinite retry |
| F23 | Do not quarantine a valid update solely for transient Telegram failure | `app/telegram_update_runtime.py` | poison/transient/offset tests | deterministic state semantics | VERIFIED WORKING | Permanent application failure can still quarantine by design |
| F24 | Persist and send due reminders once | reminder store/runtime | reminder persistence/cancel tests | deterministic | VERIFIED WORKING | External send crash window same as Telegram limitation |
| F25 | Persist and paginate review inbox without public publish | review inbox/runtime | inbox/filter/pagination tests | deterministic | VERIFIED WORKING | Human UI smoke test optional |
| F26 | Persist source-health success/failure/stale state safely | `app/source_health.py` | source-health tests | deterministic | VERIFIED WORKING | Health only reflects observed runs |
| F27 | Optional observability without forwarding private content | `app/observability.py` | Sentry scrubber/workflow tests | no real DSN required | VERIFIED WORKING | External Sentry availability optional |
| F28 | Validate/load 16,306 historical ChannelStyle examples | channel corpus/style modules | corpus hash/count/rebuild tests; app check | deterministic tracked corpus | VERIFIED WORKING | Corpus quality is separate from factual authority |
| F29 | Give historical dates zero style-authority/recency weight | style retrieval | date-score tests | deterministic | VERIFIED WORKING | None actionable |
| F30 | Enforce fidelity for identities, numbers/dates, URLs, hashtags, laughter, speakers | translation/hardening modules | extensive Part 3/4 deterministic tests | full live model output still quota-blocked | VERIFIED WORKING | Hard gates do not prove prose quality |
| F31 | Use ChannelStyle writer as primary production writer when artifacts validate | `app/channel_style_application.py`, entrypoint | ProductionWriterWiring tests | default-branch live log: `Channel translation v2 active as PRIMARY` with 16,306 examples | VERIFIED WORKING | Human prose-quality gate remains separate (F35) |
| F32 | Learn only explicit confirmed feedback; never auto-learn generated/rejected drafts | style feedback runtime | feedback/rejection tests | deterministic | VERIFIED WORKING | Human confirmation remains required |
| F33 | Deterministic Part 4 benchmark harness: freshness/checkpoint/resume/quality gate | benchmark tools/workflow | benchmark harness/cache/freshness tests | checkpoint artifacts observed | VERIFIED WORKING | Live generation separate |
| F34 | Generate complete live Gemini benchmark on approved model | Gemini/benchmark workflow | harness works | real attempts return 429 `RESOURCE_EXHAUSTED` | BLOCKED BY EXTERNAL ACCESS | Wait for project/account quota; resume existing checkpoint |
| F35 | Human quality gate on representative real SOURCE→OLD→NEW cases | Part 4 process | prior human review correctly failed poor cases | no fresh complete benchmark after latest hardening | IMPLEMENTED BUT NOT VERIFIED | Complete live benchmark, then human review; do not auto-pass |
| F36 | AO3 pagination continues across non-qualifying pages, stops on true empty/failure/count/cap | `app/fic_digest.py` | AO3 reliability tests including 25-page cap | live AO3 intentionally absent from CI | VERIFIED WORKING | HTML structure is external |
| F37 | AO3/X detail retrieval paced/serial; fic work metadata classified new/updated/unchanged | `app/fic_digest.py`, `app/fic_state.py` | serial/pacing + fic-state/chapter tests | default-branch live run built `x=13`, `ao3_pool=48`, `ao3_list=36` after bounded transient recovery | VERIFIED WORKING | Live HTML changes remain external |
| F38 | Nightly fic digest preserves ordering and resumes partial delivery | fic digest + message receipts | formatting/chunk/delivery-key tests | default-branch live run confirmed delivery for both X and AO3 lists | VERIFIED WORKING | Telegram's residual acceptance-before-receipt window remains |
| F39 | Use Actions cache for best-effort JSON/private SQLite continuity | main/fic workflows | workflow tests and prior workflow cache use | GitHub cache is evictable | PARTIALLY WORKING | Never treat cache as authoritative; encrypted recovery recommended |
| F40 | Authenticated encrypted state recovery from newest valid artifact | `tools/state_backup.py`, main/fic workflows | roundtrip, tamper, wrong-key, atomic rollback, workflow-selection tests | default-branch run created and uploaded ciphertext artifact `private-state-backup`; healthy state was not deleted to force restore | PARTIALLY WORKING | Use a disposable controlled restore drill; do not destroy healthy production state |

## Totals

- VERIFIED WORKING: 32
- IMPLEMENTED BUT NOT VERIFIED: 5
- PARTIALLY WORKING: 2
- BROKEN: 0
- UNREACHABLE: 0
- MISSING: 0
- BLOCKED BY EXTERNAL ACCESS: 1

Total: 40 capabilities.

> F35 is intentionally not promoted to VERIFIED simply because the harness exists. It needs human judgment over a fresh complete live benchmark. F40 remains partial because production artifact creation is proven but a destructive live restore was intentionally not forced.
