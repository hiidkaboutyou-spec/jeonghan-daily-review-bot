from __future__ import annotations

"""Stable semantic CLI entrypoint for the production Daily watchdog.

The transport hardening remains implemented by ``daily_watchdog_hardening``
during the compatibility phase. The hardening module is loaded only when this
entrypoint executes, keeping ordinary imports side-effect free while preserving
the exact production credential boundary before the watchdog starts.
"""

try:
    from tools import daily_watchdog as _watchdog
except ModuleNotFoundError:  # direct `python tools/...py` execution
    import daily_watchdog as _watchdog


def _install_transport_hardening() -> None:
    try:
        from tools import daily_watchdog_hardening as _transport_hardening  # noqa: F401
    except ModuleNotFoundError:  # direct `python tools/...py` execution
        import daily_watchdog_hardening as _transport_hardening  # noqa: F401


def main() -> int:
    """Install protected artifact transport, then run the canonical watchdog."""
    _install_transport_hardening()
    return _watchdog.main()


if __name__ == "__main__":
    raise SystemExit(main())
