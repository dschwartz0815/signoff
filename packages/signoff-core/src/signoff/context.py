"""Execution context handed to every verifier.

Implements the contract in ``docs/protocol.md`` §4.3 plus the
runtime-level hooks described in ``CLAUDE.md`` §8.4 (``ctx.exec``).

What belongs here vs in ``signoff.runtime``
-------------------------------------------
``signoff.runtime`` owns *where and how* a verifier executes. This
module owns *what the verifier sees* while it's running: a deliverable,
a claim, a workspace, HTTP + judge clients, a logger, a budget, and a
pair of result constructors (:meth:`VerifierContext.ok` /
:meth:`VerifierContext.fail`).

Because the context carries non-serialisable references (clients,
loggers), it is a plain ``@dataclass(slots=True)`` rather than a
Pydantic model.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from collections.abc import Awaitable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

from signoff.models import Claim, Deliverable, Severity, VerifierResult

if TYPE_CHECKING:
    from signoff.runtime.base import RuntimePolicy, VerifierMeta


__all__ = [
    "ExecResult",
    "FetchResult",
    "HttpClient",
    "JudgeClient",
    "JudgeResult",
    "VerifierContext",
    "make_context",
]


_logger = logging.getLogger("signoff.context")


# ---------------------------------------------------------------------------
# Result types for ctx.exec / ctx.fetch / ctx.judge
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ExecResult:
    """Outcome of a :meth:`VerifierContext.exec` invocation."""

    exit_code: int
    stdout: str
    stderr: str
    duration_ms: int


@dataclass(frozen=True, slots=True)
class FetchResult:
    """Outcome of a :meth:`HttpClient.get` / ``.head`` call.

    ``ok`` is ``True`` when the HTTP layer reached the server and the
    response doesn't indicate failure (status < 400, no transport error,
    no policy rejection). ``error`` carries a human-readable reason
    when ``ok`` is False — useful both for verifier authors writing
    :meth:`VerifierContext.fail` messages and for the harness when a
    fetch fails outside a verifier's own logic.
    """

    ok: bool
    status_code: int
    url: str
    text: str
    headers: Mapping[str, str]
    duration_ms: int
    error: str | None = None
    attempts: int = 1
    #: URL after following redirects. When empty at construction, falls
    #: back to ``url`` in ``__post_init__`` so legacy callers get a
    #: sensible value without changing the constructor.
    final_url: str = ""
    from_cache: bool = False

    def __post_init__(self) -> None:
        if not self.final_url:
            object.__setattr__(self, "final_url", self.url)


@dataclass(frozen=True, slots=True)
class JudgeResult:
    """Outcome of an LLM-judge invocation.

    ``label`` is domain-specific per method:

    - :meth:`JudgeClient.check_entailment` →
      ``"supported" | "contradicted" | "not_addressed"``.
    - :meth:`JudgeClient.check_policy_compliance` →
      ``"compliant" | "violation"``.
    - :meth:`JudgeClient.classify` → one of the caller-supplied
      ``labels`` list.

    ``model`` and ``prompt_version`` are mandatory for audit: a verdict
    that relied on an LLM should always be re-traceable to *which*
    model and *which* prompt produced it, even if the deployment later
    swaps either out. ``raw_response`` keeps the provider's decoded
    response around for debugging — optional, because fakes and caches
    won't always have it.

    ``excerpt`` defaults to ``None`` and is only populated when the
    judge relied on a verbatim quote from the input passage. The
    entailment prompt in ``signoff-judge`` requires the model to emit
    one for ``supported`` labels.

    Backwards-compatibility: every field added since the first
    :class:`JudgeResult` landing has a default, so existing
    ``JudgeResult(label=..., explanation=..., excerpt=..., cost_usd=...)``
    call sites keep working.
    """

    label: str
    explanation: str
    excerpt: str | None
    cost_usd: float
    confidence: float = 1.0
    model: str = ""
    prompt_version: str = ""
    raw_response: Mapping[str, Any] | None = None


# ---------------------------------------------------------------------------
# Client protocols — real implementations arrive in follow-up PRs
# ---------------------------------------------------------------------------


@runtime_checkable
class HttpClient(Protocol):
    """Rate-limited async HTTP client (protocol §4.3).

    Real httpx-backed implementation lands in a follow-up PR. Tests use
    :class:`signoff.testing.FakeHttpClient`.
    """

    async def get(
        self,
        url: str,
        *,
        timeout: int = 10,
        headers: Mapping[str, str] | None = None,
    ) -> FetchResult: ...

    async def head(
        self,
        url: str,
        *,
        timeout: int = 10,
        follow_redirects: bool = True,
    ) -> FetchResult: ...


@runtime_checkable
class JudgeClient(Protocol):
    """LLM judge client (protocol §4.3).

    Three methods, each returning a :class:`JudgeResult` with a
    method-specific set of legal ``label`` values:

    - :meth:`check_entailment` — does ``passage`` support ``claim``?
      Labels: ``supported`` | ``contradicted`` | ``not_addressed``.
    - :meth:`check_policy_compliance` — does ``output`` comply with
      ``policy``? Labels: ``compliant`` | ``violation``.
    - :meth:`classify` — general-purpose labelling against a
      caller-supplied ``labels`` list.

    Real Anthropic/OpenAI-backed implementations live in
    :mod:`signoff_judge`. Tests use :class:`signoff.testing.FakeJudge`.
    """

    async def check_entailment(
        self,
        *,
        claim: str,
        passage: str,
        context: str | None = None,
    ) -> JudgeResult: ...

    async def check_policy_compliance(
        self,
        *,
        output: str,
        policy: str,
        examples_of_violations: list[str] | None = None,
    ) -> JudgeResult: ...

    async def classify(
        self,
        *,
        text: str,
        labels: list[str],
        rubric: str | None = None,
    ) -> JudgeResult: ...


# ---------------------------------------------------------------------------
# VerifierContext
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class VerifierContext:
    """Implements ``docs/protocol.md`` §4.3 VerifierContext.

    Constructed per verifier invocation by the harness. The runtime
    stamps ``current_verifier_meta`` and ``current_claim`` immediately
    before dispatching ``fn(claim, ctx)`` so :meth:`ok` / :meth:`fail`
    can fill in ``verifier`` and ``claim_id``.
    """

    deliverable: Deliverable
    http: HttpClient
    judge: JudgeClient
    policy: RuntimePolicy
    workspace: Path = field(default_factory=Path.cwd)
    logger: logging.Logger = field(default_factory=lambda: logging.getLogger("signoff.verifier"))
    budget_remaining_usd: float = 0.0
    current_verifier_meta: VerifierMeta | None = None
    current_claim: Claim | None = None

    # -- result constructors ------------------------------------------------

    def ok(self, **overrides: Any) -> VerifierResult:
        """Construct a passing :class:`VerifierResult`.

        Fills ``verifier`` from :attr:`current_verifier_meta`,
        ``claim_id`` from :attr:`current_claim`, defaults
        ``severity=Severity.INFO``, ``passed=True``,
        ``reason='Verified successfully'``, ``cost_usd=0.0``,
        ``duration_ms=0`` (the runtime overwrites ``duration_ms``).
        Any of those can be overridden via ``**overrides``.
        """
        payload = self._base_result_payload()
        payload.update(
            passed=True,
            severity=Severity.INFO,
            reason="Verified successfully",
            cost_usd=0.0,
            duration_ms=0,
        )
        payload.update(overrides)
        return VerifierResult.model_validate(payload)

    def fail(
        self,
        reason: str,
        suggestion: str | None = None,
        severity: Severity = Severity.BLOCKER,
        **overrides: Any,
    ) -> VerifierResult:
        """Construct a failing :class:`VerifierResult`.

        Enforces ``docs/protocol.md`` §3.5: ``severity=BLOCKER`` requires
        a non-null ``suggestion``. Raises :class:`ValueError` at
        construction time if that invariant is violated — caught early
        inside the verifier author's code rather than surfacing as a
        Pydantic error later.
        """
        if severity == Severity.BLOCKER and suggestion is None:
            raise ValueError(
                "§3.5 invariant: ctx.fail(severity=BLOCKER) requires a non-null suggestion."
            )
        payload = self._base_result_payload()
        payload.update(
            passed=False,
            severity=severity,
            reason=reason,
            suggestion=suggestion,
            cost_usd=0.0,
            duration_ms=0,
        )
        payload.update(overrides)
        return VerifierResult.model_validate(payload)

    def _base_result_payload(self) -> dict[str, Any]:
        if self.current_verifier_meta is None:
            raise RuntimeError(
                "ctx.ok()/ctx.fail() called without current_verifier_meta set. "
                "The runtime stamps this immediately before invoking the verifier."
            )
        claim_id = self.current_claim.id if self.current_claim is not None else None
        return {
            "verifier": self.current_verifier_meta.fully_qualified_name,
            "claim_id": claim_id,
        }

    # -- I/O helpers --------------------------------------------------------

    async def fetch(self, url: str, *, timeout: int = 10) -> FetchResult:
        """Convenience GET via :attr:`http`."""
        return await self.http.get(url, timeout=timeout)

    async def exec(
        self,
        cmd: list[str],
        *,
        cwd: Path | None = None,
        timeout: int = 30,
        env: Mapping[str, str] | None = None,
    ) -> ExecResult:
        """Execute a subcommand. Verifiers MUST use this instead of
        ``subprocess.run`` so the runtime can route execution (local
        subprocess, ``docker exec``, etc.).

        The LocalRuntime implementation uses :func:`asyncio.create_subprocess_exec`.
        Other runtimes override by constructing a :class:`VerifierContext`
        subclass or by swapping the method.
        """
        if not cmd:
            raise ValueError("ctx.exec called with empty cmd list.")
        started = time.perf_counter()
        proc: asyncio.subprocess.Process | None = None
        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=str(cwd) if cwd is not None else None,
                env=dict(env) if env is not None else None,
            )
            wait_coro: Awaitable[tuple[bytes, bytes]] = proc.communicate()
            stdout_bytes, stderr_bytes = await asyncio.wait_for(wait_coro, timeout=timeout)
            exit_code = proc.returncode if proc.returncode is not None else -1
        except TimeoutError:
            if proc is not None and proc.returncode is None:
                proc.kill()
                # Drain pipes so asyncio doesn't warn about orphaned transports.
                with contextlib.suppress(TimeoutError, ProcessLookupError):
                    await asyncio.wait_for(proc.communicate(), timeout=2)
            raise
        duration_ms = int((time.perf_counter() - started) * 1000)
        return ExecResult(
            exit_code=exit_code,
            stdout=stdout_bytes.decode("utf-8", errors="replace"),
            stderr=stderr_bytes.decode("utf-8", errors="replace"),
            duration_ms=duration_ms,
        )


# ---------------------------------------------------------------------------
# Convenience factory for tests and ad-hoc callers
# ---------------------------------------------------------------------------


def make_context(
    *,
    deliverable: Deliverable,
    http: HttpClient,
    judge: JudgeClient,
    policy: RuntimePolicy | None = None,
    workspace: Path | None = None,
    budget_remaining_usd: float = 0.0,
    logger: logging.Logger | None = None,
) -> VerifierContext:
    """Build a :class:`VerifierContext` with sensible defaults.

    ``current_verifier_meta`` and ``current_claim`` are left ``None`` —
    the runtime or the caller stamps them just before invoking the
    verifier.
    """
    from signoff.runtime.base import RuntimePolicy  # local import: avoid cycle

    return VerifierContext(
        deliverable=deliverable,
        http=http,
        judge=judge,
        policy=policy if policy is not None else RuntimePolicy(),
        workspace=workspace if workspace is not None else Path.cwd(),
        logger=logger if logger is not None else logging.getLogger("signoff.verifier"),
        budget_remaining_usd=budget_remaining_usd,
    )
