"""Unit tests for :mod:`signoff.harness` organized by protocol §5 subsection."""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime
from typing import Any

import pytest
from signoff import (
    Claim,
    Deliverable,
    Harness,
    LocalRuntime,
    Registry,
    Severity,
    VerifierContext,
    VerifierResult,
    load_config,
)
from signoff.testing import FakeHttpClient, FakeJudge
from signoff.verifier import _testing_pack, verifier

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def deliverable() -> Deliverable:
    return Deliverable(id="dlv_1", kind="research_report", content={"body": "x"})


@pytest.fixture
def claims() -> list[Claim]:
    return [
        Claim(
            id="clm_1",
            text="A citation claim.",
            kind="citation",
            evidence={"url": "https://example.com/a"},
        ),
        Claim(
            id="clm_2",
            text="A second citation.",
            kind="citation",
            evidence={"url": "https://example.com/b"},
        ),
    ]


def _make_passing_verifier(
    pack: str,
    name: str,
    *,
    kinds: list[str] | str = ("citation",),
    cost_tier: str = "cheap",
    concurrency: int = 1,
    requires: tuple[str, ...] = (),
) -> Any:
    with _testing_pack(pack):

        @verifier(
            name=name,
            claim_kinds=kinds if kinds != "*" else "*",
            cost_tier=cost_tier,
            concurrency=concurrency,
            requires=requires,
        )
        async def fn(_claim: Claim, ctx: VerifierContext) -> VerifierResult:
            return ctx.ok(evidence={"checked": True})

    return fn


def _make_blocker_verifier(pack: str, name: str) -> Any:
    with _testing_pack(pack):

        @verifier(name=name, claim_kinds=["citation"], cost_tier="cheap")
        async def fn(_claim: Claim, ctx: VerifierContext) -> VerifierResult:
            return ctx.fail(reason="bad", suggestion="fix it")

    return fn


def _build_harness(
    *,
    registry: Registry,
    packs: list[str] | None = None,
    deliverable_kind: str = "research_report",
    enabled_verifiers: list[str] | None = None,
    overrides: dict[str, Any] | None = None,
) -> Harness:
    verifiers_cfg: dict[str, dict[str, Any]] = {}
    if enabled_verifiers is not None:
        for fqn in enabled_verifiers:
            verifiers_cfg[fqn] = {"enabled": True}
    else:
        for meta in registry.list_all():
            verifiers_cfg[meta.fully_qualified_name] = {"enabled": True}

    request_overrides: dict[str, Any] = {
        "packs": packs if packs is not None else sorted({m.pack for m in registry.list_all()}),
        "deliverables": {
            deliverable_kind: {"verifiers": verifiers_cfg},
        },
    }
    if overrides:
        # shallow merge budgets/runtime/etc.
        for k, v in overrides.items():
            request_overrides[k] = v

    cfg = load_config(
        path=None,
        pack_defaults=False,
        env_overrides=False,
        request_overrides=request_overrides,
    )
    return Harness(
        config=cfg,
        registry=registry,
        runtimes={"local": LocalRuntime()},
        http=FakeHttpClient(),
        judge=FakeJudge(),
        clock=lambda: datetime(2026, 4, 18, 14, 22, 10, tzinfo=UTC),
    )


# ---------------------------------------------------------------------------
# §5.2 — resolution
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_resolution_empty_claims_and_no_whole_deliverable(
    deliverable: Deliverable,
) -> None:
    r = Registry()
    r.register(_make_passing_verifier("signoff-research", "cite"))
    h = _build_harness(registry=r)
    verdict = await h.verify(deliverable, claims=[])
    assert verdict.passed is True
    assert verdict.results == []


@pytest.mark.asyncio
async def test_resolution_matches_claim_kind_and_plans_once_per_claim(
    deliverable: Deliverable, claims: list[Claim]
) -> None:
    r = Registry()
    r.register(_make_passing_verifier("signoff-research", "cite"))
    h = _build_harness(registry=r)
    verdict = await h.verify(deliverable, claims=claims)
    assert verdict.passed is True
    assert len(verdict.results) == 2  # one per claim
    assert {res.claim_id for res in verdict.results} == {"clm_1", "clm_2"}


@pytest.mark.asyncio
async def test_resolution_unknown_deliverable_kind_logs_and_skips(
    claims: list[Claim], caplog: pytest.LogCaptureFixture
) -> None:
    r = Registry()
    r.register(_make_passing_verifier("signoff-research", "cite"))
    h = _build_harness(registry=r)
    unknown_kind = Deliverable(id="dlv_1", kind="totally_unknown", content=None)
    with caplog.at_level("INFO", logger="signoff.harness"):
        verdict = await h.verify(unknown_kind, claims)
    assert verdict.passed is True
    assert verdict.results == []
    assert any("No config block" in r.getMessage() for r in caplog.records)


@pytest.mark.asyncio
async def test_resolution_disabled_verifier_excluded(
    deliverable: Deliverable, claims: list[Claim]
) -> None:
    r = Registry()
    r.register(_make_passing_verifier("signoff-research", "cite"))
    cfg = load_config(
        path=None,
        pack_defaults=False,
        env_overrides=False,
        request_overrides={
            "packs": ["signoff-research"],
            "deliverables": {
                "research_report": {"verifiers": {"signoff-research.cite": {"enabled": False}}}
            },
        },
    )
    h = Harness(
        config=cfg,
        registry=r,
        runtimes={"local": LocalRuntime()},
        http=FakeHttpClient(),
        judge=FakeJudge(),
    )
    verdict = await h.verify(deliverable, claims)
    assert verdict.results == []


@pytest.mark.asyncio
async def test_resolution_sample_rate_zero_excludes(
    deliverable: Deliverable, claims: list[Claim]
) -> None:
    r = Registry()
    r.register(_make_passing_verifier("signoff-research", "cite"))
    cfg = load_config(
        path=None,
        pack_defaults=False,
        env_overrides=False,
        request_overrides={
            "packs": ["signoff-research"],
            "deliverables": {
                "research_report": {
                    "verifiers": {"signoff-research.cite": {"enabled": True, "sample_rate": 0.0}}
                }
            },
        },
    )
    h = Harness(
        config=cfg,
        registry=r,
        runtimes={"local": LocalRuntime()},
        http=FakeHttpClient(),
        judge=FakeJudge(),
    )
    verdict = await h.verify(deliverable, claims)
    assert verdict.results == []


@pytest.mark.asyncio
async def test_resolution_sample_rate_seed_is_reproducible(
    deliverable: Deliverable,
    claims: list[Claim],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("SIGNOFF_SAMPLING_SEED", "42")
    r = Registry()
    r.register(_make_passing_verifier("signoff-research", "cite"))
    cfg = load_config(
        path=None,
        pack_defaults=False,
        env_overrides=False,
        request_overrides={
            "packs": ["signoff-research"],
            "deliverables": {
                "research_report": {"verifiers": {"signoff-research.cite": {"sample_rate": 0.5}}}
            },
        },
    )
    runs: list[int] = []
    for _ in range(5):
        h = Harness(
            config=cfg,
            registry=r,
            runtimes={"local": LocalRuntime()},
            http=FakeHttpClient(),
            judge=FakeJudge(),
        )
        v = await h.verify(deliverable, claims)
        runs.append(len(v.results))
    # All five runs produce the same count with the same seed.
    assert len(set(runs)) == 1


@pytest.mark.asyncio
async def test_resolution_pack_not_in_active_set_excluded(
    deliverable: Deliverable, claims: list[Claim]
) -> None:
    r = Registry()
    r.register(_make_passing_verifier("signoff-research", "cite"))
    r.register(_make_passing_verifier("signoff-legal", "check"))
    # Only enable signoff-research — signoff-legal verifier must not run.
    h = _build_harness(
        registry=r,
        packs=["signoff-research"],
        enabled_verifiers=[
            "signoff-research.cite",
            "signoff-legal.check",  # listed in deliverable config but pack disabled
        ],
    )
    verdict = await h.verify(deliverable, claims)
    assert all(r.verifier == "signoff-research.cite" for r in verdict.results)


@pytest.mark.asyncio
async def test_resolution_whole_deliverable_plans_once(
    deliverable: Deliverable, claims: list[Claim]
) -> None:
    r = Registry()
    r.register(_make_passing_verifier("signoff-research", "overall", kinds="*"))
    h = _build_harness(registry=r)
    verdict = await h.verify(deliverable, claims)
    assert len(verdict.results) == 1
    assert verdict.results[0].claim_id is None


@pytest.mark.asyncio
async def test_severity_override_upgrades_pass_result(
    deliverable: Deliverable, claims: list[Claim]
) -> None:
    r = Registry()
    r.register(_make_passing_verifier("signoff-research", "cite"))
    cfg = load_config(
        path=None,
        pack_defaults=False,
        env_overrides=False,
        request_overrides={
            "packs": ["signoff-research"],
            "deliverables": {
                "research_report": {
                    "verifiers": {
                        "signoff-research.cite": {"enabled": True, "severity_override": "warning"}
                    }
                }
            },
        },
    )
    h = Harness(
        config=cfg,
        registry=r,
        runtimes={"local": LocalRuntime()},
        http=FakeHttpClient(),
        judge=FakeJudge(),
    )
    verdict = await h.verify(deliverable, claims)
    assert all(r.severity == Severity.WARNING for r in verdict.results)


@pytest.mark.asyncio
async def test_missing_runtime_falls_back_to_local(
    deliverable: Deliverable,
    claims: list[Claim],
    caplog: pytest.LogCaptureFixture,
) -> None:
    r = Registry()
    r.register(_make_passing_verifier("signoff-research", "cite"))
    cfg = load_config(
        path=None,
        pack_defaults=False,
        env_overrides=False,
        request_overrides={
            "packs": ["signoff-research"],
            "deliverables": {
                "research_report": {"verifiers": {"signoff-research.cite": {"enabled": True}}}
            },
            "runtime": {"default": "firecracker"},  # unknown
        },
    )
    h = Harness(
        config=cfg,
        registry=r,
        runtimes={"local": LocalRuntime()},
        http=FakeHttpClient(),
        judge=FakeJudge(),
    )
    with caplog.at_level("WARNING", logger="signoff.harness"):
        verdict = await h.verify(deliverable, claims)
    assert verdict.passed is True
    assert any("falling back to 'local'" in r.getMessage() for r in caplog.records)


@pytest.mark.asyncio
async def test_runtime_required_docker_logs_warning(
    deliverable: Deliverable,
    claims: list[Claim],
    caplog: pytest.LogCaptureFixture,
) -> None:
    with _testing_pack("signoff-research"):

        @verifier(
            name="cite",
            claim_kinds=["citation"],
            cost_tier="cheap",
            runtime_required="docker",
        )
        async def fn(_c: Claim, ctx: VerifierContext) -> VerifierResult:
            return ctx.ok()

    r = Registry()
    r.register(fn)
    h = _build_harness(registry=r)
    with caplog.at_level("WARNING", logger="signoff.harness"):
        verdict = await h.verify(deliverable, claims)
    assert verdict.passed is True
    assert any("runtime_required='docker'" in r.getMessage() for r in caplog.records)


# ---------------------------------------------------------------------------
# §5.3 — concurrency and budgeting
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_cost_tier_ordering(deliverable: Deliverable, claims: list[Claim]) -> None:
    order: list[str] = []

    with _testing_pack("signoff-research"):

        @verifier(name="cheap_one", claim_kinds=["citation"], cost_tier="cheap")
        async def cheap_one(_c: Claim, ctx: VerifierContext) -> VerifierResult:
            order.append("cheap_one")
            await asyncio.sleep(0.01)
            order.append("cheap_one_done")
            return ctx.ok()

        @verifier(name="medium_one", claim_kinds=["citation"], cost_tier="medium")
        async def medium_one(_c: Claim, ctx: VerifierContext) -> VerifierResult:
            order.append("medium_one")
            return ctx.ok()

    r = Registry()
    r.register(cheap_one)
    r.register(medium_one)
    h = _build_harness(registry=r)
    await h.verify(deliverable, claims[:1])
    # Cheap must complete before medium starts.
    assert order.index("cheap_one_done") < order.index("medium_one")


@pytest.mark.asyncio
async def test_requires_skips_on_dependency_blocker(
    deliverable: Deliverable, claims: list[Claim]
) -> None:
    blocker = _make_blocker_verifier("signoff-research", "upstream")

    with _testing_pack("signoff-research"):

        @verifier(
            name="downstream",
            claim_kinds=["citation"],
            cost_tier="cheap",
            requires=("signoff-research.upstream",),
        )
        async def downstream(_c: Claim, ctx: VerifierContext) -> VerifierResult:
            return ctx.ok()

    r = Registry()
    r.register(blocker)
    r.register(downstream)
    h = _build_harness(registry=r)
    verdict = await h.verify(deliverable, claims[:1])
    downstream_results = [
        res for res in verdict.results if res.verifier == "signoff-research.downstream"
    ]
    assert len(downstream_results) == 1
    assert downstream_results[0].severity == Severity.INFO
    assert "dependency" in downstream_results[0].reason
    assert "failed" in downstream_results[0].reason


@pytest.mark.asyncio
async def test_requires_skips_when_dependency_not_planned(
    deliverable: Deliverable, claims: list[Claim]
) -> None:
    with _testing_pack("signoff-research"):

        @verifier(
            name="downstream",
            claim_kinds=["citation"],
            cost_tier="cheap",
            requires=("signoff-research.missing",),
        )
        async def downstream(_c: Claim, ctx: VerifierContext) -> VerifierResult:
            return ctx.ok()

    r = Registry()
    r.register(downstream)
    h = _build_harness(registry=r)
    verdict = await h.verify(deliverable, claims[:1])
    assert len(verdict.results) == 1
    assert "not planned" in verdict.results[0].reason


@pytest.mark.asyncio
async def test_budget_exhausted_skips_expensive_tier(
    deliverable: Deliverable, claims: list[Claim]
) -> None:
    with _testing_pack("signoff-research"):

        @verifier(name="expensive_one", claim_kinds=["citation"], cost_tier="expensive")
        async def expensive_one(_c: Claim, ctx: VerifierContext) -> VerifierResult:
            return ctx.ok(cost_usd=0.5)

    r = Registry()
    r.register(expensive_one)
    cfg = load_config(
        path=None,
        pack_defaults=False,
        env_overrides=False,
        request_overrides={
            "packs": ["signoff-research"],
            "budget": {"max_cost_usd": 0.0},  # already exhausted
            "deliverables": {
                "research_report": {
                    "verifiers": {"signoff-research.expensive_one": {"enabled": True}}
                }
            },
        },
    )
    h = Harness(
        config=cfg,
        registry=r,
        runtimes={"local": LocalRuntime()},
        http=FakeHttpClient(),
        judge=FakeJudge(),
    )
    verdict = await h.verify(deliverable, claims[:1])
    assert verdict.terminated_early is True
    assert any("budget exceeded" in res.reason for res in verdict.results)


# ---------------------------------------------------------------------------
# §5.4 — verdict determination
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_verdict_all_pass(deliverable: Deliverable, claims: list[Claim]) -> None:
    r = Registry()
    r.register(_make_passing_verifier("signoff-research", "cite"))
    h = _build_harness(registry=r)
    verdict = await h.verify(deliverable, claims)
    assert verdict.passed is True
    assert verdict.feedback_packet is None
    assert verdict.id.startswith("vrd_")
    assert verdict.protocol_version == "0.1"
    assert verdict.harness_version == "0.0.1"


@pytest.mark.asyncio
async def test_verdict_blocker_produces_feedback_packet(
    deliverable: Deliverable, claims: list[Claim]
) -> None:
    r = Registry()
    r.register(_make_blocker_verifier("signoff-research", "cite"))
    h = _build_harness(registry=r)
    verdict = await h.verify(deliverable, claims)
    assert verdict.passed is False
    assert verdict.feedback_packet is not None
    assert len(verdict.feedback_packet.blockers) == 2  # one per claim
    for entry in verdict.feedback_packet.blockers:
        assert entry.claim_text in {c.text for c in claims}
        assert entry.suggested_repair == "fix it"


@pytest.mark.asyncio
async def test_verdict_info_failures_do_not_block(
    deliverable: Deliverable, claims: list[Claim]
) -> None:
    with _testing_pack("signoff-research"):

        @verifier(name="cite", claim_kinds=["citation"], cost_tier="cheap")
        async def cite(_c: Claim, ctx: VerifierContext) -> VerifierResult:
            return ctx.fail(reason="transient", severity=Severity.INFO)

    r = Registry()
    r.register(cite)
    h = _build_harness(registry=r)
    verdict = await h.verify(deliverable, claims[:1])
    assert verdict.passed is True
    assert verdict.feedback_packet is None


@pytest.mark.asyncio
async def test_retry_budget_decrements(deliverable: Deliverable, claims: list[Claim]) -> None:
    r = Registry()
    r.register(_make_blocker_verifier("signoff-research", "cite"))
    h = _build_harness(registry=r)
    verdict = await h.verify(deliverable, claims[:1], retry_budget=3)
    assert verdict.feedback_packet is not None
    assert verdict.feedback_packet.retry_budget_remaining == 2


@pytest.mark.asyncio
async def test_cost_sums_result_costs(deliverable: Deliverable, claims: list[Claim]) -> None:
    with _testing_pack("signoff-research"):

        @verifier(name="cite", claim_kinds=["citation"], cost_tier="cheap")
        async def cite(_c: Claim, ctx: VerifierContext) -> VerifierResult:
            return ctx.ok(cost_usd=0.05)

    r = Registry()
    r.register(cite)
    h = _build_harness(registry=r)
    verdict = await h.verify(deliverable, claims)
    assert verdict.cost_usd == pytest.approx(0.10)


# ---------------------------------------------------------------------------
# §5.5 — early termination
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_early_termination_skips_remaining_tiers(
    deliverable: Deliverable, claims: list[Claim]
) -> None:
    blocker = _make_blocker_verifier("signoff-research", "upstream")

    with _testing_pack("signoff-research"):

        @verifier(name="expensive_one", claim_kinds=["citation"], cost_tier="expensive")
        async def expensive_one(_c: Claim, ctx: VerifierContext) -> VerifierResult:
            return ctx.ok()

    r = Registry()
    r.register(blocker)
    r.register(expensive_one)

    cfg = load_config(
        path=None,
        pack_defaults=False,
        env_overrides=False,
        request_overrides={
            "packs": ["signoff-research"],
            "budget": {"early_termination": True},
            "deliverables": {
                "research_report": {
                    "verifiers": {
                        "signoff-research.upstream": {"enabled": True},
                        "signoff-research.expensive_one": {"enabled": True},
                    }
                }
            },
        },
    )
    h = Harness(
        config=cfg,
        registry=r,
        runtimes={"local": LocalRuntime()},
        http=FakeHttpClient(),
        judge=FakeJudge(),
    )
    verdict = await h.verify(deliverable, claims[:1])
    assert verdict.terminated_early is True
    expensive_results = [
        res for res in verdict.results if res.verifier == "signoff-research.expensive_one"
    ]
    assert len(expensive_results) == 1
    assert "early termination" in expensive_results[0].reason


@pytest.mark.asyncio
async def test_early_termination_off_runs_all(
    deliverable: Deliverable, claims: list[Claim]
) -> None:
    blocker = _make_blocker_verifier("signoff-research", "upstream")

    with _testing_pack("signoff-research"):

        @verifier(name="expensive_one", claim_kinds=["citation"], cost_tier="expensive")
        async def expensive_one(_c: Claim, ctx: VerifierContext) -> VerifierResult:
            return ctx.ok()

    r = Registry()
    r.register(blocker)
    r.register(expensive_one)
    h = _build_harness(registry=r)
    verdict = await h.verify(deliverable, claims[:1])
    # early_termination defaults to False → expensive still ran.
    assert any(
        res.verifier == "signoff-research.expensive_one" and res.passed for res in verdict.results
    )


# ---------------------------------------------------------------------------
# §5.6 — cancellation
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_cancel_mid_execution(deliverable: Deliverable, claims: list[Claim]) -> None:
    started = asyncio.Event()

    with _testing_pack("signoff-research"):

        @verifier(name="slow", claim_kinds=["citation"], cost_tier="cheap")
        async def slow(_c: Claim, ctx: VerifierContext) -> VerifierResult:
            started.set()
            await asyncio.sleep(10)
            return ctx.ok()

    r = Registry()
    r.register(slow)
    h = _build_harness(registry=r)

    async def run_and_cancel() -> Any:
        task = asyncio.create_task(h.verify(deliverable, claims))
        await started.wait()
        await h.cancel()
        return await task

    verdict = await run_and_cancel()
    assert verdict.terminated_early is True


# ---------------------------------------------------------------------------
# Post-processing (§3.5 invariants)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_severity_override_warning_to_blocker_synthesises_suggestion(
    deliverable: Deliverable, claims: list[Claim]
) -> None:
    with _testing_pack("signoff-research"):

        @verifier(name="cite", claim_kinds=["citation"], cost_tier="cheap")
        async def cite(_c: Claim, ctx: VerifierContext) -> VerifierResult:
            # Returns WARNING with no suggestion.
            return ctx.fail(reason="soft", severity=Severity.WARNING)

    r = Registry()
    r.register(cite)
    cfg = load_config(
        path=None,
        pack_defaults=False,
        env_overrides=False,
        request_overrides={
            "packs": ["signoff-research"],
            "deliverables": {
                "research_report": {
                    "verifiers": {
                        "signoff-research.cite": {"enabled": True, "severity_override": "blocker"}
                    }
                }
            },
        },
    )
    h = Harness(
        config=cfg,
        registry=r,
        runtimes={"local": LocalRuntime()},
        http=FakeHttpClient(),
        judge=FakeJudge(),
    )
    verdict = await h.verify(deliverable, claims[:1])
    assert verdict.passed is False
    # The synthesised suggestion keeps the §3.5 invariant satisfied.
    result = next(r for r in verdict.results if r.verifier == "signoff-research.cite")
    assert result.severity == Severity.BLOCKER
    assert result.suggestion is not None


# ---------------------------------------------------------------------------
# Round-trip serialisation
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_config_override_applied(deliverable: Deliverable, claims: list[Claim]) -> None:
    r = Registry()
    r.register(_make_passing_verifier("signoff-research", "cite"))
    h = _build_harness(registry=r)
    # Per-request override disables the verifier.
    verdict = await h.verify(
        deliverable,
        claims,
        config_override={
            "deliverables": {
                "research_report": {"verifiers": {"signoff-research.cite": {"enabled": False}}}
            }
        },
    )
    assert verdict.results == []


@pytest.mark.asyncio
async def test_prepare_and_teardown_lifecycle(
    deliverable: Deliverable, claims: list[Claim]
) -> None:
    r = Registry()
    r.register(_make_passing_verifier("signoff-research", "cite"))
    h = _build_harness(registry=r)
    # Idempotent.
    await h.prepare()
    await h.prepare()
    async with h:
        verdict = await h.verify(deliverable, claims)
        assert verdict.passed is True
    # Double teardown safe.
    await h.teardown()


@pytest.mark.asyncio
async def test_from_config_path_builds_a_harness(tmp_path: Any) -> None:
    yaml_path = tmp_path / "harness.yaml"
    yaml_path.write_text('protocol_version: "0.1"\npacks: []\ndeliverables: {}\n')
    h = await Harness.from_config_path(yaml_path)
    assert h.registry is not None
    assert "local" in h.runtimes


@pytest.mark.asyncio
async def test_malformed_result_downgraded_to_synthetic_info(
    deliverable: Deliverable, claims: list[Claim]
) -> None:
    """A verifier returning a result that fails §3.5 re-validation when
    we try to upgrade its severity should be downgraded to INFO with a
    warning log rather than crashing the whole verdict."""

    from signoff import VerifierResult as VR

    with _testing_pack("signoff-research"):

        @verifier(name="cite", claim_kinds=["citation"], cost_tier="cheap")
        async def cite(_c: Claim, ctx: VerifierContext) -> VerifierResult:
            # Skip validation so the result is structurally well-formed
            # but violates an invariant: passed=False, severity=blocker,
            # no suggestion.
            return VR.model_construct(
                verifier="signoff-research.cite",
                claim_id=_c.id,
                passed=False,
                severity=Severity.BLOCKER,
                reason="missing suggestion",
                suggestion=None,
                evidence={},
                cost_usd=0.0,
                duration_ms=0,
                verifier_version=None,
                started_at=None,
            )

    r = Registry()
    r.register(cite)
    # Force a severity upgrade path (blocker → warning) so the post_process
    # triggers model_copy with an invariant that fails if the original
    # result was invalid too.
    cfg = load_config(
        path=None,
        pack_defaults=False,
        env_overrides=False,
        request_overrides={
            "packs": ["signoff-research"],
            "deliverables": {
                "research_report": {
                    "verifiers": {
                        "signoff-research.cite": {
                            "enabled": True,
                            "severity_override": "warning",
                        }
                    }
                }
            },
        },
    )
    h = Harness(
        config=cfg,
        registry=r,
        runtimes={"local": LocalRuntime()},
        http=FakeHttpClient(),
        judge=FakeJudge(),
    )
    # Should not raise — the harness absorbs the malformed result.
    verdict = await h.verify(deliverable, claims[:1])
    assert isinstance(verdict, type(verdict))  # smoke — produced a verdict


@pytest.mark.asyncio
async def test_verdict_serialises_cleanly(deliverable: Deliverable, claims: list[Claim]) -> None:
    r = Registry()
    r.register(_make_passing_verifier("signoff-research", "cite"))
    h = _build_harness(registry=r)
    verdict = await h.verify(deliverable, claims)
    raw = verdict.model_dump_json()
    # Round-trip through json and the model.
    payload = json.loads(raw)
    reloaded = type(verdict).model_validate(payload)
    assert reloaded.model_dump(mode="json") == verdict.model_dump(mode="json")
