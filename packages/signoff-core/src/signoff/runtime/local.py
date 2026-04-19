"""In-process default :class:`~signoff.runtime.base.Runtime`.

``LocalRuntime`` runs verifiers in the harness's own process. No
sandboxing, no resource caps. It is the zero-dependency default that
ships in ``signoff-core`` and is appropriate for trusted contexts:
unit tests, single-user development, controlled CI. Untrusted
deliverables belong in a sandboxed runtime such as ``DockerRuntime``
from the ``signoff-runtime-docker`` package (Phase 1).

What LocalRuntime enforces
--------------------------
- ``policy.timeout_seconds`` — via :func:`asyncio.wait_for`.
- Protocol §4.4 error handling: verifier exceptions and timeouts are
  translated into synthetic :class:`~signoff.models.VerifierResult`
  entries with ``severity=info`` so the harness records them as
  transient infrastructure problems rather than blockers.
- Cooperative cancellation: :class:`asyncio.CancelledError` is
  re-raised unchanged so the harness can honour protocol §5.6.

What LocalRuntime explicitly does NOT enforce
---------------------------------------------
- ``policy.cpu_limit`` / ``policy.memory_limit_bytes`` — verifiers
  share the harness's process; the runtime cannot cap them.
- ``policy.network`` — verifiers can call ``ctx.http`` freely.
- Filesystem isolation.

A one-shot WARNING is logged the first time LocalRuntime sees a
non-None resource limit or a non-``"open"`` network setting, so
operators notice that they've handed a policy to a runtime that
can't honour it.
"""

from __future__ import annotations

import asyncio
import logging
import time
import traceback
from typing import TYPE_CHECKING

from signoff.models import Severity, VerifierResult
from signoff.runtime.base import (
    Runtime,
    RuntimePolicy,
    VerifierMeta,
)

if TYPE_CHECKING:
    from signoff.context import VerifierContext
    from signoff.models import Claim
    from signoff.runtime.base import VerifierCallable


__all__ = ["LocalRuntime"]


_logger = logging.getLogger("signoff.runtime.local")


class LocalRuntime:
    """In-process runtime. See module docstring for semantics.

    Implements :class:`~signoff.runtime.base.Runtime`.
    """

    runtime_id: str = "local"

    def __init__(self) -> None:
        self._prepared: set[str] = set()
        self._warned_resource_limits: bool = False
        self._warned_network: bool = False

    async def prepare(self, verifier_meta: VerifierMeta) -> None:
        """No-op that remembers which verifiers have been prepared.

        Idempotent: calling with the same meta twice is a DEBUG log, not
        an error.
        """
        fqn = verifier_meta.fully_qualified_name
        if fqn in self._prepared:
            _logger.debug("LocalRuntime.prepare called twice for %s", fqn)
            return
        self._prepared.add(fqn)
        _logger.debug("LocalRuntime registered verifier %s", fqn)

    async def execute(
        self,
        fn: VerifierCallable,
        *,
        claim: Claim,
        ctx: VerifierContext,
        policy: RuntimePolicy,
    ) -> VerifierResult:
        """Run ``fn(claim, ctx)`` with a timeout and §4.4 error handling."""
        self._maybe_warn_about_policy(policy)

        # Stamp context state the verifier needs. ctx.ok/ctx.fail read these.
        ctx.current_claim = claim
        ctx.policy = policy

        started = time.perf_counter()
        result: VerifierResult
        try:
            result = await asyncio.wait_for(fn(claim, ctx), timeout=policy.timeout_seconds)
        except asyncio.CancelledError:
            # Cooperative cancellation from the harness per §5.6 — re-raise.
            raise
        except TimeoutError:
            result = self._synthetic_timeout_result(ctx, claim, policy)
        except Exception as exc:
            result = self._synthetic_exception_result(ctx, claim, exc)

        measured_ms = int((time.perf_counter() - started) * 1000)
        # Runtime measurement is authoritative. If the verifier already
        # reported something else, keep the runtime value and surface
        # the discrepancy at DEBUG so observability isn't misled.
        if result.duration_ms and result.duration_ms != measured_ms:
            _logger.debug(
                "Verifier %s reported duration_ms=%d; runtime measured %d; using runtime value.",
                result.verifier,
                result.duration_ms,
                measured_ms,
            )
        return result.model_copy(update={"duration_ms": measured_ms})

    async def teardown(self) -> None:
        """No-op. Idempotent: safe to call even if :meth:`prepare` was
        never called."""
        self._prepared.clear()

    # ------------------------------------------------------------------ helpers

    def _maybe_warn_about_policy(self, policy: RuntimePolicy) -> None:
        if not self._warned_resource_limits and (
            policy.cpu_limit is not None or policy.memory_limit_bytes is not None
        ):
            _logger.warning(
                "LocalRuntime was given cpu_limit=%s memory_limit_bytes=%s; "
                "these cannot be enforced in-process. Use a sandboxed runtime "
                "(e.g. DockerRuntime from signoff-runtime-docker) for untrusted code.",
                policy.cpu_limit,
                policy.memory_limit_bytes,
            )
            self._warned_resource_limits = True
        if not self._warned_network and policy.network != "open":
            _logger.warning(
                "LocalRuntime was given network=%r; this cannot be enforced in-process. "
                "Use a sandboxed runtime to restrict verifier network access.",
                policy.network,
            )
            self._warned_network = True

    def _synthetic_timeout_result(
        self,
        ctx: VerifierContext,
        claim: Claim,
        policy: RuntimePolicy,
    ) -> VerifierResult:
        """§4.4 category 2: transient infra failure → severity=info."""
        verifier_name = (
            ctx.current_verifier_meta.fully_qualified_name
            if ctx.current_verifier_meta is not None
            else _verifier_fallback_name(ctx, claim)
        )
        return VerifierResult(
            verifier=verifier_name,
            claim_id=claim.id,
            passed=False,
            severity=Severity.INFO,
            reason=f"Verifier timed out after {policy.timeout_seconds}s",
            suggestion=None,
            evidence={"timeout_seconds": policy.timeout_seconds},
            cost_usd=0.0,
            duration_ms=0,
        )

    def _synthetic_exception_result(
        self,
        ctx: VerifierContext,
        claim: Claim,
        exc: BaseException,
    ) -> VerifierResult:
        """§4.4 category 3: verifier bug → severity=info with traceback."""
        _logger.warning("Verifier raised %s: %s", type(exc).__name__, exc)
        verifier_name = (
            ctx.current_verifier_meta.fully_qualified_name
            if ctx.current_verifier_meta is not None
            else _verifier_fallback_name(ctx, claim)
        )
        short = str(exc)[:200]
        tb = traceback.format_exc()[:5000]
        return VerifierResult(
            verifier=verifier_name,
            claim_id=claim.id,
            passed=False,
            severity=Severity.INFO,
            reason=f"Verifier raised {type(exc).__name__}: {short}",
            suggestion=None,
            evidence={"exception_type": type(exc).__name__, "traceback": tb},
            cost_usd=0.0,
            duration_ms=0,
        )


def _verifier_fallback_name(ctx: VerifierContext, claim: Claim) -> str:
    """Used only when a runtime is invoked without ``current_verifier_meta``
    (tests or misbehaving harnesses). Keeps the synthetic result well-formed
    — §7.2 requires ``<pack>.<name>`` lowercase — without swallowing the
    underlying bug.
    """
    del claim, ctx
    return "signoff-core.unknown_verifier"


# Structural conformance: assignable-to check catches any attribute
# drift between LocalRuntime and the Runtime protocol at import time.
# The matching isinstance check lives in the test suite.
_runtime_type_check: type[Runtime] = LocalRuntime
