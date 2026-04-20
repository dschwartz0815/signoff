"""``types_check`` — run mypy against the materialised workspace."""

from __future__ import annotations

import re

from pydantic import ValidationError
from signoff import Claim, Severity, VerifierContext, VerifierResult, verifier

from signoff_code.verifiers._common import (
    catch_workspace_error,
    excerpt,
    prepared_workspace,
)
from signoff_code.workspace import WorkspaceError

__all__ = ["types_check"]


# mypy error lines look like: ``path/to/file.py:12: error: ... [error-code]``
_MYPY_ERROR_RE = re.compile(
    r"^(?P<path>[^:]+):(?P<line>\d+):(?:\s*(?P<col>\d+):)?\s*error:\s*(?P<msg>.+)$",
    re.MULTILINE,
)

#: How many parsed errors to surface in the suggestion. Keeping this
#: short keeps feedback packets actionable — an agent does better
#: fixing three concrete errors than skimming fifty.
_MAX_ERRORS_IN_SUGGESTION = 3


@verifier(
    name="types_check",
    claim_kinds="*",
    cost_tier="cheap",
    concurrency=1,
    runtime_required="docker",
    timeout_seconds=120,
)
async def types_check(_claim: Claim, ctx: VerifierContext) -> VerifierResult:
    """Run ``mypy`` against the changed files.

    Scope defaults to the change surface (``changed_paths`` from
    the deliverable) so mypy isn't asked to type-check the whole
    project every time — prohibitively slow for realistic
    codebases. A verifier-level config can flip it to ``"full"``
    by passing ``additional_paths=[\"\"]`` (via per_verifier options
    in a future commit) but the default is narrow.
    """
    try:
        change, workspace_root = prepared_workspace(ctx)
    except (WorkspaceError, ValidationError) as exc:
        return catch_workspace_error(ctx, exc)

    py_paths = [p for p in change.changed_paths if p.endswith(".py")]
    if not py_paths:
        return ctx.ok(
            evidence={
                "tool": "mypy",
                "skipped": True,
                "reason": "no .py paths in change",
                "changed_paths": change.changed_paths,
            }
        )
    args = [
        "python",
        "-m",
        "mypy",
        "--no-error-summary",
        "--show-error-codes",
        "--follow-imports=silent",
        *py_paths,
    ]
    result = await ctx.exec(args, cwd=workspace_root, timeout=120)

    errors = _parse_errors(result.stdout)
    evidence: dict[str, object] = {
        **excerpt(result),
        "tool": "mypy",
        "changed_paths": change.changed_paths,
        "error_count": len(errors),
    }

    if result.exit_code == 0:
        return ctx.ok(evidence=evidence)
    if result.exit_code == 1 and errors:
        top = errors[:_MAX_ERRORS_IN_SUGGESTION]
        suggestion = "Fix the type errors:\n" + "\n".join(
            f"- {e['path']}:{e['line']}: {e['message']}" for e in top
        )
        return ctx.fail(
            reason=f"mypy reported {len(errors)} error(s).",
            severity=Severity.BLOCKER,
            suggestion=suggestion,
            evidence={**evidence, "errors": errors},
        )
    return ctx.fail(
        reason=f"mypy exited with code {result.exit_code} and no parseable errors.",
        severity=Severity.INFO,
        suggestion=None,
        evidence=evidence,
    )


def _parse_errors(stdout: str) -> list[dict[str, object]]:
    return [
        {
            "path": m.group("path"),
            "line": int(m.group("line")),
            "message": m.group("msg").strip(),
        }
        for m in _MYPY_ERROR_RE.finditer(stdout)
    ]
