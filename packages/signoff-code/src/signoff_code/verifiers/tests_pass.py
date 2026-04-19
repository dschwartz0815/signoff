"""``tests_pass`` — run the project's pytest suite inside the sandbox."""

from __future__ import annotations

import logging
import re

from pydantic import ValidationError
from signoff import Claim, Severity, VerifierContext, VerifierResult, verifier

from signoff_code.verifiers._common import (
    catch_workspace_error,
    excerpt,
    materialize_from_ctx,
)
from signoff_code.workspace import WorkspaceError

__all__ = ["tests_pass"]


_logger = logging.getLogger("signoff_code.verifiers.tests_pass")


# --- pytest output parsing -------------------------------------------------


#: Matches each count component (``42 passed``, ``3 failed``, ``1 error``,
#: ``5 skipped``) in pytest's summary line. Applied separately per key
#: rather than one mega-pattern so a missing component doesn't make the
#: whole parse return zeros.
_PYTEST_COUNT_PATTERNS = {
    "passed": re.compile(r"(\d+)\s+passed\b"),
    "failed": re.compile(r"(\d+)\s+failed\b"),
    "errors": re.compile(r"(\d+)\s+error(?:s)?\b"),
    "skipped": re.compile(r"(\d+)\s+skipped\b"),
}

_FIRST_FAILING_TEST_RE = re.compile(r"^FAILED (?P<nodeid>[^\s]+)", re.MULTILINE)


@verifier(
    name="tests_pass",
    claim_kinds="*",
    cost_tier="cheap",
    concurrency=1,
    runtime_required="docker",
    timeout_seconds=300,
)
async def tests_pass(_claim: Claim, ctx: VerifierContext) -> VerifierResult:
    """Run ``pytest`` against the materialised workspace.

    Exit-code map:

    - 0 → pass. Evidence carries any parsed counts.
    - 1 or 2 → tests failed / collection error → BLOCKER. Suggestion
      names the first failing test; evidence holds the tail of stdout.
    - 5 → no tests collected → WARNING (might be a docs-only or
      config-only change; the harness shouldn't block on it).
    - Anything else → treat as infra failure, INFO-severity
      synthetic per protocol §4.4.
    """
    try:
        change, workspace = await materialize_from_ctx(ctx)
    except (WorkspaceError, ValidationError) as exc:
        return catch_workspace_error(ctx, exc)

    async with workspace:
        pytest_args = ["-q", "--tb=short"]
        result = await ctx.exec(
            ["python", "-m", "pytest", *pytest_args],
            cwd=workspace.root,
            timeout=300,
        )

    summary = _parse_summary(result.stdout)
    evidence: dict[str, object] = {
        **excerpt(result),
        "tool": "pytest",
        "changed_paths": change.changed_paths,
        **summary,
    }

    if result.exit_code == 0:
        return ctx.ok(evidence=evidence)
    if result.exit_code == 5:
        return ctx.fail(
            reason="pytest collected no tests for this change.",
            severity=Severity.WARNING,
            suggestion=(
                "If this is a test-free change (docs, config), override "
                "this verifier's severity in config; otherwise add tests "
                "that cover the new behaviour."
            ),
            evidence=evidence,
        )
    if result.exit_code in (1, 2):
        first_failure = _first_failing_node(result.stdout)
        suggestion = (
            f"First failing test: {first_failure}. Inspect stdout and fix."
            if first_failure
            else "Tests failed; inspect stdout for the first failure."
        )
        return ctx.fail(
            reason=f"pytest failed (exit {result.exit_code})",
            severity=Severity.BLOCKER,
            suggestion=suggestion,
            evidence={**evidence, "first_failing_node": first_failure},
        )
    return ctx.fail(
        reason=f"pytest exited unexpectedly with code {result.exit_code}",
        severity=Severity.INFO,
        suggestion=None,
        evidence=evidence,
    )


def _parse_summary(stdout: str) -> dict[str, int]:
    """Extract the ``passed / failed / errors / skipped`` counts.

    Pytest's human-readable summary line is a grab-bag; we grep for
    each canonical part independently and return zero for anything
    we don't find. Best-effort; parser drift on a new pytest version
    is a cosmetic issue, not a correctness one.
    """
    counts = {"passed": 0, "failed": 0, "errors": 0, "skipped": 0}
    for key, pattern in _PYTEST_COUNT_PATTERNS.items():
        match = pattern.search(stdout)
        if match is not None:
            counts[key] = int(match.group(1))
    return counts


def _first_failing_node(stdout: str) -> str | None:
    match = _FIRST_FAILING_TEST_RE.search(stdout)
    return match.group("nodeid") if match else None
