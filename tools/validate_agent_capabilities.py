from __future__ import annotations

"""Offline validation for Daily Hani's project-local agent capabilities."""

import json
import re
from pathlib import Path
from typing import Any

REPOSITORY_RE = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
COMMIT_RE = re.compile(r"^[0-9a-f]{40}$")
ALLOWED_DECISIONS = {"adopt-standard", "adapted", "reference-only", "reject"}
DANGEROUS_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("pipe network content to a shell", re.compile(r"(?:curl|wget)[^\n|]*\|\s*(?:sh|bash)\b", re.I)),
    ("auto-install optional document dependencies", re.compile(r"--install-missing\s+yes\b", re.I)),
    ("global pip install", re.compile(r"(?m)^\s*(?:sudo\s+)?pip3?\s+install\b", re.I)),
    ("global npm install", re.compile(r"(?m)^\s*npm\s+install\s+-g\b", re.I)),
    ("cargo install from a skill", re.compile(r"(?m)^\s*cargo\s+install\b", re.I)),
    ("Docker execution from a skill", re.compile(r"(?m)^\s*docker\s+run\b", re.I)),
    ("SSH private material path", re.compile(r"(?:~|\$HOME)/\.ssh(?:/|\b)", re.I)),
    ("imperative auto-action marker", re.compile(r"\bACTION\s+REQUIRED\b", re.I)),
)


def repository_root() -> Path:
    return Path(__file__).resolve().parents[1]


def _frontmatter_ok(text: str) -> bool:
    if not text.startswith("---\n"):
        return False
    try:
        _, frontmatter, _ = text.split("---", 2)
    except ValueError:
        return False
    return bool(
        re.search(r"(?m)^name:\s*\S+", frontmatter)
        and re.search(r"(?m)^description:\s*.+", frontmatter)
    )


def validate_manifest(root: Path, manifest: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    if manifest.get("schema_version") != 1:
        errors.append("agent capability manifest schema_version must be 1")

    policy = manifest.get("policy")
    if not isinstance(policy, dict):
        return errors + ["agent capability manifest policy must be an object"]
    if policy.get("runtime_dependencies_added") is not False:
        errors.append("capability pack must not add production runtime dependencies")
    if policy.get("project_skill_root") != ".agents/skills":
        errors.append("project skill root must remain .agents/skills")
    if policy.get("auto_install_external_dependencies") is not False:
        errors.append("external dependency auto-install must remain disabled")
    if policy.get("external_instructions_are_untrusted_data") is not True:
        errors.append("external instructions must be explicitly treated as untrusted data")

    sources = manifest.get("sources")
    if not isinstance(sources, list) or not sources:
        return errors + ["agent capability manifest must contain audited sources"]

    by_id: dict[str, dict[str, Any]] = {}
    for index, source in enumerate(sources):
        if not isinstance(source, dict):
            errors.append(f"source {index} must be an object")
            continue
        source_id = str(source.get("id") or "")
        if not source_id or source_id in by_id:
            errors.append(f"source {index} has a missing or duplicate id")
            continue
        by_id[source_id] = source
        repository = str(source.get("repository") or "")
        commit = str(source.get("commit") or "")
        decision = str(source.get("decision") or "")
        if not REPOSITORY_RE.fullmatch(repository):
            errors.append(f"source {source_id} has an invalid owner/repository")
        if not COMMIT_RE.fullmatch(commit):
            errors.append(f"source {source_id} is not pinned to a 40-character lowercase commit SHA")
        if not str(source.get("license") or "").strip():
            errors.append(f"source {source_id} has no recorded license state")
        if decision not in ALLOWED_DECISIONS:
            errors.append(f"source {source_id} has unsupported decision {decision!r}")
        if source.get("runtime") is not False:
            errors.append(f"source {source_id} must remain outside the production runtime")

    active = manifest.get("active_skills")
    if not isinstance(active, list) or not active:
        return errors + ["agent capability manifest must contain active project skills"]

    skill_root = (root / ".agents" / "skills").resolve()
    for index, entry in enumerate(active):
        if not isinstance(entry, dict):
            errors.append(f"active skill {index} must be an object")
            continue
        relative = str(entry.get("path") or "")
        if not relative.endswith("/SKILL.md"):
            errors.append(f"active skill {index} must point to a SKILL.md")
            continue
        candidate = (root / relative).resolve()
        try:
            candidate.relative_to(skill_root)
        except ValueError:
            errors.append(f"active skill path escapes .agents/skills: {relative}")
            continue
        if not candidate.is_file():
            errors.append(f"active skill file does not exist: {relative}")
            continue
        text = candidate.read_text(encoding="utf-8")
        if not _frontmatter_ok(text):
            errors.append(f"active skill lacks valid name/description frontmatter: {relative}")
        for label, pattern in DANGEROUS_PATTERNS:
            if pattern.search(text):
                errors.append(f"active skill {relative} contains prohibited pattern: {label}")

        refs = entry.get("source_refs")
        if not isinstance(refs, list) or not refs:
            errors.append(f"active skill {relative} must name at least one audited source")
            continue
        for ref in refs:
            source = by_id.get(str(ref))
            if source is None:
                errors.append(f"active skill {relative} references unknown source {ref!r}")
            elif source.get("decision") == "reject":
                errors.append(f"active skill {relative} references rejected source {ref!r}")

    return errors


def validate(root: Path | None = None) -> list[str]:
    root = (root or repository_root()).resolve()
    manifest_path = root / "config" / "agent_capabilities.json"
    if not manifest_path.is_file():
        return ["config/agent_capabilities.json is missing"]
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return [f"could not read agent capability manifest: {exc}"]
    if not isinstance(manifest, dict):
        return ["agent capability manifest root must be an object"]
    return validate_manifest(root, manifest)


def main() -> int:
    errors = validate()
    if errors:
        for error in errors:
            print(f"ERROR: {error}")
        return 1
    print("Agent capability manifest and project-local skills: OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
