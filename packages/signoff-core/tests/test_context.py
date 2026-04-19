"""Tests for :mod:`signoff.context` and :mod:`signoff.testing`."""

from __future__ import annotations

import asyncio
import sys

import pytest
from signoff import (
    Claim,
    Deliverable,
    FetchResult,
    JudgeResult,
    Severity,
    VerifierMeta,
    make_context,
)
from signoff.testing import FakeHttpClient, FakeJudge


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


# ---------------------------------------------------------------------------
# ctx.ok / ctx.fail
# ---------------------------------------------------------------------------


def test_ctx_ok_fills_required_fields(
    deliverable: Deliverable, meta: VerifierMeta, claim: Claim
) -> None:
    ctx = make_context(deliverable=deliverable, http=FakeHttpClient(), judge=FakeJudge())
    ctx.current_verifier_meta = meta
    ctx.current_claim = claim
    r = ctx.ok(evidence={"url": "x"})
    assert r.passed is True
    assert r.severity == Severity.INFO
    assert r.verifier == "signoff-research.citation_existence"
    assert r.claim_id == "clm_1"
    assert r.evidence == {"url": "x"}
    assert r.reason == "Verified successfully"


def test_ctx_ok_without_current_claim_has_null_claim_id(
    deliverable: Deliverable, meta: VerifierMeta
) -> None:
    ctx = make_context(deliverable=deliverable, http=FakeHttpClient(), judge=FakeJudge())
    ctx.current_verifier_meta = meta
    r = ctx.ok()
    assert r.claim_id is None


def test_ctx_fail_blocker_requires_suggestion(
    deliverable: Deliverable, meta: VerifierMeta, claim: Claim
) -> None:
    ctx = make_context(deliverable=deliverable, http=FakeHttpClient(), judge=FakeJudge())
    ctx.current_verifier_meta = meta
    ctx.current_claim = claim
    with pytest.raises(ValueError, match=r"§3\.5 invariant"):
        ctx.fail("bad", severity=Severity.BLOCKER)


def test_ctx_fail_blocker_with_suggestion_ok(
    deliverable: Deliverable, meta: VerifierMeta, claim: Claim
) -> None:
    ctx = make_context(deliverable=deliverable, http=FakeHttpClient(), judge=FakeJudge())
    ctx.current_verifier_meta = meta
    ctx.current_claim = claim
    r = ctx.fail("bad", suggestion="do this")
    assert r.passed is False
    assert r.severity == Severity.BLOCKER
    assert r.suggestion == "do this"


def test_ctx_fail_warning_without_suggestion_allowed(
    deliverable: Deliverable, meta: VerifierMeta, claim: Claim
) -> None:
    ctx = make_context(deliverable=deliverable, http=FakeHttpClient(), judge=FakeJudge())
    ctx.current_verifier_meta = meta
    ctx.current_claim = claim
    r = ctx.fail("soft issue", severity=Severity.WARNING)
    assert r.passed is False
    assert r.severity == Severity.WARNING
    assert r.suggestion is None


def test_ctx_ok_without_verifier_meta_raises(deliverable: Deliverable, claim: Claim) -> None:
    ctx = make_context(deliverable=deliverable, http=FakeHttpClient(), judge=FakeJudge())
    ctx.current_claim = claim
    with pytest.raises(RuntimeError, match="current_verifier_meta"):
        ctx.ok()


# ---------------------------------------------------------------------------
# ctx.exec
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_ctx_exec_runs_subcommand(deliverable: Deliverable) -> None:
    ctx = make_context(deliverable=deliverable, http=FakeHttpClient(), judge=FakeJudge())
    result = await ctx.exec(
        [sys.executable, "-c", "print('hi'); import sys; sys.stderr.write('err')"]
    )
    assert result.exit_code == 0
    assert "hi" in result.stdout
    assert "err" in result.stderr
    assert result.duration_ms >= 0


@pytest.mark.asyncio
async def test_ctx_exec_empty_cmd_raises(deliverable: Deliverable) -> None:
    ctx = make_context(deliverable=deliverable, http=FakeHttpClient(), judge=FakeJudge())
    with pytest.raises(ValueError):
        await ctx.exec([])


@pytest.mark.asyncio
async def test_ctx_exec_timeout_raises(deliverable: Deliverable) -> None:
    ctx = make_context(deliverable=deliverable, http=FakeHttpClient(), judge=FakeJudge())
    with pytest.raises(asyncio.TimeoutError):
        await ctx.exec([sys.executable, "-c", "import time; time.sleep(5)"], timeout=1)


@pytest.mark.asyncio
async def test_ctx_fetch_routes_through_http(deliverable: Deliverable) -> None:
    http = FakeHttpClient()
    http.register(
        "https://example.com/x",
        FetchResult(
            ok=True,
            status_code=200,
            url="https://example.com/x",
            text="body",
            headers={"content-type": "text/plain"},
            duration_ms=5,
        ),
    )
    ctx = make_context(deliverable=deliverable, http=http, judge=FakeJudge())
    result = await ctx.fetch("https://example.com/x")
    assert result.ok is True
    assert result.status_code == 200
    assert ("GET", "https://example.com/x") in http.calls


# ---------------------------------------------------------------------------
# FakeHttpClient
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_fake_http_returns_registered_response() -> None:
    http = FakeHttpClient(
        {
            "https://a": FetchResult(
                ok=True, status_code=200, url="https://a", text="ok", headers={}, duration_ms=0
            )
        }
    )
    got = await http.get("https://a")
    assert got.status_code == 200
    head = await http.head("https://a")
    assert head.status_code == 200
    assert [("GET", "https://a"), ("HEAD", "https://a")] == http.calls


@pytest.mark.asyncio
async def test_fake_http_unknown_url_raises() -> None:
    http = FakeHttpClient()
    with pytest.raises(LookupError):
        await http.get("https://unknown")


# ---------------------------------------------------------------------------
# FakeJudge
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_fake_judge_returns_queued_then_default() -> None:
    default = JudgeResult(
        label="not_addressed", explanation="no default", excerpt=None, cost_usd=0.0
    )
    j = FakeJudge(default=default)
    first = JudgeResult(label="supported", explanation="good", excerpt="x", cost_usd=0.01)
    second = JudgeResult(label="contradicted", explanation="bad", excerpt="y", cost_usd=0.02)
    j.queue(first, second)

    r1 = await j.check_entailment(claim="c", passage="p")
    r2 = await j.check_entailment(claim="c", passage="p")
    r3 = await j.check_entailment(claim="c", passage="p")
    assert (r1, r2, r3) == (first, second, default)
    assert len(j.calls) == 3


def test_fetch_result_backfills_final_url_from_url() -> None:
    """``FetchResult.final_url`` defaults to the request URL when not
    explicitly provided, so existing callers don't have to change."""
    r = FetchResult(
        ok=True,
        status_code=200,
        url="https://example.com/page",
        text="hi",
        headers={},
        duration_ms=1,
    )
    assert r.final_url == "https://example.com/page"
    assert r.error is None
    assert r.attempts == 1
    assert r.from_cache is False


def test_fetch_result_preserves_explicit_final_url() -> None:
    """When the caller sets ``final_url`` (e.g., after a redirect chain),
    ``__post_init__`` leaves it alone."""
    r = FetchResult(
        ok=True,
        status_code=200,
        url="https://example.com/a",
        text="hi",
        headers={},
        duration_ms=1,
        final_url="https://example.com/b",
    )
    assert r.final_url == "https://example.com/b"


def test_fetch_result_error_field_for_failures() -> None:
    r = FetchResult(
        ok=False,
        status_code=0,
        url="https://example.com/",
        text="",
        headers={},
        duration_ms=5,
        error="robots.txt disallows /admin for Signoff/0.0",
        attempts=1,
    )
    assert r.ok is False
    assert r.error is not None and "robots" in r.error


@pytest.mark.asyncio
async def test_fake_judge_without_default_raises_when_drained() -> None:
    j = FakeJudge()
    with pytest.raises(LookupError):
        await j.check_entailment(claim="c", passage="p")
