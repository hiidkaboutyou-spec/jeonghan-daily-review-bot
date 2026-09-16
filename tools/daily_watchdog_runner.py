from __future__ import annotations

"""Stable semantic CLI entrypoint for the production Daily watchdog.

Artifact transport is installed explicitly from ``daily_watchdog_transport``
before the watchdog decision engine starts. Keeping installation in the runner
makes ordinary imports side-effect free while preserving the production
credential boundary.
"""

try:
    from tools import daily_watchdog as _watchdog
    from tools import daily_watchdog_transport as _transport
except ModuleNotFoundError:  # direct `python tools/...py` execution
    import daily_watchdog as _watchdog
    import daily_watchdog_transport as _transport


def _install_transport_hardening() -> None:
    """Install the canonical credential-safe artifact transport idempotently."""
    _transport.install()


def main() -> int:
    """Install protected artifact transport, then run the canonical watchdog."""
    _install_transport_hardening()
    return _watchdog.main()


if __name__ == "__main__":
    raise SystemExit(main())
