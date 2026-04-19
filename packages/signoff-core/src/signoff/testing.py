"""Test helpers shipped with ``signoff-core``.

Deterministic, in-memory :class:`HttpClient` and :class:`JudgeClient`
stand-ins for use in unit and integration tests of verifiers, packs,
and the harness. Intentionally exposed as a submodule rather than
re-exported from :mod:`signoff` — this is an opt-in testing surface,
not part of the runtime public API.
"""

from __future__ import annotations

from collections import deque
from collections.abc import Mapping

from signoff.context import FetchResult, JudgeResult

__all__ = ["FakeHttpClient", "FakeJudge"]


class FakeHttpClient:
    """Deterministic, programmable HTTP client.

    Pre-register responses keyed by URL. Unknown URLs raise
    :class:`LookupError` so tests surface unexpected traffic instead of
    silently returning defaults.
    """

    def __init__(self, responses: Mapping[str, FetchResult] | None = None) -> None:
        self._responses: dict[str, FetchResult] = dict(responses or {})
        self.calls: list[tuple[str, str]] = []  # (method, url)

    def register(self, url: str, response: FetchResult) -> None:
        """Add or replace a response for ``url``."""
        self._responses[url] = response

    async def get(
        self,
        url: str,
        *,
        timeout: int = 10,
        headers: Mapping[str, str] | None = None,
    ) -> FetchResult:
        self.calls.append(("GET", url))
        return self._lookup(url)

    async def head(
        self,
        url: str,
        *,
        timeout: int = 10,
        follow_redirects: bool = True,
    ) -> FetchResult:
        self.calls.append(("HEAD", url))
        return self._lookup(url)

    def _lookup(self, url: str) -> FetchResult:
        if url not in self._responses:
            raise LookupError(
                f"FakeHttpClient has no registered response for {url!r}. "
                f"Call .register(url, FetchResult(...)) before invoking."
            )
        return self._responses[url]


class FakeJudge:
    """Deterministic LLM-judge stand-in.

    Tests queue specific :class:`JudgeResult` s with :meth:`queue`;
    each call to :meth:`check_entailment` pops one. Once the queue is
    drained, subsequent calls fall back to the ``default`` supplied at
    construction, or raise :class:`LookupError` if no default was set.
    """

    def __init__(self, default: JudgeResult | None = None) -> None:
        self._queue: deque[JudgeResult] = deque()
        self._default = default
        self.calls: list[dict[str, str]] = []

    def queue(self, *results: JudgeResult) -> None:
        """Append ``results`` to the response queue in order."""
        self._queue.extend(results)

    async def check_entailment(self, *, claim: str, passage: str) -> JudgeResult:
        self.calls.append({"claim": claim, "passage": passage})
        if self._queue:
            return self._queue.popleft()
        if self._default is not None:
            return self._default
        raise LookupError(
            "FakeJudge queue is empty and no default was provided. "
            "Call .queue(JudgeResult(...)) or pass default=JudgeResult(...) to __init__."
        )
