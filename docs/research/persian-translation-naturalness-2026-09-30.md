# Persian translation naturalness research — 2026-09-30

## Goal

Make private-review translations read like native Persian while preserving the exact source meaning, speaker/attribution, names, numbers, dates, links, hashtags, laughter, emotion and uncertainty.

The owner specifically asked for output that is:
- روان؛
- عامیانه when the source is conversational/social;
- دقیق؛
- human/native rather than translated-sounding;
- immediately understandable to a Persian speaker.

## Current production baseline

The production writer already has strong factual safeguards:
- source-grounded direct EN/KO/JA/mixed → Persian generation;
- channel-style memory and paired demonstrations;
- canonical entity spelling;
- deterministic hard-fact and semantic checks;
- a bounded second repair call only when a candidate fails checks;
- 16k+ channel-style examples plus the existing voice profile.

Human benchmark evidence showed that the remaining failures are not primarily missing facts. Several rejected cases were semantically understandable but still sounded machine-translated or bookish, including:
- formal sequencing such as «ابتدا / سپس / بعداً» in a social explanation;
- «به‌روزرسانی» / «از جمله» in an Instagram-style post;
- unnatural predicate shapes such as «بانمک می‌دونستت»;
- literal social-media syntax.

## GitHub research

### roshan-research/hazm

Hazm is a mature Persian NLP toolkit with normalization, tokenization, lemmatization and parsing. Its current README states that the latest release requires Python 3.12+, while this repository's production workflow uses Python 3.11.

Decision: **do not add it as a runtime dependency in this stage**. Upgrading the bot's Python/runtime only for orthographic normalization would be disproportionate and does not solve semantic naturalness by itself.

Repository:
https://github.com/roshan-research/hazm

### ICTRC/Parsivar

Parsivar provides Persian normalization, half-space correction, tokenization, stemming, tagging, parsing and spell checking. Its public README still documents older Python/NLTK-era integration patterns and optional external spell resources.

Decision: useful background for Persian preprocessing, but not a production dependency for this stage. The bot's problem is translationese and register, not missing tokenization.

Repository:
https://github.com/ICTRC/Parsivar

### Dadmatech/DadmaTools

DadmaTools is an Apache-2.0 Persian NLP toolkit and explicitly includes an **informal2formal** task.

Decision: this is useful evidence that informal/formal register needs its own treatment, but the provided direction is opposite to the owner's desired output. Installing a model whose advertised transformation formalizes colloquial Persian would be the wrong default for a fan-channel translator.

Repository:
https://github.com/Dadmatech/DadmaTools

### amirivojdan/shekar

Shekar is a modern Persian NLP toolkit focused on normalization and real-world Persian. Its README explicitly demonstrates normalization that preserves conversational forms such as:
- «نمی‌رم»
- «خونه»
- «می‌تونه»
- «خونه‌مون»

That is directly aligned with the desired principle: **correct Persian orthography does not require turning conversational Persian into bookish Persian**.

Decision: use this design principle in our prompts/gates, but do not install the package solely for generation. A generic normalizer cannot decide whether a source should sound like a reaction, a live dialogue, an interview, or an official notice.

Repository:
https://github.com/amirivojdan/shekar

### omidkashefi/Mizan

Mizan is a large Persian-English parallel corpus with about one million sentence pairs, collected from literary works.

Decision: valuable for general Persian-English research, but not suitable as the primary style authority for a modern social/fandom translator. Literary parallel text can improve formal translation but can also push the exact direction the owner dislikes: bookish Persian. The existing real channel corpus remains the stronger style source.

Repository:
https://github.com/omidkashefi/Mizan

## Implementation decision

Do not add a new heavy dependency or a fourth unconditional model call.

Instead:

1. expand deterministic naturalness detection using high-confidence patterns taken from rejected human benchmark cases;
2. cover conversational explanations and social updates, not only dialogue/reactions;
3. add paired source→native-Persian demonstrations for relationship phrasing, Instagram copy and long explanations;
4. strengthen the existing repair prompt so a failed candidate is rewritten **Persian-first**, while SOURCE remains factual authority;
5. permit sentence-level restructuring when needed for native Persian, while locking subject/object, negation, causal relations, ownership, intensity, time, numbers and speaker identity;
6. keep official/factual notices exempt from forced slang;
7. keep the ordinary production path at one generation call; the second call remains failure-triggered only.

## New naturalness contract

For conversational/social content:

- «اول / بعد / بعدش» is preferred over mechanically imported «ابتدا / سپس / بعداً» when the source register is casual;
- «آپدیت» is preferred over «به‌روزرسانی» in channel/social copy;
- English structures like “including two with X” should be recast as natural Persian, e.g. «که توی دوتاشون X هم هست»;
- relationship predicates should be rebuilt naturally, e.g. “used to think you were cute” → «قبلاً فکر می‌کرد خیلی بامزه‌ای», not «بامزه می‌دونستت»;
- explicit pronouns are not copied mechanically when Persian naturally drops them;
- colloquial spelling is allowed when the source register is colloquial;
- none of these preferences may loosen factual fidelity.

For official/factual notices, natural clear Persian remains the target; the system must not inject slang merely to satisfy a colloquial preference.

## Safety

- no new dependency;
- no Python/runtime upgrade;
- no new provider or secret;
- no extra normal-path Gemini call;
- no public auto-posting;
- all existing hard-fact/entity/semantic gates remain active;
- repaired output is still rechecked before it can be treated as clean;
- uncertain or still-bad output remains private manual review.
