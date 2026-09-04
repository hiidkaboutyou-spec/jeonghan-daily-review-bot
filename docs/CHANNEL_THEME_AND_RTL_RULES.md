# Channel Theme & RTL Rules

These are explicit admin-authored rules and therefore outrank inferred corpus patterns, Tumblr inspiration, and model creativity.

## Theme behavior

The channel does not use one universal caption/header format.

Some recurring content families have their own established theme/template. Examples explicitly called out by the admin include:

- Jeonghan Instagram posts;
- BANILA CO / brand-related posts;
- other recurring content families that can be discovered from historical channel data.

The system must learn these as context-specific theme families rather than flattening every post into one generic style.

For recurring themed families, preserve the stable recognizable structure while still allowing gradual evolution from approved recent examples.

## General/default header grammar

For many ordinary channel updates, the common structure is roughly:

1. date;
2. a symbol, emoji, or symbol+emoji combination;
3. the program/source/event/story name or contextual label;
4. then the body/caption.

This is a learned grammar, not a rigid literal template. The exact ornament, spacing, casing, and label style should be chosen from approved channel history and current theme context.

## Persian RTL / bidi requirement

Persian is right-to-left. Decorative Unicode symbols, emoji, dates, Latin text, punctuation, and Persian text can render in visually incorrect order if the generator simply concatenates tokens in logical order.

Therefore RTL correctness is a product requirement, not cosmetic polish.

Rules:

- Never assume that placing a symbol before Persian text in the raw string will render visually as intended.
- Generate and test the final Telegram-visible layout with bidirectional text behavior in mind.
- Treat mixed Persian + Latin + digits + emoji + Unicode ornament combinations as bidi-sensitive.
- Prefer historically proven channel layouts for mixed-direction headers.
- Preserve exact spacing and directional placement from approved examples when a pattern is known-good.
- Avoid decorative combinations that reverse, jump across the line, attach to the wrong phrase, or make the header visually unbalanced in Telegram.
- Theme extraction from the historical corpus should store both the logical string and the rendered/structural ordering pattern where possible.
- Any future theme renderer should include regression tests for representative Persian RTL headers with date + symbol/emoji + Persian label and date + symbol/emoji + Latin label.

## Authority

When inferred style conflicts with these rules, these explicit admin rules win.

Priority remains:

1. explicit admin rules/corrections;
2. approved final channel posts;
3. recent context-specific historical patterns;
4. broader historical corpus;
5. Tumblr inspiration;
6. model creativity.
