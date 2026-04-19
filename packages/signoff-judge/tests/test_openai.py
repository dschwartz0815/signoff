"""Unit tests for :class:`OpenAIJudge`.

Like the Anthropic tests, the real ``openai.AsyncOpenAI`` is mocked
and response objects are minimal dataclasses shaped like the SDK's
real types.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import httpx
import openai
import pytest
from signoff_judge import (
    JudgeClientConfig,
    JudgeInfrastructureError,
    JudgeMalformedResponseError,
    JudgeRefusalError,
    OpenAIJudge,
)


@dataclass
class _Msg:
    content: str | None = None
    refusal: str | None = None


@dataclass
class _Choice:
    message: _Msg
    finish_reason: str = "stop"


@dataclass
class _Usage:
    prompt_tokens: int = 38
    completion_tokens: int = 12


@dataclass
class _Completion:
    choices: list[_Choice] = field(default_factory=list)
    usage: _Usage = field(default_factory=_Usage)


def _ok(payload: dict[str, Any]) -> _Completion:
    return _Completion(choices=[_Choice(message=_Msg(content=json.dumps(payload)))])


def _mock_client(*, return_value: Any | None = None, side_effects: list[Any] | None = None) -> Any:
    client = MagicMock(spec=openai.AsyncOpenAI)
    client.chat = MagicMock()
    client.chat.completions = MagicMock()
    create_mock = AsyncMock()
    if side_effects is not None:
        create_mock.side_effect = side_effects
    else:
        create_mock.return_value = return_value
    client.chat.completions.create = create_mock
    client.close = AsyncMock()
    return client


def _cfg(**overrides: Any) -> JudgeClientConfig:
    base: dict[str, Any] = {
        "provider": "openai",
        "model": "gpt-4o-mini",
        "max_retries": 0,
        "retry_backoff_base": 0.0,
        "retry_max_backoff": 0.0,
        "timeout_seconds": 5.0,
    }
    base.update(overrides)
    return JudgeClientConfig(**base)


def _status_error(kind: type[openai.APIStatusError], status: int) -> Exception:
    request = httpx.Request("POST", "https://api.openai.com/v1/chat/completions")
    response = httpx.Response(status_code=status, request=request)
    return kind(message="x", response=response, body=None)


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


async def test_check_entailment_happy_path() -> None:
    client = _mock_client(
        return_value=_ok(
            {
                "label": "supported",
                "explanation": "it's the same",
                "excerpt": "Paris",
                "confidence": 0.9,
            }
        )
    )
    async with OpenAIJudge(_cfg(), client=client) as judge:
        result = await judge.check_entailment(claim="c", passage="p")
    assert result.label == "supported"
    assert result.model == "gpt-4o-mini"
    assert result.prompt_version == "1.0.0"
    assert result.raw_response is not None
    assert result.raw_response["usage"]["input_tokens"] == 38
    assert result.raw_response["usage"]["output_tokens"] == 12
    assert result.cost_usd > 0.0


async def test_response_format_uses_json_schema() -> None:
    client = _mock_client(
        return_value=_ok(
            {
                "label": "not_addressed",
                "explanation": "x",
                "excerpt": None,
                "confidence": 0.5,
            }
        )
    )
    async with OpenAIJudge(_cfg(), client=client) as judge:
        await judge.check_entailment(claim="c", passage="p")
    call = client.chat.completions.create.await_args
    assert call.kwargs["response_format"]["type"] == "json_schema"
    assert call.kwargs["response_format"]["json_schema"]["strict"] is True
    assert "label" in call.kwargs["response_format"]["json_schema"]["schema"]["properties"]


async def test_messages_include_system_and_user() -> None:
    client = _mock_client(
        return_value=_ok(
            {
                "label": "supported",
                "explanation": "x",
                "excerpt": "x",
                "confidence": 0.7,
            }
        )
    )
    async with OpenAIJudge(_cfg(), client=client) as judge:
        await judge.check_entailment(claim="c", passage="p")
    messages = client.chat.completions.create.await_args.kwargs["messages"]
    assert [m["role"] for m in messages] == ["system", "user"]


# ---------------------------------------------------------------------------
# Retry behaviour
# ---------------------------------------------------------------------------


async def test_rate_limit_retry_then_success() -> None:
    ok = _ok(
        {
            "label": "supported",
            "explanation": "x",
            "excerpt": "x",
            "confidence": 0.9,
        }
    )
    client = _mock_client(side_effects=[_status_error(openai.RateLimitError, 429), ok])
    async with OpenAIJudge(_cfg(max_retries=1), client=client) as judge:
        result = await judge.check_entailment(claim="c", passage="p")
    assert result.label == "supported"
    assert client.chat.completions.create.await_count == 2


async def test_internal_server_error_retries_exhaust() -> None:
    errs = [
        _status_error(openai.InternalServerError, 502),
        _status_error(openai.InternalServerError, 503),
    ]
    client = _mock_client(side_effects=errs)
    async with OpenAIJudge(_cfg(max_retries=1), client=client) as judge:
        with pytest.raises(JudgeInfrastructureError):
            await judge.check_entailment(claim="c", passage="p")
    assert client.chat.completions.create.await_count == 2


async def test_bad_request_not_retried() -> None:
    client = _mock_client(side_effects=[_status_error(openai.BadRequestError, 400)])
    async with OpenAIJudge(_cfg(max_retries=3), client=client) as judge:
        with pytest.raises(openai.BadRequestError):
            await judge.check_entailment(claim="c", passage="p")
    assert client.chat.completions.create.await_count == 1


# ---------------------------------------------------------------------------
# Parsing edge cases
# ---------------------------------------------------------------------------


async def test_refusal_via_message_field() -> None:
    client = _mock_client(
        return_value=_Completion(
            choices=[_Choice(message=_Msg(refusal="policy block"), finish_reason="stop")]
        )
    )
    async with OpenAIJudge(_cfg(), client=client) as judge:
        with pytest.raises(JudgeRefusalError, match="policy block"):
            await judge.check_entailment(claim="c", passage="p")


async def test_refusal_via_finish_reason() -> None:
    client = _mock_client(
        return_value=_Completion(
            choices=[_Choice(message=_Msg(content=""), finish_reason="content_filter")]
        )
    )
    async with OpenAIJudge(_cfg(), client=client) as judge:
        with pytest.raises(JudgeRefusalError):
            await judge.check_entailment(claim="c", passage="p")


async def test_non_json_content_raises_malformed() -> None:
    client = _mock_client(
        return_value=_Completion(choices=[_Choice(message=_Msg(content="not-json-at-all"))])
    )
    async with OpenAIJudge(_cfg(), client=client) as judge:
        with pytest.raises(JudgeMalformedResponseError):
            await judge.check_entailment(claim="c", passage="p")


async def test_provider_mismatch_raises() -> None:
    with pytest.raises(ValueError, match="provider='openai'"):
        OpenAIJudge(JudgeClientConfig(provider="anthropic"))
