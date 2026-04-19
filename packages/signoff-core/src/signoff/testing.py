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
from typing import Any

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
    each judge call pops one regardless of which method (entailment,
    policy, classify) was invoked — tests that care about the shape of
    the call can inspect :attr:`calls`, which records the method name
    and kwargs for every invocation. Once the queue is drained,
    subsequent calls fall back to ``default``, or raise
    :class:`LookupError` if no default was set.
    """

    def __init__(self, default: JudgeResult | None = None) -> None:
        self._queue: deque[JudgeResult] = deque()
        self._default = default
        self.calls: list[dict[str, Any]] = []

    def queue(self, *results: JudgeResult) -> None:
        """Append ``results`` to the response queue in order."""
        self._queue.extend(results)

    async def check_entailment(
        self,
        *,
        claim: str,
        passage: str,
        context: str | None = None,
    ) -> JudgeResult:
        self.calls.append(
            {
                "method": "check_entailment",
                "claim": claim,
                "passage": passage,
                "context": context,
            }
        )
        return self._pop("check_entailment")

    async def check_policy_compliance(
        self,
        *,
        output: str,
        policy: str,
        examples_of_violations: list[str] | None = None,
    ) -> JudgeResult:
        self.calls.append(
            {
                "method": "check_policy_compliance",
                "output": output,
                "policy": policy,
                "examples_of_violations": examples_of_violations,
            }
        )
        return self._pop("check_policy_compliance")

    async def classify(
        self,
        *,
        text: str,
        labels: list[str],
        rubric: str | None = None,
    ) -> JudgeResult:
        self.calls.append(
            {
                "method": "classify",
                "text": text,
                "labels": labels,
                "rubric": rubric,
            }
        )
        return self._pop("classify")

    def _pop(self, method_name: str) -> JudgeResult:
        if self._queue:
            return self._queue.popleft()
        if self._default is not None:
            return self._default
        raise LookupError(
            f"FakeJudge queue is empty and no default was provided for {method_name}(). "
            "Call .queue(JudgeResult(...)) or pass default=JudgeResult(...) to __init__."
        )
