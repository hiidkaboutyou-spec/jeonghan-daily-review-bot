from __future__ import annotations

from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parents[1]
AGENTS = ROOT / "AGENTS.md"
SKILLS = ROOT / ".agents" / "skills"

PROJECT_SKILLS = {
    "jeonghan-daily-review-bot": {"project-next-stage", "evidence-first-engineering", "hani-production-acceptance", "agent-session-safety", "verified-agent-orchestration"},
    "Persian-Literary-Translation-Engine": {"project-next-stage", "evidence-first-engineering", "translation-engine-acceptance"},
}


def fail(message: str) -> None:
    print(f"AGENT HARNESS FAIL: {message}", file=sys.stderr)
    raise SystemExit(1)


def parse_frontmatter(path: Path) -> dict[str, str]:
    text = path.read_text(encoding="utf-8")
    if not text.startswith("---\n"):
        fail(f"{path.relative_to(ROOT)} missing YAML frontmatter")
    end = text.find("\n---\n", 4)
    if end < 0:
        fail(f"{path.relative_to(ROOT)} has unterminated YAML frontmatter")
    rows: dict[str, str] = {}
    for line in text[4:end].splitlines():
        if ":" not in line:
            continue
        key, value = line.split(":", 1)
        rows[key.strip()] = value.strip()
    return rows


def main() -> int:
    if not AGENTS.exists():
        fail("AGENTS.md missing")
    if not SKILLS.is_dir():
        fail(".agents/skills missing")

    repo_name = ROOT.name
    required = PROJECT_SKILLS.get(repo_name)
    if required is None:
        fail(f"validator has no contract for repository {repo_name!r}")

    agents_text = AGENTS.read_text(encoding="utf-8")
    for skill in sorted(required):
        path = SKILLS / skill / "SKILL.md"
        if not path.exists():
            fail(f"required skill missing: {skill}")
        meta = parse_frontmatter(path)
        if meta.get("name") != skill:
            fail(f"{skill} frontmatter name mismatch: {meta.get('name')!r}")
        if not meta.get("description"):
            fail(f"{skill} frontmatter description missing")
        if skill != "project-next-stage" and f".agents/skills/{skill}/SKILL.md" not in agents_text:
            fail(f"AGENTS.md does not activate {skill}")

    mandatory_phrase = "Mandatory evidence-first task contract"
    if mandatory_phrase not in agents_text:
        fail("AGENTS.md missing mandatory evidence-first contract")

    forbidden_claims = [
        r"skip failing tests",
        r"force[- ]merge",
        r"green CI (?:always|proves) production",
    ]
    lowered = agents_text.casefold()
    for pattern in forbidden_claims:
        if re.search(pattern, lowered):
            fail(f"unsafe agent contract text matched: {pattern}")

    print(f"AGENT HARNESS PASS: {repo_name}; skills={','.join(sorted(required))}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
