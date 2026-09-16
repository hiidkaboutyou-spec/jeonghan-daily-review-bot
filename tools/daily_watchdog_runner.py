from __future__ import annotations

"""Stable semantic CLI entrypoint for the production Daily watchdog.

The transport hardening remains implemented by ``daily_watchdog_hardening``
during the compatibility phase. Importing that module installs the protected
artifact-fetch implementation on the canonical watchdog client. Keeping this
entrypoint deliberately thin lets the production workflow use a durable name
without duplicating or weakening the cross-origin credential boundary.
"""

try:
    from tools import daily_watchdog as _watchdog
    from tools import daily_watchdog_hardening as _transport_hardening  # noqa: F401
except ModuleNotFoundError:  # direct `python tools/...py` execution
    import daily_watchdog as _watchdog
    import daily_watchdog_hardening as _transport_hardening  # noqa: F401


def main() -> int:
    """Run the canonical watchdog after transport hardening is installed."""
    return _watchdog.main()


if __name__ == "__main__":
    raise SystemExit(main())
