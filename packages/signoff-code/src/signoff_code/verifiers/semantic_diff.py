"""``semantic_diff`` — LLM-backed check that the diff matches the stated intent.

Catches the class of failure where the agent said "I added null
checks" but actually just caught and swallowed the exception. Runs
``ctx.judge.check_entailment(claim=intent, passage=diff)`` and maps
the label back to a :class:`VerifierResult`.

The entailment prompt from ``signoff-judge`` is general enough to
cover diff-vs-intent checking. If live-tuning reveals it's not,
authoring a dedicated ``semantic_diff.md`` is a follow-up — see the
notes in the pack README.
"""

from __future__ import annotations

import logging

from pydantic import ValidationError
from signoff import Claim, JudgeResult, Severity, VerifierContext, VerifierResult, verifier

from signoff_code.verifiers._common import (
    catch_workspace_error,
    code_change_content,
)
from signoff_code.workspace import WorkspaceError

__all__ = ["semantic_diff"]


_logger = logging.getLogger("signoff_code.verifiers.semantic_diff")


#: Skip the judge when the ``intent`` string is shorter than this —
#: fewer than ~10 chars means the caller didn't actually describe
#: the change ("fix", "wip", etc.) and the judge is going to refuse
#: or return low-confidence noise.
_MIN_INTENT_LENGTH = 10

#: Don't send diffs beyond this many lines to the judge. The
#: entailment prompt expects to reason holistically about a passage;
#: past a few hundred lines the model's signal degrades and the token
#: bill climbs. Operators can bump this via per_verifier config.
_DEFAULT_MAX_DIFF_LINES = 2000


@verifier(
    name="semantic_diff",
    claim_kinds="*",
    cost_tier="medium",
    concurrency=1,
    runtime_required="docker",
    timeout_seconds=60,
)
async def semantic_diff(_claim: Claim, ctx: VerifierContext) -> VerifierResult:
    """Ask the judge: does this diff plausibly do what the intent says?

    - Empty / trivially-short intent → OK with ``skipped=True``
      (not every code-change has a human-readable intent).
    - Diff exceeds the line cap → WARNING with ``skipped=True`` so
      the operator notices they've handed the judge a payload too
      large to reason about.
    - ``label="supported"`` → OK; evidence includes the judge's
      excerpt and confidence.
    - ``label="contradicted"`` → WARNING (operator may promote to
      blocker via severity_override) with the judge's explanation
      surfaced in the suggestion.
    - ``label="not_addressed"`` → WARNING with a suggestion noting
      the apparent mismatch.
    """
    try:
        change = code_change_content(ctx.deliverable)
    except (ValidationError, WorkspaceError, TypeError) as exc:
        return catch_workspace_error(ctx, exc)  # type: ignore[arg-type]

    if not change.intent or len(change.intent.strip()) < _MIN_INTENT_LENGTH:
        return ctx.ok(
            evidence={
                "tool": "semantic_diff",
                "skipped": True,
                "reason": "intent missing or too short for a meaningful judge call",
            }
        )

    passage = _build_passage(change)
    if passage is None:
        return ctx.ok(
            evidence={
                "tool": "semantic_diff",
                "skipped": True,
                "reason": "no diff or files payload to evaluate",
            }
        )

    line_count = passage.count("\n") + 1
    if line_count > _DEFAULT_MAX_DIFF_LINES:
        return ctx.fail(
            reason=(
                f"Diff is {line_count} lines; semantic_diff is capped at {_DEFAULT_MAX_DIFF_LINES}."
            ),
            severity=Severity.WARNING,
            suggestion=(
                "Split the change into smaller pieces or raise the cap via "
                "per_verifier config if you're confident the judge can reason "
                "over a payload this size."
            ),
            evidence={
                "tool": "semantic_diff",
                "skipped": True,
                "reason": "diff exceeds _DEFAULT_MAX_DIFF_LINES",
                "line_count": line_count,
            },
        )

    try:
        judge_result = await ctx.judge.check_entailment(claim=change.intent, passage=passage)
    except Exception as exc:
        _logger.warning("semantic_diff: judge call failed (%s); treating as INFO.", exc)
        return ctx.fail(
            reason=f"Judge call failed: {type(exc).__name__}: {exc}",
            severity=Severity.INFO,
            suggestion=None,
            evidence={
                "tool": "semantic_diff",
                "error_class": type(exc).__name__,
                "error": str(exc)[:400],
            },
        )

    return _result_from_label(ctx, change.intent, judge_result)


def _build_passage(change: object) -> str | None:
    """Join the change payload into a single string the judge can read."""
    diff = getattr(change, "diff", None)
    files = getattr(change, "files", None)
    if diff:
        return str(diff)
    if files:
        chunks = []
        for path, content in sorted(dict(files).items()):
            chunks.append(f"# ---- {path} ----\n{content}")
        return "\n".join(chunks)
    return None


def _result_from_label(
    ctx: VerifierContext, intent: str, judge_result: JudgeResult
) -> VerifierResult:
    evidence = {
        "tool": "semantic_diff",
        "intent": intent,
        "label": judge_result.label,
        "confidence": judge_result.confidence,
        "excerpt": judge_result.excerpt,
        "explanation": judge_result.explanation,
        "cost_usd": judge_result.cost_usd,
        "model": judge_result.model,
        "prompt_version": judge_result.prompt_version,
    }
    if judge_result.label == "supported":
        return ctx.ok(evidence=evidence)
    if judge_result.label == "contradicted":
        return ctx.fail(
            reason=(
                "Judge says the diff contradicts the stated intent: "
                f"{judge_result.explanation[:200]}"
            ),
            severity=Severity.WARNING,
            suggestion=(
                "Either the intent is wrong or the change doesn't do what "
                "it claims. Review the explanation in evidence and rework "
                "one of the two."
            ),
            evidence=evidence,
        )
    # "not_addressed" — change is unrelated to the intent.
    return ctx.fail(
        reason=(
            "Judge says the diff does not address the stated intent: "
            f"{judge_result.explanation[:200]}"
        ),
        severity=Severity.WARNING,
        suggestion=(
            "The diff looks unrelated to what you said it would do. Check "
            "whether you're committing the right change, or refine the "
            "intent to match what the diff actually does."
        ),
        evidence=evidence,
    )
