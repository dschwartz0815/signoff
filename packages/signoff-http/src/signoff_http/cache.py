"""Optional LRU + TTL response cache for :class:`HttpxClient`.

Disabled by default (see :attr:`HttpxClientConfig.cache_enabled`).
Stores only successful GET responses — HEAD is small and uncommon
enough that caching it isn't worth the extra surface area.

URL normalization (lowercase scheme+host, strip default ports, drop
fragment) is applied before the cache key is computed so trivial
variants share an entry.
"""

from __future__ import annotations

import asyncio
import time
from collections import OrderedDict
from dataclasses import dataclass
from typing import TYPE_CHECKING
from urllib.parse import urlsplit, urlunsplit

if TYPE_CHECKING:
    from signoff import FetchResult

__all__ = ["ResponseCache", "normalize_url"]


_DEFAULT_PORTS = {"http": "80", "https": "443"}


def normalize_url(url: str) -> str:
    """Return a canonical form of ``url`` for cache-key purposes.

    Lowercases the scheme and host, strips the fragment, and removes
    the port if it matches the scheme's default. Leaves the path,
    query, and case of path components alone — cache equivalence
    across those would require per-site knowledge we don't have.
    """
    parts = urlsplit(url)
    scheme = parts.scheme.lower()
    host = (parts.hostname or "").lower()
    port = parts.port
    netloc = host
    if parts.username or parts.password:
        userinfo = parts.username or ""
        if parts.password is not None:
            userinfo = f"{userinfo}:{parts.password}"
            netloc = f"{userinfo}@{host}"
    if port is not None and str(port) != _DEFAULT_PORTS.get(scheme):
        netloc = f"{netloc}:{port}"
    return urlunsplit((scheme, netloc, parts.path or "/", parts.query, ""))


@dataclass(slots=True)
class _Entry:
    value: FetchResult
    expires_at: float


class ResponseCache:
    """Async-safe bounded TTL cache keyed by normalised URL.

    Eviction is strict LRU up to :attr:`max_entries`; entries past
    their TTL are discarded on access. Only responses with ``ok=True``
    and ``status_code < 400`` are stored (:meth:`put` short-circuits).
    """

    def __init__(self, *, max_entries: int, ttl_seconds: float) -> None:
        if max_entries < 1:
            raise ValueError("max_entries must be >= 1")
        self._max_entries = max_entries
        self._ttl = ttl_seconds
        self._items: OrderedDict[str, _Entry] = OrderedDict()
        self._lock = asyncio.Lock()

    async def get(self, url: str) -> FetchResult | None:
        key = normalize_url(url)
        async with self._lock:
            entry = self._items.get(key)
            if entry is None:
                return None
            if entry.expires_at <= time.monotonic():
                del self._items[key]
                return None
            self._items.move_to_end(key)
            return entry.value

    async def put(self, url: str, value: FetchResult) -> None:
        if not value.ok or value.status_code >= 400:
            return
        key = normalize_url(url)
        async with self._lock:
            self._items[key] = _Entry(value=value, expires_at=time.monotonic() + self._ttl)
            self._items.move_to_end(key)
            while len(self._items) > self._max_entries:
                self._items.popitem(last=False)

    async def clear(self) -> None:
        async with self._lock:
            self._items.clear()

    def __len__(self) -> int:
        return len(self._items)
