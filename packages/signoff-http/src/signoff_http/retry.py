"""Retry classification and backoff calculation for :class:`HttpxClient`.

Keep this module pure: no I/O, no ``httpx.AsyncClient`` reference, no
``asyncio`` sleeps. That way the retry policy is exhaustively
testable as a state machine without standing up a server.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

import httpx

__all__ = [
    "RETRYABLE_STATUS_CODES",
    "RetryDecision",
    "backoff_seconds",
    "classify",
    "parse_retry_after",
]


#: Status codes that are eligible for retry on idempotent requests. 429
#: is included because servers that mean "slow down" respond with it
#: and usually include ``Retry-After``.
RETRYABLE_STATUS_CODES: frozenset[int] = frozenset({429, 502, 503, 504})


@dataclass(frozen=True, slots=True)
class RetryDecision:
    """Outcome of evaluating one attempt against the retry policy.

    ``reason`` is the short tag recorded in
    :attr:`FetchResult.evidence` (e.g. ``"http_503"``,
    ``"connect_timeout"``); no free-form text, to keep the audit log
    grep-friendly.
    """

    should_retry: bool
    reason: str
    #: Server-requested backoff in seconds (from ``Retry-After`` header).
    #: ``None`` means "use exponential backoff". The caller still clamps
    #: against :attr:`HttpxClientConfig.retry_max_backoff`.
    server_backoff: float | None = None


def classify(
    *,
    method: Literal["GET", "HEAD"],
    response: httpx.Response | None,
    exception: BaseException | None,
) -> RetryDecision:
    """Decide whether to retry a single HTTP attempt.

    Only ``GET`` and ``HEAD`` are retried — they're the only methods
    :class:`HttpxClient` exposes and both are idempotent. The ``method``
    parameter is there so a future ``POST`` opt-in has a single place
    to change.
    """
    if exception is not None:
        if isinstance(exception, httpx.ConnectTimeout | httpx.ReadTimeout):
            return RetryDecision(
                should_retry=True,
                reason="connect_timeout"
                if isinstance(exception, httpx.ConnectTimeout)
                else "read_timeout",
            )
        if isinstance(exception, httpx.ConnectError):
            return RetryDecision(should_retry=True, reason="connect_error")
        if isinstance(exception, httpx.RemoteProtocolError):
            return RetryDecision(should_retry=True, reason="protocol_error")
        # TLS / DNS / programming errors are non-retryable; surface
        # the class name so the audit log is still useful.
        return RetryDecision(should_retry=False, reason=f"non_retryable_{type(exception).__name__}")

    assert response is not None  # exhaustive with the branch above
    status = response.status_code
    if status in RETRYABLE_STATUS_CODES:
        server = parse_retry_after(response.headers.get("retry-after"))
        return RetryDecision(should_retry=True, reason=f"http_{status}", server_backoff=server)
    # Everything else (2xx/3xx/4xx other than 429) is terminal.
    return RetryDecision(should_retry=False, reason=f"http_{status}")


def parse_retry_after(header: str | None) -> float | None:
    """Parse the ``Retry-After`` header as a delta in seconds.

    RFC 7231 allows either an integer delta-seconds or an HTTP-date.
    We only honour delta-seconds here — HTTP-date handling would pull
    in timezone parsing for a tiny minority of real-world responses,
    and the fallback (exponential backoff) is already sane.
    """
    if not header:
        return None
    try:
        value = float(header.strip())
    except ValueError:
        return None
    if value < 0:
        return None
    return value


def backoff_seconds(
    *,
    attempt: int,
    base: float,
    factor: float,
    max_backoff: float,
    server_hint: float | None = None,
) -> float:
    """Compute the delay before retrying attempt ``attempt`` (1-indexed).

    When the server supplies a ``Retry-After`` it wins, clamped by
    ``max_backoff``. Otherwise: ``base * factor**(attempt - 1)``,
    clamped. No jitter — we'd add it if we were hammering a single
    host at scale, but for verifier workloads (a handful of checks per
    deliverable) deterministic timing makes tests far easier to write.
    """
    if server_hint is not None:
        return min(server_hint, max_backoff)
    if attempt < 1:
        attempt = 1
    delay = base * (factor ** (attempt - 1))
    return min(delay, max_backoff)
