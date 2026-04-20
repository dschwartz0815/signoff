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
# from_config_path: defaults vs overrides
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_from_config_path_with_no_overrides(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The headline API — ``await Harness.from_config_path("harness.yaml")``
    with no other arguments — must work and must emit INFO logs for
    the Phase 0 fake HTTP / judge defaults."""
    from signoff.registry import default_registry

    default_registry.clear()
    default_registry.register(_pass_cite())
    monkeypatch.setattr(
        "signoff.registry.Registry.discovered",
        classmethod(lambda cls: default_registry),
    )

    cfg_path = tmp_path / "harness.yaml"
    cfg_path.write_text(
        'protocol_version: "0.1"\n'
        "packs: [signoff-research]\n"
        "deliverables:\n"
        "  research_report:\n"
        "    verifiers:\n"
        "      signoff-research.citation_smoke:\n"
        "        enabled: true\n"
    )

    d = Deliverable(id="dlv_1", kind="research_report", content=None)
    c = [Claim(id="clm_a", text="x", kind="citation", evidence={"url": "u"})]

    with caplog.at_level("INFO", logger="signoff.harness"):
        async with await Harness.from_config_path(cfg_path, pack_defaults=False) as h:
            verdict = await h.verify(d, c)

    assert verdict.passed is True
    messages = "\n".join(r.getMessage() for r in caplog.records)
    # Default providers are httpx and anthropic — verify both routing
    # logs fire (the actual judge call happens inside a verifier, which
    # this trivial citation_smoke doesn't make).
    assert "HTTP provider=httpx" in messages
    assert "HttpxClient" in messages
    assert "Judge provider=anthropic" in messages
    assert "AnthropicJudge" in messages


@pytest.mark.asyncio
async def test_from_config_path_http_provider_fake_uses_fake_client(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Explicit ``http: { provider: fake }`` keeps the FakeHttpClient path."""
    from signoff.registry import default_registry
    from signoff.testing import FakeHttpClient as _Fake

    default_registry.clear()
    default_registry.register(_pass_cite())
    monkeypatch.setattr(
        "signoff.registry.Registry.discovered",
        classmethod(lambda cls: default_registry),
    )

    cfg_path = tmp_path / "harness.yaml"
    cfg_path.write_text(
        'protocol_version: "0.1"\n'
        "packs: [signoff-research]\n"
        "http:\n"
        "  provider: fake\n"
        "deliverables:\n"
        "  research_report:\n"
        "    verifiers:\n"
        "      signoff-research.citation_smoke:\n"
        "        enabled: true\n"
    )
    with caplog.at_level("INFO", logger="signoff.harness"):
        h = await Harness.from_config_path(cfg_path, pack_defaults=False)
    try:
        assert isinstance(h.http, _Fake)
    finally:
        await h.close() if hasattr(h, "close") else None
    messages = "\n".join(r.getMessage() for r in caplog.records)
    assert "HTTP provider=fake" in messages


@pytest.mark.asyncio
async def test_from_config_path_overrides_suppress_info_logs(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Passing real http/judge suppresses the Phase 0 INFO logs."""
    r = Registry()
    r.register(_pass_cite())
    cfg_path = tmp_path / "harness.yaml"
    cfg_path.write_text(
        'protocol_version: "0.1"\n'
        "packs: [signoff-research]\n"
        "deliverables:\n"
        "  research_report:\n"
        "    verifiers:\n"
        "      signoff-research.citation_smoke:\n"
        "        enabled: true\n"
    )

    with caplog.at_level("INFO", logger="signoff.harness"):
        h = await Harness.from_config_path(
            cfg_path,
            registry=r,
            runtimes=[LocalRuntime()],
            http=FakeHttpClient(),
            judge=FakeJudge(),
            pack_defaults=False,
        )
    msgs = "\n".join(rec.getMessage() for rec in caplog.records)
    assert "Using FakeHttpClient" not in msgs
    assert "Using FakeJudge" not in msgs
    assert h.registry is r


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


@pytest.mark.asyncio
async def test_from_config_path_httpx_fallback_when_signoff_http_missing(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """If ``signoff-http`` isn't installed, provider=httpx falls back to
    FakeHttpClient with a WARNING rather than ImportError."""
    from signoff.registry import default_registry
    from signoff.testing import FakeHttpClient as _Fake

    default_registry.clear()
    default_registry.register(_pass_cite())
    monkeypatch.setattr(
        "signoff.registry.Registry.discovered",
        classmethod(lambda cls: default_registry),
    )

    # Simulate the package being absent.
    import builtins

    real_import = builtins.__import__

    def fake_import(name, *args, **kwargs):  # type: ignore[no-untyped-def]
        if name == "signoff_http":
            raise ImportError("signoff_http not installed for this test")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake_import)

    cfg_path = tmp_path / "harness.yaml"
    cfg_path.write_text(
        'protocol_version: "0.1"\n'
        "packs: [signoff-research]\n"
        "deliverables:\n"
        "  research_report:\n"
        "    verifiers:\n"
        "      signoff-research.citation_smoke:\n"
        "        enabled: true\n"
    )
    with caplog.at_level("WARNING", logger="signoff.harness"):
        h = await Harness.from_config_path(cfg_path, pack_defaults=False)
    assert isinstance(h.http, _Fake)
    messages = "\n".join(r.getMessage() for r in caplog.records)
    assert "signoff-http is not installed" in messages


@pytest.mark.asyncio
async def test_from_config_path_judge_provider_fake_uses_fake_client(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Explicit ``judge: { provider: fake }`` keeps the FakeJudge path."""
    from signoff.registry import default_registry
    from signoff.testing import FakeJudge as _FakeJudge

    default_registry.clear()
    default_registry.register(_pass_cite())
    monkeypatch.setattr(
        "signoff.registry.Registry.discovered",
        classmethod(lambda cls: default_registry),
    )

    cfg_path = tmp_path / "harness.yaml"
    cfg_path.write_text(
        'protocol_version: "0.1"\n'
        "packs: [signoff-research]\n"
        "judge:\n"
        "  provider: fake\n"
        "deliverables:\n"
        "  research_report:\n"
        "    verifiers:\n"
        "      signoff-research.citation_smoke:\n"
        "        enabled: true\n"
    )
    with caplog.at_level("INFO", logger="signoff.harness"):
        h = await Harness.from_config_path(cfg_path, pack_defaults=False)
    assert isinstance(h.judge, _FakeJudge)
    messages = "\n".join(r.getMessage() for r in caplog.records)
    assert "Judge provider=fake" in messages


@pytest.mark.asyncio
async def test_from_config_path_judge_fallback_when_signoff_judge_missing(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """provider=anthropic falls back to FakeJudge + WARNING when the
    signoff-judge package is not importable."""
    from signoff.registry import default_registry
    from signoff.testing import FakeJudge as _FakeJudge

    default_registry.clear()
    default_registry.register(_pass_cite())
    monkeypatch.setattr(
        "signoff.registry.Registry.discovered",
        classmethod(lambda cls: default_registry),
    )

    import builtins

    real_import = builtins.__import__

    def fake_import(name, *args, **kwargs):  # type: ignore[no-untyped-def]
        if name == "signoff_judge":
            raise ImportError("signoff_judge not installed for this test")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake_import)

    cfg_path = tmp_path / "harness.yaml"
    cfg_path.write_text(
        'protocol_version: "0.1"\n'
        "packs: [signoff-research]\n"
        "judge:\n"
        "  provider: anthropic\n"
        "deliverables:\n"
        "  research_report:\n"
        "    verifiers:\n"
        "      signoff-research.citation_smoke:\n"
        "        enabled: true\n"
    )
    with caplog.at_level("WARNING", logger="signoff.harness"):
        h = await Harness.from_config_path(cfg_path, pack_defaults=False)
    assert isinstance(h.judge, _FakeJudge)
    messages = "\n".join(r.getMessage() for r in caplog.records)
    assert "signoff-judge is not installed" in messages


@pytest.mark.asyncio
async def test_from_config_path_skips_docker_runtime_when_not_requested(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Default config uses runtime=local; no DockerRuntime should be
    built even if signoff-runtime-docker is importable."""
    from signoff.registry import default_registry

    default_registry.clear()
    default_registry.register(_pass_cite())
    monkeypatch.setattr(
        "signoff.registry.Registry.discovered",
        classmethod(lambda cls: default_registry),
    )
    cfg_path = tmp_path / "harness.yaml"
    cfg_path.write_text(
        'protocol_version: "0.1"\n'
        "packs: [signoff-research]\n"
        "deliverables:\n"
        "  research_report:\n"
        "    verifiers:\n"
        "      signoff-research.citation_smoke:\n"
        "        enabled: true\n"
    )
    h = await Harness.from_config_path(cfg_path, pack_defaults=False)
    assert set(h.runtimes.keys()) == {"local"}


@pytest.mark.asyncio
async def test_from_config_path_builds_docker_runtime_when_requested(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``runtime: default: docker`` triggers DockerRuntime construction
    when the package is installed."""
    from signoff.registry import default_registry

    default_registry.clear()
    default_registry.register(_pass_cite())
    monkeypatch.setattr(
        "signoff.registry.Registry.discovered",
        classmethod(lambda cls: default_registry),
    )
    cfg_path = tmp_path / "harness.yaml"
    cfg_path.write_text(
        'protocol_version: "0.1"\n'
        "packs: [signoff-research]\n"
        "runtime:\n"
        "  default: docker\n"
        "deliverables:\n"
        "  research_report:\n"
        "    verifiers:\n"
        "      signoff-research.citation_smoke:\n"
        "        enabled: true\n"
    )
    h = await Harness.from_config_path(cfg_path, pack_defaults=False)
    assert set(h.runtimes.keys()) == {"local", "docker"}


@pytest.mark.asyncio
async def test_from_config_path_docker_fallback_when_package_missing(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """With runtime=docker but no signoff_runtime_docker installed,
    log a WARNING and fall back to LocalRuntime only."""
    from signoff.registry import default_registry

    default_registry.clear()
    default_registry.register(_pass_cite())
    monkeypatch.setattr(
        "signoff.registry.Registry.discovered",
        classmethod(lambda cls: default_registry),
    )
    import builtins

    real_import = builtins.__import__

    def fake_import(name, *args, **kwargs):  # type: ignore[no-untyped-def]
        if name == "signoff_runtime_docker":
            raise ImportError("pretend not installed")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake_import)
    cfg_path = tmp_path / "harness.yaml"
    cfg_path.write_text(
        'protocol_version: "0.1"\n'
        "packs: [signoff-research]\n"
        "runtime:\n"
        "  default: docker\n"
        "deliverables:\n"
        "  research_report:\n"
        "    verifiers:\n"
        "      signoff-research.citation_smoke:\n"
        "        enabled: true\n"
    )
    with caplog.at_level("WARNING", logger="signoff.harness"):
        h = await Harness.from_config_path(cfg_path, pack_defaults=False)
    assert set(h.runtimes.keys()) == {"local"}
    messages = "\n".join(r.getMessage() for r in caplog.records)
    assert "signoff-runtime-docker is not installed" in messages


@pytest.mark.asyncio
async def test_docker_runtime_policy_image_wins_over_env(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Regression: ``runtime_policy.docker.image`` in YAML must win
    over ``SIGNOFF_DOCKER_DEFAULT_IMAGE`` in the environment.

    Before this fix, the harness constructed
    ``DockerRuntime(DockerRuntimeConfig())`` with no arguments, so
    the YAML field was silently ignored — the only way to set the
    image was the env var. Now the harness reads
    ``runtime_policy.docker.image`` from the config and passes it
    as an init kwarg, which pydantic-settings ranks above env
    vars. (See docs/configuration.md § "SIGNOFF_DOCKER_*" for the
    full precedence story.)
    """
    from signoff.registry import default_registry

    default_registry.clear()
    default_registry.register(_pass_cite())
    monkeypatch.setattr(
        "signoff.registry.Registry.discovered",
        classmethod(lambda cls: default_registry),
    )
    monkeypatch.setenv("SIGNOFF_DOCKER_DEFAULT_IMAGE", "from-env:tag")
    monkeypatch.setenv("SIGNOFF_DOCKER_VERIFY_SIGNATURES", "false")

    cfg_path = tmp_path / "harness.yaml"
    cfg_path.write_text(
        'protocol_version: "0.1"\n'
        "packs: [signoff-research]\n"
        "runtime:\n"
        "  default: docker\n"
        "runtime_policy:\n"
        "  docker:\n"
        "    image: from-yaml:tag\n"
        "deliverables:\n"
        "  research_report:\n"
        "    verifiers:\n"
        "      signoff-research.citation_smoke:\n"
        "        enabled: true\n"
    )
    h = await Harness.from_config_path(cfg_path, pack_defaults=False)
    docker_runtime = h.runtimes["docker"]
    assert docker_runtime._config.default_image == "from-yaml:tag"  # type: ignore[attr-defined]


@pytest.mark.asyncio
async def test_docker_env_fills_defaults_when_yaml_silent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """When YAML doesn't mention a field, the env var (or code
    default) supplies it — pydantic-settings precedence."""
    from signoff.registry import default_registry

    default_registry.clear()
    default_registry.register(_pass_cite())
    monkeypatch.setattr(
        "signoff.registry.Registry.discovered",
        classmethod(lambda cls: default_registry),
    )
    monkeypatch.setenv("SIGNOFF_DOCKER_DEFAULT_IMAGE", "from-env:tag")
    monkeypatch.setenv("SIGNOFF_DOCKER_VERIFY_SIGNATURES", "false")

    cfg_path = tmp_path / "harness.yaml"
    cfg_path.write_text(
        'protocol_version: "0.1"\n'
        "packs: [signoff-research]\n"
        "runtime:\n"
        "  default: docker\n"
        "deliverables:\n"
        "  research_report:\n"
        "    verifiers:\n"
        "      signoff-research.citation_smoke:\n"
        "        enabled: true\n"
    )
    h = await Harness.from_config_path(cfg_path, pack_defaults=False)
    docker_runtime = h.runtimes["docker"]
    assert docker_runtime._config.default_image == "from-env:tag"  # type: ignore[attr-defined]
