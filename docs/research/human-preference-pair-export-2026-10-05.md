# Human preference-pair export research — 2026-10-05

## External reference

Reviewed `FareedKhan-dev/train-llm-from-scratch` (MIT), especially its post-training data path for human preference learning. The useful transferable contract is the simple local record shape:

- `prompt`
- `chosen`
- `rejected`

Its DPO/reward-model code demonstrates why explicit pairwise human preference data is a stronger training/evaluation substrate than trying to infer preference from generic logs.

## What was not adopted

The Daily Assistant does **not** need to pretrain a Transformer, run PPO/GRPO, add PyTorch, download public RLHF corpora, or auto-fine-tune itself. Those paths add large dependency/compute/privacy costs and do not solve the current product gap.

The project already owns:
- factual source/update evidence;
- authoritative private-review drafts;
- confirmed final user edits;
- conservative user-voice calibration eligibility;
- holdout and no-auto-learn safeguards.

Therefore the missing capability was only a safe bridge from confirmed private review evidence to an explicit local preference corpus.

## Implemented

`tools/export_human_preference_pairs.py` exports active confirmed edits into JSONL rows with `prompt/chosen/rejected`.

Safety rules:
- default input is the existing local `.state/state.json` plus `.state/private-review.sqlite3`;
- default output is under ignored `.state/exports/`;
- only `calibration_eligible=eligible` records export by default;
- revoked/superseded edits are excluded;
- the current draft body must still match the fingerprint recorded when edit capture started;
- missing/stale draft or update evidence fails closed per pair;
- no API/model/network call occurs;
- no automatic training occurs;
- output contains private text and must never be committed or uploaded automatically.

The exporter is deliberately data-only so a future evaluation/fine-tuning experiment can consume the same evidence without coupling model training to production Telegram/runtime code.

## Why this helps Hani

Once enough real final edits exist, the project can evaluate whether a candidate model/ranker actually prefers the user's confirmed wording over the bot draft on held-out examples. That is a materially stronger future quality signal than synthetic style examples alone.

It does **not** prove that DPO or a reward model should be adopted. Training remains a separate future benchmark and resource/privacy decision.
