"""Tests for :class:`signoff.runtime.local.LocalRuntime`."""

from __future__ import annotations

import asyncio
import logging

import pytest
from signoff import (
    Claim,
    Deliverable,
    LocalRuntime,
    RuntimePolicy,
    Severity,
    VerifierContext,
    VerifierMeta,
    VerifierResult,
    make_context,
)
from signoff.testing import FakeHttpClient, FakeJudge

# ---------------------------------------------------------------------------
# fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def meta() -> VerifierMeta:
    return VerifierMeta(
        name="citation_existence",
        pack="signoff-research",
        claim_kinds=("citation",),
        cost_tier="cheap",
        concurrency=1,
    )


@pytest.fixture
def claim() -> Claim:
    return Claim(id="clm_1", text="A claim.", kind="citation")


@pytest.fixture
def deliverable() -> Deliverable:
    return Deliverable(id="dlv_1", kind="research_report", content=None)


@pytest.fixture
def ctx(deliverable: Deliverable, meta: VerifierMeta) -> VerifierContext:
    c = make_context(deliverable=deliverable, http=FakeHttpClient(), judge=FakeJudge())
    c.current_verifier_meta = meta
    return c


# ---------------------------------------------------------------------------
# happy path
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_happy_path_returns_result_with_duration_stamped(
    ctx: VerifierContext, claim: Claim
) -> None:
    async def verifier(c: Claim, x: VerifierContext) -> VerifierResult:
        return x.ok(evidence={"checked": True})

    rt = LocalRuntime()
    result = await rt.execute(verifier, claim=claim, ctx=ctx, policy=RuntimePolicy())
    assert result.passed is True
    assert result.severity == Severity.INFO
    assert result.verifier == "signoff-research.citation_existence"
    assert result.claim_id == "clm_1"
    # duration_ms is stamped by the runtime (>=0; may legitimately be 0
    # for extremely fast verifiers on fast machines).
    assert result.duration_ms >= 0


# ---------------------------------------------------------------------------
# §4.4 category 3: verifier exception → synthetic INFO failure
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_verifier_exception_becomes_synthetic_info_failure(
    ctx: VerifierContext, claim: Claim
) -> None:
    async def boom(_c: Claim, _x: VerifierContext) -> VerifierResult:
        raise ValueError("deliberate boom — 0123456789" * 30)

    rt = LocalRuntime()
    result = await rt.execute(boom, claim=claim, ctx=ctx, policy=RuntimePolicy())
    assert result.passed is False
    assert result.severity == Severity.INFO
    assert "ValueError" in result.reason
    assert result.evidence["exception_type"] == "ValueError"
    assert "traceback" in result.evidence
    # Bounded strings per §4.4 to prevent runaway evidence.
    assert len(result.evidence["traceback"]) <= 5000
    # reason is bounded to ~200 chars of the exception message.
    assert len(result.reason) <= 300


# ---------------------------------------------------------------------------
# §4.4 category 2: timeout → synthetic INFO failure
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_verifier_timeout_becomes_synthetic_info_failure(
    ctx: VerifierContext, claim: Claim
) -> None:
    async def slow(_c: Claim, _x: VerifierContext) -> VerifierResult:
        await asyncio.sleep(5)
        raise AssertionError("unreachable")

    rt = LocalRuntime()
    policy = RuntimePolicy(timeout_seconds=1)
    result = await rt.execute(slow, claim=claim, ctx=ctx, policy=policy)
    assert result.passed is False
    assert result.severity == Severity.INFO
    assert "timed out" in result.reason
    assert result.evidence["timeout_seconds"] == 1
    assert result.suggestion is None


@pytest.mark.asyncio
async def test_timeout_cancels_verifier_cleanly(ctx: VerifierContext, claim: Claim) -> None:
    started = asyncio.Event()
    cancelled = asyncio.Event()

    async def sleepy(_c: Claim, _x: VerifierContext) -> VerifierResult:
        started.set()
        try:
            await asyncio.sleep(10)
        except asyncio.CancelledError:
            cancelled.set()
            raise
        raise AssertionError("unreachable")

    rt = LocalRuntime()
    await rt.execute(sleepy, claim=claim, ctx=ctx, policy=RuntimePolicy(timeout_seconds=1))
    assert started.is_set()
    # The inner coroutine received CancelledError — verified by event set.
    # A small grace tick to allow the except block to complete.
    await asyncio.sleep(0)
    assert cancelled.is_set()


# ---------------------------------------------------------------------------
# §5.6 cooperative cancellation: CancelledError propagates
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_external_cancellation_propagates(ctx: VerifierContext, claim: Claim) -> None:
    async def slow(_c: Claim, _x: VerifierContext) -> VerifierResult:
        await asyncio.sleep(5)
        raise AssertionError("unreachable")

    rt = LocalRuntime()
    task = asyncio.create_task(
        rt.execute(slow, claim=claim, ctx=ctx, policy=RuntimePolicy(timeout_seconds=5))
    )
    await asyncio.sleep(0)  # let task start
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task


# ---------------------------------------------------------------------------
# resource limits: warn, then execute
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_resource_limits_warn_but_execute(
    ctx: VerifierContext, claim: Claim, caplog: pytest.LogCaptureFixture
) -> None:
    async def verifier(_c: Claim, x: VerifierContext) -> VerifierResult:
        return x.ok()

    rt = LocalRuntime()
    policy = RuntimePolicy(cpu_limit=2.0, memory_limit_bytes=1024 * 1024 * 512)

    with caplog.at_level(logging.WARNING, logger="signoff.runtime.local"):
        result = await rt.execute(verifier, claim=claim, ctx=ctx, policy=policy)
    assert result.passed is True
    assert any("cannot be enforced in-process" in rec.getMessage() for rec in caplog.records)

    # Second call must NOT re-warn (idempotent warning).
    caplog.clear()
    with caplog.at_level(logging.WARNING, logger="signoff.runtime.local"):
        await rt.execute(verifier, claim=claim, ctx=ctx, policy=policy)
    assert not any("cannot be enforced" in rec.getMessage() for rec in caplog.records)


@pytest.mark.asyncio
async def test_network_policy_warn_but_execute(
    ctx: VerifierContext, claim: Claim, caplog: pytest.LogCaptureFixture
) -> None:
    async def verifier(_c: Claim, x: VerifierContext) -> VerifierResult:
        return x.ok()

    rt = LocalRuntime()
    with caplog.at_level(logging.WARNING, logger="signoff.runtime.local"):
        await rt.execute(verifier, claim=claim, ctx=ctx, policy=RuntimePolicy(network="none"))
    assert any("network=" in rec.getMessage() for rec in caplog.records)


# ---------------------------------------------------------------------------
# prepare/teardown idempotency
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_prepare_and_teardown_are_idempotent(meta: VerifierMeta) -> None:
    rt = LocalRuntime()
    await rt.teardown()  # safe before prepare
    await rt.prepare(meta)
    await rt.prepare(meta)  # second call is a no-op
    await rt.teardown()
    await rt.teardown()  # safe to call twice


# ---------------------------------------------------------------------------
# concurrency
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_concurrent_executes_all_complete(ctx: VerifierContext, claim: Claim) -> None:
    call_count = 0

    async def verifier(_c: Claim, x: VerifierContext) -> VerifierResult:
        nonlocal call_count
        call_count += 1
        await asyncio.sleep(0.01)
        return x.ok(evidence={"n": call_count})

    rt = LocalRuntime()
    results = await asyncio.gather(
        *(rt.execute(verifier, claim=claim, ctx=ctx, policy=RuntimePolicy()) for _ in range(8))
    )
    assert len(results) == 8
    assert all(r.passed for r in results)


# ---------------------------------------------------------------------------
# duration stamping
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_runtime_duration_overrides_verifier_reported_value(
    ctx: VerifierContext, claim: Claim, caplog: pytest.LogCaptureFixture
) -> None:
    async def verifier(_c: Claim, x: VerifierContext) -> VerifierResult:
        await asyncio.sleep(0.05)
        return x.ok(duration_ms=99999)  # bogus verifier-reported duration

    rt = LocalRuntime()
    with caplog.at_level(logging.DEBUG, logger="signoff.runtime.local"):
        result = await rt.execute(verifier, claim=claim, ctx=ctx, policy=RuntimePolicy())
    assert result.duration_ms != 99999
    assert result.duration_ms >= 40  # at least the sleep
