"""``BaseJudge`` — shared retry / error / structured-output logic.

Provider subclasses (:class:`AnthropicJudge`, :class:`OpenAIJudge`)
override exactly one method: :meth:`_complete_structured`. Everything
else — prompt loading, schema validation, retry policy, cost
accounting, mapping into :class:`JudgeResult` — lives here so the
two providers can't drift in subtle ways.
"""

from __future__ import annotations

import asyncio
import logging
import random
from abc import ABC, abstractmethod
from collections.abc import Mapping
from types import TracebackType
from typing import Any, ClassVar

from signoff import JudgeResult

from signoff_judge.config import JudgeClientConfig
from signoff_judge.cost import estimate_cost
from signoff_judge.errors import (
    JudgeInfrastructureError,
    JudgeMalformedResponseError,
)
from signoff_judge.prompts import PromptRegistry, PromptTemplate

__all__ = ["BaseJudge"]


_logger = logging.getLogger("signoff_judge.base")


class BaseJudge(ABC):
    """Shared scaffolding for every provider-backed judge.

    Concrete subclasses declare :attr:`PROVIDER_NAME` and implement
    :meth:`_complete_structured`. They SHOULD translate provider
    SDK exceptions into :class:`RetryableProviderError` (retry) or
    :class:`JudgeInfrastructureError` / :class:`JudgeRefusalError`
    / :class:`JudgeMalformedResponseError` (terminal).
    """

    PROVIDER_NAME: ClassVar[str] = ""

    def __init__(
        self,
        config: JudgeClientConfig | None = None,
        *,
        prompt_registry: PromptRegistry | None = None,
    ) -> None:
        self._config = config if config is not None else JudgeClientConfig()
        self._prompts = prompt_registry or PromptRegistry(user_root=self._config.prompt_root)
        self._closed = False

    # -- lifecycle ----------------------------------------------------------

    async def __aenter__(self) -> BaseJudge:
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        await self.close()

    async def close(self) -> None:
        """Subclasses may override to close provider SDK clients.

        Idempotent — callers can invoke ``close()`` multiple times or
        combine it with the async-context form without tripping on a
        double-close.
        """
        self._closed = True

    # -- public JudgeClient surface ----------------------------------------

    async def check_entailment(
        self,
        *,
        claim: str,
        passage: str,
        context: str | None = None,
    ) -> JudgeResult:
        return await self._run(
            prompt_name="entailment",
            render_kwargs={"claim": claim, "passage": passage, "context": context},
        )

    async def check_policy_compliance(
        self,
        *,
        output: str,
        policy: str,
        examples_of_violations: list[str] | None = None,
    ) -> JudgeResult:
        return await self._run(
            prompt_name="policy_compliance",
            render_kwargs={
                "output": output,
                "policy": policy,
                "examples_of_violations": examples_of_violations,
            },
        )

    async def classify(
        self,
        *,
        text: str,
        labels: list[str],
        rubric: str | None = None,
    ) -> JudgeResult:
        if not labels:
            raise ValueError("classify() requires at least one label.")
        return await self._run(
            prompt_name="classify",
            render_kwargs={"text": text, "labels": labels, "rubric": rubric},
        )

    # -- to be implemented by providers ------------------------------------

    @abstractmethod
    async def _complete_structured(
        self,
        *,
        system: str,
        user: str,
        output_schema: Mapping[str, Any],
    ) -> _StructuredResponse:
        """Make one provider call that returns a parsed dict matching
        ``output_schema``, plus token usage.

        Concrete implementations should raise
        :class:`RetryableProviderError` for transient failures
        (429 / 5xx / network) and one of the terminal ``Judge*Error``
        classes for everything else.
        """

    # -- internals ---------------------------------------------------------

    async def _run(
        self,
        *,
        prompt_name: str,
        render_kwargs: dict[str, Any],
    ) -> JudgeResult:
        if self._closed:
            raise RuntimeError(f"{type(self).__name__} is closed")
        template = self._prompts.get(prompt_name)
        # Strip None-valued optional kwargs so render() sees them as
        # "not supplied" rather than explicit None (the prompt's
        # {% if x %} treats both the same, but filtering here makes the
        # PromptTemplate.render() unexpected-variable check easier to
        # reason about).
        filtered = {
            k: v
            for k, v in render_kwargs.items()
            if k in template.required_variables
            or (k in template.optional_variables and v is not None)
        }
        system, user = template.render(**filtered)

        raw = await self._with_retries(
            system=system, user=user, output_schema=template.output_schema
        )
        self._validate_against_schema(raw.payload, template)
        return _to_judge_result(raw, template=template, model=self._config.model)

    async def _with_retries(
        self,
        *,
        system: str,
        user: str,
        output_schema: Mapping[str, Any],
    ) -> _StructuredResponse:
        attempts = self._config.max_retries + 1
        retries: list[dict[str, Any]] = []
        last_exc: BaseException | None = None
        for attempt in range(1, attempts + 1):
            try:
                resp = await asyncio.wait_for(
                    self._complete_structured(
                        system=system, user=user, output_schema=output_schema
                    ),
                    timeout=self._config.timeout_seconds,
                )
            except asyncio.CancelledError:
                raise
            except TimeoutError as exc:
                last_exc = exc
                reason = "timeout"
                retry_after: float | None = None
            except RetryableProviderError as exc:
                last_exc = exc
                reason = exc.reason
                retry_after = exc.retry_after
            else:
                if retries:
                    resp.retries = retries
                return resp

            if attempt == attempts:
                break
            delay = self._backoff(attempt=attempt, server_hint=retry_after)
            retries.append({"attempt": attempt, "reason": reason, "backoff_ms": int(delay * 1000)})
            if delay > 0:
                await asyncio.sleep(delay)

        assert last_exc is not None  # exhaustive
        raise JudgeInfrastructureError(
            f"{self.PROVIDER_NAME or 'judge'}: exhausted {attempts} attempt(s): "
            f"{type(last_exc).__name__}: {last_exc}"
        ) from last_exc

    def _backoff(self, *, attempt: int, server_hint: float | None) -> float:
        cfg = self._config
        if server_hint is not None:
            return min(server_hint, cfg.retry_max_backoff)
        base = cfg.retry_backoff_base * (cfg.retry_backoff_factor ** (attempt - 1))
        # Tiny jitter so two parallel judges don't retry in lockstep
        # against the same 429'd host. Deterministic when
        # ``SIGNOFF_SAMPLING_SEED`` isn't set isn't a goal here —
        # retry timing is not part of the verdict.
        jitter = random.uniform(0.0, 0.05 * base)
        return min(cfg.retry_max_backoff, base + jitter)

    @staticmethod
    def _validate_against_schema(payload: Mapping[str, Any], template: PromptTemplate) -> None:
        schema = template.output_schema
        required = schema.get("required", [])
        for key in required:
            if key not in payload:
                raise JudgeMalformedResponseError(
                    f"Judge response for prompt {template.name!r}@{template.version} "
                    f"missing required key {key!r}. Got keys: {sorted(payload.keys())}"
                )
        label_schema = schema.get("properties", {}).get("label", {})
        enum = label_schema.get("enum")
        if enum is not None and payload.get("label") not in enum:
            raise JudgeMalformedResponseError(
                f"Judge returned label={payload.get('label')!r} for "
                f"prompt {template.name!r}; expected one of {enum}."
            )
        conf = payload.get("confidence")
        if not isinstance(conf, int | float) or not (0.0 <= float(conf) <= 1.0):
            raise JudgeMalformedResponseError(
                f"Judge returned confidence={conf!r} for prompt "
                f"{template.name!r}; expected a number in [0, 1]."
            )


# ---------------------------------------------------------------------------
# Provider-subclass plumbing
# ---------------------------------------------------------------------------


class RetryableProviderError(Exception):
    """Provider subclass raises this for transient failures.

    ``reason`` is a short grep-friendly tag
    (``"rate_limit"``, ``"server_5xx"``, ``"connection"``) stored in
    ``JudgeResult.raw_response["retries"]``. ``retry_after`` is the
    server-requested backoff in seconds, if any.
    """

    def __init__(self, reason: str, *, retry_after: float | None = None, cause: str = "") -> None:
        super().__init__(cause or reason)
        self.reason = reason
        self.retry_after = retry_after


class _StructuredResponse:
    """Container for the parsed output + token usage of one judge call."""

    __slots__ = ("input_tokens", "output_tokens", "payload", "raw", "retries")

    def __init__(
        self,
        *,
        payload: Mapping[str, Any],
        input_tokens: int,
        output_tokens: int,
        raw: Mapping[str, Any] | None = None,
    ) -> None:
        self.payload = dict(payload)
        self.input_tokens = input_tokens
        self.output_tokens = output_tokens
        self.retries: list[dict[str, Any]] = []
        self.raw: Mapping[str, Any] | None = raw


def _to_judge_result(
    resp: _StructuredResponse, *, template: PromptTemplate, model: str
) -> JudgeResult:
    raw_response: dict[str, Any] = {
        "usage": {
            "input_tokens": resp.input_tokens,
            "output_tokens": resp.output_tokens,
        }
    }
    if resp.retries:
        raw_response["retries"] = resp.retries
    if resp.raw is not None:
        raw_response["provider"] = dict(resp.raw)
    cost = estimate_cost(model, resp.input_tokens, resp.output_tokens)
    return JudgeResult(
        label=str(resp.payload["label"]),
        explanation=str(resp.payload.get("explanation", "")),
        excerpt=_coerce_excerpt(resp.payload.get("excerpt")),
        cost_usd=cost,
        confidence=float(resp.payload.get("confidence", 1.0)),
        model=model,
        prompt_version=template.version,
        raw_response=raw_response,
    )


def _coerce_excerpt(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None
