"""End-to-end integration tests for the Harness.

These exercise the public surface: load a real YAML config, register
two trivial verifiers, run verify() on a deliverable + claims, and
assert the Verdict round-trips through Pydantic cleanly. The happy
path here is the "Signoff goes from scaffolding to real thing" gate.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from signoff import (
    Claim,
    Deliverable,
    Harness,
    LocalRuntime,
    Registry,
    Verdict,
    VerifierContext,
    VerifierResult,
    load_config,
)
from signoff.testing import FakeHttpClient, FakeJudge
from signoff.verifier import _testing_pack, verifier

# ---------------------------------------------------------------------------
# Fixtures: two trivial verifiers (one per-claim, one whole-deliverable)
# ---------------------------------------------------------------------------


def _pass_cite() -> Any:
    with _testing_pack("signoff-research"):

        @verifier(name="citation_smoke", claim_kinds=["citation"], cost_tier="cheap")
        async def citation_smoke(_c: Claim, ctx: VerifierContext) -> VerifierResult:
            return ctx.ok(evidence={"url": _c.evidence.get("url")})

    return citation_smoke


def _overall() -> Any:
    with _testing_pack("signoff-research"):

        @verifier(name="overall_shape", claim_kinds="*", cost_tier="medium")
        async def overall_shape(_c: Claim, ctx: VerifierContext) -> VerifierResult:
            return ctx.ok(evidence={"deliverable_id": ctx.deliverable.id})

    return overall_shape


def _fail_cite() -> Any:
    with _testing_pack("signoff-research"):

        @verifier(name="citation_smoke", claim_kinds=["citation"], cost_tier="cheap")
        async def fail_cite(claim: Claim, ctx: VerifierContext) -> VerifierResult:
            return ctx.fail(
                reason="Forced failure for test.",
                suggestion="Fix the claim.",
                evidence={"url": claim.evidence.get("url"), "excerpt": "HTTP 404"},
            )

    return fail_cite


@pytest.fixture
def deliverable() -> Deliverable:
    return Deliverable(
        id="dlv_integ1",
        kind="research_report",
        content={"title": "t", "body": "b"},
        metadata={"agent_id": "agent-42", "session_id": "sess-1", "retry_count": 0},
    )


@pytest.fixture
def claims() -> list[Claim]:
    return [
        Claim(
            id="clm_a",
            text="Citation A.",
            kind="citation",
            evidence={"url": "https://a.example"},
        ),
        Claim(
            id="clm_b",
            text="Citation B.",
            kind="citation",
            evidence={"url": "https://b.example"},
        ),
        Claim(
            id="clm_c",
            text="Citation C.",
            kind="citation",
            evidence={"url": "https://c.example"},
        ),
    ]


def _yaml(packs: list[str]) -> str:
    return (
        'protocol_version: "0.1"\n'
        f"packs: {packs}\n"
        "deliverables:\n"
        "  research_report:\n"
        "    verifiers:\n"
        "      signoff-research.citation_smoke:\n"
        "        enabled: true\n"
        "      signoff-research.overall_shape:\n"
        "        enabled: true\n"
    )


# ---------------------------------------------------------------------------
# End-to-end happy path
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_end_to_end_happy_path(
    deliverable: Deliverable, claims: list[Claim], tmp_path: Path
) -> None:
    r = Registry()
    r.register(_pass_cite())
    r.register(_overall())

    cfg_path = tmp_path / "harness.yaml"
    cfg_path.write_text(_yaml(["signoff-research"]))

    cfg = load_config(path=cfg_path, pack_defaults=False, env_overrides=False)
    async with Harness(
        config=cfg,
        registry=r,
        runtimes=[LocalRuntime()],
        http=FakeHttpClient(),
        judge=FakeJudge(),
    ) as h:
        verdict = await h.verify(deliverable, claims)

    assert verdict.passed is True
    assert verdict.feedback_packet is None
    # 3 citation results (one per claim) + 1 whole-deliverable result.
    assert len(verdict.results) == 4
    by_verifier = {r.verifier: 0 for r in verdict.results}
    for res in verdict.results:
        by_verifier[res.verifier] = by_verifier.get(res.verifier, 0) + 1
    assert by_verifier["signoff-research.citation_smoke"] == 3
    assert by_verifier["signoff-research.overall_shape"] == 1
    assert verdict.cost_usd == 0.0
    assert verdict.duration_ms >= 0
    assert verdict.id.startswith("vrd_")
    # Whole-deliverable result has claim_id=None per §3.5.
    whole = next(r for r in verdict.results if r.verifier.endswith("overall_shape"))
    assert whole.claim_id is None


# ---------------------------------------------------------------------------
# End-to-end failure path
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_end_to_end_failure_produces_feedback_packet(
    deliverable: Deliverable, claims: list[Claim], tmp_path: Path
) -> None:
    r = Registry()
    r.register(_fail_cite())
    r.register(_overall())

    cfg_path = tmp_path / "harness.yaml"
    cfg_path.write_text(_yaml(["signoff-research"]))

    cfg = load_config(path=cfg_path, pack_defaults=False, env_overrides=False)
    async with Harness(
        config=cfg,
        registry=r,
        runtimes=[LocalRuntime()],
        http=FakeHttpClient(),
        judge=FakeJudge(),
    ) as h:
        verdict = await h.verify(deliverable, claims, retry_budget=3)

    assert verdict.passed is False
    assert verdict.feedback_packet is not None
    assert len(verdict.feedback_packet.blockers) == 3  # one per claim
    for entry in verdict.feedback_packet.blockers:
        assert entry.verifier == "signoff-research.citation_smoke"
        assert entry.suggested_repair == "Fix the claim."
        assert entry.claim_text in {c.text for c in claims}
        assert entry.evidence_excerpt is not None
    assert verdict.feedback_packet.retry_budget_remaining == 2


# ---------------------------------------------------------------------------
# Determinism (with sampling seed + fixed clock)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_determinism_with_sampling_seed(
    deliverable: Deliverable,
    claims: list[Claim],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Two runs with identical inputs + SIGNOFF_SAMPLING_SEED must produce
    identical verdict payloads once volatile fields (id, timestamps,
    duration_ms) are masked out."""

    monkeypatch.setenv("SIGNOFF_SAMPLING_SEED", "7")

    r = Registry()
    r.register(_pass_cite())
    r.register(_overall())

    cfg_path = tmp_path / "harness.yaml"
    cfg_path.write_text(_yaml(["signoff-research"]))

    async def _run() -> dict[str, Any]:
        cfg = load_config(path=cfg_path, pack_defaults=False, env_overrides=False)
        async with Harness(
            config=cfg,
            registry=r,
            runtimes=[LocalRuntime()],
            http=FakeHttpClient(),
            judge=FakeJudge(),
        ) as h:
            v = await h.verify(deliverable, claims)
        return _mask_volatiles(v.model_dump(mode="json"))

    first = await _run()
    second = await _run()
    assert first == second


def _mask_volatiles(payload: dict[str, Any]) -> dict[str, Any]:
    """Null out fields that legitimately vary across runs."""
    payload.pop("id", None)
    payload["started_at"] = "<masked>"
    payload["completed_at"] = "<masked>"
    payload["duration_ms"] = 0
    for res in payload.get("results", []):
        res["duration_ms"] = 0
        res.pop("started_at", None)
    return payload


# ---------------------------------------------------------------------------
# Round-trip serialization
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_verdict_round_trips_via_pydantic(
    deliverable: Deliverable, claims: list[Claim], tmp_path: Path
) -> None:
    r = Registry()
    r.register(_fail_cite())
    r.register(_overall())
    cfg_path = tmp_path / "harness.yaml"
    cfg_path.write_text(_yaml(["signoff-research"]))
    cfg = load_config(path=cfg_path, pack_defaults=False, env_overrides=False)
    async with Harness(
        config=cfg,
        registry=r,
        runtimes=[LocalRuntime()],
        http=FakeHttpClient(),
        judge=FakeJudge(),
    ) as h:
        verdict = await h.verify(deliverable, claims, retry_budget=1)

    raw = verdict.model_dump_json()
    reloaded = Verdict.model_validate_json(raw)
    assert reloaded.model_dump(mode="json") == verdict.model_dump(mode="json")


# ---------------------------------------------------------------------------
# Demo snippet shape (the "gate" for moving to MCP wiring)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_demo_snippet_from_readme(
    deliverable: Deliverable, claims: list[Claim], tmp_path: Path
) -> None:
    """Mirrors the demo in docs/harness.md / the PR description:

        async with await Harness.from_config_path("examples/minimal.yaml") as h:
            verdict = await h.verify(deliverable, claims)

    Uses a temporary YAML so the test is self-contained. Asserts the
    shape you'd expect to paste into a demo readme.
    """
    r = Registry()
    r.register(_pass_cite())

    cfg_path = tmp_path / "harness.yaml"
    cfg_path.write_text(
        'protocol_version: "0.1"\n'
        "packs:\n"
        "  - signoff-research\n"
        "deliverables:\n"
        "  research_report:\n"
        "    verifiers:\n"
        "      signoff-research.citation_smoke:\n"
        "        enabled: true\n"
    )

    cfg = load_config(path=cfg_path, pack_defaults=False, env_overrides=False)
    async with Harness(
        config=cfg,
        registry=r,
        runtimes=[LocalRuntime()],
        http=FakeHttpClient(),
        judge=FakeJudge(),
    ) as h:
        verdict = await h.verify(deliverable, claims)

    rendered = verdict.model_dump_json(indent=2)
    assert '"passed": true' in rendered
    assert '"signoff-research.citation_smoke"' in rendered
    assert verdict.protocol_version == "0.1"
    assert verdict.harness_version == "0.0.1"


# ---------------------------------------------------------------------------
# Integration: requires dependencies across tiers
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_dependency_chain_cheap_then_medium(
    deliverable: Deliverable, claims: list[Claim], tmp_path: Path
) -> None:
    with _testing_pack("signoff-research"):

        @verifier(name="cheap_one", claim_kinds=["citation"], cost_tier="cheap")
        async def cheap_one(_c: Claim, ctx: VerifierContext) -> VerifierResult:
            return ctx.ok()

        @verifier(
            name="medium_one",
            claim_kinds=["citation"],
            cost_tier="medium",
            requires=("signoff-research.cheap_one",),
        )
        async def medium_one(_c: Claim, ctx: VerifierContext) -> VerifierResult:
            return ctx.ok(evidence={"deps_satisfied": True})

    r = Registry()
    r.register(cheap_one)
    r.register(medium_one)

    cfg = load_config(
        path=None,
        pack_defaults=False,
        env_overrides=False,
        request_overrides={
            "packs": ["signoff-research"],
            "deliverables": {
                "research_report": {
                    "verifiers": {
                        "signoff-research.cheap_one": {"enabled": True},
                        "signoff-research.medium_one": {"enabled": True},
                    }
                }
            },
        },
    )
    async with Harness(
        config=cfg,
        registry=r,
        runtimes=[LocalRuntime()],
        http=FakeHttpClient(),
        judge=FakeJudge(),
    ) as h:
        verdict = await h.verify(deliverable, claims)

    assert verdict.passed is True
    # Each claim has one cheap_one result and one medium_one result.
    cheap_count = sum(1 for res in verdict.results if res.verifier.endswith("cheap_one"))
    medium_count = sum(1 for res in verdict.results if res.verifier.endswith("medium_one"))
    assert cheap_count == 3
    assert medium_count == 3
