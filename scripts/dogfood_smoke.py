"""Live-network smoke for :class:`signoff_http.HttpxClient`.

Usage:

    uv run python scripts/dogfood_smoke.py https://example.com/

Reads ``SIGNOFF_HTTP_*`` env vars the same way any deployment would —
so it doubles as a quick confirmation that an env override actually
reached the client. Prints a single-line JSON-ish summary per URL plus
the elapsed wall-clock time.

This script is intentionally tiny: it is evidence, not a tool. Don't
grow it into a CLI.
"""

from __future__ import annotations

import asyncio
import logging
import sys
import time

from signoff_http import HttpxClient, HttpxClientConfig


async def _one(http: HttpxClient, url: str) -> None:
    started = time.perf_counter()
    result = await http.get(url)
    elapsed_ms = int((time.perf_counter() - started) * 1000)
    print(
        f"{url}  →  ok={result.ok}  status={result.status_code}  "
        f"attempts={result.attempts}  from_cache={result.from_cache}  "
        f"error={result.error!r}  bytes={len(result.text)}  "
        f"elapsed_ms={elapsed_ms}"
    )


async def _main(urls: list[str]) -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    config = HttpxClientConfig()
    print(
        "HttpxClient config: "
        f"connect_timeout={config.connect_timeout} "
        f"read_timeout={config.read_timeout} "
        f"total_timeout={config.total_timeout} "
        f"max_retries={config.max_retries} "
        f"respect_robots_txt={config.respect_robots_txt} "
        f"verify_tls={config.verify_tls} "
        f"cache_enabled={config.cache_enabled}"
    )
    async with HttpxClient(config) as http:
        for url in urls:
            await _one(http, url)
    return 0


if __name__ == "__main__":
    argv = sys.argv[1:] or ["https://example.com/"]
    sys.exit(asyncio.run(_main(argv)))
