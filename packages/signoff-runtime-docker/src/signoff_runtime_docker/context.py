"""``DockerVerifierContext`` — the context we hand to verifiers.

Subclasses :class:`signoff.VerifierContext` and overrides exactly
:meth:`exec` so every ``ctx.exec(cmd, ...)`` call from a verifier
body routes into the sandbox container. Every other attribute
(``deliverable``, ``http``, ``judge``, ``policy``, ``workspace``,
``logger``, ``ok``, ``fail``, ``fetch``) passes through to the
parent — network / judge calls intentionally stay on the host so the
verifier's own Python code can still reach the outside world; only
the subprocess side is sandboxed.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from signoff.context import ExecResult, VerifierContext

from signoff_runtime_docker.exec import DockerExec

__all__ = ["DockerVerifierContext"]


@dataclass(slots=True)
class DockerVerifierContext(VerifierContext):
    """VerifierContext whose ``exec`` calls route to a container.

    Constructed by :class:`DockerRuntime` immediately before invoking
    the verifier. The ``docker_exec`` slot is the bridge; all other
    fields are forwarded from the caller's :class:`VerifierContext`.
    """

    docker_exec: DockerExec | None = None

    async def exec(
        self,
        cmd: list[str],
        *,
        cwd: Path | None = None,
        timeout: int = 30,
        env: Mapping[str, str] | None = None,
    ) -> ExecResult:
        if self.docker_exec is None:
            return await super().exec(cmd, cwd=cwd, timeout=timeout, env=env)
        return await self.docker_exec.run(cmd, cwd=cwd, timeout=timeout, env=env)


def wrap_context(original: VerifierContext, docker_exec: DockerExec) -> DockerVerifierContext:
    """Build a fresh :class:`DockerVerifierContext` that mirrors
    ``original``'s field values and delegates ``exec`` to ``docker_exec``.

    We don't mutate ``original`` because the harness may re-use it for
    other verifier runs (e.g. whole-deliverable verifiers that don't
    want the sandbox).
    """
    return DockerVerifierContext(
        deliverable=original.deliverable,
        http=original.http,
        judge=original.judge,
        policy=original.policy,
        workspace=original.workspace,
        logger=original.logger
        if isinstance(original.logger, logging.Logger)
        else logging.getLogger("signoff.verifier"),
        budget_remaining_usd=original.budget_remaining_usd,
        current_verifier_meta=original.current_verifier_meta,
        current_claim=original.current_claim,
        docker_exec=docker_exec,
    )
