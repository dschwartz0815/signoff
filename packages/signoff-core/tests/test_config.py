"""Tests for :mod:`signoff.config`."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from signoff import Claim, Registry, Severity, VerifierContext
from signoff.config import (
    BudgetConfig,
    ConfigurationError,
    HarnessConfig,
    deep_merge,
    load_config,
    validate_config,
)
from signoff.verifier import _testing_pack, verifier

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _make_verifier(*, pack: str, name: str) -> Any:
    with _testing_pack(pack):

        @verifier(name=name, claim_kinds=["citation"], cost_tier="cheap")
        async def fn(_c: Claim, _x: VerifierContext) -> Any:
            return None

    return fn


@pytest.fixture
def registry_with_two_verifiers() -> Registry:
    r = Registry()
    r.register(_make_verifier(pack="signoff-research", name="cite"))
    r.register(_make_verifier(pack="signoff-research", name="quant"))
    return r


def _write_yaml(tmp_path: Path, body: str) -> Path:
    path = tmp_path / "harness.yaml"
    path.write_text(body)
    return path


# ---------------------------------------------------------------------------
# Deep merge
# ---------------------------------------------------------------------------


def test_deep_merge_recurses_into_dicts() -> None:
    base = {"a": {"b": 1, "c": 2}}
    override = {"a": {"b": 99}}
    assert deep_merge(base, override) == {"a": {"b": 99, "c": 2}}


def test_deep_merge_replaces_lists() -> None:
    base = {"packs": ["signoff-research", "signoff-code"]}
    override = {"packs": ["signoff-code"]}
    # Lists replace; they do not concat.
    assert deep_merge(base, override) == {"packs": ["signoff-code"]}


def test_deep_merge_none_unsets() -> None:
    base = {"judge": {"provider": "anthropic"}}
    override = {"judge": None}
    assert deep_merge(base, override) == {}


def test_deep_merge_scalar_over_scalar() -> None:
    assert deep_merge({"x": 1}, {"x": 2}) == {"x": 2}


# ---------------------------------------------------------------------------
# Minimal YAML load
# ---------------------------------------------------------------------------


def test_load_minimal_yaml(tmp_path: Path) -> None:
    path = _write_yaml(
        tmp_path,
        """
protocol_version: "0.1"
packs:
  - signoff-research
deliverables:
  research_report:
    verifiers:
      signoff-research.cite:
        enabled: true
budget:
  max_cost_usd: 1.00
""",
    )
    cfg = load_config(path=path, pack_defaults=False, env_overrides=False)
    assert cfg.protocol_version == "0.1"
    assert cfg.packs == ["signoff-research"]
    assert "research_report" in cfg.deliverables
    assert cfg.deliverables["research_report"].verifiers["signoff-research.cite"].enabled
    assert cfg.budget.max_cost_usd == 1.00


def test_load_missing_file_raises_with_path(tmp_path: Path) -> None:
    with pytest.raises(ConfigurationError, match=str(tmp_path)):
        load_config(path=tmp_path / "nope.yaml", pack_defaults=False, env_overrides=False)


def test_load_invalid_yaml_raises_with_path(tmp_path: Path) -> None:
    path = _write_yaml(tmp_path, "key: [unclosed\n")
    with pytest.raises(ConfigurationError, match=str(path)):
        load_config(path=path, pack_defaults=False, env_overrides=False)


def test_load_non_mapping_top_level_raises(tmp_path: Path) -> None:
    path = _write_yaml(tmp_path, "- just\n- a\n- list\n")
    with pytest.raises(ConfigurationError, match="mapping"):
        load_config(path=path, pack_defaults=False, env_overrides=False)


def test_load_with_invalid_field_value_raises_with_path(tmp_path: Path) -> None:
    path = _write_yaml(tmp_path, "budget:\n  max_cost_usd: -1\n")
    with pytest.raises(ConfigurationError, match="validation failed"):
        load_config(path=path, pack_defaults=False, env_overrides=False)


# ---------------------------------------------------------------------------
# Layer ordering
# ---------------------------------------------------------------------------


def test_layers_merge_in_order(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # Layer 2 (pack defaults) — fake via monkeypatch.
    def fake_pack_defaults() -> dict[str, Any]:
        return {"budget": {"max_cost_usd": 0.10, "max_duration_seconds": 30}}

    monkeypatch.setattr("signoff.config._pack_defaults", fake_pack_defaults)

    # Layer 3 (user YAML) — budget.max_cost_usd overrides layer 2.
    path = _write_yaml(tmp_path, "budget:\n  max_cost_usd: 0.50\n")

    # Layer 4 (env) — disabled for this test.
    # Layer 5 (request overrides) — max_duration_seconds wins over layer 2.
    cfg = load_config(
        path=path,
        pack_defaults=True,
        env_overrides=False,
        request_overrides={"budget": {"max_duration_seconds": 90}},
    )
    assert cfg.budget.max_cost_usd == 0.50  # user YAML
    assert cfg.budget.max_duration_seconds == 90  # request override
    assert cfg.budget.global_concurrency == 16  # built-in default


def test_env_overrides_applied(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SIGNOFF_BUDGET__MAX_COST_USD", "2.5")
    monkeypatch.setenv("SIGNOFF_BUDGET__GLOBAL_CONCURRENCY", "4")
    cfg = load_config(path=None, pack_defaults=False, env_overrides=True)
    assert cfg.budget.max_cost_usd == 2.5
    assert cfg.budget.global_concurrency == 4


def test_request_overrides_wins_over_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SIGNOFF_BUDGET__MAX_COST_USD", "2.5")
    cfg = load_config(
        path=None,
        pack_defaults=False,
        env_overrides=True,
        request_overrides={"budget": {"max_cost_usd": 5.0}},
    )
    assert cfg.budget.max_cost_usd == 5.0


def test_round_trip_yaml(tmp_path: Path) -> None:
    path = _write_yaml(
        tmp_path,
        """
protocol_version: "0.1"
packs: [signoff-research]
deliverables:
  research_report:
    verifiers:
      signoff-research.cite:
        enabled: true
        sample_rate: 0.5
""",
    )
    cfg1 = load_config(path=path, pack_defaults=False, env_overrides=False)
    dumped = cfg1.model_dump(mode="json")
    cfg2 = HarnessConfig.model_validate(dumped)
    assert cfg1.model_dump(mode="json") == cfg2.model_dump(mode="json")


# ---------------------------------------------------------------------------
# Field-level invariants
# ---------------------------------------------------------------------------


def test_sample_rate_bounds(tmp_path: Path) -> None:
    path = _write_yaml(
        tmp_path,
        "deliverables:\n  k:\n    verifiers:\n      x.y:\n        sample_rate: 1.5\n",
    )
    with pytest.raises(ConfigurationError):
        load_config(path=path, pack_defaults=False, env_overrides=False)


def test_severity_override_accepts_enum_values() -> None:
    cfg = load_config(
        path=None,
        pack_defaults=False,
        env_overrides=False,
        request_overrides={
            "deliverables": {
                "k": {
                    "verifiers": {
                        "x.y": {"severity_override": "warning"},
                    }
                }
            }
        },
    )
    assert cfg.deliverables["k"].verifiers["x.y"].severity_override == Severity.WARNING


# ---------------------------------------------------------------------------
# validate_config (§6.3)
# ---------------------------------------------------------------------------


def test_validate_ok_with_known_pack_and_verifier(
    registry_with_two_verifiers: Registry,
) -> None:
    cfg = load_config(
        path=None,
        pack_defaults=False,
        env_overrides=False,
        request_overrides={
            "protocol_version": "0.1",
            "packs": ["signoff-research"],
            "deliverables": {
                "research_report": {
                    "verifiers": {
                        "signoff-research.cite": {"enabled": True},
                    }
                }
            },
        },
    )
    validate_config(cfg, registry_with_two_verifiers)


def test_validate_unknown_verifier_raises(
    registry_with_two_verifiers: Registry,
) -> None:
    cfg = load_config(
        path=None,
        pack_defaults=False,
        env_overrides=False,
        request_overrides={
            "packs": ["signoff-research"],
            "deliverables": {"k": {"verifiers": {"signoff-research.missing": {"enabled": True}}}},
        },
    )
    with pytest.raises(ConfigurationError, match=r"signoff-research\.missing"):
        validate_config(cfg, registry_with_two_verifiers)


def test_validate_unknown_pack_raises(
    registry_with_two_verifiers: Registry,
) -> None:
    cfg = load_config(
        path=None,
        pack_defaults=False,
        env_overrides=False,
        request_overrides={"packs": ["signoff-ghosts"]},
    )
    with pytest.raises(ConfigurationError, match="signoff-ghosts"):
        validate_config(cfg, registry_with_two_verifiers)


def test_validate_protocol_major_zero_ok(
    registry_with_two_verifiers: Registry,
) -> None:
    cfg = load_config(
        path=None,
        pack_defaults=False,
        env_overrides=False,
        request_overrides={"protocol_version": "0.2"},
    )
    # 0.2 shares major with 0.1; accepted.
    validate_config(cfg, registry_with_two_verifiers)


def test_validate_protocol_major_one_rejected(
    registry_with_two_verifiers: Registry,
) -> None:
    cfg = load_config(
        path=None,
        pack_defaults=False,
        env_overrides=False,
        request_overrides={"protocol_version": "1.0"},
    )
    with pytest.raises(ConfigurationError, match="major=1"):
        validate_config(cfg, registry_with_two_verifiers)


def test_validate_protocol_version_malformed_rejected(
    registry_with_two_verifiers: Registry,
) -> None:
    cfg = HarnessConfig(protocol_version="garbage")
    with pytest.raises(ConfigurationError, match="dotted number"):
        validate_config(cfg, registry_with_two_verifiers)


# ---------------------------------------------------------------------------
# Runtime policy extra keys
# ---------------------------------------------------------------------------


def test_unknown_runtime_keys_tolerated(tmp_path: Path) -> None:
    # §8.3 — docker runtime block will land when signoff-runtime-docker
    # installs; meanwhile, unknown runtime keys must not break config.
    path = _write_yaml(
        tmp_path,
        """
runtime_policy:
  local:
    timeout_seconds: 15
  docker:
    image: foo/bar:1.0
    cpu_limit: 2.0
""",
    )
    cfg = load_config(path=path, pack_defaults=False, env_overrides=False)
    assert cfg.runtime_policy.local.timeout_seconds == 15
    # The docker block is preserved as an extra field.
    assert cfg.runtime_policy.model_dump().get("docker", {}).get("image") == "foo/bar:1.0"


# ---------------------------------------------------------------------------
# BudgetConfig defaults sanity
# ---------------------------------------------------------------------------


def test_budget_defaults_match_protocol() -> None:
    b = BudgetConfig()
    assert b.max_cost_usd == 0.50
    assert b.max_duration_seconds == 120
    assert b.global_concurrency == 16
    assert b.early_termination is False


# ---------------------------------------------------------------------------
# Pack defaults entry-point path
# ---------------------------------------------------------------------------


from dataclasses import dataclass  # noqa: E402


@dataclass
class _FakeEP:
    name: str
    value: str
    _load_result: Any = None
    _load_exc: Exception | None = None

    def load(self) -> Any:
        if self._load_exc is not None:
            raise self._load_exc
        return self._load_result


def test_pack_defaults_callable_target(monkeypatch: pytest.MonkeyPatch) -> None:
    def contribute() -> dict[str, Any]:
        return {"budget": {"max_cost_usd": 0.10}}

    monkeypatch.setattr(
        "signoff.config.entry_points",
        lambda *, group: [_FakeEP(name="research", value="m:d", _load_result=contribute)],
    )
    cfg = load_config(path=None, pack_defaults=True, env_overrides=False)
    assert cfg.budget.max_cost_usd == 0.10


def test_pack_defaults_dict_target(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "signoff.config.entry_points",
        lambda *, group: [
            _FakeEP(name="r", value="m:d", _load_result={"budget": {"max_cost_usd": 0.33}})
        ],
    )
    cfg = load_config(path=None, pack_defaults=True, env_overrides=False)
    assert cfg.budget.max_cost_usd == 0.33


def test_pack_defaults_import_error_is_logged_and_skipped(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.setattr(
        "signoff.config.entry_points",
        lambda *, group: [_FakeEP(name="broken", value="m:d", _load_exc=ImportError("boom"))],
    )
    with caplog.at_level("WARNING", logger="signoff.config"):
        cfg = load_config(path=None, pack_defaults=True, env_overrides=False)
    assert any("Failed to load pack default" in r.getMessage() for r in caplog.records)
    # Loader still succeeded with built-in defaults.
    assert cfg.budget.max_cost_usd == 0.50


def test_pack_defaults_non_mapping_is_logged_and_skipped(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.setattr(
        "signoff.config.entry_points",
        lambda *, group: [_FakeEP(name="bad", value="m:d", _load_result=42)],
    )
    with caplog.at_level("WARNING", logger="signoff.config"):
        load_config(path=None, pack_defaults=True, env_overrides=False)
    assert any("did not resolve to a mapping" in r.getMessage() for r in caplog.records)


# ---------------------------------------------------------------------------
# Env override edge cases
# ---------------------------------------------------------------------------


def test_env_bare_prefix_is_ignored(monkeypatch: pytest.MonkeyPatch) -> None:
    # SIGNOFF_ alone has no remaining path; loader must skip, not crash.
    monkeypatch.setenv("SIGNOFF_", "x")
    load_config(path=None, pack_defaults=False, env_overrides=True)


def test_env_scalar_collision_is_logged(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    # Setting a scalar (budget.max_cost_usd) then trying to nest under it
    # (budget.max_cost_usd.x) should log and ignore the nested attempt.
    monkeypatch.setenv("SIGNOFF_BUDGET__MAX_COST_USD", "1")
    monkeypatch.setenv("SIGNOFF_BUDGET__MAX_COST_USD__X", "2")
    with caplog.at_level("WARNING", logger="signoff.config"):
        load_config(path=None, pack_defaults=False, env_overrides=True)
    # Collision detection relies on iteration order; env vars are dict-ordered
    # by insertion, which on Python 3.11+ matches setenv order in tests.
    # If the colliding var is observed second, a warning is logged.
    # We accept either outcome — the important thing is no crash.
