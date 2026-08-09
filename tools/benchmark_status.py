from __future__ import annotations

import argparse
import json
import os
from pathlib import Path


class BenchmarkStatusError(RuntimeError):
    pass


def _has_quota_evidence(payload: dict) -> bool:
    for case in payload.get("cases", []):
        if not isinstance(case, dict):
            continue
        diagnostics = case.get("api_diagnostics", {})
        if not isinstance(diagnostics, dict):
            continue
        for pipeline in diagnostics.values():
            if isinstance(pipeline, dict) and (
                pipeline.get("quota_429") is True
                and (pipeline.get("api_status") == 429 or pipeline.get("exception_class") == "RESOURCE_EXHAUSTED")
            ):
                return True
    return False


def validate_and_record(path: Path, exit_code: int) -> str:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise BenchmarkStatusError("benchmark checkpoint is missing or malformed") from exc
    if not isinstance(payload, dict):
        raise BenchmarkStatusError("benchmark checkpoint must be a JSON object")

    if exit_code == 0:
        if payload.get("quality_status") != "PASS":
            raise BenchmarkStatusError("exit 0 requires quality_status=PASS")
        return "PASS"

    if exit_code != 3:
        raise BenchmarkStatusError(f"benchmark failed with exit code {exit_code}")
    if payload.get("quality_status") != "INCOMPLETE":
        raise BenchmarkStatusError("exit 3 is controlled only for an INCOMPLETE checkpoint")
    if not _has_quota_evidence(payload):
        raise BenchmarkStatusError("exit 3 checkpoint has no verified 429/RESOURCE_EXHAUSTED evidence")

    payload.update(
        {
            "execution_status": "BLOCKED_BY_EXTERNAL_QUOTA",
            "quota_blocked": True,
            "quality_status": "INCOMPLETE",
            "human_gate_status": "NOT_PASSED",
            "merge_authorization": "NOT_GRANTED",
        }
    )
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    temp.replace(path)
    return "BLOCKED_BY_EXTERNAL_QUOTA"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--checkpoint", type=Path, required=True)
    parser.add_argument("--exit-code", type=int, required=True)
    args = parser.parse_args()
    try:
        status = validate_and_record(args.checkpoint, args.exit_code)
    except BenchmarkStatusError as exc:
        print(f"Benchmark status validation failed: {exc}")
        return 2
    print(f"benchmark_status={status}")
    output = os.getenv("GITHUB_OUTPUT", "").strip()
    if output:
        with Path(output).open("a", encoding="utf-8") as handle:
            handle.write(f"status={status}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
