"""``lint_clean`` — run ruff against the materialised workspace."""

from __future__ import annotations

import json

from pydantic import ValidationError
from signoff import Claim, Severity, VerifierContext, VerifierResult, verifier

from signoff_code.verifiers._common import (
    catch_workspace_error,
    excerpt,
    prepared_workspace,
)
from signoff_code.workspace import WorkspaceError

__all__ = ["lint_clean"]


@verifier(
    name="lint_clean",
    claim_kinds="*",
    cost_tier="cheap",
    concurrency=1,
    runtime_required="docker",
    timeout_seconds=60,
)
async def lint_clean(_claim: Claim, ctx: VerifierContext) -> VerifierResult:
    """Run ``ruff check`` on the changed paths.

    Lint findings default to WARNING severity — a stylistic nit
    shouldn't fail a deliverable the way a broken test would. An
    operator who wants stricter linting promotes it via
    ``severity_override: blocker`` in the config for this verifier.
    """
    try:
        change, workspace_root = prepared_workspace(ctx)
    except (WorkspaceError, ValidationError) as exc:
        return catch_workspace_error(ctx, exc)

    py_paths = [p for p in change.changed_paths if p.endswith(".py")]
    if not py_paths:
        return ctx.ok(
            evidence={
                "tool": "ruff",
                "skipped": True,
                "reason": "no .py paths in change",
                "changed_paths": change.changed_paths,
            }
        )
    # ``--no-cache`` is mandatory under the sandbox's read-only
    # workspace mount: ruff tries to create ``.ruff_cache/`` in
    # the working directory, which fails with EROFS on a ro mount
    # per CLAUDE.md §8's safe-by-default posture. Caching would
    # only help for repeated runs on the same files, and every
    # verification gets a fresh materialised workspace, so the
    # cache is pure overhead anyway.
    args = [
        "python",
        "-m",
        "ruff",
        "check",
        "--no-fix",
        "--no-cache",
        "--output-format=json",
        *py_paths,
    ]
    result = await ctx.exec(args, cwd=workspace_root, timeout=60)

    findings = _parse_findings(result.stdout)
    evidence: dict[str, object] = {
        **excerpt(result),
        "tool": "ruff",
        "changed_paths": change.changed_paths,
        "finding_count": len(findings),
    }

    if result.exit_code == 0:
        return ctx.ok(evidence=evidence)
    if findings:
        # Surface up to the first three findings in the suggestion
        # so agents have something to fix rather than a word salad.
        top = findings[:3]
        suggestion = "Ruff findings:\n" + "\n".join(_format_finding(f) for f in top)
        return ctx.fail(
            reason=f"ruff reported {len(findings)} finding(s).",
            severity=Severity.WARNING,
            suggestion=suggestion,
            evidence={**evidence, "findings": findings[:25]},
        )
    return ctx.fail(
        reason=f"ruff exited with code {result.exit_code} and no parseable findings.",
        severity=Severity.INFO,
        suggestion=None,
        evidence=evidence,
    )


def _format_finding(finding: dict[str, object]) -> str:
    filename = finding.get("filename", "?")
    location = finding.get("location")
    row: object = "?"
    if isinstance(location, dict):
        row = location.get("row", "?")
    code = finding.get("code", "?")
    message = finding.get("message", "")
    return f"- {filename}:{row} {code} {message}"


def _parse_findings(stdout: str) -> list[dict[str, object]]:
    """``ruff check --output-format=json`` prints a JSON array.

    Ruff occasionally emits a warning on stderr in addition to the
    JSON, so parse leniently: anything that isn't a JSON array is
    treated as "no structured findings" and we fall back to the
    exec's non-zero exit code.
    """
    stdout = stdout.strip()
    if not stdout:
        return []
    try:
        parsed = json.loads(stdout)
    except json.JSONDecodeError:
        return []
    if isinstance(parsed, list):
        return [entry for entry in parsed if isinstance(entry, dict)]
    return []
