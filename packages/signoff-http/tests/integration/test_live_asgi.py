"""Integration tests that run HttpxClient against an in-process ASGI app.

Uses :class:`httpx.ASGITransport` so we never touch a real socket — tests
still exercise the full request path (headers, streaming, redirects,
size caps) but stay hermetic and fast.
"""

from __future__ import annotations

import pytest

pytest.importorskip("starlette")

import httpx
from signoff_http import HttpxClient, HttpxClientConfig
from signoff_http.robots import RobotsChecker
from starlette.applications import Starlette
from starlette.responses import PlainTextResponse, RedirectResponse, Response
from starlette.routing import Route

pytestmark = pytest.mark.integration


async def _ok(_request: object) -> PlainTextResponse:
    return PlainTextResponse("hello world")


async def _big(_request: object) -> Response:
    return Response("X" * 4096, media_type="text/plain")


async def _redir(_request: object) -> RedirectResponse:
    return RedirectResponse(url="/ok", status_code=302)


async def _robots(_request: object) -> PlainTextResponse:
    return PlainTextResponse("User-agent: *\nDisallow: /blocked\n")


def _app() -> Starlette:
    return Starlette(
        routes=[
            Route("/ok", _ok),
            Route("/big", _big),
            Route("/redir", _redir),
            Route("/robots.txt", _robots),
            Route("/blocked", _ok),
        ]
    )


def _transport() -> httpx.ASGITransport:
    return httpx.ASGITransport(app=_app())


def _cfg(**overrides: object) -> HttpxClientConfig:
    defaults: dict[str, object] = {
        "respect_robots_txt": False,
        "max_retries": 0,
        "connect_timeout": 1.0,
        "read_timeout": 1.0,
        "total_timeout": 5.0,
    }
    defaults.update(overrides)
    return HttpxClientConfig(**defaults)  # type: ignore[arg-type]


async def test_basic_get_against_asgi_app() -> None:
    async with HttpxClient(_cfg(), transport=_transport()) as http:
        result = await http.get("http://testserver/ok")
    assert result.ok is True
    assert result.text == "hello world"
    assert result.status_code == 200


async def test_redirect_records_final_url() -> None:
    async with HttpxClient(_cfg(), transport=_transport()) as http:
        result = await http.get("http://testserver/redir")
    assert result.ok is True
    assert result.final_url.endswith("/ok")
    assert result.text == "hello world"


async def test_size_cap_enforced_against_real_streamed_response() -> None:
    async with HttpxClient(
        _cfg(max_response_bytes=1024), transport=_transport()
    ) as http:
        result = await http.get("http://testserver/big")
    assert result.ok is False
    assert len(result.text) == 1024
    assert result.error is not None and "exceeded_1024" in result.error


async def test_robots_disallow_blocks_against_real_parser() -> None:
    config = HttpxClientConfig(
        respect_robots_txt=True,
        max_retries=0,
        connect_timeout=1.0,
        read_timeout=1.0,
        total_timeout=5.0,
    )
    robots = RobotsChecker(
        user_agent=config.user_agent,
        cache_seconds=60,
        transport=_transport(),
    )
    async with HttpxClient(config, robots=robots, transport=_transport()) as http:
        result = await http.get("http://testserver/blocked")
    assert result.ok is False
    assert result.error is not None and "robots.txt disallows" in result.error
