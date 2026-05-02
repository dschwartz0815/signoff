"""Unit tests for :class:`DockerRuntimeConfig`."""

from __future__ import annotations

import pytest
from signoff_runtime_docker import DockerRuntimeConfig


def test_defaults_are_secure() -> None:
    cfg = DockerRuntimeConfig()
    assert cfg.default_network == "none"
    assert cfg.workspace_mount_mode == "ro"
    assert cfg.read_only_rootfs is True
    assert cfg.run_as_uid == 10001 and cfg.run_as_gid == 10001
    # ``verify_signatures`` default flipped from True to "auto" in the
    # launch fix (F3): hard-failing on missing cosign was a poor
    # quickstart experience and the smart-detect resolves it cleanly.
    # Production deployments still pin =true explicitly to keep the
    # strict contract — see docs/runtimes.md § Image trust.
    assert cfg.verify_signatures == "auto"
    assert cfg.auto_remove is True
    assert cfg.keep_on_failure is False


def test_verify_signatures_explicit_true_still_supported() -> None:
    """Production deployments override the new ``auto`` default with an
    explicit ``true`` to keep the existing strict contract."""
    cfg = DockerRuntimeConfig(verify_signatures=True)
    assert cfg.verify_signatures is True


def test_verify_signatures_env_var_accepts_auto(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``SIGNOFF_DOCKER_VERIFY_SIGNATURES=auto`` round-trips the literal
    rather than coercing through bool. Pydantic's union ordering picks
    ``Literal["auto"]`` for the string ``"auto"`` and ``bool`` for
    ``"true"``/``"false"`` — both paths must work."""
    monkeypatch.setenv("SIGNOFF_DOCKER_VERIFY_SIGNATURES", "auto")
    cfg = DockerRuntimeConfig()
    assert cfg.verify_signatures == "auto"

    monkeypatch.setenv("SIGNOFF_DOCKER_VERIFY_SIGNATURES", "true")
    cfg = DockerRuntimeConfig()
    assert cfg.verify_signatures is True

    monkeypatch.setenv("SIGNOFF_DOCKER_VERIFY_SIGNATURES", "false")
    cfg = DockerRuntimeConfig()
    assert cfg.verify_signatures is False


def test_env_overrides(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SIGNOFF_DOCKER_DEFAULT_CPU_LIMIT", "0.5")
    monkeypatch.setenv("SIGNOFF_DOCKER_DEFAULT_MEMORY_LIMIT_MB", "256")
    monkeypatch.setenv("SIGNOFF_DOCKER_VERIFY_SIGNATURES", "false")
    monkeypatch.setenv("SIGNOFF_DOCKER_MAX_CONCURRENT_CONTAINERS", "3")
    cfg = DockerRuntimeConfig()
    assert cfg.default_cpu_limit == 0.5
    assert cfg.default_memory_limit_mb == 256
    assert cfg.verify_signatures is False
    assert cfg.max_concurrent_containers == 3


def test_pull_policy_restricted() -> None:
    with pytest.raises(ValueError):
        DockerRuntimeConfig(pull_policy="sometimes")  # type: ignore[arg-type]


def test_network_mode_restricted() -> None:
    with pytest.raises(ValueError):
        DockerRuntimeConfig(default_network="host")  # type: ignore[arg-type]
