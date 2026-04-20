"""``smoke_imports`` — ``python -c 'import ...'`` every changed module.

Catches the class of bugs that tests miss when the broken code path
sits behind a feature flag or is only reachable via a command-line
entry point — importing the module itself fails the syntax / top-level
evaluation that tests never exercise.
"""

from __future__ import annotations

from pathlib import PurePosixPath

from pydantic import ValidationError
from signoff import Claim, Severity, VerifierContext, VerifierResult, verifier
from signoff.context import ExecResult

from signoff_code.verifiers._common import (
    DEFAULT_STDOUT_EXCERPT_BYTES,
    catch_workspace_error,
    prepared_workspace,
)
from signoff_code.workspace import WorkspaceError

__all__ = ["module_name_for_path", "smoke_imports"]


@verifier(
    name="smoke_imports",
    claim_kinds="*",
    cost_tier="cheap",
    concurrency=1,
    runtime_required="docker",
    timeout_seconds=60,
)
async def smoke_imports(_claim: Claim, ctx: VerifierContext) -> VerifierResult:
    """Import every changed ``.py`` module inside the sandbox.

    Non-Python changes are silently skipped — this is the "smoke
    imports" check, not "everything we touched is importable". We
    import modules one at a time so the first failure pinpoints
    exactly which module is broken; stopping on the first failure is
    fine because a broken import usually poisons everything
    downstream.
    """
    try:
        change, workspace_root = prepared_workspace(ctx)
    except (WorkspaceError, ValidationError) as exc:
        return catch_workspace_error(ctx, exc)

    modules: list[tuple[str, str]] = []
    for path in change.changed_paths:
        if not path.endswith(".py"):
            continue
        name = module_name_for_path(path)
        if name:
            modules.append((path, name))

    if not modules:
        return ctx.ok(
            evidence={
                "tool": "python -c import",
                "skipped": True,
                "reason": "no importable .py paths in change",
                "changed_paths": change.changed_paths,
            }
        )

    attempted: list[dict[str, object]] = []
    for path, module in modules:
        result = await ctx.exec(
            ["python", "-c", f"import {module}"],
            cwd=workspace_root,
            timeout=30,
            env={"PYTHONPATH": "."},
        )
        attempted.append(
            {
                "path": path,
                "module": module,
                "exit_code": result.exit_code,
                "stderr": _tail(result.stderr, DEFAULT_STDOUT_EXCERPT_BYTES),
            }
        )
        if result.exit_code != 0:
            return ctx.fail(
                reason=f"Import failed for module {module!r} ({path}).",
                severity=Severity.BLOCKER,
                suggestion=(
                    f"`python -c 'import {module}'` failed; fix the "
                    "top-level error before continuing."
                ),
                evidence={
                    "tool": "python -c import",
                    "changed_paths": change.changed_paths,
                    "failed_module": module,
                    "failed_path": path,
                    "traceback": _tail(result.stderr, DEFAULT_STDOUT_EXCERPT_BYTES),
                    "attempted": attempted,
                },
            )

    return ctx.ok(
        evidence={
            "tool": "python -c import",
            "changed_paths": change.changed_paths,
            "imported": [m for _, m in modules],
        }
    )


def module_name_for_path(path: str) -> str | None:
    """Translate a project-relative path to a dotted module name.

    Strips a leading ``src/`` (common Python project layout), removes
    the ``.py`` suffix, and turns path separators into dots. Returns
    ``None`` for anything that obviously isn't an importable module
    (``__init__.py`` without a package, empty path, etc.) so the
    verifier skips it silently.
    """
    if not path.endswith(".py"):
        return None
    p = PurePosixPath(path)
    if p.parts and p.parts[0] == "src":
        p = PurePosixPath(*p.parts[1:])
    if not p.parts:
        return None
    stem = p.parts[-1][: -len(".py")]
    parts = list(p.parts[:-1])
    if stem == "__init__":
        if not parts:
            return None
        return ".".join(parts)
    parts.append(stem)
    return ".".join(parts)


def _tail(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    return "... [truncated]\n" + text[-limit:]


# Keep the ExecResult import live; used in the return-type chain.
_ = ExecResult
