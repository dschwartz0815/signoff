"""End-to-end tests that drive each verifier against a real Docker
daemon + the locally-built ``signoff/code-sandbox:dev`` image.

Gated behind :mod:`pytest.mark.docker`. Default ``pytest`` runs skip
these via the repo-wide ``-m "not live and not docker"`` addopts.
Run explicitly with::

    # First, build the image once:
    docker build -t signoff/code-sandbox:dev packages/signoff-code
    # Then:
    uv run pytest -m docker packages/signoff-code

Each test materialises a workspace from a file-fixture, wraps it in
a :class:`Deliverable`, and runs the target verifier through
:class:`DockerRuntime`. Unit tests (with mocked ``ctx.exec``) already
cover parsing / severity mapping; these tests prove the full wiring
— including ``runtime_required='docker'``-backed execution inside
the sandbox image — works against a real engine.
"""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any

import pytest

pytest.importorskip("docker")

import docker
from signoff import (
    Claim,
    Deliverable,
    Registry,
    Severity,
    VerifierContext,
    VerifierMeta,
    make_context,
)
from signoff.runtime.base import RuntimePolicy
from signoff.testing import FakeHttpClient, FakeJudge
from signoff_code import CodeChangeDeliverable
from signoff_code.verifiers.lint_clean import lint_clean
from signoff_code.verifiers.smoke_imports import smoke_imports
from signoff_code.verifiers.tests_pass import tests_pass
from signoff_code.verifiers.types_check import types_check
from signoff_runtime_docker import DockerRuntime, DockerRuntimeConfig

_SANDBOX_IMAGE = "signoff/code-sandbox:dev"
_FIXTURES = Path(__file__).parent.parent / "fixtures"


def _daemon_available() -> bool:
    try:
        client = docker.from_env()
        client.ping()
        return True
    except Exception:
        return False


def _image_available() -> bool:
    try:
        client = docker.from_env()
        client.images.get(_SANDBOX_IMAGE)
        return True
    except Exception:
        return False


pytestmark = [
    pytest.mark.docker,
    pytest.mark.skipif(not _daemon_available(), reason="Docker daemon not reachable"),
    pytest.mark.skipif(
        not _image_available(),
        reason=(
            "signoff/code-sandbox:dev not built. Run: "
            "`docker build -t signoff/code-sandbox:dev packages/signoff-code`"
        ),
    ),
]


def _runtime_config() -> DockerRuntimeConfig:
    return DockerRuntimeConfig(
        verify_signatures=False,
        default_image=_SANDBOX_IMAGE,
        pull_policy="never",
        default_cpu_limit=1.0,
        default_memory_limit_mb=512,
        default_timeout_seconds=60,
        # Generous workspace mount: these tests run `patch`/`python`
        # which need a writable /workspace for pytest's .pyc files.
        workspace_mount_mode="rw",
    )


def _deliverable_from_fixture(fixture_dir: Path, *, intent: str) -> Deliverable:
    files: dict[str, str] = {}
    for path in fixture_dir.iterdir():
        if path.is_file():
            files[path.name] = path.read_text()
    change = CodeChangeDeliverable(intent=intent, files=files)
    return Deliverable(id=f"dlv_{fixture_dir.name}", kind="code_change", content=change)


def _meta_for(verifier: Any, *, name: str) -> VerifierMeta:
    # The decorator already attached .signoff_meta; re-use it so the
    # runtime_required check fires as in production.
    meta: VerifierMeta = verifier.signoff_meta
    return VerifierMeta(
        name=name,
        pack=meta.pack,
        claim_kinds=meta.claim_kinds,
        cost_tier=meta.cost_tier,
        concurrency=meta.concurrency,
        timeout_seconds=meta.timeout_seconds,
        version=meta.version,
        requires=meta.requires,
        runtime_required=meta.runtime_required,
    )


async def _ctx_for(
    deliverable: Deliverable, meta: VerifierMeta
) -> tuple[VerifierContext, Any]:
    """Build a ctx with the workspace already materialised.

    These tests bypass the harness (they call ``runtime.execute``
    directly), so the ``signoff.deliverable_preparers`` entry-point
    hook that verify() would normally invoke never fires. Call the
    preparer manually so ``ctx.workspace`` matches what a verifier
    running through the real harness would see. Returns the ctx and
    a cleanup coroutine the caller must await.
    """
    from signoff_code.prepare import prepare_code_change

    result = await prepare_code_change(deliverable, FakeHttpClient())
    assert result is not None, "prepare_code_change returned None"
    workspace_root, cleanup = result
    ctx = make_context(
        deliverable=deliverable,
        http=FakeHttpClient(),
        judge=FakeJudge(),
        workspace=workspace_root,
    )
    ctx.current_verifier_meta = meta
    return ctx, cleanup


def _claim() -> Claim:
    return Claim.model_construct(id="whole_deliverable", text="", kind="citation", evidence={})


# ---------------------------------------------------------------------------
# tests_pass — passing and failing fixtures
# ---------------------------------------------------------------------------


async def test_tests_pass_against_passing_fixture() -> None:
    deliverable = _deliverable_from_fixture(
        _FIXTURES / "passing_change", intent="add basic calculator"
    )
    meta = _meta_for(tests_pass, name="tests_pass")
    ctx, _ws_cleanup = await _ctx_for(deliverable, meta)
    try:
        async with DockerRuntime(_runtime_config()) as runtime:
            verdict = await runtime.execute(
                tests_pass,
                claim=_claim(),
                ctx=ctx,
                policy=RuntimePolicy(timeout_seconds=60),
            )
    finally:
        await _ws_cleanup()
    assert verdict.passed is True, verdict.reason
    assert verdict.evidence["tool"] == "pytest"
    assert verdict.evidence["passed"] >= 2


async def test_tests_pass_against_failing_fixture() -> None:
    deliverable = _deliverable_from_fixture(
        _FIXTURES / "failing_test", intent="add broken calculator"
    )
    meta = _meta_for(tests_pass, name="tests_pass")
    ctx, _ws_cleanup = await _ctx_for(deliverable, meta)
    try:
        async with DockerRuntime(_runtime_config()) as runtime:
            verdict = await runtime.execute(
                tests_pass,
                claim=_claim(),
                ctx=ctx,
                policy=RuntimePolicy(timeout_seconds=60),
            )
    finally:
        await _ws_cleanup()
    assert verdict.passed is False
    assert verdict.severity == Severity.BLOCKER
    assert verdict.suggestion is not None
    # The first failing nodeid should name test_add_basic.
    assert "test_add_basic" in (verdict.evidence.get("first_failing_node") or "")


# ---------------------------------------------------------------------------
# types_check — clean and error fixtures
# ---------------------------------------------------------------------------


async def test_types_check_against_passing_fixture() -> None:
    deliverable = _deliverable_from_fixture(
        _FIXTURES / "passing_change", intent="add basic calculator"
    )
    meta = _meta_for(types_check, name="types_check")
    ctx, _ws_cleanup = await _ctx_for(deliverable, meta)
    try:
        async with DockerRuntime(_runtime_config()) as runtime:
            verdict = await runtime.execute(
                types_check,
                claim=_claim(),
                ctx=ctx,
                policy=RuntimePolicy(timeout_seconds=60),
            )
    finally:
        await _ws_cleanup()
    assert verdict.passed is True, verdict.reason


async def test_types_check_against_type_error_fixture() -> None:
    deliverable = _deliverable_from_fixture(_FIXTURES / "type_error", intent="introduce type error")
    meta = _meta_for(types_check, name="types_check")
    ctx, _ws_cleanup = await _ctx_for(deliverable, meta)
    try:
        async with DockerRuntime(_runtime_config()) as runtime:
            verdict = await runtime.execute(
                types_check,
                claim=_claim(),
                ctx=ctx,
                policy=RuntimePolicy(timeout_seconds=60),
            )
    finally:
        await _ws_cleanup()
    assert verdict.passed is False
    assert verdict.severity == Severity.BLOCKER
    assert verdict.evidence["error_count"] >= 1


# ---------------------------------------------------------------------------
# smoke_imports — broken-import fixture
# ---------------------------------------------------------------------------


async def test_smoke_imports_catches_broken_import() -> None:
    deliverable = _deliverable_from_fixture(
        _FIXTURES / "broken_import", intent="add calculator with import bug"
    )
    meta = _meta_for(smoke_imports, name="smoke_imports")
    ctx, _ws_cleanup = await _ctx_for(deliverable, meta)
    try:
        async with DockerRuntime(_runtime_config()) as runtime:
            verdict = await runtime.execute(
                smoke_imports,
                claim=_claim(),
                ctx=ctx,
                policy=RuntimePolicy(timeout_seconds=30),
            )
    finally:
        await _ws_cleanup()
    assert verdict.passed is False
    assert verdict.severity == Severity.BLOCKER
    assert verdict.evidence["failed_module"] == "calculator"
    assert "this_module_does_not_exist" in (verdict.evidence.get("traceback") or "")


# ---------------------------------------------------------------------------
# lint_clean — passing fixture goes green
# ---------------------------------------------------------------------------


async def test_lint_clean_against_passing_fixture() -> None:
    deliverable = _deliverable_from_fixture(
        _FIXTURES / "passing_change", intent="add basic calculator"
    )
    meta = _meta_for(lint_clean, name="lint_clean")
    ctx, _ws_cleanup = await _ctx_for(deliverable, meta)
    try:
        async with DockerRuntime(_runtime_config()) as runtime:
            verdict = await runtime.execute(
                lint_clean,
                claim=_claim(),
                ctx=ctx,
                policy=RuntimePolicy(timeout_seconds=30),
            )
    finally:
        await _ws_cleanup()
    assert verdict.passed is True, verdict.reason


# Keep unused imports live.
_ = (shutil, Registry)
