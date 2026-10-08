# Papermorph + train-llm-from-scratch adoption review — 2026-10-05

## Repositories reviewed

- `DozenTwelve/Papermorph` — MIT
- `FareedKhan-dev/train-llm-from-scratch` — MIT

## Decision for the Jeonghan Daily Review Bot

### Papermorph: do not adopt

Papermorph's core value is a static interactive-book pipeline: PDF splitting, storyboards, SVG stages, narration/TTS, chapter playback, quizzes and browser delivery checks.

That does not close a measured gap in the Daily bot. The bot's primary surfaces are Telegram review, source collection, translation, media and editorial ordering. Adding a browser-book engine, TTS or Playwright preview dependency would increase maintenance without improving those owner-visible paths.

### Train-LLM: adopt the preference-data shape, not the training stack

The repository demonstrates a clean `prompt / chosen / rejected` representation for preference learning and carefully tests pair integrity/truncation.

The Daily bot already has stronger project-specific foundations:

- confirmed final-edit capture in the private review database;
- factual/style separation;
- user-voice calibration eligibility;
- holdout logic;
- `AUTO_LEARN = False`.

Therefore the useful missing piece is **not** DPO/PPO/GRPO or a new Transformer. It is a privacy-safe way to turn real confirmed edits into local preference pairs for later offline evaluation.

Implemented:

- bounded listing of confirmed final-edit metadata;
- local JSONL exporter;
- exact authoritative-draft fingerprint check before pairing;
- default exclusion of undecided/ineligible edits;
- atomic local output under ignored `.state/`;
- tests for chosen/rejected shape, eligibility, stale-draft rejection and UTF-8 output.

No PyTorch/Transformers/training dependency is added. No automatic model update is enabled. No private pair is uploaded by CI.
