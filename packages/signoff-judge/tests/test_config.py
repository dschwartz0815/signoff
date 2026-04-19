"""Unit tests for :class:`JudgeClientConfig` + :func:`resolve_api_key`."""

from __future__ import annotations

import pytest
from signoff_judge.config import JudgeClientConfig, resolve_api_key


def test_defaults_use_fake_provider() -> None:
    cfg = JudgeClientConfig()
    assert cfg.provider == "fake"
    assert cfg.model == "claude-haiku-4-5"
    assert cfg.temperature == 0.0


def test_env_overrides(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SIGNOFF_JUDGE_PROVIDER", "anthropic")
    monkeypatch.setenv("SIGNOFF_JUDGE_MAX_TOKENS", "2048")
    monkeypatch.setenv("SIGNOFF_JUDGE_API_KEY", "secret-123")
    cfg = JudgeClientConfig()
    assert cfg.provider == "anthropic"
    assert cfg.max_tokens == 2048
    assert cfg.api_key is not None and cfg.api_key.get_secret_value() == "secret-123"


def test_resolve_api_key_prefers_signoff_namespace() -> None:
    cfg = JudgeClientConfig(provider="anthropic", api_key="sig-key")  # type: ignore[arg-type]
    assert resolve_api_key(cfg) == "sig-key"


def test_resolve_api_key_falls_back_to_provider_env(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("SIGNOFF_JUDGE_API_KEY", raising=False)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "anthr-key")
    cfg = JudgeClientConfig(provider="anthropic")
    assert resolve_api_key(cfg) == "anthr-key"


def test_resolve_api_key_openai_fallback(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("SIGNOFF_JUDGE_API_KEY", raising=False)
    monkeypatch.setenv("OPENAI_API_KEY", "oa-key")
    cfg = JudgeClientConfig(provider="openai")
    assert resolve_api_key(cfg) == "oa-key"


def test_resolve_api_key_fake_provider_returns_none(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("SIGNOFF_JUDGE_API_KEY", raising=False)
    cfg = JudgeClientConfig(provider="fake")
    assert resolve_api_key(cfg) is None


def test_temperature_validation() -> None:
    with pytest.raises(ValueError):
        JudgeClientConfig(temperature=1.5)
