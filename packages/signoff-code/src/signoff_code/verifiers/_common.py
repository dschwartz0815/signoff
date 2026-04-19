"""Shared helpers for the signoff-code verifiers.

Every verifier follows the same pattern: materialise a workspace from
the deliverable, run one or more tools via ``ctx.exec``, interpret
exit codes, and pack the output into a :class:`VerifierResult`. The
helpers here are the shared boilerplate so each verifier file stays
focused on "what does this tool's output mean".
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING

from pydantic import ValidationError

from signoff_code.deliverable import CodeChangeDeliverable
from signoff_code.workspace import Workspace, WorkspaceError

if TYPE_CHECKING:
    from signoff import Deliverable, Severity, VerifierContext, VerifierResult
    from signoff.context import ExecResult

__all__ = [
    "DEFAULT_STDOUT_EXCERPT_BYTES",
    "code_change_content",
    "excerpt",
    "materialize_from_ctx",
    "workspace_error_result",
]


_logger = logging.getLogger("signoff_code.verifiers._common")

#: How much of ``ExecResult.stdout`` or ``.stderr`` to keep in
#: ``VerifierResult.evidence``. Tight enough to keep feedback packets
#: small; loose enough to carry a useful excerpt.
DEFAULT_STDOUT_EXCERPT_BYTES = 16 * 1024


def code_change_content(deliverable: Deliverable) -> CodeChangeDeliverable:
    """Extract :class:`CodeChangeDeliverable` from a :class:`Deliverable`.

    Accepts either an already-validated instance (what a caller would
    supply when constructing the deliverable in Python) or the dict
    form that round-trips through JSON when the harness receives a
    ``request_signoff`` call. Validation errors bubble up so the
    caller can surface them as BLOCKER results.
    """
    payload = deliverable.content
    if isinstance(payload, CodeChangeDeliverable):
        return payload
    if isinstance(payload, dict):
        return CodeChangeDeliverable.model_validate(payload)
    raise TypeError(
        f"Deliverable.content must be a CodeChangeDeliverable or dict for "
        f"signoff-code verifiers; got {type(payload).__name__}."
    )


async def materialize_from_ctx(
    ctx: VerifierContext,
    *,
    tmp_root: Path | None = None,
) -> tuple[CodeChangeDeliverable, Workspace]:
    """Pull the code-change payload from ``ctx.deliverable`` and
    return a materialised workspace.

    The caller owns the returned :class:`Workspace` and must call
    ``cleanup()`` (or use it as an async context manager) before
    returning its verifier result.
    """
    change = code_change_content(ctx.deliverable)
    workspace = await Workspace.materialize(change, http=ctx.http, tmp_root=tmp_root)
    return change, workspace


def workspace_error_result(
    ctx: VerifierContext,
    *,
    reason: str,
    severity: Severity,
    suggestion: str | None = None,
    evidence: dict[str, object] | None = None,
) -> VerifierResult:
    """Construct a ``ctx.fail`` result for the failure modes that
    don't involve running a tool (workspace build failed, prechecks
    skipped, etc.). Kept here so every verifier formats these
    identically."""
    return ctx.fail(
        reason=reason, severity=severity, suggestion=suggestion, evidence=evidence or {}
    )


def excerpt(
    result: ExecResult | None, *, limit: int = DEFAULT_STDOUT_EXCERPT_BYTES
) -> dict[str, object]:
    """Evidence fragment for an :class:`ExecResult`.

    Returns a dict with ``stdout``, ``stderr``, ``exit_code``, and
    ``duration_ms``, each stream truncated at ``limit`` bytes with a
    ``[... truncated]`` marker. ``None`` input returns an empty dict
    (used when the tool was skipped).
    """
    if result is None:
        return {}
    return {
        "exit_code": result.exit_code,
        "duration_ms": result.duration_ms,
        "stdout": _truncate(result.stdout, limit),
        "stderr": _truncate(result.stderr, limit),
    }


def _truncate(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    return text[:limit] + "\n... [truncated]\n"


def catch_workspace_error(
    ctx: VerifierContext, exc: WorkspaceError | ValidationError
) -> VerifierResult:
    """Turn a workspace-materialisation failure into a BLOCKER result.

    A deliverable the harness can't even unpack must not pass —
    BLOCKER severity with a suggestion that names the specific
    failure mode.
    """
    from signoff import Severity  # local: avoid import cycle

    message = str(exc)
    return ctx.fail(
        reason=f"Could not materialise workspace: {message[:400]}",
        severity=Severity.BLOCKER,
        suggestion="Check the diff format / file paths / base revision and retry.",
        evidence={"error_class": type(exc).__name__, "error": message[:1024]},
    )
