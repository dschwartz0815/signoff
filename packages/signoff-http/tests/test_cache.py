"""Unit tests for :class:`signoff_http.cache.ResponseCache`."""

from __future__ import annotations

import asyncio

import pytest
from signoff import FetchResult
from signoff_http.cache import ResponseCache, normalize_url


def _ok(url: str = "https://a/x", status: int = 200) -> FetchResult:
    return FetchResult(
        ok=status < 400,
        status_code=status,
        url=url,
        text="hi",
        headers={},
        duration_ms=1,
    )


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("HTTPS://Example.COM/Foo", "https://example.com/Foo"),
        ("http://example.com:80/", "http://example.com/"),
        ("https://example.com:443/x?a=1#frag", "https://example.com/x?a=1"),
        ("https://example.com:8443/x", "https://example.com:8443/x"),
        ("https://example.com", "https://example.com/"),
    ],
)
def test_normalize_url(raw: str, expected: str) -> None:
    assert normalize_url(raw) == expected


async def test_cache_hit_then_miss_after_ttl() -> None:
    cache = ResponseCache(max_entries=10, ttl_seconds=0.05)
    await cache.put("https://a/x", _ok())
    hit = await cache.get("https://a/x")
    assert hit is not None
    await asyncio.sleep(0.08)
    assert await cache.get("https://a/x") is None


async def test_cache_ignores_non_ok() -> None:
    cache = ResponseCache(max_entries=10, ttl_seconds=60)
    await cache.put("https://a/x", _ok(status=500))
    assert await cache.get("https://a/x") is None


async def test_cache_lru_eviction() -> None:
    cache = ResponseCache(max_entries=2, ttl_seconds=60)
    await cache.put("https://a/1", _ok("https://a/1"))
    await cache.put("https://a/2", _ok("https://a/2"))
    await cache.get("https://a/1")  # promote
    await cache.put("https://a/3", _ok("https://a/3"))
    assert await cache.get("https://a/2") is None
    assert await cache.get("https://a/1") is not None
    assert await cache.get("https://a/3") is not None


async def test_cache_normalises_before_lookup() -> None:
    cache = ResponseCache(max_entries=10, ttl_seconds=60)
    await cache.put("https://EXAMPLE.com/x", _ok())
    assert await cache.get("https://example.com:443/x") is not None


async def test_cache_clear() -> None:
    cache = ResponseCache(max_entries=10, ttl_seconds=60)
    await cache.put("https://a/x", _ok())
    await cache.clear()
    assert await cache.get("https://a/x") is None
    assert len(cache) == 0
