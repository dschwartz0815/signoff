"""Tests for :mod:`signoff.verifier`."""

from __future__ import annotations

from typing import Any

import pytest
from signoff import Claim, VerifierContext
from signoff.verifier import _testing_pack, verifier

# ---------------------------------------------------------------------------
# happy path
# ---------------------------------------------------------------------------


def test_valid_decorator_attaches_meta() -> None:
    with _testing_pack("signoff-research"):

        @verifier(
            name="citation_existence",
            claim_kinds=["citation"],
            cost_tier="cheap",
            concurrency=10,
            timeout_seconds=15,
            version="0.1.0",
            requires=("signoff-research.url_parse",),
            runtime_required="local",
        )
        async def fn(claim: Claim, ctx: VerifierContext) -> Any:
            return ctx.ok()

    meta = fn.signoff_meta
    assert meta.name == "citation_existence"
    assert meta.pack == "signoff-research"
    assert meta.fully_qualified_name == "signoff-research.citation_existence"
    assert meta.claim_kinds == ("citation",)
    assert meta.cost_tier == "cheap"
    assert meta.concurrency == 10
    assert meta.timeout_seconds == 15
    assert meta.version == "0.1.0"
    assert meta.requires == ("signoff-research.url_parse",)
    assert meta.runtime_required == "local"


def test_whole_deliverable_verifier_star_kind() -> None:
    with _testing_pack("signoff-research"):

        @verifier(name="overall_coherence", claim_kinds="*", cost_tier="medium")
        async def fn(_claim: Claim, _ctx: VerifierContext) -> Any:
            return None

    assert fn.signoff_meta.claim_kinds == ("*",)


def test_list_star_single_is_ok() -> None:
    with _testing_pack("signoff-research"):

        @verifier(name="overall", claim_kinds=["*"], cost_tier="cheap")
        async def fn(_claim: Claim, _ctx: VerifierContext) -> Any:
            return None

    assert fn.signoff_meta.claim_kinds == ("*",)


def test_pack_namespaced_kind_accepted() -> None:
    with _testing_pack("signoff-legal"):

        @verifier(
            name="check_clause",
            claim_kinds=["legal.clause_reference"],
            cost_tier="cheap",
        )
        async def fn(_c: Claim, _x: VerifierContext) -> Any:
            return None

    assert fn.signoff_meta.claim_kinds == ("legal.clause_reference",)


# ---------------------------------------------------------------------------
# validation — name
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "bad_name",
    ["", "Bad", "has-dash", "has space", "9leading", "x" * 65],
)
def test_invalid_name_raises(bad_name: str) -> None:
    with _testing_pack("signoff-research"), pytest.raises(ValueError, match="name="):

        @verifier(name=bad_name, claim_kinds=["citation"], cost_tier="cheap")
        async def fn(_c: Claim, _x: VerifierContext) -> Any:
            return None


# ---------------------------------------------------------------------------
# validation — claim_kinds
# ---------------------------------------------------------------------------


def test_unscoped_unknown_kind_rejected() -> None:
    with _testing_pack("signoff-research"), pytest.raises(ValueError, match=r"§3\.3\.1"):

        @verifier(name="x", claim_kinds=["made_up"], cost_tier="cheap")
        async def fn(_c: Claim, _x: VerifierContext) -> Any:
            return None


def test_empty_claim_kinds_rejected() -> None:
    with _testing_pack("signoff-research"), pytest.raises(ValueError, match="non-empty"):

        @verifier(name="x", claim_kinds=[], cost_tier="cheap")
        async def fn(_c: Claim, _x: VerifierContext) -> Any:
            return None


def test_mixed_star_and_specific_rejected() -> None:
    with _testing_pack("signoff-research"), pytest.raises(ValueError, match="cannot mix"):

        @verifier(name="x", claim_kinds=["*", "citation"], cost_tier="cheap")
        async def fn(_c: Claim, _x: VerifierContext) -> Any:
            return None


def test_namespace_form_rejected_when_malformed() -> None:
    with _testing_pack("signoff-research"), pytest.raises(ValueError, match="pack namespace"):

        @verifier(name="x", claim_kinds=["Legal.Foo"], cost_tier="cheap")
        async def fn(_c: Claim, _x: VerifierContext) -> Any:
            return None


# ---------------------------------------------------------------------------
# validation — other fields
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("bad_tier", ["free", "costly", "CHEAP", ""])
def test_invalid_cost_tier_rejected(bad_tier: str) -> None:
    with _testing_pack("signoff-research"), pytest.raises(ValueError, match="cost_tier"):

        @verifier(name="x", claim_kinds=["citation"], cost_tier=bad_tier)  # type: ignore[arg-type]
        async def fn(_c: Claim, _x: VerifierContext) -> Any:
            return None


@pytest.mark.parametrize("bad_n", [0, -1, True])  # bool is not a valid int here
def test_concurrency_must_be_positive_int(bad_n: int | bool) -> None:
    with _testing_pack("signoff-research"), pytest.raises(ValueError, match="concurrency"):

        @verifier(
            name="x",
            claim_kinds=["citation"],
            cost_tier="cheap",
            concurrency=bad_n,  # type: ignore[arg-type]
        )
        async def fn(_c: Claim, _x: VerifierContext) -> Any:
            return None


def test_timeout_must_be_positive_int() -> None:
    with _testing_pack("signoff-research"), pytest.raises(ValueError, match="timeout_seconds"):

        @verifier(
            name="x",
            claim_kinds=["citation"],
            cost_tier="cheap",
            timeout_seconds=0,
        )
        async def fn(_c: Claim, _x: VerifierContext) -> Any:
            return None


def test_requires_must_be_fully_qualified() -> None:
    with _testing_pack("signoff-research"), pytest.raises(ValueError, match=r"§4\.1"):

        @verifier(
            name="x",
            claim_kinds=["citation"],
            cost_tier="cheap",
            requires=["unqualified"],
        )
        async def fn(_c: Claim, _x: VerifierContext) -> Any:
            return None


def test_runtime_required_must_be_in_allowed_set() -> None:
    with _testing_pack("signoff-research"), pytest.raises(ValueError, match="runtime_required"):

        @verifier(
            name="x",
            claim_kinds=["citation"],
            cost_tier="cheap",
            runtime_required="firecracker",  # type: ignore[arg-type]
        )
        async def fn(_c: Claim, _x: VerifierContext) -> Any:
            return None


# ---------------------------------------------------------------------------
# signature validation
# ---------------------------------------------------------------------------


def test_sync_function_rejected() -> None:
    with _testing_pack("signoff-research"), pytest.raises(TypeError, match="async"):

        @verifier(name="x", claim_kinds=["citation"], cost_tier="cheap")
        def fn(_c: Claim, _x: VerifierContext) -> Any:  # not async — should raise
            return None


def test_wrong_arity_rejected() -> None:
    with _testing_pack("signoff-research"), pytest.raises(TypeError, match="two parameters"):

        @verifier(name="x", claim_kinds=["citation"], cost_tier="cheap")
        async def fn(_c: Claim) -> Any:  # one arg — should raise
            return None


def test_varargs_rejected() -> None:
    # Exactly two named params (passes arity) but one is **kwargs so we
    # catch the "positional-only" rule.
    with _testing_pack("signoff-research"), pytest.raises(TypeError, match="positional"):

        @verifier(name="x", claim_kinds=["citation"], cost_tier="cheap")
        async def fn(_c: Claim, **kwargs: Any) -> Any:
            return None


# ---------------------------------------------------------------------------
# pack-name detection
# ---------------------------------------------------------------------------


def test_pack_name_inferred_from_module(monkeypatch: pytest.MonkeyPatch) -> None:
    # Simulate a function living inside signoff_research.verifiers.foo.
    async def fn(_c: Claim, _x: VerifierContext) -> Any:
        return None

    monkeypatch.setattr(fn, "__module__", "signoff_research.verifiers.foo")
    wrapped = verifier(name="x", claim_kinds=["citation"], cost_tier="cheap")(fn)
    assert wrapped.signoff_meta.pack == "signoff-research"


def test_non_signoff_module_rejected() -> None:
    async def fn(_c: Claim, _x: VerifierContext) -> Any:
        return None

    fn.__module__ = "random_third_party.mod"
    with pytest.raises(ValueError, match="signoff_"):
        verifier(name="x", claim_kinds=["citation"], cost_tier="cheap")(fn)


def test_testing_pack_overrides_module() -> None:
    async def fn(_c: Claim, _x: VerifierContext) -> Any:
        return None

    fn.__module__ = "random_third_party.mod"
    with _testing_pack("signoff-custom"):
        wrapped = verifier(name="x", claim_kinds=["citation"], cost_tier="cheap")(fn)
    assert wrapped.signoff_meta.pack == "signoff-custom"
