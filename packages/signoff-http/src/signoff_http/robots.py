"""robots.txt fetcher + evaluator with a per-host TTL cache.

Uses :mod:`urllib.robotparser` for parsing (stdlib, no surprises) but
fetches the file with a short-timeout :class:`httpx.AsyncClient` so
the robots check isn't gated on the main client's retry policy.

Stance (per ``docs/protocol.md`` §4.6 draft): if robots.txt is missing
(404), unreachable, or malformed, we **do not** restrict. This is the
conservative choice for a verifier — a site operator who actively
wants to block Signoff publishes a valid disallow; everyone else's
misconfiguration shouldn't cause false "blocked" failures in verdicts.
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit
from urllib.robotparser import RobotFileParser

import httpx

__all__ = ["RobotsChecker", "RobotsDecision"]


_logger = logging.getLogger("signoff_http.robots")


@dataclass(frozen=True, slots=True)
class RobotsDecision:
    """Outcome of a robots.txt lookup for one (host, path, UA) tuple."""

    allowed: bool
    #: Human-readable reason, recorded in ``FetchResult.error`` when a
    #: fetch is rejected. Empty string when allowed.
    reason: str = ""


@dataclass(slots=True)
class _HostEntry:
    parser: RobotFileParser
    #: Monotonic-seconds timestamp after which this entry is stale.
    expires_at: float
    #: True when the robots.txt fetch actually succeeded. Fetch
    #: failures still cache an "allow everything" parser (to avoid
    #: hammering broken hosts on every request) but are logged
    #: differently and a future health check can inspect this.
    reachable: bool


class RobotsChecker:
    """Per-host robots.txt cache with a configurable TTL.

    Construct one per :class:`HttpxClient`; it shares nothing across
    clients so tests don't have to fight global state. Safe to call
    from many coroutines concurrently — per-host locks serialise the
    first fetch so we don't stampede a host with concurrent GETs.
    """

    def __init__(
        self,
        *,
        user_agent: str,
        cache_seconds: float,
        fetch_timeout: float = 5.0,
        verify_tls: bool = True,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._user_agent = user_agent
        self._cache_seconds = cache_seconds
        self._fetch_timeout = fetch_timeout
        self._verify_tls = verify_tls
        # Test-only injection point; production callers leave this None.
        self._transport = transport
        self._cache: dict[str, _HostEntry] = {}
        self._locks: dict[str, asyncio.Lock] = {}
        self._global_lock = asyncio.Lock()

    async def check(self, url: str) -> RobotsDecision:
        """Return whether ``url`` is allowed for the configured UA."""
        parts = urlsplit(url)
        host_key = f"{parts.scheme}://{parts.netloc}".lower()
        path = parts.path or "/"
        if parts.query:
            path = f"{path}?{parts.query}"
        entry = await self._get_or_fetch(host_key)
        if entry.parser.can_fetch(self._user_agent, path):
            return RobotsDecision(allowed=True)
        return RobotsDecision(
            allowed=False,
            reason=f"robots.txt disallows {path} for {self._user_agent}",
        )

    async def _get_or_fetch(self, host_key: str) -> _HostEntry:
        now = time.monotonic()
        cached = self._cache.get(host_key)
        if cached is not None and cached.expires_at > now:
            return cached

        lock = await self._lock_for(host_key)
        async with lock:
            cached = self._cache.get(host_key)
            if cached is not None and cached.expires_at > time.monotonic():
                return cached
            entry = await self._fetch(host_key)
            self._cache[host_key] = entry
            return entry

    async def _lock_for(self, host_key: str) -> asyncio.Lock:
        async with self._global_lock:
            lock = self._locks.get(host_key)
            if lock is None:
                lock = asyncio.Lock()
                self._locks[host_key] = lock
            return lock

    async def _fetch(self, host_key: str) -> _HostEntry:
        parser = RobotFileParser()
        parser.set_url(f"{host_key}/robots.txt")
        reachable = False
        try:
            kwargs: dict[str, Any] = {
                "timeout": self._fetch_timeout,
                "follow_redirects": True,
                "headers": {"User-Agent": self._user_agent},
            }
            if self._transport is not None:
                kwargs["transport"] = self._transport
            else:
                kwargs["verify"] = self._verify_tls
            async with httpx.AsyncClient(**kwargs) as client:
                resp = await client.get(f"{host_key}/robots.txt")
            if resp.status_code == 200:
                parser.parse(resp.text.splitlines())
                reachable = True
            elif 400 <= resp.status_code < 500:
                # Missing / forbidden robots.txt = no rules.
                parser.parse([])
                reachable = True
            else:
                # 5xx: treat as "unknown" → allow, but log.
                _logger.warning(
                    "robots.txt for %s returned %s; defaulting to allow.",
                    host_key,
                    resp.status_code,
                )
                parser.parse([])
        except (httpx.HTTPError, OSError) as exc:
            _logger.warning(
                "robots.txt fetch for %s failed (%s); defaulting to allow.",
                host_key,
                exc.__class__.__name__,
            )
            parser.parse([])

        return _HostEntry(
            parser=parser,
            expires_at=time.monotonic() + self._cache_seconds,
            reachable=reachable,
        )
