"""Shared fixtures for :mod:`signoff_code.verifiers` unit tests.

We drive each verifier against a real filesystem workspace (so the
``Workspace.materialize`` path runs end-to-end) but mock
``ctx.exec`` so we don't actually invoke pytest / mypy / ruff /
python. The mock lets tests set per-command exit codes and stdout.
"""

from __future__ import annotations

import dataclasses
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import pytest
from signoff import (
    Claim,
    Deliverable,
    VerifierContext,
    VerifierMeta,
    make_context,
)
from signoff.context import ExecResult
from signoff.runtime.base import RuntimePolicy
from signoff.testing import FakeHttpClient, FakeJudge
from signoff_code import CodeChangeDeliverable


@dataclasses.dataclass
class _Recorded:
    cmd: list[str]
    cwd: Path | None
    env: Mapping[str, str] | None
    timeout: int


class _FakeCtx(VerifierContext):
    """VerifierContext that records ``ctx.exec`` invocations and
    returns a queued :class:`ExecResult` per call.

    Each verifier test stages the exact responses it expects.
    """

    calls: list[_Recorded]
    responses: list[ExecResult]

    def __init__(
        self,
        deliverable: Deliverable,
        meta: VerifierMeta,
        *,
        responses: list[ExecResult],
    ) -> None:
        base = make_context(deliverable=deliverable, http=FakeHttpClient(), judge=FakeJudge())
        super().__init__(
            deliverable=base.deliverable,
            http=base.http,
            judge=base.judge,
            policy=base.policy,
            workspace=base.workspace,
            logger=base.logger,
            budget_remaining_usd=base.budget_remaining_usd,
            current_verifier_meta=meta,
            current_claim=None,
        )
        # Mutable bookkeeping — allowed on the slots-based dataclass
        # because we declare them as class attrs that get assigned
        # fresh instances below.
        object.__setattr__(self, "calls", [])
        object.__setattr__(self, "responses", list(responses))

    async def exec(  # type: ignore[override]
        self,
        cmd: list[str],
        *,
        cwd: Path | None = None,
        timeout: int = 30,
        env: Mapping[str, str] | None = None,
    ) -> ExecResult:
        self.calls.append(_Recorded(cmd=cmd, cwd=cwd, env=env, timeout=timeout))
        if not self.responses:
            raise AssertionError(f"_FakeCtx ran out of responses (cmd={cmd!r}).")
        return self.responses.pop(0)


@pytest.fixture
def make_ctx() -> Any:
    def _build(
        change: CodeChangeDeliverable,
        meta: VerifierMeta,
        *,
        responses: list[ExecResult],
    ) -> _FakeCtx:
        deliverable = Deliverable(id="dlv_1", kind="code_change", content=change)
        return _FakeCtx(deliverable=deliverable, meta=meta, responses=responses)

    return _build


@pytest.fixture
def synthetic_claim() -> Claim:
    """Whole-deliverable synthetic claim — same shape the harness
    supplies to claim_kinds='*' verifiers."""
    return Claim.model_construct(id="whole_deliverable", text="", kind="citation", evidence={})


@pytest.fixture
def meta_for() -> Any:
    def _build(name: str) -> VerifierMeta:
        return VerifierMeta(
            name=name,
            pack="signoff-code",
            claim_kinds=("*",),
            cost_tier="cheap",
            concurrency=1,
            runtime_required="docker",
        )

    return _build


def exec_result(
    *,
    exit_code: int = 0,
    stdout: str = "",
    stderr: str = "",
    duration_ms: int = 0,
) -> ExecResult:
    return ExecResult(
        exit_code=exit_code,
        stdout=stdout,
        stderr=stderr,
        duration_ms=duration_ms,
    )


# Re-export for convenience in test modules.
__all__ = ["RuntimePolicy", "exec_result"]
