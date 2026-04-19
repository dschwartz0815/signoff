"""Real ``httpx``-backed :class:`~signoff.HttpClient` implementation.

Design goals (in decreasing importance):

1. **Correctness of evidence.** Every :class:`FetchResult` records what
   actually happened — retries, redirects, cache hits, robots
   rejections — so verifier authors (and auditors reading the
   log) can reason about the outcome without re-running the request.
2. **Safe defaults.** Size caps, timeouts, TLS verification on,
   identifiable User-Agent. ``docs/protocol.md`` §4.6 draft.
3. **Never raise.** Transport failures become ``ok=False`` results
   with :attr:`FetchResult.error` populated. The only exceptions that
   escape are ``asyncio.CancelledError`` (must propagate) and
   programmer errors (bad URL type, closed client).
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Mapping
from types import TracebackType
from typing import Any, Literal

import httpx
from signoff import FetchResult

from signoff_http.cache import ResponseCache
from signoff_http.config import HttpxClientConfig
from signoff_http.retry import backoff_seconds, classify
from signoff_http.robots import RobotsChecker

__all__ = ["HttpxClient"]


_logger = logging.getLogger("signoff_http.client")


class HttpxClient:
    """Production HTTP client satisfying :class:`signoff.HttpClient`.

    Construct once per harness. The underlying :class:`httpx.AsyncClient`
    is created lazily on first use (or in :meth:`__aenter__`) and
    reused across every request so connection pooling works. Call
    :meth:`close` or use the async-context-manager form to release
    sockets.
    """

    def __init__(
        self,
        config: HttpxClientConfig | None = None,
        *,
        robots: RobotsChecker | None = None,
        cache: ResponseCache | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._config = config if config is not None else HttpxClientConfig()
        self._client: httpx.AsyncClient | None = None
        self._closed = False
        self._lock = asyncio.Lock()
        # ``transport`` is an escape hatch for tests — production code
        # should never pass it. Kept out of the docstring so the public
        # surface stays minimal.
        self._transport = transport

        if not self._config.verify_tls:
            _logger.warning(
                "HttpxClient starting with verify_tls=False — TLS certificate "
                "verification is disabled. Do not use this outside controlled "
                "test environments."
            )

        self._robots: RobotsChecker | None
        if self._config.respect_robots_txt:
            self._robots = robots or RobotsChecker(
                user_agent=self._config.user_agent,
                cache_seconds=float(self._config.robots_txt_cache_seconds),
                verify_tls=self._config.verify_tls,
            )
        else:
            self._robots = None

        self._cache: ResponseCache | None
        if self._config.cache_enabled:
            self._cache = cache or ResponseCache(
                max_entries=self._config.cache_max_entries,
                ttl_seconds=float(self._config.cache_ttl_seconds),
            )
        else:
            self._cache = None

    # -- lifecycle ----------------------------------------------------------

    async def __aenter__(self) -> HttpxClient:
        await self._ensure_client()
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        await self.close()

    async def close(self) -> None:
        """Close the underlying httpx client. Idempotent."""
        async with self._lock:
            if self._client is not None:
                await self._client.aclose()
                self._client = None
            self._closed = True

    async def _ensure_client(self) -> httpx.AsyncClient:
        if self._closed:
            raise RuntimeError("HttpxClient is closed")
        if self._client is not None:
            return self._client
        async with self._lock:
            if self._client is not None:
                return self._client
            cfg = self._config
            kwargs: dict[str, Any] = {
                "timeout": httpx.Timeout(
                    connect=cfg.connect_timeout,
                    read=cfg.read_timeout,
                    write=cfg.read_timeout,
                    pool=cfg.connect_timeout,
                ),
                "limits": httpx.Limits(
                    max_connections=cfg.max_connections,
                    max_keepalive_connections=cfg.max_keepalive_connections,
                    keepalive_expiry=cfg.keepalive_expiry,
                ),
                "follow_redirects": cfg.follow_redirects,
                "max_redirects": cfg.max_redirects,
                "headers": {"User-Agent": cfg.user_agent},
            }
            if self._transport is not None:
                kwargs["transport"] = self._transport
            else:
                kwargs["verify"] = cfg.verify_tls
            self._client = httpx.AsyncClient(**kwargs)
            return self._client

    # -- public HttpClient surface -----------------------------------------

    async def get(
        self,
        url: str,
        *,
        timeout: int = 10,
        headers: Mapping[str, str] | None = None,
    ) -> FetchResult:
        return await self._request(
            "GET",
            url,
            timeout=timeout,
            headers=headers,
            follow_redirects=self._config.follow_redirects,
            max_bytes=self._config.max_response_bytes,
        )

    async def head(
        self,
        url: str,
        *,
        timeout: int = 10,
        follow_redirects: bool = True,
    ) -> FetchResult:
        return await self._request(
            "HEAD",
            url,
            timeout=timeout,
            headers=None,
            follow_redirects=follow_redirects,
            max_bytes=self._config.max_response_bytes_head,
        )

    # -- internals ---------------------------------------------------------

    async def _request(
        self,
        method: Literal["GET", "HEAD"],
        url: str,
        *,
        timeout: int,
        headers: Mapping[str, str] | None,
        follow_redirects: bool,
        max_bytes: int,
    ) -> FetchResult:
        started = time.perf_counter()

        effective_timeout = self._clamp_timeout(float(timeout))
        merged_headers = self._merge_headers(headers)

        # Cache hit short-circuits robots and retries — if we served
        # this URL recently we already cleared both.
        if method == "GET" and self._cache is not None:
            cached = await self._cache.get(url)
            if cached is not None:
                return _mark_from_cache(cached)

        if self._robots is not None:
            robots_decision = await self._robots.check(url)
            if not robots_decision.allowed:
                return FetchResult(
                    ok=False,
                    status_code=0,
                    url=url,
                    text="",
                    headers={},
                    duration_ms=self._elapsed_ms(started),
                    error=robots_decision.reason,
                    attempts=0,
                )

        try:
            client = await self._ensure_client()
        except RuntimeError as exc:
            # Closed client is a programmer error, but we still prefer
            # a structured result so harness logs stay uniform.
            return FetchResult(
                ok=False,
                status_code=0,
                url=url,
                text="",
                headers={},
                duration_ms=self._elapsed_ms(started),
                error=str(exc),
                attempts=0,
            )

        max_attempts = self._config.max_retries + 1
        retries: list[dict[str, Any]] = []
        last_error: str | None = None

        for attempt in range(1, max_attempts + 1):
            remaining = max(0.05, self._config.total_timeout - (time.perf_counter() - started))
            per_attempt_timeout = min(effective_timeout, remaining)
            try:
                result = await self._single_attempt(
                    client,
                    method=method,
                    url=url,
                    headers=merged_headers,
                    follow_redirects=follow_redirects,
                    max_bytes=max_bytes,
                    timeout=per_attempt_timeout,
                    started=started,
                    attempt=attempt,
                    retries=retries,
                )
            except asyncio.CancelledError:
                raise
            except (httpx.HTTPError, OSError) as exc:
                decision = classify(method=method, response=None, exception=exc)
                last_error = f"{decision.reason}: {exc.__class__.__name__}"
                if (
                    not decision.should_retry
                    or attempt == max_attempts
                    or self._no_budget_left(started)
                ):
                    return FetchResult(
                        ok=False,
                        status_code=0,
                        url=url,
                        text="",
                        headers={},
                        duration_ms=self._elapsed_ms(started),
                        error=last_error,
                        attempts=attempt,
                    )
                await self._sleep_for_retry(
                    attempt=attempt,
                    decision_reason=decision.reason,
                    server_hint=decision.server_backoff,
                    retries=retries,
                    started=started,
                )
                continue

            # _single_attempt returned a FetchResult — either terminal
            # or a retry signal via ``result.error`` + status in the
            # retryable set.
            if result.ok:
                if method == "GET" and self._cache is not None:
                    await self._cache.put(url, result)
                return result
            decision = classify(method=method, response=_synthetic_response(result), exception=None)
            last_error = result.error or decision.reason
            if (
                not decision.should_retry
                or attempt == max_attempts
                or self._no_budget_left(started)
            ):
                return FetchResult(
                    ok=result.ok,
                    status_code=result.status_code,
                    url=result.url,
                    text=result.text,
                    headers=result.headers,
                    duration_ms=self._elapsed_ms(started),
                    error=last_error,
                    attempts=attempt,
                    final_url=result.final_url,
                )
            await self._sleep_for_retry(
                attempt=attempt,
                decision_reason=decision.reason,
                server_hint=decision.server_backoff,
                retries=retries,
                started=started,
            )

        # Loop exhausted without returning — should be unreachable, but
        # surface it explicitly rather than raising.
        return FetchResult(
            ok=False,
            status_code=0,
            url=url,
            text="",
            headers={},
            duration_ms=self._elapsed_ms(started),
            error=last_error or "exhausted_retries",
            attempts=max_attempts,
        )

    async def _single_attempt(
        self,
        client: httpx.AsyncClient,
        *,
        method: Literal["GET", "HEAD"],
        url: str,
        headers: dict[str, str],
        follow_redirects: bool,
        max_bytes: int,
        timeout: float,
        started: float,
        attempt: int,
        retries: list[dict[str, Any]],
    ) -> FetchResult:
        request = client.build_request(
            method,
            url,
            headers=headers,
            timeout=httpx.Timeout(
                connect=min(timeout, self._config.connect_timeout),
                read=timeout,
                write=timeout,
                pool=timeout,
            ),
        )
        response = await client.send(
            request, stream=(method == "GET"), follow_redirects=follow_redirects
        )
        try:
            if method == "HEAD":
                body = ""
                truncated = False
            else:
                body, truncated = await _read_bounded(response, max_bytes)
        finally:
            if method == "GET":
                await response.aclose()

        status = response.status_code
        final_url = str(response.url)
        headers_out = {k.lower(): v for k, v in response.headers.items()}

        error: str | None = None
        if truncated:
            error = f"response_exceeded_{max_bytes}_bytes"
        elif status >= 400:
            error = f"http_{status}"

        ok = status < 400 and not truncated
        result = FetchResult(
            ok=ok,
            status_code=status,
            url=url,
            text=body,
            headers=headers_out,
            duration_ms=self._elapsed_ms(started),
            error=error,
            attempts=attempt,
            final_url=final_url,
        )
        if retries:
            # Evidence is not part of FetchResult's declared fields,
            # but verifier authors who want attempt-by-attempt detail
            # can introspect via client instrumentation. For now the
            # audit trail lives in logs:
            _logger.debug(
                "HttpxClient %s %s succeeded on attempt %d after retries=%s",
                method,
                url,
                attempt,
                retries,
            )
        return result

    # -- helpers ------------------------------------------------------------

    def _clamp_timeout(self, requested: float) -> float:
        cap = self._config.total_timeout
        if requested > cap:
            _logger.debug(
                "Clamping per-request timeout %.2fs → %.2fs (SIGNOFF_HTTP_TOTAL_TIMEOUT).",
                requested,
                cap,
            )
            return cap
        return max(0.1, requested)

    def _merge_headers(self, headers: Mapping[str, str] | None) -> dict[str, str]:
        merged: dict[str, str] = {}
        if headers:
            for key, value in headers.items():
                if key.lower() == "user-agent":
                    # User-Agent is canonical per config; silently drop
                    # caller overrides so a single authoritative UA
                    # shows up in server logs.
                    _logger.debug(
                        "Ignoring caller-supplied User-Agent; using %s.",
                        self._config.user_agent,
                    )
                    continue
                merged[key] = value
        merged["User-Agent"] = self._config.user_agent
        return merged

    def _no_budget_left(self, started: float) -> bool:
        return (time.perf_counter() - started) >= self._config.total_timeout

    async def _sleep_for_retry(
        self,
        *,
        attempt: int,
        decision_reason: str,
        server_hint: float | None,
        retries: list[dict[str, Any]],
        started: float,
    ) -> None:
        delay = backoff_seconds(
            attempt=attempt,
            base=self._config.retry_backoff_base,
            factor=self._config.retry_backoff_factor,
            max_backoff=self._config.retry_max_backoff,
            server_hint=server_hint,
        )
        # Don't sleep past the total budget — shrink the delay or
        # skip the sleep so the next attempt still happens.
        elapsed = time.perf_counter() - started
        remaining = self._config.total_timeout - elapsed
        delay = max(0.0, min(delay, max(0.0, remaining - 0.05)))
        retries.append(
            {
                "attempt": attempt,
                "reason": decision_reason,
                "backoff_ms": int(delay * 1000),
            }
        )
        if delay > 0:
            await asyncio.sleep(delay)

    @staticmethod
    def _elapsed_ms(started: float) -> int:
        return int((time.perf_counter() - started) * 1000)


# ---------------------------------------------------------------------------
# Module-private helpers
# ---------------------------------------------------------------------------


async def _read_bounded(response: httpx.Response, limit: int) -> tuple[str, bool]:
    """Read up to ``limit`` bytes of ``response`` as text.

    Streams chunks so a server sending a 5 GB payload never materialises
    the whole thing in memory. Returns ``(text, truncated)``.
    """
    chunks: list[bytes] = []
    total = 0
    truncated = False
    async for chunk in response.aiter_bytes():
        total += len(chunk)
        if total > limit:
            truncated = True
            overshoot = total - limit
            chunks.append(chunk[: len(chunk) - overshoot])
            break
        chunks.append(chunk)
    encoding = response.encoding or "utf-8"
    try:
        text = b"".join(chunks).decode(encoding, errors="replace")
    except LookupError:
        text = b"".join(chunks).decode("utf-8", errors="replace")
    return text, truncated


def _mark_from_cache(result: FetchResult) -> FetchResult:
    return FetchResult(
        ok=result.ok,
        status_code=result.status_code,
        url=result.url,
        text=result.text,
        headers=result.headers,
        duration_ms=result.duration_ms,
        error=result.error,
        attempts=result.attempts,
        final_url=result.final_url,
        from_cache=True,
    )


class _StubResponse:
    """Minimal stand-in for :class:`httpx.Response` that satisfies
    :func:`signoff_http.retry.classify` for the response-based branch.

    We synthesise one from a :class:`FetchResult` when the request
    completed but the server returned a retryable status code, so the
    same classifier serves both the exception and response branches.
    """

    __slots__ = ("headers", "status_code")

    def __init__(self, status_code: int, headers: Mapping[str, str]) -> None:
        self.status_code = status_code
        self.headers = dict(headers)


def _synthetic_response(result: FetchResult) -> Any:
    return _StubResponse(result.status_code, result.headers)
