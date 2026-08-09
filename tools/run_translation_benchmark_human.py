from __future__ import annotations

"""PART 4 benchmark entrypoint with explicit human-gate freshness wiring.

The cached runner is imported as its canonical module name first, then the
production human-gate layer is asked to patch benchmark resume/checkpoint
handling. This avoids the ``python -m`` ``__main__`` import-order edge that
previously allowed a completed pre-human-gate checkpoint to be reused unchanged.
The existing successful API stage cache remains intact.
"""

from tools import run_translation_benchmark_cached as cached
from app import channel_part4_humanfix as humanfix


def main() -> int:
    humanfix._patch_cached_benchmark_resume()
    return cached.main()


if __name__ == "__main__":
    raise SystemExit(main())
