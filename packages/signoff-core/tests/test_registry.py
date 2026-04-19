"""Tests for :mod:`signoff.registry`."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

import pytest
from signoff import Claim, VerifierContext
from signoff.registry import ENTRY_POINT_GROUP, Registry
from signoff.verifier import _testing_pack, verifier

# ---------------------------------------------------------------------------
# Fixture: verifier factory
# ---------------------------------------------------------------------------


def _make_verifier(*, pack: str, name: str, claim_kinds: list[str] | str = ("citation",)) -> Any:
    kinds = claim_kinds if claim_kinds == "*" else list(claim_kinds)
    with _testing_pack(pack):

        @verifier(name=name, claim_kinds=kinds, cost_tier="cheap")
        async def fn(_c: Claim, _x: VerifierContext) -> Any:
            return None

    return fn


# ---------------------------------------------------------------------------
# Manual register / get / list
# ---------------------------------------------------------------------------


def test_register_and_get_roundtrip() -> None:
    r = Registry()
    v = _make_verifier(pack="signoff-research", name="citation_existence")
    r.register(v)
    assert "signoff-research.citation_existence" in r
    assert r.get("signoff-research.citation_existence") is v
    assert len(r) == 1


def test_register_requires_signoff_meta() -> None:
    r = Registry()
    with pytest.raises(TypeError, match="signoff_meta"):

        async def plain_fn(_c: Claim, _x: VerifierContext) -> None:
            return None

        r.register(plain_fn)  # type: ignore[arg-type]


def test_get_missing_raises_keyerror() -> None:
    r = Registry()
    with pytest.raises(KeyError, match="fully-qualified name"):
        r.get("missing.verifier")


def test_list_all_sorted_by_fqn() -> None:
    r = Registry()
    r.register(_make_verifier(pack="signoff-research", name="zzz"))
    r.register(_make_verifier(pack="signoff-code", name="aaa"))
    r.register(_make_verifier(pack="signoff-research", name="aaa"))
    fqns = [m.fully_qualified_name for m in r.list_all()]
    assert fqns == [
        "signoff-code.aaa",
        "signoff-research.aaa",
        "signoff-research.zzz",
    ]


def test_for_claim_kind_matches_exact_and_star() -> None:
    r = Registry()
    cite = _make_verifier(pack="signoff-research", name="cite")
    quant = _make_verifier(pack="signoff-research", name="quant", claim_kinds=["quantitative"])
    overall = _make_verifier(pack="signoff-research", name="overall", claim_kinds="*")
    r.register(cite)
    r.register(quant)
    r.register(overall)

    # 'citation' matches the citation verifier + the * verifier.
    matched = r.for_claim_kind("citation")
    assert sorted(_require_meta(v).fully_qualified_name for v in matched) == [
        "signoff-research.cite",
        "signoff-research.overall",
    ]
    # A kind nobody handles matches only the * verifier.
    matched = r.for_claim_kind("policy")
    assert [_require_meta(v).fully_qualified_name for v in matched] == ["signoff-research.overall"]


def test_whole_deliverable_returns_only_star() -> None:
    r = Registry()
    r.register(_make_verifier(pack="signoff-research", name="cite"))
    overall = _make_verifier(pack="signoff-research", name="overall", claim_kinds="*")
    r.register(overall)
    result = r.whole_deliverable()
    assert [_require_meta(v).fully_qualified_name for v in result] == ["signoff-research.overall"]


def test_duplicate_registration_warns_and_replaces(
    caplog: pytest.LogCaptureFixture,
) -> None:
    r = Registry()
    first = _make_verifier(pack="signoff-research", name="cite")
    second = _make_verifier(pack="signoff-research", name="cite")
    r.register(first)
    with caplog.at_level(logging.WARNING, logger="signoff.registry"):
        r.register(second)
    assert any("Duplicate" in rec.getMessage() for rec in caplog.records)
    assert r.get("signoff-research.cite") is second


def test_discovered_returns_populated_registry(monkeypatch: pytest.MonkeyPatch) -> None:
    """Registry.discovered() is equivalent to Registry() + discover()."""
    v = _make_verifier(pack="signoff-research", name="cite")
    _patch_entry_points(monkeypatch, [_FakeEntryPoint(name="cite", value="m:v", _load_result=v)])
    r = Registry.discovered()
    assert len(r) == 1
    assert "signoff-research.cite" in r


def test_clear_removes_everything() -> None:
    r = Registry()
    r.register(_make_verifier(pack="signoff-research", name="cite"))
    assert len(r) == 1
    r.clear()
    assert len(r) == 0


# ---------------------------------------------------------------------------
# Entry-point discovery
# ---------------------------------------------------------------------------


@dataclass
class _FakeEntryPoint:
    name: str
    value: str
    group: str = ENTRY_POINT_GROUP
    _load_result: Any = None
    _load_exc: Exception | None = None

    def load(self) -> Any:
        if self._load_exc is not None:
            raise self._load_exc
        return self._load_result


def _patch_entry_points(monkeypatch: pytest.MonkeyPatch, eps: list[_FakeEntryPoint]) -> None:
    def fake_entry_points(*, group: str) -> list[_FakeEntryPoint]:
        assert group == ENTRY_POINT_GROUP
        return eps

    monkeypatch.setattr("signoff.registry.entry_points", fake_entry_points)


def test_discover_loads_entry_points(monkeypatch: pytest.MonkeyPatch) -> None:
    v1 = _make_verifier(pack="signoff-research", name="cite")
    v2 = _make_verifier(pack="signoff-research", name="quant", claim_kinds=["quantitative"])
    _patch_entry_points(
        monkeypatch,
        [
            _FakeEntryPoint(name="cite", value="mod:cite", _load_result=v1),
            _FakeEntryPoint(name="quant", value="mod:quant", _load_result=v2),
        ],
    )
    r = Registry()
    assert r.discover() == 2
    assert len(r) == 2
    assert "signoff-research.cite" in r
    assert "signoff-research.quant" in r


def test_discover_skips_bad_target_with_warning(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    good = _make_verifier(pack="signoff-research", name="cite")

    async def plain(_c: Claim, _x: VerifierContext) -> None:
        return None

    _patch_entry_points(
        monkeypatch,
        [
            _FakeEntryPoint(name="bad", value="mod:bad", _load_result=plain),
            _FakeEntryPoint(name="good", value="mod:good", _load_result=good),
        ],
    )
    r = Registry()
    with caplog.at_level(logging.WARNING, logger="signoff.registry"):
        count = r.discover()
    assert count == 1
    assert "signoff-research.cite" in r
    assert any("did not resolve to a @verifier" in rec.getMessage() for rec in caplog.records)


def test_discover_skips_import_errors(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    good = _make_verifier(pack="signoff-research", name="cite")
    _patch_entry_points(
        monkeypatch,
        [
            _FakeEntryPoint(
                name="boom",
                value="mod:boom",
                _load_exc=ImportError("no such module"),
            ),
            _FakeEntryPoint(name="good", value="mod:good", _load_result=good),
        ],
    )
    r = Registry()
    with caplog.at_level(logging.WARNING, logger="signoff.registry"):
        count = r.discover()
    assert count == 1
    assert "signoff-research.cite" in r
    assert any("Failed to load" in rec.getMessage() for rec in caplog.records)


def test_discover_is_idempotent(monkeypatch: pytest.MonkeyPatch) -> None:
    v = _make_verifier(pack="signoff-research", name="cite")
    _patch_entry_points(monkeypatch, [_FakeEntryPoint(name="cite", value="m:v", _load_result=v)])
    r = Registry()
    assert r.discover() == 1
    assert r.discover() == 1  # same count; register logs duplicate warning
    assert len(r) == 1


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _require_meta(v: Any) -> Any:
    return v.signoff_meta
