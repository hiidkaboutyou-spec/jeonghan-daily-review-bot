from __future__ import annotations

import argparse
import ast
import importlib.util
import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Iterable


DEFAULT_PROTECTED_MODULES = ("app.x_recovery_integrity_runtime",)
DEFAULT_SCAN_ROOTS = ("app", "tools")


@dataclass(frozen=True)
class DynamicImportFinding:
    path: str
    line: int
    column: int
    call: str
    requested_module: str | None
    resolved_module: str | None
    argument: str
    status: str


def _call_name(node: ast.Call, importlib_aliases: set[str], import_module_aliases: set[str]) -> str | None:
    func = node.func
    if isinstance(func, ast.Name):
        if func.id == "__import__":
            return "__import__"
        if func.id in import_module_aliases:
            return "importlib.import_module"
        return None
    if isinstance(func, ast.Attribute) and func.attr == "import_module":
        if isinstance(func.value, ast.Name) and func.value.id in importlib_aliases:
            return "importlib.import_module"
    return None


def _aliases(tree: ast.AST) -> tuple[set[str], set[str]]:
    importlib_aliases = {"importlib"}
    import_module_aliases: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name == "importlib":
                    importlib_aliases.add(alias.asname or alias.name)
        elif isinstance(node, ast.ImportFrom) and node.module == "importlib":
            for alias in node.names:
                if alias.name == "import_module":
                    import_module_aliases.add(alias.asname or alias.name)
    return importlib_aliases, import_module_aliases


def _constant_string(node: ast.AST | None) -> str | None:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    return None


def _resolve_import_module(name: str, package: str | None) -> str | None:
    if not name.startswith("."):
        return name
    if not package:
        return None
    try:
        return importlib.util.resolve_name(name, package)
    except (ImportError, ValueError):
        return None


def scan_source(
    source: str,
    *,
    path: str,
    protected_modules: Iterable[str] = DEFAULT_PROTECTED_MODULES,
) -> list[DynamicImportFinding]:
    tree = ast.parse(source, filename=path)
    protected = tuple(protected_modules)
    importlib_aliases, import_module_aliases = _aliases(tree)
    findings: list[DynamicImportFinding] = []

    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        call = _call_name(node, importlib_aliases, import_module_aliases)
        if call is None or not node.args:
            continue

        requested = _constant_string(node.args[0])
        resolved: str | None = None
        status = "manual-review"

        if requested is not None:
            if call == "importlib.import_module":
                package = None
                if len(node.args) >= 2:
                    package = _constant_string(node.args[1])
                for keyword in node.keywords:
                    if keyword.arg == "package":
                        package = _constant_string(keyword.value)
                resolved = _resolve_import_module(requested, package)
            else:
                resolved = requested

            if resolved is None:
                status = "manual-review"
            elif any(
                resolved == module or resolved.startswith(module + ".")
                for module in protected
            ):
                status = "protected-module-bypass"
            else:
                status = "literal-safe"

        try:
            argument = ast.unparse(node.args[0])
        except Exception:  # pragma: no cover - ast.unparse is expected on Python 3.11.
            argument = "<unparseable>"

        findings.append(
            DynamicImportFinding(
                path=path,
                line=int(getattr(node, "lineno", 0) or 0),
                column=int(getattr(node, "col_offset", 0) or 0),
                call=call,
                requested_module=requested,
                resolved_module=resolved,
                argument=argument,
                status=status,
            )
        )

    return findings


def build_report(
    root: Path,
    *,
    scan_roots: Iterable[str] = DEFAULT_SCAN_ROOTS,
    protected_modules: Iterable[str] = DEFAULT_PROTECTED_MODULES,
) -> dict[str, object]:
    findings: list[DynamicImportFinding] = []
    parse_errors: list[dict[str, object]] = []

    for scan_root in scan_roots:
        base = root / scan_root
        if not base.exists():
            continue
        for path in sorted(base.rglob("*.py")):
            relative = str(path.relative_to(root))
            try:
                source = path.read_text(encoding="utf-8")
                findings.extend(
                    scan_source(
                        source,
                        path=relative,
                        protected_modules=protected_modules,
                    )
                )
            except (OSError, UnicodeError, SyntaxError) as exc:
                parse_errors.append({"path": relative, "error": str(exc)})

    violations = [item for item in findings if item.status == "protected-module-bypass"]
    manual = [item for item in findings if item.status == "manual-review"]
    literal_safe = [item for item in findings if item.status == "literal-safe"]

    return {
        "schema_version": 1,
        "protected_modules": list(protected_modules),
        "scan_roots": list(scan_roots),
        "summary": {
            "finding_count": len(findings),
            "protected_module_bypass_count": len(violations),
            "manual_review_count": len(manual),
            "literal_safe_count": len(literal_safe),
            "parse_error_count": len(parse_errors),
        },
        "violations": [asdict(item) for item in violations],
        "manual_review": [asdict(item) for item in manual],
        "literal_safe": [asdict(item) for item in literal_safe],
        "parse_errors": parse_errors,
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Audit app/tools for literal dynamic imports that bypass protected "
            "architecture-module ownership."
        )
    )
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parser.add_argument(
        "--json",
        type=Path,
        default=Path("architecture-dynamic-import-report.json"),
    )
    args = parser.parse_args()

    report = build_report(args.root.resolve())
    args.json.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    summary = report["summary"]
    assert isinstance(summary, dict)
    print(
        "Dynamic import audit: "
        f"{summary['protected_module_bypass_count']} protected bypass(es), "
        f"{summary['manual_review_count']} manual-review call(s), "
        f"{summary['parse_error_count']} parse error(s)."
    )

    if report["parse_errors"] or report["violations"]:
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
