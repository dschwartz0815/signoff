"""Unit tests for :func:`signoff_code.verifiers.semantic_diff.semantic_diff`.

The judge is :class:`signoff.testing.FakeJudge` so we can stage
specific :class:`JudgeResult` s per scenario.
"""

from __future__ import annotations

from signoff import (
    Claim,
    Deliverable,
    JudgeResult,
    Severity,
    VerifierContext,
    VerifierMeta,
    make_context,
)
from signoff.testing import FakeHttpClient, FakeJudge
from signoff_code import BaseReference, CodeChangeDeliverable
from signoff_code.verifiers.semantic_diff import semantic_diff


def _ctx(change: CodeChangeDeliverable, judge: FakeJudge) -> VerifierContext:
    deliverable = Deliverable(id="dlv_1", kind="code_change", content=change)
    meta = VerifierMeta(
        name="semantic_diff",
        pack="signoff-code",
        claim_kinds=("*",),
        cost_tier="medium",
        concurrency=1,
        runtime_required="docker",
    )
    ctx = make_context(deliverable=deliverable, http=FakeHttpClient(), judge=judge)
    ctx.current_verifier_meta = meta
    return ctx


def _synthetic_claim() -> Claim:
    return Claim.model_construct(id="whole_deliverable", text="", kind="citation", evidence={})


def _change(intent: str, **overrides: object) -> CodeChangeDeliverable:
    kwargs: dict[str, object] = {"intent": intent}
    if "files" not in overrides and "diff" not in overrides:
        kwargs["files"] = {"a.py": "x = 1\n"}
    kwargs.update(overrides)
    return CodeChangeDeliverable(**kwargs)  # type: ignore[arg-type]


async def test_supported_label_passes() -> None:
    judge = FakeJudge(
        default=JudgeResult(
            label="supported",
            explanation="matches",
            excerpt="+ x = 1",
            cost_usd=0.0,
            confidence=0.9,
            model="claude-haiku-4-5",
            prompt_version="1.0.0",
        )
    )
    ctx = _ctx(_change("add a new constant"), judge)
    result = await semantic_diff(_synthetic_claim(), ctx)
    assert result.passed is True
    assert result.evidence["label"] == "supported"


async def test_contradicted_label_is_warning() -> None:
    judge = FakeJudge(
        default=JudgeResult(
            label="contradicted",
            explanation="the diff removes it instead",
            excerpt=None,
            cost_usd=0.0,
            confidence=0.8,
        )
    )
    ctx = _ctx(_change("add null check"), judge)
    result = await semantic_diff(_synthetic_claim(), ctx)
    assert result.passed is False
    assert result.severity == Severity.WARNING
    assert "contradicts" in result.reason.lower()
    assert result.suggestion is not None


async def test_not_addressed_is_warning() -> None:
    judge = FakeJudge(
        default=JudgeResult(
            label="not_addressed",
            explanation="unrelated change",
            excerpt=None,
            cost_usd=0.0,
            confidence=0.6,
        )
    )
    ctx = _ctx(_change("fix the null-check crash"), judge)
    result = await semantic_diff(_synthetic_claim(), ctx)
    assert result.passed is False
    assert result.severity == Severity.WARNING
    assert "not address" in result.reason.lower()


async def test_empty_intent_skips_silently() -> None:
    judge = FakeJudge()
    ctx = _ctx(_change(""), judge)
    result = await semantic_diff(_synthetic_claim(), ctx)
    assert result.passed is True
    assert result.evidence["skipped"] is True
    assert judge.calls == []


async def test_short_intent_skips_silently() -> None:
    judge = FakeJudge()
    ctx = _ctx(_change("fix"), judge)
    result = await semantic_diff(_synthetic_claim(), ctx)
    assert result.passed is True
    assert result.evidence["skipped"] is True


async def test_large_diff_skipped_with_warning() -> None:
    # 3000-line diff: well over the 2000-line cap.
    big_diff = "--- a\n+++ b\n" + "\n".join(f"+line{i}" for i in range(3000))
    change = CodeChangeDeliverable(
        intent="add three thousand lines",
        base=BaseReference(kind="local_path", value="/tmp"),
        diff=big_diff,
    )
    judge = FakeJudge()
    ctx = _ctx(change, judge)
    result = await semantic_diff(_synthetic_claim(), ctx)
    assert result.passed is False
    assert result.severity == Severity.WARNING
    assert result.evidence["skipped"] is True
    assert judge.calls == []


async def test_judge_failure_becomes_info() -> None:
    class _BoomJudge(FakeJudge):
        async def check_entailment(self, **_kwargs: object) -> JudgeResult:
            raise RuntimeError("judge down")

    ctx = _ctx(_change("some real intent here"), _BoomJudge())
    result = await semantic_diff(_synthetic_claim(), ctx)
    assert result.passed is False
    assert result.severity == Severity.INFO
    assert "RuntimeError" in result.evidence["error_class"]


async def test_diff_payload_preferred_over_files() -> None:
    """When a diff exists, it's what we hand the judge — not the
    files map, which would pass on a diff-only change."""
    judge = FakeJudge(
        default=JudgeResult(label="supported", explanation="ok", excerpt="x", cost_usd=0.0)
    )
    diff = "--- a/x\n+++ b/x\n@@ -1 +1 @@\n-x = 0\n+x = 1\n"
    change = CodeChangeDeliverable(
        intent="bump x to 1",
        base=BaseReference(kind="local_path", value="/tmp"),
        diff=diff,
    )
    ctx = _ctx(change, judge)
    await semantic_diff(_synthetic_claim(), ctx)
    assert judge.calls[0]["passage"] == diff
