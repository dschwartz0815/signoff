"""Structural checks on the :class:`Runtime` protocol surface."""

from __future__ import annotations

import pytest
from pydantic import ValidationError
from signoff import LocalRuntime, Runtime, RuntimePolicy, VerifierMeta


def test_local_runtime_satisfies_runtime_protocol() -> None:
    rt = LocalRuntime()
    assert isinstance(rt, Runtime)
    assert rt.runtime_id == "local"


def test_verifier_meta_fully_qualified_name() -> None:
    meta = VerifierMeta(
        name="citation_existence",
        pack="signoff-research",
        claim_kinds=("citation",),
        cost_tier="cheap",
        concurrency=1,
    )
    assert meta.fully_qualified_name == "signoff-research.citation_existence"
    # Dataclass is frozen → immutable attribution.
    with pytest.raises(Exception):  # FrozenInstanceError
        meta.name = "x"  # type: ignore[misc]


def test_verifier_meta_defaults() -> None:
    meta = VerifierMeta(name="n", pack="p", claim_kinds=("*",), cost_tier="medium", concurrency=4)
    assert meta.timeout_seconds == 30
    assert meta.version is None
    assert meta.requires == ()
    assert meta.runtime_required is None


def test_runtime_policy_defaults_validate() -> None:
    p = RuntimePolicy()
    assert p.timeout_seconds == 30
    assert p.cpu_limit is None
    assert p.memory_limit_bytes is None
    assert p.network == "open"
    assert p.network_allowlist == []


def test_runtime_policy_rejects_nonpositive_timeout() -> None:
    with pytest.raises(ValidationError):
        RuntimePolicy(timeout_seconds=0)


def test_runtime_policy_rejects_unknown_fields() -> None:
    with pytest.raises(ValidationError):
        RuntimePolicy.model_validate({"image": "foo"})
