"""``JudgeClientConfig`` — loaded from ``SIGNOFF_JUDGE_*`` env vars.

The ``SIGNOFF_JUDGE_`` namespace is reserved in
``docs/configuration.md`` for this package. ``SIGNOFF_JUDGE_API_KEY``
takes precedence over provider-native env vars
(``ANTHROPIC_API_KEY`` / ``OPENAI_API_KEY``) so one env can serve
multiple providers, but the provider-native vars are accepted as
fallbacks to minimise confusion for users who already have them set.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Literal

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

__all__ = ["JudgeClientConfig", "resolve_api_key"]


class JudgeClientConfig(BaseSettings):
    """Configuration for :class:`AnthropicJudge` / :class:`OpenAIJudge`.

    Loaded from ``SIGNOFF_JUDGE_*`` by default; pass an explicit
    instance when constructing a judge to override (tests always
    construct directly rather than relying on env).
    """

    model_config = SettingsConfigDict(
        env_prefix="SIGNOFF_JUDGE_",
        env_nested_delimiter="__",
        extra="ignore",
        case_sensitive=False,
    )

    # ------------------------------ Provider ---------------------------------

    provider: Literal["anthropic", "openai", "fake"] = "fake"
    model: str = "claude-haiku-4-5"

    # ------------------------------ Auth -------------------------------------

    #: API key. Prefer setting ``SIGNOFF_JUDGE_API_KEY`` so one env var
    #: works across providers. If unset, :func:`resolve_api_key` falls
    #: back to the provider-native env var.
    api_key: SecretStr | None = None

    # ------------------------------ Generation -------------------------------

    max_tokens: int = Field(default=1024, ge=1)
    #: 0.0 keeps judge output deterministic — the default choice unless
    #: a verifier explicitly wants diversity, which is rare.
    temperature: float = Field(default=0.0, ge=0.0, le=1.0)

    # ------------------------------ Timeouts / retries -----------------------

    timeout_seconds: float = Field(default=60.0, gt=0.0)
    max_retries: int = Field(default=2, ge=0)
    retry_backoff_base: float = Field(default=0.5, ge=0.0)
    retry_backoff_factor: float = Field(default=2.0, ge=1.0)
    retry_max_backoff: float = Field(default=30.0, ge=0.0)

    # ------------------------------ Prompts ----------------------------------

    #: Override directory for prompt templates. When set, the registry
    #: searches here *in addition to* the built-ins; an override file
    #: with the same name+version as a built-in shadows it. Leave
    #: ``None`` in production unless you've explicitly vendored a
    #: modified prompt.
    prompt_root: Path | None = None


def resolve_api_key(config: JudgeClientConfig) -> str | None:
    """Return the effective API key for ``config.provider``.

    Order of precedence:

    1. ``config.api_key`` (which the env loader populates from
       ``SIGNOFF_JUDGE_API_KEY``).
    2. Provider-native env var:
       - ``anthropic`` → ``ANTHROPIC_API_KEY``
       - ``openai`` → ``OPENAI_API_KEY``
    3. ``None`` — the provider SDK will raise at request time.

    Returning ``None`` deliberately defers the error: making the judge
    client constructor ask for a key when no call has been made yet
    would make unit tests with ``provider="fake"`` impossibly annoying.
    """
    if config.api_key is not None:
        return config.api_key.get_secret_value()
    fallbacks = {
        "anthropic": "ANTHROPIC_API_KEY",
        "openai": "OPENAI_API_KEY",
    }
    env_name = fallbacks.get(config.provider)
    if env_name is None:
        return None
    return os.environ.get(env_name)
