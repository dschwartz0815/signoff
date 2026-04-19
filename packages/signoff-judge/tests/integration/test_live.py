"""Live integration tests against real provider APIs. Opt-in.

Run with::

    uv run pytest -m live packages/signoff-judge

These tests require an API key. They're marked ``pytest.mark.live``
and gated on a ``SIGNOFF_JUDGE_API_KEY`` / ``ANTHROPIC_API_KEY`` /
``OPENAI_API_KEY`` env var being set for the corresponding provider;
without one, the tests skip rather than fail.

Failures here usually indicate a prompt or schema regression, not a
flake: three obviously-clear cases are picked (support, contradict,
unrelated) so any reasonable model should answer correctly. When
these fail, inspect the prompt.
"""

from __future__ import annotations

import os

import pytest
from signoff_judge import AnthropicJudge, JudgeClientConfig, OpenAIJudge

pytestmark = pytest.mark.live


# ---------------------------------------------------------------------------
# Shared fixtures for the three "obvious" cases
# ---------------------------------------------------------------------------


_CASES = [
    pytest.param(
        "Paris is the capital of France.",
        "The capital city of France is Paris, located on the river Seine.",
        "supported",
        id="supported",
    ),
    pytest.param(
        "Paris is the capital of France.",
        "Berlin is the capital of France.",
        "contradicted",
        id="contradicted",
    ),
    pytest.param(
        "Paris is the capital of France.",
        "Photosynthesis converts sunlight into chemical energy.",
        "not_addressed",
        id="not_addressed",
    ),
]


def _have_anthropic_key() -> bool:
    return bool(os.environ.get("SIGNOFF_JUDGE_API_KEY") or os.environ.get("ANTHROPIC_API_KEY"))


def _have_openai_key() -> bool:
    return bool(os.environ.get("SIGNOFF_JUDGE_API_KEY") or os.environ.get("OPENAI_API_KEY"))


# ---------------------------------------------------------------------------
# Anthropic
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(("claim", "passage", "expected"), _CASES)
async def test_anthropic_entailment_live(claim: str, passage: str, expected: str) -> None:
    if not _have_anthropic_key():
        pytest.skip("No Anthropic API key set.")
    config = JudgeClientConfig(provider="anthropic", model="claude-haiku-4-5")
    async with AnthropicJudge(config) as judge:
        result = await judge.check_entailment(claim=claim, passage=passage)
    assert result.label == expected, (
        f"Anthropic returned {result.label!r} (expected {expected!r}): {result.explanation}"
    )
    assert 0.0 <= result.confidence <= 1.0
    assert result.raw_response is not None
    assert result.raw_response["usage"]["input_tokens"] > 0


# ---------------------------------------------------------------------------
# OpenAI
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(("claim", "passage", "expected"), _CASES)
async def test_openai_entailment_live(claim: str, passage: str, expected: str) -> None:
    if not _have_openai_key():
        pytest.skip("No OpenAI API key set.")
    config = JudgeClientConfig(provider="openai", model="gpt-4o-mini")
    async with OpenAIJudge(config) as judge:
        result = await judge.check_entailment(claim=claim, passage=passage)
    assert result.label == expected, (
        f"OpenAI returned {result.label!r} (expected {expected!r}): {result.explanation}"
    )
    assert 0.0 <= result.confidence <= 1.0
    assert result.raw_response is not None
    assert result.raw_response["usage"]["input_tokens"] > 0
