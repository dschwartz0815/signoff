"""``OpenAIJudge`` — uses the ``openai`` SDK with structured outputs.

OpenAI's structured-output surface is ``response_format={"type":
"json_schema", "json_schema": {...}}`` — the API validates the model's
reply against the schema server-side before returning it, which means
we only need shallow parsing here.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Mapping
from typing import Any, cast

import openai
from openai import AsyncOpenAI

from signoff_judge.base import BaseJudge, RetryableProviderError, _StructuredResponse
from signoff_judge.config import JudgeClientConfig, resolve_api_key
from signoff_judge.errors import JudgeMalformedResponseError, JudgeRefusalError

__all__ = ["OpenAIJudge"]


_logger = logging.getLogger("signoff_judge.openai")


class OpenAIJudge(BaseJudge):
    """Judge that talks to OpenAI's Chat Completions API."""

    PROVIDER_NAME = "openai"

    def __init__(
        self,
        config: JudgeClientConfig | None = None,
        *,
        client: AsyncOpenAI | None = None,
        **kwargs: Any,
    ) -> None:
        super().__init__(config, **kwargs)
        if self._config.provider not in ("openai", "fake"):
            raise ValueError(
                f"OpenAIJudge requires provider='openai' "
                f"(or 'fake' in tests); got {self._config.provider!r}."
            )
        self._client: AsyncOpenAI | None = client
        self._owns_client = client is None

    async def close(self) -> None:
        if self._client is not None and self._owns_client:
            try:
                await self._client.close()
            except Exception:
                _logger.debug("OpenAIJudge.close: swallowed shutdown error", exc_info=True)
        await super().close()

    async def _ensure_client(self) -> AsyncOpenAI:
        if self._client is not None:
            return self._client
        api_key = resolve_api_key(self._config)
        self._client = AsyncOpenAI(api_key=api_key, max_retries=0)
        self._owns_client = True
        return self._client

    async def _complete_structured(
        self,
        *,
        system: str,
        user: str,
        output_schema: Mapping[str, Any],
    ) -> _StructuredResponse:
        client = await self._ensure_client()
        response_format = {
            "type": "json_schema",
            "json_schema": {
                "name": "judge_output",
                "strict": True,
                "schema": dict(output_schema),
            },
        }
        try:
            response = await client.chat.completions.create(
                model=self._config.model,
                max_completion_tokens=self._config.max_tokens,
                temperature=self._config.temperature,
                response_format=cast(Any, response_format),
                messages=cast(
                    Any,
                    [
                        {"role": "system", "content": system},
                        {"role": "user", "content": user},
                    ],
                ),
            )
        except openai.APIStatusError as exc:
            self._raise_from_status(exc)
        except openai.APIConnectionError as exc:
            raise RetryableProviderError("connection", cause=str(exc)) from exc
        except openai.APITimeoutError as exc:
            raise RetryableProviderError("timeout", cause=str(exc)) from exc

        return _parse_completion(response)

    @staticmethod
    def _raise_from_status(exc: openai.APIStatusError) -> None:
        status = getattr(exc, "status_code", None)
        if isinstance(exc, openai.RateLimitError):
            retry_after = _retry_after(exc)
            raise RetryableProviderError(
                "rate_limit", retry_after=retry_after, cause=str(exc)
            ) from exc
        if isinstance(exc, openai.InternalServerError) or (
            isinstance(status, int) and 500 <= status < 600
        ):
            raise RetryableProviderError("server_5xx", cause=str(exc)) from exc
        raise exc


def _retry_after(exc: openai.RateLimitError) -> float | None:
    headers = getattr(getattr(exc, "response", None), "headers", None) or {}
    raw = headers.get("retry-after") or headers.get("Retry-After")
    if raw is None:
        return None
    try:
        return float(raw)
    except ValueError:
        return None


def _parse_completion(response: Any) -> _StructuredResponse:
    choices = getattr(response, "choices", None) or []
    if not choices:
        raise JudgeMalformedResponseError("OpenAI response had no choices.")
    choice = choices[0]
    finish_reason = getattr(choice, "finish_reason", "")
    if finish_reason == "content_filter":
        raise JudgeRefusalError("OpenAI refused the request on policy grounds.")
    message = getattr(choice, "message", None)
    refusal = getattr(message, "refusal", None)
    if refusal:
        raise JudgeRefusalError(f"OpenAI refused: {refusal}")
    content = getattr(message, "content", None)
    if not content:
        raise JudgeMalformedResponseError(
            f"OpenAI response had empty content (finish_reason={finish_reason!r})."
        )
    try:
        payload = json.loads(content)
    except json.JSONDecodeError as exc:
        raise JudgeMalformedResponseError(
            f"OpenAI structured-output response wasn't valid JSON: {exc}"
        ) from exc
    if not isinstance(payload, Mapping):
        raise JudgeMalformedResponseError(
            f"OpenAI structured-output response wasn't a JSON object "
            f"(got {type(payload).__name__})."
        )
    usage = getattr(response, "usage", None)
    return _StructuredResponse(
        payload=payload,
        input_tokens=int(getattr(usage, "prompt_tokens", 0) or 0),
        output_tokens=int(getattr(usage, "completion_tokens", 0) or 0),
        raw={"finish_reason": finish_reason},
    )
