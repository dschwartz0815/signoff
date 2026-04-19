"""``AnthropicJudge`` — uses the ``anthropic`` SDK with tool use for
structured output.

Why tool use: Anthropic's Messages API guarantees structured JSON when
the model calls a tool; a plain "please return JSON" instruction is
lossier and harder to validate. We define a single tool named
``submit`` whose ``input_schema`` is our prompt's ``output_schema``,
force the model to call it via ``tool_choice``, and parse the
tool-use block's ``input`` dict.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from typing import Any, cast

import anthropic
from anthropic import AsyncAnthropic

from signoff_judge.base import BaseJudge, RetryableProviderError, _StructuredResponse
from signoff_judge.config import JudgeClientConfig, resolve_api_key
from signoff_judge.errors import JudgeMalformedResponseError, JudgeRefusalError

__all__ = ["AnthropicJudge"]


_logger = logging.getLogger("signoff_judge.anthropic")


#: Name of the tool we define so the model is forced to return
#: structured JSON. Isolated here so a future rename doesn't silently
#: break response parsing.
_SUBMIT_TOOL_NAME = "submit"


class AnthropicJudge(BaseJudge):
    """Judge that talks to Anthropic's Messages API."""

    PROVIDER_NAME = "anthropic"

    def __init__(
        self,
        config: JudgeClientConfig | None = None,
        *,
        client: AsyncAnthropic | None = None,
        **kwargs: Any,
    ) -> None:
        super().__init__(config, **kwargs)
        if self._config.provider not in ("anthropic", "fake"):
            raise ValueError(
                f"AnthropicJudge requires provider='anthropic' "
                f"(or 'fake' in tests); got {self._config.provider!r}."
            )
        self._client: AsyncAnthropic | None = client
        self._owns_client = client is None

    # -- lifecycle override -------------------------------------------------

    async def close(self) -> None:
        if self._client is not None and self._owns_client:
            try:
                await self._client.close()
            except Exception:
                _logger.debug("AnthropicJudge.close: swallowed shutdown error", exc_info=True)
        await super().close()

    async def _ensure_client(self) -> AsyncAnthropic:
        if self._client is not None:
            return self._client
        api_key = resolve_api_key(self._config)
        # Disable the SDK's own retries so BaseJudge sees every
        # transient failure and governs retries centrally.
        self._client = AsyncAnthropic(api_key=api_key, max_retries=0)
        self._owns_client = True
        return self._client

    # -- provider implementation -------------------------------------------

    async def _complete_structured(
        self,
        *,
        system: str,
        user: str,
        output_schema: Mapping[str, Any],
    ) -> _StructuredResponse:
        client = await self._ensure_client()
        tools = [
            {
                "name": _SUBMIT_TOOL_NAME,
                "description": "Submit your verdict as structured JSON.",
                "input_schema": dict(output_schema),
            }
        ]
        try:
            message = await client.messages.create(
                model=self._config.model,
                max_tokens=self._config.max_tokens,
                temperature=self._config.temperature,
                system=system,
                tools=cast(Any, tools),
                tool_choice=cast(Any, {"type": "tool", "name": _SUBMIT_TOOL_NAME}),
                messages=[{"role": "user", "content": user}],
            )
        except anthropic.APIStatusError as exc:
            self._raise_from_status(exc)
        except anthropic.APIConnectionError as exc:
            raise RetryableProviderError("connection", cause=str(exc)) from exc
        except anthropic.APITimeoutError as exc:
            raise RetryableProviderError("timeout", cause=str(exc)) from exc

        return _parse_tool_use(message)

    # -- error mapping ------------------------------------------------------

    @staticmethod
    def _raise_from_status(exc: anthropic.APIStatusError) -> None:
        status = getattr(exc, "status_code", None)
        if isinstance(exc, anthropic.RateLimitError):
            retry_after = _retry_after(exc)
            raise RetryableProviderError(
                "rate_limit", retry_after=retry_after, cause=str(exc)
            ) from exc
        if isinstance(exc, anthropic.InternalServerError) or (
            isinstance(status, int) and 500 <= status < 600
        ):
            raise RetryableProviderError("server_5xx", cause=str(exc)) from exc
        # Auth, permission, bad-request, 404 — all terminal and our fault.
        raise exc


def _retry_after(exc: anthropic.RateLimitError) -> float | None:
    headers = getattr(getattr(exc, "response", None), "headers", None) or {}
    raw = headers.get("retry-after") or headers.get("Retry-After")
    if raw is None:
        return None
    try:
        return float(raw)
    except ValueError:
        return None


def _parse_tool_use(message: Any) -> _StructuredResponse:
    """Extract the ``submit`` tool-use block from a Messages response."""
    content = getattr(message, "content", []) or []
    for block in content:
        btype = getattr(block, "type", None)
        if btype == "tool_use" and getattr(block, "name", "") == _SUBMIT_TOOL_NAME:
            tool_input = getattr(block, "input", None)
            if not isinstance(tool_input, Mapping):
                raise JudgeMalformedResponseError("Anthropic tool_use block had non-mapping input.")
            usage = getattr(message, "usage", None)
            return _StructuredResponse(
                payload=tool_input,
                input_tokens=int(getattr(usage, "input_tokens", 0) or 0),
                output_tokens=int(getattr(usage, "output_tokens", 0) or 0),
                raw={"stop_reason": getattr(message, "stop_reason", None)},
            )
    stop_reason = getattr(message, "stop_reason", "")
    if stop_reason == "refusal":
        raise JudgeRefusalError("Anthropic refused the request on policy grounds.")
    raise JudgeMalformedResponseError(
        f"Anthropic response contained no {_SUBMIT_TOOL_NAME!r} tool_use "
        f"block (stop_reason={stop_reason!r})."
    )
