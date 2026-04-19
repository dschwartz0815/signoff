"""Verification orchestrator.

Implements ``docs/protocol.md`` §5 end-to-end: resolution (§5.2),
concurrency + budgeting (§5.3), verdict determination (§5.4), early
termination (§5.5), cooperative cancellation (§5.6), and retry
bookkeeping (§5.7).

The harness composes the pieces from earlier PRs:

- :class:`~signoff.registry.Registry` — which verifiers exist.
- :class:`~signoff.config.HarnessConfig` — how they should behave.
- :class:`~signoff.runtime.Runtime` implementations — where they run.
- :class:`~signoff.context.VerifierContext` — what they see.

A demo end-to-end is in the module docstring of
:mod:`signoff.harness.__demo__`; a runnable integration test lives at
``packages/signoff-core/tests/test_harness_integration.py``.
"""

from __future__ import annotations

import asyncio
import logging
import os
import random
import secrets
import string
import time
from collections import defaultdict
from collections.abc import Callable, Mapping
from contextlib import suppress
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from signoff.config import HarnessConfig, VerifierConfig, load_config, validate_config
from signoff.context import HttpClient, JudgeClient, make_context
from signoff.models import (
    BlockerEntry,
    Claim,
    Deliverable,
    FeedbackPacket,
    Severity,
    Verdict,
    VerifierResult,
    WarningEntry,
)
from signoff.registry import Registry
from signoff.runtime.base import Runtime, RuntimePolicy, VerifierMeta
from signoff.runtime.local import LocalRuntime
from signoff.verifier import RegisteredVerifier

__all__ = ["Harness"]


_logger = logging.getLogger("signoff.harness")

_PROTOCOL_VERSION = "0.1"
_CANCEL_GRACE_SECONDS = 2.0
_EARLY_TERM_GRACE_SECONDS = 5.0
_TIER_ORDER: tuple[str, ...] = ("cheap", "medium", "expensive")


# ---------------------------------------------------------------------------
# ID generation
# ---------------------------------------------------------------------------


_ID_ALPHABET = string.ascii_uppercase + string.digits  # Crockford-ish, short + URL-safe.


def _fresh_id(prefix: str) -> str:
    return f"{prefix}_{''.join(secrets.choice(_ID_ALPHABET) for _ in range(20))}"


def _now_iso(clock: Callable[[], datetime]) -> str:
    dt = clock().astimezone(UTC)
    return (
        dt.strftime("%Y-%m-%dT%H:%M:%SZ")
        if dt.microsecond == 0
        else dt.isoformat().replace("+00:00", "Z")
    )


# ---------------------------------------------------------------------------
# Planned run
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class _PlannedVerifierRun:
    """One scheduled invocation of a verifier against one claim (or a
    whole-deliverable slot)."""

    verifier: RegisteredVerifier
    claim: Claim | None
    effective_severity: Severity | None  # None = use verifier's own severity
    effective_timeout: int
    runtime_id: str
    policy: RuntimePolicy


@dataclass
class _ExecutionState:
    """Mutable state shared across a single verify() invocation."""

    results: list[VerifierResult] = field(default_factory=list)
    cost_so_far: float = 0.0
    blocked_fqns: set[str] = field(default_factory=set)
    completion_events: dict[str, asyncio.Event] = field(default_factory=dict)
    # Remaining planned runs per verifier FQN. When this hits zero for
    # a given fqn the corresponding completion_event is set.
    pending_counts: dict[str, int] = field(default_factory=lambda: defaultdict(int))
    results_by_fqn: dict[str, list[VerifierResult]] = field(
        default_factory=lambda: defaultdict(list)
    )
    planned_fqns: set[str] = field(default_factory=set)
    terminated_early: bool = False
    cancelled: bool = False
    start_time: float = 0.0  # perf_counter timestamp


# ---------------------------------------------------------------------------
# Harness
# ---------------------------------------------------------------------------


class Harness:
    """Verification orchestrator. See module docstring for the full flow."""

    def __init__(
        self,
        *,
        config: HarnessConfig,
        registry: Registry,
        runtimes: list[Runtime] | Mapping[str, Runtime],
        http: HttpClient,
        judge: JudgeClient,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self.config = config
        self.registry = registry
        self.runtimes: dict[str, Runtime] = _normalise_runtimes(runtimes)
        if "local" not in self.runtimes:
            # Ensure a fallback always exists; §5.2 falls back to 'local'.
            self.runtimes["local"] = LocalRuntime()
        self.http = http
        self.judge = judge
        self.clock = clock
        self._prepared = False
        self._active_tasks: set[asyncio.Task[Any]] = set()
        self._cancel_event: asyncio.Event | None = None

    # -- lifecycle ----------------------------------------------------------

    @classmethod
    async def from_config_path(
        cls,
        path: Path | str,
        *,
        registry: Registry | None = None,
        runtimes: list[Runtime] | None = None,
        http: HttpClient | None = None,
        judge: JudgeClient | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> Harness:
        """Construct a :class:`Harness` from a YAML config file with
        sensible defaults for everything else.

        This is the headline entry point. Override only what you need:

            async with await Harness.from_config_path("harness.yaml") as h:
                verdict = await h.verify(deliverable, claims)

        Defaults, by argument:

        - ``registry``  →  :meth:`Registry.discovered` (loads every
          installed pack via the ``signoff.verifiers`` entry point
          group per protocol §4.2).
        - ``runtimes`` →  ``[LocalRuntime()]``.
        - ``http``     →  :class:`signoff.testing.FakeHttpClient` — a
          Phase 0 placeholder. Swapped for a real httpx-backed
          implementation in a later PR; a single INFO log line fires
          so production deployments notice they're on the fake.
        - ``judge``    →  :class:`signoff.testing.FakeJudge` — same
          Phase 0 story as ``http``.
        - ``clock``    →  wall-clock UTC.

        Raises :class:`~signoff.config.ConfigurationError` on bad config.
        """
        # TODO(phase1-http): replace FakeHttpClient with
        # signoff.http.AsyncHttpxClient when that lands. Same for judge
        # once signoff.judge.AnthropicJudge arrives.
        from signoff.testing import FakeHttpClient, FakeJudge  # lazy — test deps

        effective_registry = registry if registry is not None else Registry.discovered()
        cfg = load_config(path=path)
        validate_config(cfg, effective_registry)

        effective_runtimes: list[Runtime] = runtimes if runtimes is not None else [LocalRuntime()]
        if http is None:
            _logger.info(
                "Using FakeHttpClient — no real HTTP client configured. "
                "See docs/configuration.md for production setup."
            )
        if judge is None:
            _logger.info(
                "Using FakeJudge — no real LLM judge configured. "
                "See docs/configuration.md for production setup."
            )

        kwargs: dict[str, Any] = {
            "config": cfg,
            "registry": effective_registry,
            "runtimes": effective_runtimes,
            "http": http if http is not None else FakeHttpClient(),
            "judge": judge if judge is not None else FakeJudge(),
        }
        if clock is not None:
            kwargs["clock"] = clock
        return cls(**kwargs)

    async def prepare(self) -> None:
        """Call ``runtime.prepare()`` for every registered verifier. Idempotent."""
        if self._prepared:
            return
        for fn in (
            self.registry.get(meta.fully_qualified_name) for meta in self.registry.list_all()
        ):
            meta: VerifierMeta = fn.signoff_meta
            rt = self.runtimes.get(meta.runtime_required or self.config.runtime.default)
            if rt is None:
                rt = self.runtimes["local"]
            await rt.prepare(meta)
        self._prepared = True

    async def teardown(self) -> None:
        """Call ``runtime.teardown()`` for every runtime. Idempotent."""
        for rt in self.runtimes.values():
            with suppress(Exception):
                await rt.teardown()
        self._prepared = False

    async def __aenter__(self) -> Harness:
        await self.prepare()
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.teardown()

    # -- cancellation -------------------------------------------------------

    async def cancel(self) -> None:
        """Signal an in-flight :meth:`verify` to stop (§5.6).

        Sets the cancel event and issues ``.cancel()`` on every active
        verifier task so in-flight work unblocks promptly. The
        ``verify()`` call catches the resulting ``CancelledError`` and
        returns a Verdict with ``terminated_early=True``.
        """
        if self._cancel_event is None:
            return
        self._cancel_event.set()
        for task in list(self._active_tasks):
            if not task.done():
                task.cancel()

    # -- verify -------------------------------------------------------------

    async def verify(
        self,
        deliverable: Deliverable,
        claims: list[Claim],
        *,
        config_override: Mapping[str, Any] | None = None,
        retry_budget: int | None = None,
    ) -> Verdict:
        """Run verification and return a :class:`Verdict`. See §5 for
        the full contract."""
        started_at = _now_iso(self.clock)
        state = _ExecutionState(start_time=time.perf_counter())
        self._cancel_event = asyncio.Event()

        # Per-request config merge (§5.1).
        effective_config = self.config
        if config_override is not None:
            from signoff.config import deep_merge  # local import: avoid cycle

            merged = deep_merge(self.config.model_dump(mode="python"), dict(config_override))
            effective_config = HarnessConfig.model_validate(merged)

        # §5.2 resolution.
        planned = self._resolve_verifiers(deliverable, claims, effective_config)
        state.planned_fqns = {_fqn(p) for p in planned}
        for p in planned:
            state.pending_counts[_fqn(p)] += 1
        for fqn in state.planned_fqns:
            state.completion_events[fqn] = asyncio.Event()

        # §5.3 execution.
        try:
            await self._execute_plan(
                planned=planned,
                deliverable=deliverable,
                claims=claims,
                state=state,
                config=effective_config,
            )
        except asyncio.CancelledError:  # pragma: no cover — cancel handled below
            state.cancelled = True
            state.terminated_early = True

        completed_at = _now_iso(self.clock)
        duration_ms = int((time.perf_counter() - state.start_time) * 1000)

        verdict = self._build_verdict(
            deliverable_id=deliverable.id,
            claims=claims,
            state=state,
            duration_ms=duration_ms,
            started_at=started_at,
            completed_at=completed_at,
            retry_budget=retry_budget,
        )
        self._cancel_event = None
        return verdict

    # ------------------------------------------------------------------
    # §5.2 — resolution
    # ------------------------------------------------------------------

    def _resolve_verifiers(
        self,
        deliverable: Deliverable,
        claims: list[Claim],
        config: HarnessConfig,
    ) -> list[_PlannedVerifierRun]:
        """Build the list of planned runs for this verification."""
        deliverable_cfg = config.deliverables.get(deliverable.kind)
        if deliverable_cfg is None:
            _logger.info(
                "No config block for deliverable.kind=%r; nothing will run.",
                deliverable.kind,
            )
            return []

        active_packs: set[str] = set(
            deliverable_cfg.packs if deliverable_cfg.packs is not None else config.packs
        )
        verifier_cfgs = deliverable_cfg.verifiers
        sampler = _sampler()

        planned: list[_PlannedVerifierRun] = []

        def _consider(fn: RegisteredVerifier, claim: Claim | None) -> None:
            meta: VerifierMeta = fn.signoff_meta
            fqn = meta.fully_qualified_name
            if active_packs and meta.pack not in active_packs:
                return
            cfg = verifier_cfgs.get(fqn)
            if cfg is not None and not cfg.enabled:
                return
            sample_rate = cfg.sample_rate if cfg is not None else 1.0
            if sample_rate <= 0.0 or (sample_rate < 1.0 and sampler.random() > sample_rate):
                return

            runtime_id = self._resolve_runtime_id(meta, config)
            policy = self._resolve_policy(meta, cfg, config, runtime_id)
            planned.append(
                _PlannedVerifierRun(
                    verifier=fn,
                    claim=claim,
                    effective_severity=cfg.severity_override if cfg is not None else None,
                    effective_timeout=(
                        cfg.timeout_seconds_override
                        if cfg is not None and cfg.timeout_seconds_override is not None
                        else meta.timeout_seconds
                    ),
                    runtime_id=runtime_id,
                    policy=policy,
                )
            )

        # Per-claim verifiers.
        for claim in claims:
            for fn in self.registry.for_claim_kind(claim.kind):
                _consider(fn, claim)

        # Whole-deliverable verifiers. Registry.for_claim_kind already
        # includes '*' verifiers for every claim, so to avoid duplication
        # we add them once here with claim=None and skip their claim_kind
        # entries above. Instead we filter: only include '*' verifiers
        # once.
        seen_whole: set[str] = set()
        deduped: list[_PlannedVerifierRun] = []
        for p in planned:
            meta = p.verifier.signoff_meta
            if meta.claim_kinds == ("*",):
                if meta.fully_qualified_name in seen_whole:
                    continue
                seen_whole.add(meta.fully_qualified_name)
                # rewrite to a single claim=None planned run.
                deduped.append(
                    _PlannedVerifierRun(
                        verifier=p.verifier,
                        claim=None,
                        effective_severity=p.effective_severity,
                        effective_timeout=p.effective_timeout,
                        runtime_id=p.runtime_id,
                        policy=p.policy,
                    )
                )
            else:
                deduped.append(p)
        return deduped

    def _resolve_runtime_id(self, meta: VerifierMeta, config: HarnessConfig) -> str:
        fqn = meta.fully_qualified_name
        rid = config.runtime.per_verifier.get(fqn, config.runtime.default)
        if rid not in self.runtimes:
            _logger.warning(
                "Runtime %r not registered with harness; falling back to 'local' for %s.",
                rid,
                fqn,
            )
            rid = "local"
        if meta.runtime_required == "docker" and rid == "local":
            _logger.warning(
                "Verifier %s declares runtime_required='docker' but is scheduled against "
                "LocalRuntime; running anyway per CLAUDE.md §8.3.",
                fqn,
            )
        return rid

    def _resolve_policy(
        self,
        meta: VerifierMeta,
        verifier_cfg: VerifierConfig | None,
        config: HarnessConfig,
        runtime_id: str,
    ) -> RuntimePolicy:
        # Phase 0: only the local runtime policy block is typed. Unknown
        # runtime keys (docker/wasm, when they arrive) will get their
        # own typed blocks; for now everything falls back to the local
        # policy rather than crashing.
        del runtime_id  # reserved for Phase 1 Docker runtime wiring
        base = config.runtime_policy.local
        # Apply per-verifier timeout override if present; else the
        # verifier's own declared timeout.
        timeout = (
            verifier_cfg.timeout_seconds_override
            if verifier_cfg is not None and verifier_cfg.timeout_seconds_override is not None
            else meta.timeout_seconds
        )
        return base.model_copy(update={"timeout_seconds": timeout})

    # ------------------------------------------------------------------
    # §5.3 — execution
    # ------------------------------------------------------------------

    async def _execute_plan(
        self,
        *,
        planned: list[_PlannedVerifierRun],
        deliverable: Deliverable,
        claims: list[Claim],
        state: _ExecutionState,
        config: HarnessConfig,
    ) -> None:
        if not planned:
            return

        global_sem = asyncio.Semaphore(config.budget.global_concurrency)
        per_verifier_sems: dict[str, asyncio.Semaphore] = {}
        for p in planned:
            meta = p.verifier.signoff_meta
            per_verifier_sems.setdefault(
                meta.fully_qualified_name, asyncio.Semaphore(meta.concurrency)
            )

        # Group by tier, preserving resolution order.
        by_tier: dict[str, list[_PlannedVerifierRun]] = defaultdict(list)
        for p in planned:
            by_tier[p.verifier.signoff_meta.cost_tier].append(p)

        for tier in _TIER_ORDER:
            tier_planned = by_tier.get(tier, [])
            if not tier_planned:
                continue

            if state.cancelled or state.terminated_early:
                for p in tier_planned:
                    state.results.append(
                        self._skip_result(p, "Skipped: early termination after blocker")
                    )
                continue

            # §5.3 time-budget check (whole tier).
            if self._time_exceeded(state, config):
                _logger.info("Time budget exceeded; skipping remaining tier %s", tier)
                state.terminated_early = True
                for p in tier_planned:
                    state.results.append(self._skip_result(p, "Skipped: time budget exceeded"))
                continue

            # §5.3 cost-budget check for expensive tier.
            if tier == "expensive":
                remaining = config.budget.max_cost_usd - state.cost_so_far
                if remaining <= 0:
                    _logger.info("Cost budget exhausted; skipping expensive tier")
                    state.terminated_early = True
                    for p in tier_planned:
                        state.results.append(self._skip_result(p, "Skipped: budget exceeded"))
                    continue

            # Launch planned runs for this tier. Each run waits for its
            # own verifier's `requires` before executing.
            tasks = [
                asyncio.create_task(
                    self._run_one_wrapped(
                        p,
                        deliverable=deliverable,
                        state=state,
                        config=config,
                        global_sem=global_sem,
                        per_verifier_sem=per_verifier_sems[_fqn(p)],
                    )
                )
                for p in tier_planned
            ]
            self._active_tasks.update(tasks)
            # return_exceptions=True so a cancelled task doesn't re-raise
            # past the gather; we keep partial results for §5.6.
            tier_results = await asyncio.gather(*tasks, return_exceptions=True)
            self._active_tasks.difference_update(tasks)

            for res in tier_results:
                if isinstance(res, asyncio.CancelledError):
                    state.cancelled = True
                    state.terminated_early = True
                    continue
                if isinstance(res, BaseException):
                    _logger.warning(
                        "Unexpected exception from _run_one: %s: %s",
                        type(res).__name__,
                        res,
                    )
                    continue
                if res is None:
                    continue
                state.results.append(res)

            if state.cancelled:
                # Stop launching further tiers; collected results stay.
                return

    async def _run_one_wrapped(
        self,
        planned: _PlannedVerifierRun,
        *,
        deliverable: Deliverable,
        state: _ExecutionState,
        config: HarnessConfig,
        global_sem: asyncio.Semaphore,
        per_verifier_sem: asyncio.Semaphore,
    ) -> VerifierResult | None:
        """Wrapper that decrements the pending count for the planned
        run's verifier once the run resolves, even on cancellation.
        Setting the completion event the moment the last pending run
        finishes is what lets intra-tier requires dependencies make
        progress.
        """
        fqn = _fqn(planned)
        try:
            return await self._run_one(
                planned,
                deliverable=deliverable,
                state=state,
                config=config,
                global_sem=global_sem,
                per_verifier_sem=per_verifier_sem,
            )
        finally:
            state.pending_counts[fqn] -= 1
            if state.pending_counts[fqn] <= 0:
                ev = state.completion_events.get(fqn)
                if ev is not None:
                    ev.set()

    async def _run_one(
        self,
        planned: _PlannedVerifierRun,
        *,
        deliverable: Deliverable,
        state: _ExecutionState,
        config: HarnessConfig,
        global_sem: asyncio.Semaphore,
        per_verifier_sem: asyncio.Semaphore,
    ) -> VerifierResult | None:
        meta = planned.verifier.signoff_meta
        fqn = meta.fully_qualified_name

        # §5.6 cancel check.
        if self._cancel_event is not None and self._cancel_event.is_set():
            return self._skip_result(planned, "Skipped: verification cancelled")

        # §5.5 early-term check.
        if state.terminated_early:
            return self._skip_result(planned, "Skipped: early termination after blocker")

        # requires dependencies.
        for req in meta.requires:
            if req not in state.planned_fqns:
                return self._skip_result(planned, f"Skipped: dependency {req} not planned")
            # Wait for the dependency to complete, or for cancel.
            ev = state.completion_events.get(req)
            if ev is None:
                return self._skip_result(planned, f"Skipped: dependency {req} not planned")
            try:
                await _wait_or_cancel(ev, self._cancel_event)
            except asyncio.CancelledError:
                return self._skip_result(planned, "Skipped: verification cancelled")
            if self._cancel_event is not None and self._cancel_event.is_set():
                return self._skip_result(planned, "Skipped: verification cancelled")
            if req in state.blocked_fqns:
                return self._skip_result(planned, f"Skipped: dependency {req} failed")

        # Time budget (re-check at dispatch time for late-tier runs).
        if self._time_exceeded(state, config):
            state.terminated_early = True
            return self._skip_result(planned, "Skipped: time budget exceeded")

        # Acquire semaphores. Global first to avoid per-verifier starvation.
        async with global_sem, per_verifier_sem:
            if self._cancel_event is not None and self._cancel_event.is_set():
                return self._skip_result(planned, "Skipped: verification cancelled")

            runtime = self.runtimes[planned.runtime_id]
            ctx = make_context(
                deliverable=deliverable,
                http=self.http,
                judge=self.judge,
                policy=planned.policy,
                budget_remaining_usd=max(0.0, config.budget.max_cost_usd - state.cost_so_far),
            )
            ctx.current_verifier_meta = meta
            # For whole-deliverable planned runs the wire-format
            # claim_id is null (§3.5). The verifier still receives a
            # Claim per §4.3; we construct one via model_construct so
            # the reserved synthetic id "__deliverable__" can be used
            # without tripping the §3.1 id regex (which applies to the
            # wire format, not in-process plumbing).
            if planned.claim is not None:
                verifier_claim = planned.claim
                ctx.current_claim = planned.claim
            else:
                # §4.3 specifies id "__deliverable__" for the synthetic
                # whole-deliverable claim, but §3.1's wire-format regex
                # rejects leading underscores. Carry a placeholder id
                # through the in-process plumbing and null it out in
                # post-processing so the wire-format result's claim_id
                # is None (§3.5) and no validation ever sees
                # "__deliverable__".
                verifier_claim = Claim.model_construct(
                    id="whole_deliverable",
                    text="",
                    kind="citation",
                    evidence={},
                    span=None,
                    provenance=None,
                )
                ctx.current_claim = verifier_claim

            try:
                raw = await runtime.execute(
                    planned.verifier, claim=verifier_claim, ctx=ctx, policy=planned.policy
                )
            except asyncio.CancelledError:
                # Propagate so gather(return_exceptions=True) captures it
                # and verify() can mark the run cancelled per §5.6.
                raise

        # Post-process: override severity, stamp fqn, sanity-check invariants.
        processed = self._post_process(raw, planned, meta)

        # Update state.
        state.cost_so_far += processed.cost_usd
        state.results_by_fqn[fqn].append(processed)
        if processed.passed is False and processed.severity == Severity.BLOCKER:
            state.blocked_fqns.add(fqn)
            if config.budget.early_termination:
                state.terminated_early = True
                # best effort: signal cancel so other waiters unblock
                if self._cancel_event is not None:
                    # don't actually cancel; just mark terminated_early.
                    pass

        return processed

    # ------------------------------------------------------------------
    # §3.5 post-processing
    # ------------------------------------------------------------------

    def _post_process(
        self,
        raw: VerifierResult,
        planned: _PlannedVerifierRun,
        meta: VerifierMeta,
    ) -> VerifierResult:
        """Apply severity_override and sanity-check the result."""
        updates: dict[str, Any] = {}
        fqn = meta.fully_qualified_name
        # Whole-deliverable runs: §3.5 says claim_id MUST be null on the
        # wire. We carried a placeholder id through in-process so ctx.ok
        # validation would pass; null it here.
        if planned.claim is None and raw.claim_id is not None:
            updates["claim_id"] = None
        if raw.verifier != fqn:
            _logger.debug(
                "Verifier %s returned result with verifier=%r; stamping expected fqn.",
                fqn,
                raw.verifier,
            )
            updates["verifier"] = fqn
        if planned.effective_severity is not None and raw.severity != planned.effective_severity:
            updates["severity"] = planned.effective_severity
            # If we're upgrading to BLOCKER on a failed result, we also need
            # a suggestion — if the verifier didn't provide one, synthesise.
            if (
                planned.effective_severity == Severity.BLOCKER
                and raw.passed is False
                and raw.suggestion is None
            ):
                updates["suggestion"] = (
                    "Config upgraded this result to BLOCKER; verifier did not supply a suggestion."
                )
        if not updates:
            return raw
        try:
            return raw.model_copy(update=updates)
        except Exception as exc:
            _logger.warning(
                "Verifier %s returned malformed result: %s. Downgrading to synthetic INFO.",
                fqn,
                exc,
            )
            return VerifierResult(
                verifier=fqn,
                claim_id=raw.claim_id,
                passed=False,
                severity=Severity.INFO,
                reason=f"Verifier returned malformed result: {exc}",
                suggestion=None,
                evidence={"original_result": raw.model_dump(mode="json")},
                cost_usd=0.0,
                duration_ms=raw.duration_ms,
            )

    # ------------------------------------------------------------------
    # §5.4 — verdict
    # ------------------------------------------------------------------

    def _build_verdict(
        self,
        *,
        deliverable_id: str,
        claims: list[Claim],
        state: _ExecutionState,
        duration_ms: int,
        started_at: str,
        completed_at: str,
        retry_budget: int | None,
    ) -> Verdict:
        results = state.results
        has_blocker = any(r.passed is False and r.severity == Severity.BLOCKER for r in results)
        passed = not has_blocker

        feedback_packet: FeedbackPacket | None = None
        if not passed:
            feedback_packet = self._build_feedback_packet(
                results=results,
                claims=claims,
                cost_usd=round(state.cost_so_far, 12),
                retry_budget=retry_budget,
            )

        from signoff import __version__ as _core_version  # local import: avoid cycle

        return Verdict(
            id=_fresh_id("vrd"),
            deliverable_id=deliverable_id,
            passed=passed,
            results=results,
            feedback_packet=feedback_packet,
            cost_usd=round(state.cost_so_far, 12),
            duration_ms=duration_ms,
            protocol_version=_PROTOCOL_VERSION,
            harness_version=_core_version,
            started_at=started_at,
            completed_at=completed_at,
            terminated_early=state.terminated_early or state.cancelled,
        )

    def _build_feedback_packet(
        self,
        *,
        results: list[VerifierResult],
        claims: list[Claim],
        cost_usd: float,
        retry_budget: int | None,
    ) -> FeedbackPacket:
        claim_by_id = {c.id: c for c in claims}
        blockers: list[BlockerEntry] = []
        warnings: list[WarningEntry] = []
        for r in results:
            if r.passed or r.severity == Severity.INFO:
                continue
            entry_kwargs: dict[str, Any] = {
                "claim_id": r.claim_id,
                "claim_text": claim_by_id[r.claim_id].text if r.claim_id in claim_by_id else None,
                "verifier": r.verifier,
                "issue": r.reason,
                "suggested_repair": r.suggestion or "(no repair hint provided)",
                "evidence_excerpt": _excerpt(r.evidence),
            }
            if r.severity == Severity.BLOCKER:
                blockers.append(BlockerEntry.model_validate(entry_kwargs))
            else:
                warnings.append(WarningEntry.model_validate(entry_kwargs))
        return FeedbackPacket(
            blockers=blockers,
            warnings=warnings,
            cost_usd=cost_usd,
            retry_budget_remaining=(retry_budget - 1) if retry_budget is not None else None,
            protocol_version=_PROTOCOL_VERSION,
        )

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _time_exceeded(self, state: _ExecutionState, config: HarnessConfig) -> bool:
        elapsed = time.perf_counter() - state.start_time
        return elapsed >= config.budget.max_duration_seconds

    def _skip_result(self, planned: _PlannedVerifierRun, reason: str) -> VerifierResult:
        meta = planned.verifier.signoff_meta
        claim_id = planned.claim.id if planned.claim is not None else None
        return VerifierResult(
            verifier=meta.fully_qualified_name,
            claim_id=claim_id,
            passed=False,
            severity=Severity.INFO,
            reason=reason,
            suggestion=None,
            evidence={},
            cost_usd=0.0,
            duration_ms=0,
        )


# ---------------------------------------------------------------------------
# Module-level helpers
# ---------------------------------------------------------------------------


def _fqn(p: _PlannedVerifierRun) -> str:
    return p.verifier.signoff_meta.fully_qualified_name


def _normalise_runtimes(
    runtimes: list[Runtime] | Mapping[str, Runtime],
) -> dict[str, Runtime]:
    """Accept runtimes as a list keyed by ``runtime_id`` or a dict.

    List form is preferred — the key is just ``runtime.runtime_id`` so
    a dict duplicates the information. Dict form stays supported for
    callers that have explicit keying needs, but we validate that each
    key matches its value's ``runtime_id``.
    """
    if isinstance(runtimes, Mapping):
        out: dict[str, Runtime] = {}
        for key, rt in runtimes.items():
            if rt.runtime_id != key:
                raise ValueError(
                    f"Runtime registered under key {key!r} has runtime_id={rt.runtime_id!r}; "
                    "the key must match the runtime_id."
                )
            out[key] = rt
        return out
    seen: dict[str, Runtime] = {}
    for rt in runtimes:
        if rt.runtime_id in seen:
            raise ValueError(
                f"Two runtimes declare runtime_id={rt.runtime_id!r}; runtime ids must be unique."
            )
        seen[rt.runtime_id] = rt
    return seen


_DEFAULT_SAMPLER: random.Random | None = None


def _sampler() -> random.Random:
    """Return a :class:`random.Random` for sample_rate decisions.

    If ``SIGNOFF_SAMPLING_SEED`` is set, a new deterministic Random is
    created for each verify() call so tests reproduce exactly. Otherwise
    we reuse a process-wide un-seeded Random.
    """
    seed = os.environ.get("SIGNOFF_SAMPLING_SEED")
    if seed is not None:
        try:
            return random.Random(int(seed))
        except ValueError:
            return random.Random(seed)
    global _DEFAULT_SAMPLER
    if _DEFAULT_SAMPLER is None:
        _DEFAULT_SAMPLER = random.Random()
    return _DEFAULT_SAMPLER


async def _wait_or_cancel(event: asyncio.Event, cancel_event: asyncio.Event | None) -> None:
    """Wait for ``event``, or for ``cancel_event`` — whichever fires first."""
    if cancel_event is None:
        await event.wait()
        return
    ev_task = asyncio.create_task(event.wait())
    cancel_task = asyncio.create_task(cancel_event.wait())
    try:
        await asyncio.wait({ev_task, cancel_task}, return_when=asyncio.FIRST_COMPLETED)
    finally:
        for t in (ev_task, cancel_task):
            if not t.done():
                t.cancel()
                with suppress(asyncio.CancelledError):
                    await t


def _excerpt(evidence: Mapping[str, Any]) -> str | None:
    """Build a short evidence excerpt for a feedback entry (≤ 240 chars).

    Prefers an explicit ``excerpt`` field if the verifier set one; else
    flattens the evidence dict to a compact repr.
    """
    if not evidence:
        return None
    explicit = evidence.get("excerpt")
    if isinstance(explicit, str):
        return explicit[:240]
    try:
        rendered = repr({k: evidence[k] for k in list(evidence)[:6]})
    except Exception:
        return None
    return rendered[:240]
