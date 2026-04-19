"""Unit tests for :class:`signoff_http.client.HttpxClient`."""

from __future__ import annotations

from collections.abc import Callable

import httpx
import pytest
from signoff_http import HttpxClient, HttpxClientConfig


def _cfg(**overrides: object) -> HttpxClientConfig:
    defaults: dict[str, object] = {
        "respect_robots_txt": False,
        "max_retries": 0,
        "retry_backoff_base": 0.0,
        "retry_max_backoff": 0.0,
        "connect_timeout": 1.0,
        "read_timeout": 1.0,
        "total_timeout": 5.0,
    }
    defaults.update(overrides)
    return HttpxClientConfig(**defaults)  # type: ignore[arg-type]


def _transport(handler: Callable[[httpx.Request], httpx.Response]) -> httpx.MockTransport:
    return httpx.MockTransport(handler)


# -- happy path ------------------------------------------------------------


async def test_get_success_returns_ok_true_with_body() -> None:
    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="hello world")

    async with HttpxClient(_cfg(), transport=_transport(handler)) as http:
        result = await http.get("https://example.com/")
    assert result.ok is True
    assert result.status_code == 200
    assert result.text == "hello world"
    assert result.attempts == 1
    assert result.final_url == "https://example.com/"
    assert result.from_cache is False
    assert result.error is None


async def test_head_success_no_body() -> None:
    def handler(req: httpx.Request) -> httpx.Response:
        assert req.method == "HEAD"
        return httpx.Response(200, headers={"content-length": "42"})

    async with HttpxClient(_cfg(), transport=_transport(handler)) as http:
        result = await http.head("https://example.com/")
    assert result.ok is True
    assert result.text == ""
    assert result.headers["content-length"] == "42"


async def test_user_agent_header_applied_and_overrides_ignored() -> None:
    seen: dict[str, str] = {}

    def handler(req: httpx.Request) -> httpx.Response:
        seen["user-agent"] = req.headers.get("user-agent", "")
        return httpx.Response(200, text="")

    async with HttpxClient(_cfg(user_agent="SigTest/1.0"), transport=_transport(handler)) as http:
        await http.get("https://example.com/", headers={"User-Agent": "caller"})
    assert seen["user-agent"] == "SigTest/1.0"


async def test_4xx_is_not_retried_and_error_captured() -> None:
    count = 0

    def handler(req: httpx.Request) -> httpx.Response:
        nonlocal count
        count += 1
        return httpx.Response(404, text="nope")

    async with HttpxClient(_cfg(max_retries=3), transport=_transport(handler)) as http:
        result = await http.get("https://example.com/")
    assert count == 1
    assert result.ok is False
    assert result.status_code == 404
    assert result.error == "http_404"
    assert result.attempts == 1


# -- retry behaviour -------------------------------------------------------


async def test_503_then_200_retries_and_succeeds() -> None:
    states = iter([503, 200])

    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(next(states), text="ok")

    async with HttpxClient(
        _cfg(max_retries=2), transport=_transport(handler)
    ) as http:
        result = await http.get("https://example.com/")
    assert result.ok is True
    assert result.status_code == 200
    assert result.attempts == 2


async def test_all_attempts_503_returns_last_status() -> None:
    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(503)

    async with HttpxClient(
        _cfg(max_retries=2), transport=_transport(handler)
    ) as http:
        result = await http.get("https://example.com/")
    assert result.ok is False
    assert result.status_code == 503
    assert result.attempts == 3
    assert result.error == "http_503"


async def test_connect_error_retried_then_surfaced() -> None:
    def handler(req: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("down")

    async with HttpxClient(
        _cfg(max_retries=1), transport=_transport(handler)
    ) as http:
        result = await http.get("https://example.com/")
    assert result.ok is False
    assert result.status_code == 0
    assert result.attempts == 2
    assert result.error is not None
    assert "connect_error" in result.error


# -- size bounds -----------------------------------------------------------


async def test_response_exceeding_size_cap_marked_truncated() -> None:
    body = b"A" * 2048

    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=body)

    async with HttpxClient(
        _cfg(max_response_bytes=1024), transport=_transport(handler)
    ) as http:
        result = await http.get("https://example.com/")
    assert result.ok is False
    assert result.error is not None
    assert "exceeded_1024" in result.error
    assert len(result.text) == 1024


# -- cache -----------------------------------------------------------------


async def test_cache_hit_sets_from_cache_flag_and_skips_network() -> None:
    count = 0

    def handler(req: httpx.Request) -> httpx.Response:
        nonlocal count
        count += 1
        return httpx.Response(200, text="hi")

    async with HttpxClient(
        _cfg(cache_enabled=True, cache_ttl_seconds=60),
        transport=_transport(handler),
    ) as http:
        first = await http.get("https://example.com/x")
        second = await http.get("https://example.com/x")
    assert count == 1
    assert first.from_cache is False
    assert second.from_cache is True
    assert second.text == "hi"


async def test_cache_does_not_store_failures() -> None:
    count = 0

    def handler(req: httpx.Request) -> httpx.Response:
        nonlocal count
        count += 1
        return httpx.Response(500)

    async with HttpxClient(
        _cfg(cache_enabled=True, max_retries=0),
        transport=_transport(handler),
    ) as http:
        await http.get("https://example.com/x")
        await http.get("https://example.com/x")
    assert count == 2


# -- robots.txt ------------------------------------------------------------


async def test_robots_disallow_skips_request() -> None:
    def robots_handler(req: httpx.Request) -> httpx.Response:
        if req.url.path == "/robots.txt":
            return httpx.Response(200, text="User-agent: *\nDisallow: /secret\n")
        return httpx.Response(200, text="should not be reached")

    config = HttpxClientConfig(
        respect_robots_txt=True,
        max_retries=0,
        connect_timeout=1.0,
        read_timeout=1.0,
        total_timeout=5.0,
    )
    from signoff_http.robots import RobotsChecker

    robots = RobotsChecker(
        user_agent=config.user_agent,
        cache_seconds=60,
        transport=_transport(robots_handler),
    )
    async with HttpxClient(
        config, robots=robots, transport=_transport(robots_handler)
    ) as http:
        result = await http.get("https://example.com/secret/x")
    assert result.ok is False
    assert result.error is not None
    assert "robots.txt disallows" in result.error
    assert result.attempts == 0


# -- lifecycle -------------------------------------------------------------


async def test_close_is_idempotent() -> None:
    http = HttpxClient(_cfg(), transport=_transport(lambda r: httpx.Response(200)))
    await http.close()
    await http.close()


async def test_calls_after_close_return_structured_error() -> None:
    http = HttpxClient(_cfg(), transport=_transport(lambda r: httpx.Response(200)))
    await http.close()
    result = await http.get("https://example.com/")
    assert result.ok is False
    assert result.error is not None
    assert "closed" in result.error.lower()


# -- timeout clamping ------------------------------------------------------


async def test_per_request_timeout_clamped_to_total_timeout(
    caplog: pytest.LogCaptureFixture,
) -> None:
    import logging

    caplog.set_level(logging.DEBUG, logger="signoff_http.client")

    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="")

    async with HttpxClient(
        _cfg(total_timeout=1.0), transport=_transport(handler)
    ) as http:
        await http.get("https://example.com/", timeout=30)
    assert any("Clamping" in rec.message for rec in caplog.records)
