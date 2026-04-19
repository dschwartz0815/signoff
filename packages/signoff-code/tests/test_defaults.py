"""Tests for the ``signoff_code.defaults`` pack-defaults loader."""

from __future__ import annotations

from signoff_code.defaults import DEFAULT_CONFIG_PATH, load


def test_default_config_path_exists() -> None:
    assert DEFAULT_CONFIG_PATH.is_file()


def test_load_returns_dict_with_code_change_kind() -> None:
    cfg = load()
    assert isinstance(cfg, dict)
    assert "signoff-code" in cfg["packs"]
    assert "code_change" in cfg["deliverables"]


def test_every_verifier_enabled_by_default() -> None:
    verifiers = load()["deliverables"]["code_change"]["verifiers"]
    expected = {
        "signoff-code.smoke_imports",
        "signoff-code.tests_pass",
        "signoff-code.types_check",
        "signoff-code.lint_clean",
        "signoff-code.semantic_diff",
    }
    assert set(verifiers) == expected
    for name, block in verifiers.items():
        assert block.get("enabled") is True, f"{name} disabled by default"


def test_lint_and_semantic_diff_default_to_warning_severity() -> None:
    verifiers = load()["deliverables"]["code_change"]["verifiers"]
    assert verifiers["signoff-code.lint_clean"]["severity_override"] == "warning"
    assert verifiers["signoff-code.semantic_diff"]["severity_override"] == "warning"


def test_pack_defaults_do_not_pin_runtime() -> None:
    """signoff-code deliberately leaves ``runtime`` and
    ``runtime_policy`` alone.

    Setting a global ``runtime.default: docker`` via pack defaults
    would force DockerRuntime construction on every harness start
    that imports this pack, even when no code_change deliverable is
    verified. Operators opt into docker explicitly in their own
    YAML — ``examples/code-change.yaml`` shows the shape.
    """
    cfg = load()
    assert "runtime" not in cfg
    assert "runtime_policy" not in cfg


def test_pack_defaults_round_trip_through_config_loader(tmp_path) -> None:  # type: ignore[no-untyped-def]
    """The harness's ``load_config`` should pick up the pack default
    via the entry point and merge it in cleanly."""
    from signoff.config import load_config

    cfg = load_config(path=None, pack_defaults=True, env_overrides=False)
    # signoff-code is in the merged packs list when the pack defaults
    # layer runs. If the entry point wasn't registered, the loader
    # would produce an empty packs list.
    assert "signoff-code" in cfg.packs
    assert "code_change" in cfg.deliverables
    del tmp_path
