from __future__ import annotations

"""Narrow import-order hook for the real PART 4 cached benchmark.

When Python executes ``python -m tools.run_translation_benchmark_cached``, the
module is initially present as ``__main__`` rather than under its canonical name.
That made the previous human-gate freshness patch miss the process and allowed a
pre-human-gate 36/36 checkpoint to be reused unchanged. This hook activates only
for that exact module execution and patches benchmark resume/checkpoint handling
before the cached runner reaches ``benchmark.main()``. Normal bot startup is not
changed.
"""

import json
import sys
from pathlib import Path

from .channel_part4_humanfix import HUMAN_GATE_FINGERPRINT


def _is_cached_benchmark_main() -> bool:
    main = sys.modules.get("__main__")
    spec = getattr(main, "__spec__", None)
    return getattr(spec, "name", None) == "tools.run_translation_benchmark_cached"


def install_for_current_process() -> bool:
    if not _is_cached_benchmark_main():
        return False

    from tools import run_translation_benchmark as benchmark

    if getattr(benchmark, "_human_gate_main_hook", False):
        return True

    original_load = benchmark._load_resume
    original_write = benchmark._write_checkpoint

    def load_resume(output_path: Path) -> list[dict]:
        if not output_path.exists():
            return []
        try:
            payload = json.loads(output_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return []
        if payload.get("production_writer_fingerprint") != HUMAN_GATE_FINGERPRINT:
            print("PART4 resume invalidated: production writer fingerprint changed", flush=True)
            return []
        return original_load(output_path)

    def write_checkpoint(output_path: Path, **kwargs):
        payload = original_write(output_path, **kwargs)
        payload["production_writer_fingerprint"] = HUMAN_GATE_FINGERPRINT
        output_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        return payload

    benchmark._load_resume = load_resume
    benchmark._write_checkpoint = write_checkpoint
    benchmark._human_gate_main_hook = True
    return True


install_for_current_process()
