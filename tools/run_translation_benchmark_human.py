from __future__ import annotations

"""PART 4 benchmark entrypoint with explicit human-gate freshness wiring.

The cached runner is imported as its canonical module name first, then the
production human-gate layer is asked to patch benchmark resume/checkpoint
handling. This avoids the ``python -m`` ``__main__`` import-order edge that
previously allowed a completed pre-human-gate checkpoint to be reused unchanged.
The existing successful API stage cache remains intact.

A stale-writer refresh may legitimately need to re-process all benchmark cases.
The normal CI command historically used batch-size 1 with a 65-second cooldown,
which cannot finish 36 refreshed cases inside the 20-minute benchmark timeout.
For this human-gate entrypoint only, keep the same cooldown but enforce a
conservative minimum batch size of 4. Cached stages still avoid unnecessary API
calls, while genuinely new polish calls remain bounded and paced.
"""

import sys

from tools import run_translation_benchmark_cached as cached
from app import channel_part4_humanfix as humanfix


def _ensure_refresh_pacing(argv: list[str], *, minimum_batch_size: int = 4) -> None:
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


def main() -> int:
    humanfix._patch_cached_benchmark_resume()
    _ensure_refresh_pacing(sys.argv)
    return cached.main()


if __name__ == "__main__":
    raise SystemExit(main())
