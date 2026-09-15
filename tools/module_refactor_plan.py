from __future__ import annotations

"""Build a syntax-aware, read-only module rename/move plan.

This tool intentionally never edits source files. It uses LibCST to distinguish
real Python import references from comments/text and reports dynamic string
references separately for human review. The resulting plan is evidence for a
focused Stage C refactor, not permission to apply one automatically.
"""

import argparse
import ast
import json
from pathlib import Path
from typing import Any, Iterable

DEFAULT_SCAN_ROOTS = ("app", "tools", "tests")


def module_name_from_path(path: Path, root: Path) -> str:
    relative = path.relative_to(root).with_suffix("")
    parts = list(relative.parts)
    if parts and parts[-1] == "__init__":
        parts.pop()
    return ".".join(parts)


def module_path(root: Path, module: str) -> Path:
    return root.joinpath(*module.split(".")).with_suffix(".py")


def _load_libcst() -> tuple[Any, Any, Any]:
    try:
        import libcst as cst
        from libcst.helpers import get_full_name_for_node
        from libcst.metadata import PositionProvider
    except ImportError as exc:  # pragma: no cover - maintenance environment owns this dependency.
        raise RuntimeError(
            "libcst is required for Stage C refactor planning; install requirements-maintenance.txt"
        ) from exc
    return cst, get_full_name_for_node, PositionProvider


def _package_parts(current_module: str, *, is_package: bool, level: int) -> list[str]:
    package = current_module.split(".") if is_package else current_module.split(".")[:-1]
    drop = max(0, level - 1)
    if drop > len(package):
        return []
    if drop:
        package = package[:-drop]
    return package


def _resolve_from_module(
    current_module: str,
    *,
    is_package: bool,
    relative_level: int,
    module_name: str | None,
) -> str:
    if relative_level:
        parts = _package_parts(current_module, is_package=is_package, level=relative_level)
        if module_name:
            parts.extend(module_name.split("."))
        return ".".join(parts)
    return module_name or ""


def _replace_prefix(value: str, old_module: str, new_module: str) -> str | None:
    if value == old_module:
        return new_module
    prefix = old_module + "."
    if value.startswith(prefix):
        return new_module + value[len(old_module) :]
    return None


def _string_reference(value: str, old_module: str) -> bool:
    return value == old_module or value.startswith(old_module + ".")


def scan_file(path: Path, root: Path, old_module: str, new_module: str) -> tuple[list[dict[str, Any]], str | None]:
    cst, get_full_name_for_node, PositionProvider = _load_libcst()
    try:
        source = path.read_text(encoding="utf-8")
        module = cst.parse_module(source)
    except (OSError, UnicodeError, cst.ParserSyntaxError) as exc:
        return [], str(exc)

    current_module = module_name_from_path(path, root)
    is_package = path.name == "__init__.py"
    wrapper = cst.MetadataWrapper(module)
    references: list[dict[str, Any]] = []

    class Visitor(cst.CSTVisitor):
        METADATA_DEPENDENCIES = (PositionProvider,)

        def _record(
            self,
            node: Any,
            *,
            kind: str,
            resolved: str,
            proposal: str,
            automatic_safe: bool,
            reason: str,
        ) -> None:
            position = self.get_metadata(PositionProvider, node).start
            references.append(
                {
                    "path": str(path.relative_to(root)),
                    "line": position.line,
                    "column": position.column,
                    "kind": kind,
                    "original": module.code_for_node(node).strip(),
                    "resolved": resolved,
                    "proposal": proposal,
                    "automatic_safe": automatic_safe,
                    "import_order_sensitive": path == root / "app" / "__init__.py",
                    "reason": reason,
                }
            )

        def visit_Import(self, node: Any) -> None:
            names = list(node.names)
            for alias in names:
                imported = get_full_name_for_node(alias.name) or ""
                replacement = _replace_prefix(imported, old_module, new_module)
                if replacement is None:
                    continue
                as_name = None
                if alias.asname is not None:
                    as_name = get_full_name_for_node(alias.asname.name)
                proposal = f"replace imported module path `{imported}` with `{replacement}`"
                if as_name:
                    proposal += f" while preserving local alias `{as_name}`"
                self._record(
                    node,
                    kind="import",
                    resolved=imported,
                    proposal=proposal,
                    automatic_safe=len(names) == 1,
                    reason=(
                        "single import alias can be rewritten structurally"
                        if len(names) == 1
                        else "multi-alias import should be split/reviewed rather than rewritten broadly"
                    ),
                )

        def visit_ImportFrom(self, node: Any) -> None:
            module_name = get_full_name_for_node(node.module) if node.module is not None else None
            relative_level = len(node.relative)
            resolved_parent = _resolve_from_module(
                current_module,
                is_package=is_package,
                relative_level=relative_level,
                module_name=module_name,
            )
            parent_replacement = _replace_prefix(resolved_parent, old_module, new_module)
            if parent_replacement is not None:
                self._record(
                    node,
                    kind="from-module",
                    resolved=resolved_parent,
                    proposal=(
                        f"rewrite source module `{resolved_parent}` to `{parent_replacement}` "
                        "while preserving imported bindings"
                    ),
                    automatic_safe=True,
                    reason="the imported object bindings do not need to change when only the source module moves",
                )
                return

            if isinstance(node.names, cst.ImportStar):
                return
            aliases = list(node.names)
            for alias in aliases:
                imported_name = get_full_name_for_node(alias.name) or ""
                combined = ".".join(part for part in (resolved_parent, imported_name) if part)
                replacement = _replace_prefix(combined, old_module, new_module)
                if replacement is None:
                    continue
                new_parent, _, new_leaf = replacement.rpartition(".")
                old_local = (
                    get_full_name_for_node(alias.asname.name)
                    if alias.asname is not None
                    else imported_name.rsplit(".", 1)[-1]
                )
                proposal = (
                    f"import `{new_leaf or replacement}` from `{new_parent or '<root>'}` "
                    f"and preserve local binding `{old_local}` during the compatibility phase"
                )
                self._record(
                    node,
                    kind="from-imported-module",
                    resolved=combined,
                    proposal=proposal,
                    automatic_safe=len(aliases) == 1,
                    reason=(
                        "single imported module can preserve its old local binding with an alias"
                        if len(aliases) == 1
                        else "multi-alias from-import needs a focused split/review to avoid unrelated changes"
                    ),
                )

        def visit_SimpleString(self, node: Any) -> None:
            try:
                value = ast.literal_eval(node.value)
            except (SyntaxError, ValueError):
                return
            if not isinstance(value, str) or not _string_reference(value, old_module):
                return
            self._record(
                node,
                kind="dynamic-string",
                resolved=value,
                proposal="inspect manually; string-based imports/config references are never auto-rewritten",
                automatic_safe=False,
                reason="a string may be used by importlib, subprocess/config, serialization, or diagnostics",
            )

    wrapper.visit(Visitor())
    return references, None


def build_plan(
    root: Path,
    old_module: str,
    new_module: str,
    scan_roots: Iterable[str] = DEFAULT_SCAN_ROOTS,
) -> dict[str, Any]:
    root = root.resolve()
    references: list[dict[str, Any]] = []
    parse_errors: list[dict[str, str]] = []

    for scan_root in scan_roots:
        base = root / scan_root
        if not base.exists():
            continue
        for path in sorted(base.rglob("*.py")):
            found, error = scan_file(path, root, old_module, new_module)
            references.extend(found)
            if error:
                parse_errors.append({"path": str(path.relative_to(root)), "error": error})

    safe_count = sum(1 for item in references if item["automatic_safe"])
    dynamic_count = sum(1 for item in references if item["kind"] == "dynamic-string")
    import_order_count = sum(1 for item in references if item["import_order_sensitive"])
    old_path = module_path(root, old_module)
    new_path = module_path(root, new_module)

    return {
        "schema_version": 1,
        "old_module": old_module,
        "new_module": new_module,
        "old_module_exists": old_path.exists(),
        "new_module_exists": new_path.exists(),
        "compatibility_shim_required": old_module.startswith("app."),
        "summary": {
            "reference_count": len(references),
            "structurally_safe_reference_count": safe_count,
            "manual_review_reference_count": len(references) - safe_count,
            "dynamic_string_reference_count": dynamic_count,
            "import_order_sensitive_reference_count": import_order_count,
            "parse_error_count": len(parse_errors),
        },
        "parse_errors": parse_errors,
        "references": sorted(references, key=lambda item: (item["path"], item["line"], item["column"])),
        "safety": {
            "mutates_source": False,
            "automatic_apply_allowed": False,
            "notes": [
                "This plan is evidence only; source files are never edited by this tool.",
                "Dynamic strings, workflows, CLI/subprocess paths, persisted config/state, and import order still require direct review.",
                "For active app modules, keep the old import path as a compatibility shim until focused and full validation are green.",
            ],
        },
    }


def render_markdown(plan: dict[str, Any]) -> str:
    summary = plan["summary"]
    lines = [
        "# Hani Stage C Refactor Plan",
        "",
        f"- Old module: `{plan['old_module']}`",
        f"- Proposed semantic module: `{plan['new_module']}`",
        f"- Old module exists: `{plan['old_module_exists']}`",
        f"- Target already exists: `{plan['new_module_exists']}`",
        f"- References: {summary['reference_count']}",
        f"- Structurally safe references: {summary['structurally_safe_reference_count']}",
        f"- Manual-review references: {summary['manual_review_reference_count']}",
        f"- Dynamic string references: {summary['dynamic_string_reference_count']}",
        f"- Import-order-sensitive references: {summary['import_order_sensitive_reference_count']}",
        f"- Parse errors: {summary['parse_error_count']}",
        "",
        "> Read-only plan. It never edits source files and never authorizes an automatic move/rename.",
        "",
        "## References",
        "",
    ]
    references = plan["references"]
    if not references:
        lines.append("- No syntax-level references found. This is not proof of no runtime use.")
    else:
        for item in references:
            mode = "structural" if item["automatic_safe"] else "manual"
            lines.append(
                f"- `{item['path']}:{item['line']}` — **{item['kind']} / {mode}** — {item['proposal']}"
            )
            if item["import_order_sensitive"]:
                lines.append("  - Import order sensitive: `app/__init__.py`; preserve exact installation ordering.")
    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description="Build a read-only LibCST module refactor plan.")
    parser.add_argument("--old-module", required=True)
    parser.add_argument("--new-module", required=True)
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parser.add_argument("--json", type=Path)
    parser.add_argument("--markdown", type=Path)
    args = parser.parse_args()

    plan = build_plan(args.root, args.old_module, args.new_module)
    rendered = render_markdown(plan)
    if args.json:
        args.json.write_text(json.dumps(plan, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    if args.markdown:
        args.markdown.write_text(rendered, encoding="utf-8")
    if not args.json and not args.markdown:
        print(rendered, end="")
    return 2 if plan["parse_errors"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
