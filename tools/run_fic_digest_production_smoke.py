from __future__ import annotations

"""Bounded live smoke for the exact production fic-summary writer.

The larger manual evaluation uses real AO3 metadata. This PR gate uses invented
metadata so it can validate the live Gemini schema and all spoiler modes without
fetching or reproducing any author's story text and without sending Telegram.
"""

import os

from app.config import Settings
from app.fic_digest import (
    Fic,
    SPOILER_FULL,
    SPOILER_MEDIUM,
    SPOILER_NO,
    format_digest,
    summarize_fics_persian,
)
from app.fic_summary_quality import fic_summary_quality_issues


def _cases() -> list[Fic]:
    return [
        Fic(
            title="Invented reunion test",
            url="https://example.invalid/fic-smoke/1",
            author="smoke",
            summary="Jeonghan meets Seungcheol again and their old romantic tension returns.",
            relationships=["Choi Seungcheol/Yoon Jeonghan"],
            rating="Teen And Up Audiences",
            warnings=["No Archive Warnings Apply"],
            freeforms=["Reunions", "Mutual Pining"],
        ),
        Fic(
            title="Invented dark test",
            url="https://example.invalid/fic-smoke/2",
            author="smoke",
            summary="During a violent crisis, Jeonghan and Joshua confront grief and sexual tension.",
            relationships=["Hong Jisoo/Yoon Jeonghan"],
            rating="Explicit",
            warnings=["Graphic Depictions Of Violence"],
            freeforms=["Angst", "Sexual Tension", "Grief/Mourning"],
        ),
        Fic(
            title="Invented ensemble test",
            url="https://example.invalid/fic-smoke/3",
            author="smoke",
            summary="Jeonghan, Mingyu, and Wonwoo renegotiate a complicated established relationship.",
            relationships=["Yoon Jeonghan/Kim Mingyu/Jeon Wonwoo"],
            rating="Mature",
            warnings=["Creator Chose Not To Use Archive Warnings"],
            freeforms=["Polyamory", "Established Relationship"],
        ),
    ]


def main() -> int:
    if not os.environ.get("GEMINI_API_KEY", "").strip():
        raise SystemExit("GEMINI_API_KEY is required for production fic smoke")
    settings = Settings.load(require_secrets=False)
    fics = _cases()
    summaries = summarize_fics_persian(settings, fics, SPOILER_MEDIUM)
    failures: list[str] = []
    for fic in fics:
        outputs = {
            SPOILER_NO: fic.summary_fa_nospoiler,
            SPOILER_MEDIUM: summaries.get(fic.url, ""),
            SPOILER_FULL: fic.summary_fa_full,
        }
        for mode, output in outputs.items():
            if not output or "متن اصلی AO3" in output:
                failures.append(f"{fic.title}:{mode}:fallback_or_empty")
                continue
            issues = fic_summary_quality_issues(
                fic.summary,
                output,
                preserve_explicit_content=mode != SPOILER_NO,
            )
            failures.extend(f"{fic.title}:{mode}:{issue}" for issue in issues)
        if not fic.relationship_dynamic_fa:
            failures.append(f"{fic.title}:missing_relationship_dynamic")
        if len(fic.warnings_fa or []) < len(fic.warnings or []):
            failures.append(f"{fic.title}:missing_warning")
    for mode in (SPOILER_NO, SPOILER_MEDIUM, SPOILER_FULL):
        rendered = format_digest("smoke", fics, summaries, "ao3", mode)
        if any(fic.url not in rendered for fic in fics):
            failures.append(f"digest:{mode}:missing_work")
    if failures:
        print("FIC PRODUCTION SMOKE FAILED: " + " | ".join(failures), flush=True)
        return 1
    print("FIC PRODUCTION SMOKE PASS: grounded no/medium/full outputs; no Telegram sent", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
