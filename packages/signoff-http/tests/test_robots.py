"""Unit tests for :class:`signoff_http.robots.RobotsChecker`."""

from __future__ import annotations

import asyncio

import httpx
from signoff_http.robots import RobotsChecker


def _transport(
    text: str = "",
    *,
    status: int = 200,
    calls: list[str] | None = None,
    raise_exc: Exception | None = None,
) -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        if calls is not None:
            calls.append(str(request.url))
        if raise_exc is not None:
            raise raise_exc
        return httpx.Response(status, text=text)

    return httpx.MockTransport(handler)


async def test_robots_allow_default_when_200_empty() -> None:
    checker = RobotsChecker(
        user_agent="Signoff/0.0",
        cache_seconds=60,
        transport=_transport(""),
    )
    assert (await checker.check("https://example.com/x")).allowed is True


async def test_robots_disallow_matches() -> None:
    body = "User-agent: *\nDisallow: /private\n"
    checker = RobotsChecker(
        user_agent="Signoff/0.0",
        cache_seconds=60,
        transport=_transport(body),
    )
    assert (await checker.check("https://example.com/ok")).allowed is True
    decision = await checker.check("https://example.com/private/x")
    assert decision.allowed is False
    assert "private" in decision.reason
    assert "Signoff/0.0" in decision.reason


async def test_robots_missing_file_treated_as_allow() -> None:
    checker = RobotsChecker(
        user_agent="Signoff/0.0",
        cache_seconds=60,
        transport=_transport("", status=404),
    )
    assert (await checker.check("https://example.com/x")).allowed is True


async def test_robots_fetch_error_treated_as_allow() -> None:
    checker = RobotsChecker(
        user_agent="Signoff/0.0",
        cache_seconds=60,
        transport=_transport(raise_exc=httpx.ConnectError("boom")),
    )
    assert (await checker.check("https://example.com/x")).allowed is True


async def test_robots_cache_reuses_entry() -> None:
    calls: list[str] = []
    checker = RobotsChecker(
        user_agent="Signoff/0.0",
        cache_seconds=60,
        transport=_transport("User-agent: *\nDisallow: /x\n", calls=calls),
    )
    await checker.check("https://example.com/a")
    await checker.check("https://example.com/b")
    await checker.check("https://example.com/x")
    assert len(calls) == 1  # robots.txt fetched once.


async def test_robots_cache_expires() -> None:
    calls: list[str] = []
    checker = RobotsChecker(
        user_agent="Signoff/0.0",
        cache_seconds=0.05,
        transport=_transport("", calls=calls),
    )
    await checker.check("https://example.com/a")
    await asyncio.sleep(0.08)
    await checker.check("https://example.com/a")
    assert len(calls) == 2
