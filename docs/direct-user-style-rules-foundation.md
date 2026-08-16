# Direct User Style Rules Foundation

## Scope and authority

This foundation adds configured, deterministic presentation rules to the existing shadow-only Channel Style Rewrite path. It does not change private-review delivery or grant style output production authority.

The enforced precedence is:

1. factual fidelity;
2. direct user style rules;
3. stable real-user edit preferences;
4. historical style examples;
5. generic style heuristics.

Facts always win. A configured header, prefix, or symbol is presentation metadata, while names, dates, numbers, URLs, negation, modality, questions, speakers, quotes, and chronology remain bound to the current Translation Fusion result.

## Configured rules

Rules live in `config/direct_style_rules.json` and are selected only from current evidence:

- Jeonghan Instagram feed: `🧸    #IG ׂ ✧   ﹫ jeonghaniyoo_n`
- Jeonghan Instagram story: `jeonghaniyoo_n 𐑞✿ྀི instagram story:`
- Weverse: `୨ ࣪ ˓ بانی ثراپی    -DATE🥛 ★`
- Jeonghan Banila Co updates: `☆ اپدیت برند بانیلاکو با هانی    💒!`
- live, event, and program updates: current evidence date, a deterministic rotating symbol, and the current English title.

Eligible Persian prose may receive one of the configured prefixes, `💒 ⌕ ` or `،،⌕໋  ִ˒˒ `. URLs, list items, and dialogue turns are not prefixed. Missing, malformed, conflicting, or ambiguous evidence falls back safely without inventing a category, date, account, brand, or title.

## Determinism and durable state

Symbol and prefix selection uses a stable context hash. Recent symbol history is bounded to four entries, excludes the most recently used symbols when possible, and has safe cold-start and corrupt-state behavior.

Durable state stores only bounded rule identifiers, category, selected symbol, authority order, fingerprints, decisions, and recent symbol history. It does not persist factual or styled message bodies. Existing private-review SQLite restore/checkpoint behavior remains the storage boundary.

## Factual projection and fallback

The direct rule plan is created after faithful factual Persian exists and before historical or provider styling. The resulting candidate must contain the exact configured structural elements. Those elements are then projected away, and the remaining body must pass the existing factual lock against the faithful draft.

Any structural tampering, provider failure, historical factual leakage, or fidelity failure rejects the candidate and returns the faithful factual Persian draft. A style score cannot override a factual failure.

## Operational boundaries

- `STYLE_REWRITE_MODE=shadow`; direct rules are also shadow-only.
- `AUTO_LEARN=false`.
- Real user edit triplets remain `0`, so User-Voice Quality remains insufficient real edit data.
- Existing private-review output remains authoritative.
- Fanfic/AO3 stays lazy and independent and does not import this layer.
- Retrieval, Event/Segment grouping, Translation Fusion, media identity, seen/delivered lifecycle, Telegram receipts, and the 24 enabled sources are unchanged.
- No paid service, new database, cache, queue, or infrastructure dependency is introduced.

## Validation and rollback

The deterministic benchmark contains 46 difficult cases covering category specificity, false positives, RTL/mixed text, URLs, lists, dialogue, dates, numbers, negation, modality, questions, deterministic rotation, cold/corrupt state, and historical leakage. Pull-request CI runs it alongside the existing Final Edit Capture, Channel Style Rewrite, User Voice Calibration, Translation Fusion, full unit suite, and live translation smoke.

Rollback is isolated: disable the shadow hook or revert the config/module/integration changes. Because the layer has no delivery authority and persists no message bodies, rollback does not require data migration or Telegram cleanup.
