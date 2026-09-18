from __future__ import annotations

"""Audit literal dynamic-import bypasses for a protected application module.

Import Linter protects static import edges. This companion audit covers a narrow
set of literal dynamic-loading patterns that could otherwise bypass the protected
contract. It is intentionally conservative and read-only.
"""

import argparse
import ast
import json
from pathlib import Path
from typing import Any, Iterable

DEFAULT_SCAN_ROOTS = ("app", "tools", "tests")
DEFAULT_TARGET = "app.x_recovery_integrity_runtime"


def _module_name_from_path(path: Path, root: Path) -> str:
    relative = path.relative_to(root).with_suffix("")
    parts = list(relative.parts)
    if parts and parts[-1] == "__init__":
        parts.pop()
    return ".".join(parts)


def _attribute_name(node: ast.AST) -> str | None:
    parts: list[str] = []
    current = node
    while isinstance(current, ast.Attribute):
        parts.append(current.attr)
        current = current.value
    if isinstance(current, ast.Name):
        parts.append(current.id)
        return ".".join(reversed(parts))
    return None


def _literal_string(node: ast.AST, constants: dict[str, str]) -> str | None:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.Name):
        return constants.get(node.id)
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        left = _literal_string(node.left, constants)
        right = _literal_string(node.right, constants)
        if left is not None and right is not None:
            return left + right
    if isinstance(node, ast.JoinedStr):
        pieces: list[str] = []
        for value in node.values:
            if not isinstance(value, ast.Constant) or not isinstance(value.value, str):
                return None
            pieces.append(value.value)
        return "".join(pieces)
    return None


def _collect_literal_constants(tree: ast.AST) -> dict[str, str]:
    values: dict[str, set[str]] = {}
    for node in ast.walk(tree):
        if isinstance(node, (ast.Assign, ast.AnnAssign)):
            value_node = node.value
            if value_node is None:
                continue
            literal = _literal_string(value_node, {})
            if literal is None:
                continue
            targets: list[ast.AST]
            if isinstance(node, ast.Assign):
                targets = list(node.targets)
            else:
                targets = [node.target]
            for target in targets:
                if isinstance(target, ast.Name):
                    values.setdefault(target.id, set()).add(literal)
    return {name: next(iter(found)) for name, found in values.items() if len(found) == 1}


def _normalize_call_name(node: ast.AST, aliases: dict[str, str]) -> str | None:
    raw = _attribute_name(node)
    if raw is None and isinstance(node, ast.Name):
        raw = node.id
    if raw is None:
        return None
    first, *rest = raw.split(".")
    normalized_first = aliases.get(first, first)
    return ".".join([normalized_first, *rest]) if rest else normalized_first


def _target_match(value: str, target: str) -> bool:
    value = value.strip()
    return value == target or value.startswith(target + ".")


def _relative_target_match(value: str, target: str) -> bool:
    value = value.strip()
    leaf = target.rsplit(".", 1)[-1]
    return value.startswith(".") and value.lstrip(".") in {leaf, target}


def _record(
    findings: list[dict[str, Any]],
    *,
    path: Path,
    root: Path,
    node: ast.AST,
    importer: str,
    kind: str,
    target: str,
    detail: str,
) -> None:
    findings.append(
        {
            "path": str(path.relative_to(root)),
            "line": getattr(node, "lineno", 0),
            "column": getattr(node, "col_offset", 0),
            "importer": importer,
            "kind": kind,
            "target": target,
            "detail": detail,
        }
    )


def scan_python_file(path: Path, root: Path, protected_module: str) -> tuple[list[dict[str, Any]], str | None]:
    try:
        source = path.read_text(encoding="utf-8")
        tree = ast.parse(source, filename=str(path))
    except (OSError, UnicodeError, SyntaxError) as exc:
        return [], str(exc)

    importer = _module_name_from_path(path, root)
    aliases: dict[str, str] = {
        "__import__": "builtins.__import__",
    }

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.asname:
                    aliases[alias.asname] = alias.name
                else:
                    top_level = alias.name.split(".", 1)[0]
                    aliases[top_level] = top_level
        elif isinstance(node, ast.ImportFrom) and node.module:
            for alias in node.names:
                if alias.name == "*":
                    continue
                local = alias.asname or alias.name
                aliases[local] = f"{node.module}.{alias.name}"

    constants = _collect_literal_constants(tree)
    findings: list[dict[str, Any]] = []

    module_arg_apis = {
        "importlib.import_module",
        "builtins.__import__",
        "runpy.run_module",
        "pkgutil.resolve_name",
        "importlib.util.spec_from_file_location",
        "importlib.machinery.SourceFileLoader",
        "importlib.machinery.SourcelessFileLoader",
    }

    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            call_name = _normalize_call_name(node.func, aliases)

            if call_name in module_arg_apis and node.args:
                raw_target = _literal_string(node.args[0], constants)
                if raw_target is not None:
                    normalized = raw_target.split(":", 1)[0]
                    if _target_match(normalized, protected_module) or _relative_target_match(
                        normalized, protected_module
                    ):
                        _record(
                            findings,
                            path=path,
                            root=root,
                            node=node,
                            importer=importer,
                            kind="dynamic-import-call",
                            target=raw_target,
                            detail=f"literal protected target passed to {call_name}",
                        )

            if call_name in {"builtins.exec", "builtins.eval", "builtins.compile", "exec", "eval", "compile"} and node.args:
                payload = _literal_string(node.args[0], constants)
                if payload is not None:
                    leaf = protected_module.rsplit(".", 1)[-1]
                    if leaf in payload and "import" in payload:
                        _record(
                            findings,
                            path=path,
                            root=root,
                            node=node,
                            importer=importer,
                            kind="dynamic-code-import",
                            target=protected_module,
                            detail=f"literal code passed to {call_name} contains protected import text",
                        )

            if call_name in {"sys.modules.get", "sys.modules.pop"} and node.args:
                key = _literal_string(node.args[0], constants)
                if key is not None and _target_match(key, protected_module):
                    _record(
                        findings,
                        path=path,
                        root=root,
                        node=node,
                        importer=importer,
                        kind="sys-modules-access",
                        target=key,
                        detail=f"literal protected target accessed through {call_name}",
                    )

        elif isinstance(node, ast.Subscript):
            value_name = _normalize_call_name(node.value, aliases)
            if value_name == "sys.modules":
                key_node = node.slice
                key = _literal_string(key_node, constants)
                if key is not None and _target_match(key, protected_module):
                    _record(
                        findings,
                        path=path,
                        root=root,
                        node=node,
                        importer=importer,
                        kind="sys-modules-access",
                        target=key,
                        detail="literal protected target accessed through sys.modules[...]",
                    )

    return sorted(findings, key=lambda item: (item["path"], item["line"], item["column"])), None


def audit_repository(
    root: Path,
    protected_module: str = DEFAULT_TARGET,
    scan_roots: Iterable[str] = DEFAULT_SCAN_ROOTS,
) -> dict[str, Any]:
    root = root.resolve()
    findings: list[dict[str, Any]] = []
    parse_errors: list[dict[str, str]] = []
    files_scanned = 0

    for scan_root in scan_roots:
        base = root / scan_root
        if not base.exists():
            continue
        for path in sorted(base.rglob("*.py")):
            files_scanned += 1
            found, error = scan_python_file(path, root, protected_module)
            findings.extend(found)
            if error:
                parse_errors.append({"path": str(path.relative_to(root)), "error": error})

    return {
        "schema_version": 1,
        "protected_module": protected_module,
        "files_scanned": files_scanned,
        "violation_count": len(findings),
        "parse_error_count": len(parse_errors),
        "violations": findings,
        "parse_errors": parse_errors,
        "scope": {
            "scan_roots": list(scan_roots),
            "literal_only": True,
            "mutates_source": False,
            "notes": [
                "Import Linter remains the authority for static import edges.",
                "This audit blocks literal dynamic-loading bypasses for the protected module.",
                "Computed runtime module names still require normal code review and security review.",
            ],
        },
    }


def render_markdown(report: dict[str, Any]) -> str:
    lines = [
        "# Stage D Protected Import Bypass Audit",
        "",
        f"- Protected module: `{report['protected_module']}`",
        f"- Python files scanned: {report['files_scanned']}",
        f"- Literal dynamic-import violations: {report['violation_count']}",
        f"- Parse errors: {report['parse_error_count']}",
        "",
    ]
    if report["violations"]:
        lines.extend(["## Violations", ""])
        for item in report["violations"]:
            lines.append(
                f"- `{item['path']}:{item['line']}` — **{item['kind']}** — {item['detail']}"
            )
    else:
        lines.append("No literal dynamic-import bypasses were found.")
    if report["parse_errors"]:
        lines.extend(["", "## Parse errors", ""])
        for item in report["parse_errors"]:
            lines.append(f"- `{item['path']}` — {item['error']}")
    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description="Audit literal dynamic-import bypasses for a protected module.")
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parser.add_argument("--protected-module", default=DEFAULT_TARGET)
    parser.add_argument("--json", type=Path)
    parser.add_argument("--markdown", type=Path)
    args = parser.parse_args()

    report = audit_repository(args.root, args.protected_module)
    rendered = render_markdown(report)
    if args.json:
        args.json.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    if args.markdown:
        args.markdown.write_text(rendered, encoding="utf-8")
    if not args.json and not args.markdown:
        print(rendered, end="")

    if report["parse_error_count"]:
        return 2
    if report["violation_count"]:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
