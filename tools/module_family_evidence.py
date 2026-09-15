from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any

HISTORICAL_NAME_RE = re.compile(
    r"(?:^|_)(?:phase\d+|part\d+|finalfix|humanfix|qualityfix|hardening)(?:_|$)",
    re.IGNORECASE,
)
ENTRYPOINT_MODULES = ("app", "app.__main__")


def module_name_from_path(path: Path, root: Path) -> str:
    relative = path.relative_to(root).with_suffix("")
    parts = list(relative.parts)
    if parts and parts[-1] == "__init__":
        parts.pop()
    return ".".join(parts)


def historical_modules(root: Path) -> list[str]:
    modules: list[str] = []
    app_root = root / "app"
    for path in sorted(app_root.rglob("*.py")):
        module = module_name_from_path(path, root)
        leaf = module.rsplit(".", 1)[-1]
        if HISTORICAL_NAME_RE.search(leaf):
            modules.append(module)
    return modules


def coverage_path_for_module(module: str) -> str:
    return module.replace(".", "/") + ".py"


def _is_test_context(context: str) -> bool:
    if not context:
        return False
    lowered = context.lower()
    return (
        lowered.startswith("test_")
        or lowered.startswith("tests.")
        or ".test_" in lowered
        or ".test" in lowered
    )


def coverage_evidence(module: str, coverage_payload: dict[str, Any] | None) -> dict[str, Any]:
    if not coverage_payload:
        return {
            "available": False,
            "percent_covered": None,
            "covered_lines": None,
            "statement_count": None,
            "test_context_count": None,
            "test_context_sample": [],
        }

    file_payload = coverage_payload.get("files", {}).get(coverage_path_for_module(module))
    if not isinstance(file_payload, dict):
        return {
            "available": True,
            "percent_covered": 0.0,
            "covered_lines": 0,
            "statement_count": 0,
            "test_context_count": 0,
            "test_context_sample": [],
        }

    summary = file_payload.get("summary", {})
    contexts = file_payload.get("contexts", {})
    test_contexts: set[str] = set()
    if isinstance(contexts, dict):
        for line_contexts in contexts.values():
            if not isinstance(line_contexts, list):
                continue
            for context in line_contexts:
                if isinstance(context, str) and _is_test_context(context):
                    test_contexts.add(context)

    percent = summary.get("percent_covered")
    return {
        "available": True,
        "percent_covered": round(float(percent), 2) if isinstance(percent, (int, float)) else None,
        "covered_lines": summary.get("covered_lines"),
        "statement_count": summary.get("num_statements"),
        "test_context_count": len(test_contexts),
        "test_context_sample": sorted(test_contexts)[:12],
    }


def _safe_set(graph: Any, method_name: str, module: str) -> list[str]:
    method = getattr(graph, method_name)
    try:
        return sorted(method(module))
    except (KeyError, ValueError):
        return []


def _safe_chain(graph: Any, importer: str, imported: str) -> list[str] | None:
    try:
        chain = graph.find_shortest_chain(importer=importer, imported=imported)
    except (KeyError, ValueError):
        return None
    if chain is None:
        return None
    return list(chain)


def graph_evidence(graph: Any, module: str) -> dict[str, Any]:
    direct_importers = _safe_set(graph, "find_modules_that_directly_import", module)
    direct_dependencies = _safe_set(graph, "find_modules_directly_imported_by", module)
    downstream = _safe_set(graph, "find_downstream_modules", module)
    upstream = _safe_set(graph, "find_upstream_modules", module)

    entrypoint_chains: dict[str, list[str]] = {}
    for entrypoint in ENTRYPOINT_MODULES:
        chain = _safe_chain(graph, entrypoint, module)
        if chain:
            entrypoint_chains[entrypoint] = chain

    return {
        "direct_importers": direct_importers,
        "direct_dependencies": direct_dependencies,
        "downstream_importer_count": len(downstream),
        "upstream_dependency_count": len(upstream),
        "entrypoint_chains": entrypoint_chains,
    }


def classify_review_hint(graph_data: dict[str, Any], coverage_data: dict[str, Any]) -> tuple[str, str]:
    if graph_data["entrypoint_chains"]:
        return (
            "high",
            "runtime-linked: preserve behavior/import order and require a compatibility plan before move or removal",
        )
    if graph_data["direct_importers"] or graph_data["downstream_importer_count"]:
        return (
            "medium",
            "internally-linked: inspect all importers and regression coverage before cleanup",
        )
    if coverage_data.get("test_context_count", 0):
        return (
            "medium",
            "test-exercised without a static app entrypoint chain: investigate dynamic/plugin/compatibility use",
        )
    if coverage_data.get("available"):
        return (
            "low-investigation",
            "lowest-risk investigation candidate only; absence of static/coverage evidence is not proof of dead code",
        )
    return (
        "unknown",
        "coverage evidence unavailable; do not infer dead code from static imports alone",
    )


def build_report(root: Path, coverage_payload: dict[str, Any] | None = None) -> dict[str, Any]:
    try:
        import grimp
    except ImportError as exc:  # pragma: no cover - maintenance environment owns this dependency.
        raise RuntimeError("grimp is required for Stage B module-family evidence") from exc

    graph = grimp.build_graph(
        "app",
        include_external_packages=False,
        exclude_type_checking_imports=True,
        cache_dir=None,
    )

    candidates: list[dict[str, Any]] = []
    for module in historical_modules(root):
        graph_data = graph_evidence(graph, module)
        coverage_data = coverage_evidence(module, coverage_payload)
        risk, hint = classify_review_hint(graph_data, coverage_data)
        candidates.append(
            {
                "module": module,
                "path": coverage_path_for_module(module),
                "risk": risk,
                "review_hint": hint,
                "graph": graph_data,
                "coverage": coverage_data,
            }
        )

    risk_order = {"low-investigation": 0, "unknown": 1, "medium": 2, "high": 3}
    candidates.sort(key=lambda item: (risk_order.get(str(item["risk"]), 9), str(item["module"])))

    return {
        "schema_version": 1,
        "coverage_available": coverage_payload is not None,
        "candidate_count": len(candidates),
        "policy": {
            "report_only": True,
            "absence_of_evidence_is_not_dead_code": True,
            "automatic_move_delete_or_rename": False,
        },
        "candidates": candidates,
    }


def render_markdown(report: dict[str, Any]) -> str:
    lines = [
        "# Stage B module-family evidence",
        "",
        f"Historical-name candidates: {report['candidate_count']}",
        f"Per-test coverage evidence available: {'yes' if report['coverage_available'] else 'no'}",
        "",
        "> This report prioritizes investigation only. It never proves that a module is obsolete or safe to delete.",
        "",
        "| Module | Risk | Direct importers | Downstream importers | Coverage | Test contexts |",
        "| --- | --- | ---: | ---: | ---: | ---: |",
    ]

    for candidate in report["candidates"]:
        graph_data = candidate["graph"]
        coverage_data = candidate["coverage"]
        percent = coverage_data["percent_covered"]
        coverage_text = "n/a" if percent is None else f"{percent:.1f}%"
        test_count = coverage_data["test_context_count"]
        test_text = "n/a" if test_count is None else str(test_count)
        lines.append(
            "| `{module}` | {risk} | {direct} | {downstream} | {coverage} | {tests} |".format(
                module=candidate["module"],
                risk=candidate["risk"],
                direct=len(graph_data["direct_importers"]),
                downstream=graph_data["downstream_importer_count"],
                coverage=coverage_text,
                tests=test_text,
            )
        )

    lines.extend(["", "## Review notes", ""])
    for candidate in report["candidates"]:
        lines.append(f"### `{candidate['module']}`")
        lines.append("")
        lines.append(f"- Risk: **{candidate['risk']}**")
        lines.append(f"- Guidance: {candidate['review_hint']}")
        graph_data = candidate["graph"]
        importers = graph_data["direct_importers"]
        lines.append(f"- Direct importers: {', '.join(f'`{item}`' for item in importers) if importers else 'none found'}")
        chains = graph_data["entrypoint_chains"]
        if chains:
            for entrypoint, chain in sorted(chains.items()):
                lines.append(f"- `{entrypoint}` chain: {' → '.join(f'`{item}`' for item in chain)}")
        coverage_data = candidate["coverage"]
        contexts = coverage_data["test_context_sample"]
        if contexts:
            lines.append("- Example test contexts: " + ", ".join(f"`{context}`" for context in contexts))
        lines.append("")

    return "\n".join(lines)


def load_coverage_payload(path: Path | None) -> dict[str, Any] | None:
    if path is None or not path.exists():
        return None
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("coverage JSON must contain an object")
    return payload


def main() -> int:
    parser = argparse.ArgumentParser(description="Build evidence for safely classifying Hani's historical module families.")
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parser.add_argument("--coverage-json", type=Path, default=Path("module-test-coverage.json"))
    parser.add_argument("--json", type=Path, default=Path("module-family-evidence.json"))
    parser.add_argument("--markdown", type=Path, default=Path("module-family-evidence.md"))
    args = parser.parse_args()

    root = args.root.resolve()
    coverage_payload = load_coverage_payload(args.coverage_json)
    report = build_report(root, coverage_payload)
    args.json.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    args.markdown.write_text(render_markdown(report), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
