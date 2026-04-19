"""Unit tests for :func:`translate_policy` and related helpers."""

from __future__ import annotations

import logging

import pytest
from signoff.runtime.base import RuntimePolicy
from signoff_runtime_docker import DockerRuntimeConfig
from signoff_runtime_docker.policy import (
    NETWORK_ALLOWLIST_DOWNGRADE_WARNING,
    translate_policy,
)


def _cfg(**overrides: object) -> DockerRuntimeConfig:
    base: dict[str, object] = {"verify_signatures": False}
    base.update(overrides)
    return DockerRuntimeConfig(**base)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# Defaults
# ---------------------------------------------------------------------------


def test_defaults_produce_secure_baseline() -> None:
    config = _cfg()
    policy = RuntimePolicy(timeout_seconds=30)
    kwargs = translate_policy(policy, config)
    assert kwargs["cap_drop"] == ["ALL"]
    assert kwargs["cap_add"] == []
    assert "no-new-privileges" in kwargs["security_opt"]
    assert kwargs["read_only"] is True
    assert kwargs["tmpfs"]["/tmp"] == f"rw,size={config.tmpfs_size_mb}m"
    # ``user`` is NOT a HostConfig key — the runtime passes it to
    # ``create_container`` directly. translate_policy must not emit it.
    assert "user" not in kwargs
    assert kwargs["pids_limit"] == config.default_pids_limit
    assert kwargs["auto_remove"] is True


def test_defaults_use_config_memory_and_cpu_when_policy_unset() -> None:
    config = _cfg(default_cpu_limit=0.5, default_memory_limit_mb=256)
    kwargs = translate_policy(RuntimePolicy(timeout_seconds=30), config)
    assert kwargs["mem_limit"] == f"{256 * 1024 * 1024}b"
    assert kwargs["nano_cpus"] == int(0.5 * 1e9)


# ---------------------------------------------------------------------------
# Policy overrides
# ---------------------------------------------------------------------------


def test_policy_memory_override_applied() -> None:
    config = _cfg(default_memory_limit_mb=256)
    policy = RuntimePolicy(timeout_seconds=30, memory_limit_bytes=2 * 1024 * 1024 * 1024)
    kwargs = translate_policy(policy, config)
    assert kwargs["mem_limit"] == f"{2 * 1024 * 1024 * 1024}b"


def test_policy_cpu_override_applied() -> None:
    config = _cfg(default_cpu_limit=0.5)
    policy = RuntimePolicy(timeout_seconds=30, cpu_limit=4.0)
    kwargs = translate_policy(policy, config)
    assert kwargs["nano_cpus"] == int(4.0 * 1e9)


# ---------------------------------------------------------------------------
# Network translation
# ---------------------------------------------------------------------------


def test_network_none_passes_through() -> None:
    kwargs = translate_policy(
        RuntimePolicy(timeout_seconds=30, network="none"),
        _cfg(default_network="bridge"),
    )
    assert kwargs["network_mode"] == "none"


def test_network_open_takes_config_default() -> None:
    kwargs = translate_policy(
        RuntimePolicy(timeout_seconds=30, network="open"),
        _cfg(default_network="none"),
    )
    # policy.network='open' defers to config; config says 'none'.
    assert kwargs["network_mode"] == "none"


def test_network_allowlist_downgrades_to_bridge_with_warning(
    caplog: pytest.LogCaptureFixture,
) -> None:
    from signoff_runtime_docker import policy as policy_mod

    # Reset one-shot warning flag so this test is order-independent.
    policy_mod._ALLOWLIST_WARNED = False  # type: ignore[attr-defined]
    with caplog.at_level(logging.WARNING, logger="signoff_runtime_docker.policy"):
        kwargs = translate_policy(
            RuntimePolicy(timeout_seconds=30, network="allowlist"),
            _cfg(default_network="none"),
        )
    assert kwargs["network_mode"] == "bridge"
    assert any(NETWORK_ALLOWLIST_DOWNGRADE_WARNING in rec.message for rec in caplog.records)


def test_config_default_allowlist_also_downgrades() -> None:
    """The config can set default_network='allowlist' even though the
    runtime vocabulary treats it the same as the policy vocabulary —
    we still downgrade."""
    from signoff_runtime_docker import policy as policy_mod

    policy_mod._ALLOWLIST_WARNED = True  # type: ignore[attr-defined]  # silence second warning
    kwargs = translate_policy(
        RuntimePolicy(timeout_seconds=30, network="open"),
        _cfg(default_network="allowlist"),
    )
    assert kwargs["network_mode"] == "bridge"
