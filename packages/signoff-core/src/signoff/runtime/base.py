"""Runtime abstraction per ``CLAUDE.md`` §8.

A :class:`Runtime` defines *where* and *how* a verifier executes.
``signoff-core`` ships :class:`~signoff.runtime.local.LocalRuntime`;
sandboxed implementations (Docker, Firecracker, Wasm) live in their own
packages and plug in by satisfying this protocol.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Literal, Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field

if TYPE_CHECKING:
    from signoff.context import VerifierContext
    from signoff.models import Claim, VerifierResult


__all__ = [
    "Runtime",
    "RuntimeInfrastructureError",
    "RuntimePolicy",
    "RuntimePolicyViolationError",
    "RuntimeResourceLimitError",
    "RuntimeTimeoutError",
    "SignoffRuntimeError",
    "VerifierMeta",
]


# ---------------------------------------------------------------------------
# RuntimePolicy (CLAUDE.md §8.3)
# ---------------------------------------------------------------------------


class RuntimePolicy(BaseModel):
    """Per-execution policy ceiling.

    The caller (harness) constructs a ``RuntimePolicy`` from the merged
    config (``CLAUDE.md`` §8.3) and passes it to :meth:`Runtime.execute`.
    The runtime is responsible for enforcing whatever subset of the
    policy it can — :class:`~signoff.runtime.local.LocalRuntime` honours
    ``timeout_seconds`` and nothing else; sandboxed runtimes honour more.

    Runtime-specific policy fields (e.g. ``image`` for Docker) live on
    each runtime's own subclass, not here.
    """

    model_config = ConfigDict(extra="forbid")

    timeout_seconds: int = Field(default=30, ge=1, description="Wall-clock ceiling per execution.")
    cpu_limit: float | None = Field(
        default=None,
        description="CPU cores. None = no limit. Ignored by LocalRuntime.",
    )
    memory_limit_bytes: int | None = Field(
        default=None,
        description="Memory ceiling in bytes. None = no limit. Ignored by LocalRuntime.",
    )
    network: Literal["none", "allowlist", "open"] = Field(
        default="open",
        description="Network posture. LocalRuntime ignores this (no isolation).",
    )
    network_allowlist: list[str] = Field(
        default_factory=list,
        description="Domains permitted when network='allowlist'.",
    )


# ---------------------------------------------------------------------------
# VerifierMeta — protocol §4.1 metadata as a simple dataclass
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class VerifierMeta:
    """Implements ``docs/protocol.md`` §4.1 verifier registration metadata.

    Constructed by the harness from the ``@verifier`` decorator (next PR)
    or from entry-point discovery. Runtime-layer code receives instances
    of this so it can prepare resources, attribute results, and enforce
    ``runtime_required``.
    """

    name: str
    pack: str
    claim_kinds: tuple[str, ...]
    cost_tier: Literal["cheap", "medium", "expensive"]
    concurrency: int
    timeout_seconds: int = 30
    version: str | None = None
    requires: tuple[str, ...] = field(default_factory=tuple)
    runtime_required: Literal["local", "docker"] | None = None

    @property
    def fully_qualified_name(self) -> str:
        """``<pack>.<name>`` per §4.1. Used as ``VerifierResult.verifier``."""
        return f"{self.pack}.{self.name}"


# ---------------------------------------------------------------------------
# Runtime protocol (CLAUDE.md §8.1)
# ---------------------------------------------------------------------------


VerifierCallable = Callable[["Claim", "VerifierContext"], Awaitable["VerifierResult"]]


@runtime_checkable
class Runtime(Protocol):
    """Defines where and how a verifier's work executes.

    Implements ``CLAUDE.md`` §8.1. Conforming implementations MUST:

    - Expose a stable ``runtime_id`` (e.g. ``"local"``, ``"docker"``) so
      config matching and result attribution have something to key on.
    - Translate infrastructure failure into a synthetic
      :class:`~signoff.models.VerifierResult` per protocol §4.4
      (``severity=info``, ``passed=false``) rather than raising.
    - Re-raise :class:`asyncio.CancelledError` unchanged so the harness
      can honour protocol §5.6 cooperative cancellation.
    - Be idempotent in :meth:`prepare` and :meth:`teardown`.
    """

    runtime_id: str

    async def prepare(self, verifier_meta: VerifierMeta) -> None:
        """Called once per verifier at harness startup.

        Implementations pull images, warm subprocess pools, verify
        cosign signatures, etc. No-op for trivial runtimes. MUST be
        idempotent: the harness may call it again if a verifier is
        re-registered.
        """
        ...

    async def execute(
        self,
        fn: VerifierCallable,
        *,
        claim: Claim,
        ctx: VerifierContext,
        policy: RuntimePolicy,
    ) -> VerifierResult:
        """Run ``fn(claim, ctx)`` under this runtime's isolation model."""
        ...

    async def teardown(self) -> None:
        """Release runtime resources. Safe to call even if :meth:`prepare`
        was never called. Idempotent."""
        ...


# ---------------------------------------------------------------------------
# Error hierarchy
# ---------------------------------------------------------------------------


class SignoffRuntimeError(Exception):
    """Base for runtime-internal failures.

    Distinct from a verifier returning ``passed=false``: a
    ``SignoffRuntimeError`` is caught by :meth:`Runtime.execute` and
    translated into a synthetic :class:`~signoff.models.VerifierResult`
    per protocol §4.4. It never escapes the runtime to the harness.

    Named to avoid shadowing the Python builtin :class:`RuntimeError`.
    """


class RuntimeTimeoutError(SignoffRuntimeError):
    """Raised internally when an execution exceeds ``policy.timeout_seconds``."""


class RuntimeResourceLimitError(SignoffRuntimeError):
    """Raised internally when a sandboxed runtime tripped a CPU/memory cap."""


class RuntimePolicyViolationError(SignoffRuntimeError):
    """Raised internally when a verifier violated network or fs policy."""


class RuntimeInfrastructureError(SignoffRuntimeError):
    """Raised internally for transient infra failures (DNS, image pull, etc.)."""
