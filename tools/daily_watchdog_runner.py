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
        from tools import daily_watchdog_hardening as _transport_hardening
    except ModuleNotFoundError:  # direct `python tools/...py` execution
        import daily_watchdog_hardening as _transport_hardening

    # Do not rely solely on the compatibility module's import-time side effect:
    # module caching means a later caller may need to restore the protected
    # method explicitly. Assignment is idempotent and keeps execution order clear.
    _watchdog.GitHubActionsClient.fetch_latest_production_outcome = (
        _transport_hardening._fetch_latest_production_outcome
    )


def main() -> int:
    """Install protected artifact transport, then run the canonical watchdog."""
    _install_transport_hardening()
    return _watchdog.main()


if __name__ == "__main__":
    raise SystemExit(main())
