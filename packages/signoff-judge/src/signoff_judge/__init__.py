"""Real LLM-judge implementations for Signoff verifiers.

Drop-in replacement for :class:`signoff.testing.FakeJudge` in
production harness wiring, satisfying the
:class:`signoff.JudgeClient` protocol with two providers:

- :class:`AnthropicJudge` — uses the official ``anthropic`` SDK and
  its tool-use feature for structured output.
- :class:`OpenAIJudge` — uses the official ``openai`` SDK and its
  ``response_format`` structured-outputs feature.

Import path convention (same as ``signoff-http`` / ``signoff-mcp``):
pip ``signoff-judge`` → module ``signoff_judge``.
"""

from __future__ import annotations

__version__ = "0.0.1"

from signoff_judge.base import BaseJudge, RetryableProviderError
from signoff_judge.config import JudgeClientConfig
from signoff_judge.cost import RATES, ModelRates, estimate_cost
from signoff_judge.errors import (
    JudgeError,
    JudgeInfrastructureError,
    JudgeMalformedResponseError,
    JudgeRefusalError,
)
from signoff_judge.prompts import (
    PromptNotFoundError,
    PromptRegistry,
    PromptTemplate,
)

__all__ = [
    "RATES",
    "BaseJudge",
    "JudgeClientConfig",
    "JudgeError",
    "JudgeInfrastructureError",
    "JudgeMalformedResponseError",
    "JudgeRefusalError",
    "ModelRates",
    "PromptNotFoundError",
    "PromptRegistry",
    "PromptTemplate",
    "RetryableProviderError",
    "__version__",
    "estimate_cost",
]
