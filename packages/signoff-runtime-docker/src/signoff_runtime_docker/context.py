"""``DockerVerifierContext`` — composition wrapper around
:class:`signoff.VerifierContext`.

Routes every ``ctx.exec(cmd, ...)`` call from a verifier body into
the sandbox container. Every other attribute (``deliverable``,
``http``, ``judge``, ``policy``, ``workspace``, ``logger``,
``ok``, ``fail``, ``fetch``, ``current_*``) is delegated to the
wrapped context — verifier authors see no behavioural difference
from the in-process ``VerifierContext`` other than where their
subprocess calls land.

Architectural note (was the bug fix): an earlier version
inherited from :class:`VerifierContext` via
``@dataclass(slots=True)``. The dataclass decorator's slots
transformation creates a *new* class object, which means the
subclass's bare ``super()`` calls captured ``__class__`` as the
pre-transform class — leading to ``TypeError: super(type, obj):
obj must be an instance or subtype of type`` whenever
``docker_exec`` was ``None`` and we tried to fall through to the
parent's ``exec``. Composition sidesteps the whole mess: no
inheritance, no slot transform, no super() call.
"""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

from signoff.context import ExecResult, VerifierContext

from signoff_runtime_docker.exec import DockerExec

if TYPE_CHECKING:
    from signoff import Severity, VerifierResult

__all__ = ["DockerVerifierContext", "wrap_context"]


_PASSTHROUGH_ATTRS = frozenset(
    {
        "deliverable",
        "http",
        "judge",
        "policy",
        "workspace",
        "logger",
        "budget_remaining_usd",
        "current_verifier_meta",
        "current_claim",
    }
)


class DockerVerifierContext:
    """A wrapper that delegates everything to a :class:`VerifierContext`
    except :meth:`exec`, which routes through a :class:`DockerExec`.

    Structurally compatible with :class:`VerifierContext` — every
    attribute access falls through to the wrapped instance, every
    method on :class:`VerifierContext` is exposed verbatim. Verifier
    authors annotating their parameters as ``ctx: VerifierContext``
    keep working; the runtime hands them this wrapper transparently.
    """

    __slots__ = ("_docker_exec", "_wrapped")

    # Slot type annotations so mypy knows what attribute access
    # through self._wrapped returns (VerifierContext methods).
    _wrapped: VerifierContext
    _docker_exec: DockerExec | None

    def __init__(
        self,
        wrapped: VerifierContext,
        docker_exec: DockerExec | None = None,
    ) -> None:
        # Direct slot assignments — bypass __setattr__ so the
        # delegation logic doesn't fire during construction.
        object.__setattr__(self, "_wrapped", wrapped)
        object.__setattr__(self, "_docker_exec", docker_exec)

    # -- attribute delegation ------------------------------------------------

    def __getattr__(self, name: str) -> Any:
        # __getattr__ only fires on misses; our two slots are read
        # via slot descriptors, so they never reach here.
        return getattr(self._wrapped, name)

    def __setattr__(self, name: str, value: Any) -> None:
        # The harness sets ``current_verifier_meta`` and
        # ``current_claim`` on the ctx after construction; forward
        # those (and every other passthrough attr) to the wrapped
        # context so verifier-side reads stay coherent.
        if name in _PASSTHROUGH_ATTRS:
            setattr(self._wrapped, name, value)
        else:
            object.__setattr__(self, name, value)

    # -- exec override -------------------------------------------------------

    async def exec(
        self,
        cmd: list[str],
        *,
        cwd: Path | None = None,
        timeout: int = 30,
        env: Mapping[str, str] | None = None,
    ) -> ExecResult:
        """Route the exec into the sandbox; fall back to the wrapped
        context's exec when ``docker_exec`` is ``None`` (used in
        tests and the LocalRuntime-degraded path)."""
        if self._docker_exec is None:
            return await self._wrapped.exec(cmd, cwd=cwd, timeout=timeout, env=env)
        return await self._docker_exec.run(cmd, cwd=cwd, timeout=timeout, env=env)

    # -- explicit method delegation -----------------------------------------
    #
    # ``__getattr__`` covers attribute access (``ctx.workspace``,
    # ``ctx.deliverable.id``), but bound-method-via-descriptor
    # lookup is a touchier surface — verifier authors call ``ctx.ok(...)``
    # / ``ctx.fail(...)`` / ``ctx.fetch(...)`` constantly, and we want
    # those to be normal method calls (not chained ``ctx._wrapped.fn``
    # delegation). Forwarding wrappers keep the call site identical.

    def ok(self, **overrides: Any) -> VerifierResult:
        return self._wrapped.ok(**overrides)

    def fail(
        self,
        reason: str,
        suggestion: str | None = None,
        severity: Severity | None = None,
        **overrides: Any,
    ) -> VerifierResult:
        if severity is None:
            return self._wrapped.fail(reason, suggestion=suggestion, **overrides)
        return self._wrapped.fail(reason, suggestion=suggestion, severity=severity, **overrides)

    async def fetch(self, url: str, *, timeout: int = 10) -> Any:
        return await self._wrapped.fetch(url, timeout=timeout)


def wrap_context(original: VerifierContext, docker_exec: DockerExec) -> DockerVerifierContext:
    """Build a :class:`DockerVerifierContext` that delegates to
    ``original`` and routes ``exec`` calls through ``docker_exec``.

    The original :class:`VerifierContext` is unchanged — the
    harness can keep using it for other verifier runs that don't
    want the sandbox.
    """
    return DockerVerifierContext(wrapped=original, docker_exec=docker_exec)


# A DockerVerifierContext is structurally compatible with
# :class:`VerifierContext` — verifier code annotated as receiving
# ``VerifierContext`` keeps working transparently. We expose the cast
# as a tiny convenience for callers that thread the value through
# strictly-typed plumbing.
def _as_verifier_context(dvc: DockerVerifierContext) -> VerifierContext:
    return cast(VerifierContext, dvc)
