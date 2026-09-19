from __future__ import annotations

"""Report-only Stage D audit for Fanfic/Daily shadow-stack isolation.

Import Linter owns static direct/indirect import-chain evidence. This companion
audit covers two gaps for the Fanfic pilot:

1. literal dynamic-loading attempts inside app.fic_digest itself; and
2. a clean-process runtime import probe, because importing a regular Python
   submodule executes app/__init__.py and static module graphs do not model that
   package-initialization side effect as an edge from app.fic_digest.

The pilot is read-only and never imports manuscripts, contacts providers, or
changes runtime state.
"""

import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
from typing import Any, Iterable

try:
    from tools.protected_import_bypass_audit import scan_python_file
except ModuleNotFoundError:  # Direct execution: python tools/fanfic_import_isolation_audit.py
    from protected_import_bypass_audit import scan_python_file


SOURCE_MODULE = "app.fic_digest"
PACKAGE_INIT_WITNESS = "app.event_fusion_private_runtime"
FORBIDDEN_MODULES = (
    "app.translation_fusion",
    "app.translation_fusion_runtime",
    "app.translation_fusion_state_compat",
    "app.channel_style_rewrite",
    "app.channel_style_rewrite_state_compat",
    "app.user_voice_calibration",
    "app.user_voice_calibration_state_compat",
    "app.forward_ready_package",
    "app.forward_ready_state_compat",
    "app.fused_private_review_delivery",
)
_MARKER = "__HANI_FANFIC_ISOLATION__="


def _module_path(root: Path, module: str) -> Path:
    return root.joinpath(*module.split(".")).with_suffix(".py")


def audit_literal_dynamic_imports(
    root: Path,
    *,
    source_module: str = SOURCE_MODULE,
    forbidden_modules: Iterable[str] = FORBIDDEN_MODULES,
) -> tuple[list[dict[str, Any]], list[dict[str, str]]]:
    root = root.resolve()
    source_path = _module_path(root, source_module)
    if not source_path.exists():
        return [], [{"path": str(source_path), "error": "source module does not exist"}]

    findings_by_key: dict[tuple[Any, ...], dict[str, Any]] = {}
    parse_errors: list[dict[str, str]] = []
    for target in forbidden_modules:
        findings, error = scan_python_file(source_path, root, target)
        if error:
            parse_errors.append({"path": str(source_path.relative_to(root)), "error": error})
            break
        for item in findings:
            key = (
                item.get("path"),
                item.get("line"),
                item.get("column"),
                item.get("kind"),
                item.get("target"),
            )
            findings_by_key[key] = item

    findings = sorted(
        findings_by_key.values(),
        key=lambda item: (
            item.get("path", ""),
            item.get("line", 0),
            item.get("column", 0),
            item.get("target", ""),
        ),
    )
    return findings, parse_errors


def runtime_import_probe(
    root: Path,
    *,
    source_module: str = SOURCE_MODULE,
    forbidden_modules: Iterable[str] = FORBIDDEN_MODULES,
    witness_module: str = PACKAGE_INIT_WITNESS,
    timeout_seconds: float = 30.0,
) -> dict[str, Any]:
    root = root.resolve()
    forbidden = tuple(forbidden_modules)
    payload = json.dumps(
        {
            "source_module": source_module,
            "forbidden_modules": forbidden,
            "witness_module": witness_module,
        },
        separators=(",", ":"),
    )
    code = (
        "import importlib,json,sys\n"
        f"cfg=json.loads({payload!r})\n"
        "importlib.import_module(cfg['source_module'])\n"
        "result={"
        "'source_loaded': cfg['source_module'] in sys.modules,"
        "'package_init_witness_loaded': cfg['witness_module'] in sys.modules,"
        "'forbidden_loaded': sorted(m for m in cfg['forbidden_modules'] if m in sys.modules)"
        "}\n"
        f"print({_MARKER!r}+json.dumps(result,sort_keys=True,separators=(',',':')))\n"
    )
    env = os.environ.copy()
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    env["PYTHONNOUSERSITE"] = "1"

    try:
        completed = subprocess.run(
            [sys.executable, "-c", code],
            cwd=root,
            env=env,
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        return {
            "ok": False,
            "error": f"runtime import probe timed out after {timeout_seconds:.1f}s",
            "returncode": None,
            "source_loaded": False,
            "package_init_witness_loaded": False,
            "forbidden_loaded": [],
            "stdout_tail": (exc.stdout or "")[-2000:] if isinstance(exc.stdout, str) else "",
            "stderr_tail": (exc.stderr or "")[-2000:] if isinstance(exc.stderr, str) else "",
        }

    marker_line = next(
        (line for line in reversed(completed.stdout.splitlines()) if line.startswith(_MARKER)),
        None,
    )
    if completed.returncode != 0 or marker_line is None:
        return {
            "ok": False,
            "error": (
                f"runtime import probe exited {completed.returncode}"
                if completed.returncode != 0
                else "runtime import probe did not emit its result marker"
            ),
            "returncode": completed.returncode,
            "source_loaded": False,
            "package_init_witness_loaded": False,
            "forbidden_loaded": [],
            "stdout_tail": completed.stdout[-2000:],
            "stderr_tail": completed.stderr[-2000:],
        }

    try:
        result = json.loads(marker_line[len(_MARKER) :])
    except json.JSONDecodeError as exc:
        return {
            "ok": False,
            "error": f"runtime import probe emitted invalid JSON: {exc}",
            "returncode": completed.returncode,
            "source_loaded": False,
            "package_init_witness_loaded": False,
            "forbidden_loaded": [],
            "stdout_tail": completed.stdout[-2000:],
            "stderr_tail": completed.stderr[-2000:],
        }

    source_loaded = bool(result.get("source_loaded"))
    witness_loaded = bool(result.get("package_init_witness_loaded"))
    forbidden_loaded = sorted(
        value for value in result.get("forbidden_loaded", []) if isinstance(value, str)
    )
    valid = source_loaded and witness_loaded
    return {
        "ok": valid,
        "error": None if valid else "runtime probe did not exercise the expected package initializer",
        "returncode": completed.returncode,
        "source_loaded": source_loaded,
        "package_init_witness_loaded": witness_loaded,
        "forbidden_loaded": forbidden_loaded,
        "stdout_tail": completed.stdout[-2000:],
        "stderr_tail": completed.stderr[-2000:],
    }


def audit_repository(
    root: Path,
    *,
    source_module: str = SOURCE_MODULE,
    forbidden_modules: Iterable[str] = FORBIDDEN_MODULES,
    witness_module: str = PACKAGE_INIT_WITNESS,
) -> dict[str, Any]:
    forbidden = tuple(forbidden_modules)
    findings, parse_errors = audit_literal_dynamic_imports(
        root,
        source_module=source_module,
        forbidden_modules=forbidden,
    )
    runtime = runtime_import_probe(
        root,
        source_module=source_module,
        forbidden_modules=forbidden,
        witness_module=witness_module,
    )
    runtime_loaded = runtime["forbidden_loaded"]
    error_count = len(parse_errors) + (0 if runtime["ok"] else 1)
    violation_count = len(findings) + len(runtime_loaded)

    return {
        "schema_version": 1,
        "source_module": source_module,
        "forbidden_modules": list(forbidden),
        "package_init_witness": witness_module,
        "literal_dynamic_violation_count": len(findings),
        "runtime_loaded_forbidden_count": len(runtime_loaded),
        "violation_count": violation_count,
        "parse_error_count": len(parse_errors),
        "error_count": error_count,
        "literal_dynamic_violations": findings,
        "parse_errors": parse_errors,
        "runtime_probe": runtime,
        "scope": {
            "report_only": True,
            "mutates_source": False,
            "notes": [
                "Import Linter remains authoritative for static direct/indirect import chains.",
                "Literal dynamic-import scanning is intentionally scoped to app.fic_digest.",
                "The clean-process probe covers package __init__.py eager-loading side effects.",
                "Shared Fanfic primitives are not forbidden.",
            ],
        },
    }


def render_markdown(report: dict[str, Any]) -> str:
    runtime = report["runtime_probe"]
    lines = [
        "# Stage D Fanfic Import Isolation Audit",
        "",
        f"- Source module: `{report['source_module']}`",
        f"- Forbidden Daily-only modules: {len(report['forbidden_modules'])}",
        f"- Literal dynamic-import violations: {report['literal_dynamic_violation_count']}",
        f"- Runtime-loaded forbidden modules: {report['runtime_loaded_forbidden_count']}",
        f"- Parse/probe errors: {report['error_count']}",
        f"- Package-init witness loaded: {runtime['package_init_witness_loaded']}",
        "",
    ]
    if report["literal_dynamic_violations"]:
        lines.extend(["## Literal dynamic-import violations", ""])
        for item in report["literal_dynamic_violations"]:
            lines.append(
                f"- `{item['path']}:{item['line']}` — **{item['kind']}** — {item['detail']}"
            )
    if runtime["forbidden_loaded"]:
        lines.extend(["", "## Forbidden modules loaded by clean Fanfic import", ""])
        lines.extend(f"- `{module}`" for module in runtime["forbidden_loaded"])
    if report["parse_errors"]:
        lines.extend(["", "## Parse errors", ""])
        for item in report["parse_errors"]:
            lines.append(f"- `{item['path']}` — {item['error']}")
    if runtime["error"]:
        lines.extend(["", "## Runtime probe error", "", runtime["error"]])
    if report["violation_count"] == 0 and report["error_count"] == 0:
        lines.extend(
            [
                "Fanfic import isolation is currently clean:",
                "no literal dynamic bypass was found and a clean import of app.fic_digest did not eager-load the Daily-only shadow stack.",
            ]
        )
    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Report Fanfic/Daily shadow-stack import isolation evidence."
    )
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parser.add_argument("--json", type=Path)
    parser.add_argument("--markdown", type=Path)
    args = parser.parse_args()

    report = audit_repository(args.root)
    rendered = render_markdown(report)
    if args.json:
        args.json.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    if args.markdown:
        args.markdown.write_text(rendered, encoding="utf-8")
    if not args.json and not args.markdown:
        print(rendered, end="")

    if report["error_count"]:
        return 2
    if report["violation_count"]:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
