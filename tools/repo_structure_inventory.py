from __future__ import annotations

import argparse
import ast
import json
import re
from collections import Counter
from pathlib import Path
from typing import Iterable

HISTORICAL_NAME_RE = re.compile(
    r"(?:^|_)(?:phase\d+|part\d+|finalfix|humanfix|qualityfix|hardening)(?:_|$)",
    re.IGNORECASE,
)

CATEGORY_PATTERNS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("events", ("event_", "timeline")),
    ("fanfic", ("fic_", "ao3")),
    ("sources", ("source_", "configured_source", "x_", "provider", "collector")),
    ("editorial", ("channel_", "translation", "caption", "voice", "style", "ai")),
    ("state_recovery", ("archive", "state", "recovery", "backup", "callback", "private_review")),
    ("delivery", ("telegram", "delivery", "bot")),
    ("observability", ("completeness", "zero_silent", "health", "watchdog", "outcome", "benchmark")),
)


def _module_name(path: Path, root: Path) -> str:
    relative = path.relative_to(root).with_suffix("")
    parts = list(relative.parts)
    if parts and parts[-1] == "__init__":
        parts.pop()
    return ".".join(parts)


def _category(module: str) -> str:
    leaf = module.rsplit(".", 1)[-1].lower()
    for category, patterns in CATEGORY_PATTERNS:
        if any(pattern in leaf for pattern in patterns):
            return category
    return "other"


def _relative_base(module: str, is_package: bool, level: int) -> list[str]:
    package = module.split(".") if is_package else module.split(".")[:-1]
    drop = max(0, level - 1)
    if drop >= len(package):
        return []
    return package[: len(package) - drop]


def _internal_imports(tree: ast.AST, module: str, is_package: bool) -> list[str]:
    imports: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name == "app" or alias.name.startswith("app.") or alias.name == "tools" or alias.name.startswith("tools."):
                    imports.add(alias.name)
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                base = _relative_base(module, is_package, node.level)
                if node.module:
                    base.extend(node.module.split("."))
                    target = ".".join(base)
                    if target:
                        imports.add(target)
                else:
                    for alias in node.names:
                        target = ".".join([*base, alias.name])
                        if target:
                            imports.add(target)
            elif node.module and (
                node.module == "app"
                or node.module.startswith("app.")
                or node.module == "tools"
                or node.module.startswith("tools.")
            ):
                imports.add(node.module)
    return sorted(imports)


def build_inventory(root: Path, scan_roots: Iterable[str] = ("app", "tools")) -> dict[str, object]:
    modules: list[dict[str, object]] = []
    parse_errors: list[dict[str, str]] = []

    for scan_root in scan_roots:
        base = root / scan_root
        if not base.exists():
            continue
        for path in sorted(base.rglob("*.py")):
            module = _module_name(path, root)
            try:
                tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            except (OSError, SyntaxError, UnicodeError) as exc:
                parse_errors.append({"path": str(path.relative_to(root)), "error": str(exc)})
                continue

            leaf = module.rsplit(".", 1)[-1]
            modules.append(
                {
                    "path": str(path.relative_to(root)),
                    "module": module,
                    "category": _category(module),
                    "historical_name": bool(HISTORICAL_NAME_RE.search(leaf)),
                    "internal_imports": _internal_imports(tree, module, path.name == "__init__.py"),
                }
            )

    category_counts = Counter(str(item["category"]) for item in modules)
    historical = [str(item["module"]) for item in modules if item["historical_name"]]
    return {
        "schema_version": 1,
        "summary": {
            "module_count": len(modules),
            "category_counts": dict(sorted(category_counts.items())),
            "historical_name_count": len(historical),
            "parse_error_count": len(parse_errors),
        },
        "historical_name_modules": historical,
        "parse_errors": parse_errors,
        "modules": modules,
    }


def render_markdown(inventory: dict[str, object]) -> str:
    summary = inventory["summary"]
    assert isinstance(summary, dict)
    lines = [
        "# Hani Repository Structure Inventory",
        "",
        f"- Python modules scanned: {summary['module_count']}",
        f"- Historical phase/fix-style names: {summary['historical_name_count']}",
        f"- Parse errors: {summary['parse_error_count']}",
        "",
        "## Responsibility counts",
        "",
    ]
    category_counts = summary["category_counts"]
    assert isinstance(category_counts, dict)
    for category, count in sorted(category_counts.items()):
        lines.append(f"- `{category}`: {count}")

    lines.extend(["", "## Historical-name candidates", ""])
    historical = inventory["historical_name_modules"]
    assert isinstance(historical, list)
    if historical:
        lines.extend(f"- `{module}`" for module in historical)
    else:
        lines.append("- None")

    lines.extend(
        [
            "",
            "> These are cleanup candidates only. A historical name is not evidence that a module is dead or safe to move.",
            "",
        ]
    )
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="Inventory Hani Python modules without mutating the repository.")
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parser.add_argument("--json", type=Path, default=Path("repo-structure.json"))
    parser.add_argument("--markdown", type=Path, default=Path("repo-structure.md"))
    args = parser.parse_args()

    root = args.root.resolve()
    inventory = build_inventory(root)
    args.json.write_text(json.dumps(inventory, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    args.markdown.write_text(render_markdown(inventory), encoding="utf-8")

    if inventory["parse_errors"]:
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
