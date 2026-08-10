from __future__ import annotations

"""PART 4 benchmark entrypoint for the exact production translation-v2 behavior.

The benchmark keeps its existing checkpoint/stage-cache machinery, but the NEW
writer is explicitly upgraded per instance to the same direct-v2 behavior used by
the private-review application. A distinct fingerprint prevents any pre-v2 case
from satisfying the human quality gate.
"""

import hashlib
import sys
from pathlib import Path

from tools import run_translation_benchmark_cached as cached
from tools import run_translation_benchmark as benchmark
from app import channel_part4_humanfix as humanfix
from app.channel_translation import ChannelStyleCaptionWriter as BaseChannelStyleCaptionWriter
from app.channel_translation_v2_install import install_direct_v2

ROOT = Path(__file__).resolve().parents[1]
_PRODUCTION_FINGERPRINT_PATHS = (
    ROOT / "app/channel_translation_v2_install.py",
    ROOT / "app/channel_translation_v2.py",
    ROOT / "app/channel_part4_humanfix.py",
    ROOT / "app/translation_safety.py",
    ROOT / "app/channel_entities.py",
    ROOT / "app/channel_quality.py",
    ROOT / "app/organizer.py",
    ROOT / "tools/run_translation_benchmark.py",
)


def _production_fingerprint(paths: tuple[Path, ...] = _PRODUCTION_FINGERPRINT_PATHS) -> str:
    """Bind completed benchmark cases to the code that produced and judged them."""
    digest = hashlib.sha256()
    for path in paths:
        digest.update(path.name.encode("utf-8"))
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return f"channel-direct-v2-human-gate-{digest.hexdigest()[:16]}"


class BenchmarkProductionWriter(BaseChannelStyleCaptionWriter):
    """Benchmark constructor that installs the exact production v2 instance behavior."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        install_direct_v2(self)


def _ensure_refresh_pacing(argv: list[str], *, minimum_batch_size: int = 3) -> None:
    for index, value in enumerate(argv):
        if value == "--batch-size" and index + 1 < len(argv):
            try:
                current = int(argv[index + 1])
            except ValueError:
                return
            if current < minimum_batch_size:
                argv[index + 1] = str(minimum_batch_size)
            return
        if value.startswith("--batch-size="):
            try:
                current = int(value.split("=", 1)[1])
            except ValueError:
                return
            if current < minimum_batch_size:
                argv[index] = f"--batch-size={minimum_batch_size}"
            return


def _install_v2_benchmark_behavior() -> None:
    # run_translation_benchmark.run() resolves this module symbol at execution time.
    benchmark.ChannelStyleCaptionWriter = BenchmarkProductionWriter

    original_retry = benchmark._new_needs_quota_retry
    original_mode = benchmark._output_mode

    def v2_retry(writer, messages):
        fallback = str(getattr(writer, "last_diagnostics", {}).get("fallback", ""))
        if fallback == "direct_generation_unavailable":
            return benchmark._is_quota_log(messages)
        return original_retry(writer, messages)

    def v2_output_mode(fallback, api_diag, new_output, source):
        if fallback == "direct_generation_unavailable":
            return "neutral_fallback"
        diagnostics_mode = ""
        # Successful direct-v2 outputs have no fallback and are styled evidence.
        if not fallback:
            return "styled"
        return original_mode(fallback, api_diag, new_output, source)

    benchmark._new_needs_quota_retry = v2_retry
    benchmark._output_mode = v2_output_mode


def main() -> int:
    # This is evidence for a different production pipeline; old completed cases must
    # be invalidated even if their old hard verifier happened to pass.
    humanfix.HUMAN_GATE_FINGERPRINT = _production_fingerprint()
    _install_v2_benchmark_behavior()
    humanfix._patch_cached_benchmark_resume()
    _ensure_refresh_pacing(sys.argv)
    return cached.main()


if __name__ == "__main__":
    raise SystemExit(main())
