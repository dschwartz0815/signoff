"""CLI for the Signoff MCP server.

Usage examples::

    python -m signoff_mcp                           # stdio, ./signoff.yaml
    python -m signoff_mcp --config path/to.yaml     # custom config
    python -m signoff_mcp --transport http --port 8765
    python -m signoff_mcp --health                  # probe a running HTTP server

The ``signoff-mcp`` console script (declared in ``pyproject.toml``)
points here so ``signoff-mcp [...]`` works without ``python -m``.
"""

from __future__ import annotations

import argparse
import asyncio
import sys

from signoff_mcp import __version__
from signoff_mcp.server import serve


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="signoff-mcp",
        description="Signoff MCP server — exposes the Harness over MCP.",
    )
    parser.add_argument(
        "--config",
        default="signoff.yaml",
        help="Path to the harness YAML config (default: %(default)s).",
    )
    parser.add_argument(
        "--transport",
        choices=["stdio", "http"],
        default="stdio",
        help="Transport to expose (default: %(default)s).",
    )
    parser.add_argument(
        "--host",
        default="127.0.0.1",
        help="Bind address for the HTTP transport (default: %(default)s).",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=8765,
        help="Port for the HTTP transport (default: %(default)s).",
    )
    parser.add_argument(
        "--health",
        action="store_true",
        help=(
            "Probe a running HTTP server's /health endpoint and exit 0 if "
            "healthy, 1 otherwise. Not applicable to stdio."
        ),
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"signoff-mcp {__version__}",
    )
    return parser


def _run_health_probe(transport: str, host: str, port: int) -> int:
    if transport != "http":
        sys.stderr.write("--health requires --transport http (stdio has no health endpoint).\n")
        return 1
    import httpx

    url = f"http://{host}:{port}/health"
    try:
        resp = httpx.get(url, timeout=5)
    except Exception as exc:
        sys.stderr.write(f"health probe failed: {type(exc).__name__}: {exc}\n")
        return 1
    if resp.status_code != 200:
        sys.stderr.write(f"health probe {url}: HTTP {resp.status_code}\n")
        return 1
    sys.stdout.write(f"{resp.text}\n")
    return 0


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    if args.health:
        return _run_health_probe(args.transport, args.host, args.port)
    try:
        asyncio.run(
            serve(
                config_path=args.config,
                transport=args.transport,
                host=args.host,
                port=args.port,
            )
        )
    except KeyboardInterrupt:
        return 130
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
