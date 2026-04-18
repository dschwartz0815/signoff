"""Entry point for the Signoff MCP server.

Phase 0 scaffold: supports ``--health`` and ``--version`` probes, and
otherwise blocks on a signal so the container stays up for
``docker compose up -d``. Real server wiring (stdio/HTTP MCP transport
and the ``request_signoff`` / ``list_verifiers`` tools per
``docs/protocol.md`` §7.3) lands in a follow-up PR.
"""

from __future__ import annotations

import signal
import sys
import threading

from signoff_mcp import __version__


def _run_scaffold() -> int:
    """Block until SIGTERM/SIGINT so the container stays alive in dev.

    Replace with the real MCP server loop when the protocol surface lands.
    """
    print(
        f"signoff-mcp {__version__}: scaffold mode (no tool surface wired yet). "
        "See docs/protocol.md §7.3.",
        flush=True,
    )
    stop = threading.Event()

    def _handle(_signum: int, _frame: object) -> None:
        stop.set()

    signal.signal(signal.SIGTERM, _handle)
    signal.signal(signal.SIGINT, _handle)
    stop.wait()
    return 0


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    if "--health" in args:
        print(f"signoff-mcp {__version__} healthy")
        return 0
    if "--version" in args:
        print(__version__)
        return 0
    return _run_scaffold()


if __name__ == "__main__":
    raise SystemExit(main())
