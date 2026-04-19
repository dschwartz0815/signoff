"""Integration tests against a real Docker daemon.

Gated behind ``pytest.mark.docker``. Default ``pytest`` runs skip
these via the global ``addopts: -m "not live"`` config — the
``docker`` marker is also declared ``not live`` so both modes
deselect by default. Run explicitly with::

    uv run pytest -m docker packages/signoff-runtime-docker

Tests skip gracefully (with a clear message) when:
- The Docker daemon is unreachable.
- The generic-sandbox dev image isn't present locally. Build it via
  ``docker build -t signoff/generic-sandbox:dev packages/signoff-runtime-docker/images/generic-sandbox``.

Coverage here complements the unit tests: bind-mount enforcement,
read-only rootfs, network isolation, container lifecycle, and
label propagation. Unit tests cover every branch; these tests
prove the wiring against a real engine.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import pytest

pytest.importorskip("docker")

import docker
from signoff import (
    Claim,
    Deliverable,
    VerifierMeta,
    make_context,
)
from signoff.runtime.base import RuntimePolicy
from signoff.testing import FakeHttpClient, FakeJudge
from signoff_runtime_docker import DockerRuntime, DockerRuntimeConfig

pytestmark = pytest.mark.docker


_SANDBOX_IMAGE = "signoff/generic-sandbox:dev"


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


_DAEMON_MISSING = not _daemon_available()
_IMAGE_MISSING = not _image_available() if not _DAEMON_MISSING else True


pytestmark = [
    pytest.mark.docker,
    pytest.mark.skipif(_DAEMON_MISSING, reason="Docker daemon not reachable"),
    pytest.mark.skipif(
        _IMAGE_MISSING,
        reason=(
            "generic-sandbox:dev image not built. Run: "
            "`docker build -t signoff/generic-sandbox:dev "
            "packages/signoff-runtime-docker/images/generic-sandbox`"
        ),
    ),
]


def _cfg(**overrides: Any) -> DockerRuntimeConfig:
    base: dict[str, Any] = {
        "verify_signatures": False,
        "default_image": _SANDBOX_IMAGE,
        "pull_policy": "never",
        # Tight-but-reasonable defaults so runaway tests can't hog the
        # developer's daemon.
        "default_cpu_limit": 1.0,
        "default_memory_limit_mb": 256,
        "default_timeout_seconds": 10,
    }
    base.update(overrides)
    return DockerRuntimeConfig(**base)


def _meta() -> VerifierMeta:
    return VerifierMeta(
        name="live_smoke",
        pack="signoff-runtime-docker",
        claim_kinds=("citation",),
        cost_tier="cheap",
        concurrency=1,
        runtime_required="docker",
    )


def _claim() -> Claim:
    return Claim(id="clm_live", text="c", kind="citation")


def _deliverable() -> Deliverable:
    return Deliverable(id="dlv_live", kind="research_report", content=None)


def _make_ctx(workspace: Path) -> Any:
    ctx = make_context(
        deliverable=_deliverable(),
        http=FakeHttpClient(),
        judge=FakeJudge(),
        workspace=workspace,
    )
    ctx.current_verifier_meta = _meta()
    return ctx


async def test_exec_echo_hello(tmp_path: Path) -> None:
    async with DockerRuntime(_cfg()) as runtime:

        async def verifier(claim: Claim, ctx: Any) -> Any:
            result = await ctx.exec(["echo", "hello"])
            assert result.exit_code == 0
            assert "hello" in result.stdout
            return ctx.ok(evidence={"stdout": result.stdout.strip()})

        verdict = await runtime.execute(
            verifier,
            claim=_claim(),
            ctx=_make_ctx(tmp_path),
            policy=RuntimePolicy(timeout_seconds=10),
        )
    assert verdict.passed is True
    assert verdict.evidence["runtime"] == "docker"
    assert "hello" in verdict.evidence["stdout"]
    assert verdict.evidence["image"] == _SANDBOX_IMAGE


async def test_read_only_workspace_blocks_writes(tmp_path: Path) -> None:
    async with DockerRuntime(_cfg()) as runtime:

        async def verifier(claim: Claim, ctx: Any) -> Any:
            result = await ctx.exec(["sh", "-c", "echo nope > /workspace/secret.txt; echo $?"])
            # Read-only bind → the redirect fails; sh's exit code is non-zero.
            return ctx.ok(
                evidence={
                    "stdout": result.stdout.strip(),
                    "stderr": result.stderr.strip(),
                }
            )

        verdict = await runtime.execute(
            verifier,
            claim=_claim(),
            ctx=_make_ctx(tmp_path),
            policy=RuntimePolicy(timeout_seconds=10),
        )
    # sh reports a non-zero exit via $?; the write failure surfaced.
    assert "0" != verdict.evidence["stdout"]


async def test_tmpfs_writes_succeed(tmp_path: Path) -> None:
    async with DockerRuntime(_cfg()) as runtime:

        async def verifier(claim: Claim, ctx: Any) -> Any:
            result = await ctx.exec(["sh", "-c", "echo ok > /tmp/out && cat /tmp/out"])
            return ctx.ok(evidence={"stdout": result.stdout.strip()})

        verdict = await runtime.execute(
            verifier,
            claim=_claim(),
            ctx=_make_ctx(tmp_path),
            policy=RuntimePolicy(timeout_seconds=10),
        )
    assert verdict.evidence["stdout"] == "ok"


async def test_network_none_blocks_outbound(tmp_path: Path) -> None:
    async with DockerRuntime(_cfg()) as runtime:

        async def verifier(claim: Claim, ctx: Any) -> Any:
            # ``getent hosts example.com`` in a no-network container
            # fails with a non-zero exit: getent can't resolve without
            # a network namespace route. We don't require curl here.
            result = await ctx.exec(["sh", "-c", "getent hosts example.com; echo E=$?"])
            return ctx.ok(evidence={"exec_exit": result.exit_code, "stdout": result.stdout})

        verdict = await runtime.execute(
            verifier,
            claim=_claim(),
            ctx=_make_ctx(tmp_path),
            policy=RuntimePolicy(timeout_seconds=10, network="none"),
        )
    # The getent command reports a non-zero status; whatever the exact
    # number, "E=0" must not appear.
    assert "E=0" not in verdict.evidence["stdout"]


async def test_verifier_timeout_returns_synthetic_result(tmp_path: Path) -> None:
    async with DockerRuntime(_cfg()) as runtime:

        async def verifier(claim: Claim, ctx: Any) -> Any:
            await ctx.exec(["sleep", "30"])
            return ctx.ok()

        verdict = await runtime.execute(
            verifier,
            claim=_claim(),
            ctx=_make_ctx(tmp_path),
            policy=RuntimePolicy(timeout_seconds=2),
        )
    assert verdict.passed is False
    assert "timed out" in verdict.reason.lower()


async def test_labels_present_on_container(tmp_path: Path) -> None:
    client = docker.from_env()
    async with DockerRuntime(_cfg(), docker_client=client) as runtime:
        captured: dict[str, Any] = {}

        async def verifier(claim: Claim, ctx: Any) -> Any:
            # Use `docker inspect` at the host level — we look up the
            # container mid-run by label.
            for c in client.containers.list(filters={"label": "signoff.harness=true"}):
                if c.labels.get("signoff.verifier") == _meta().fully_qualified_name:
                    captured["labels"] = dict(c.labels)
                    break
            return ctx.ok()

        await runtime.execute(
            verifier,
            claim=_claim(),
            ctx=_make_ctx(tmp_path),
            policy=RuntimePolicy(timeout_seconds=10),
        )
    labels = captured.get("labels") or {}
    assert labels.get("signoff.harness") == "true"
    assert labels.get("signoff.verifier") == _meta().fully_qualified_name
    assert labels.get("signoff.claim_id") == _claim().id
    assert "signoff.run_id" in labels


async def test_container_cleaned_up_after_completion(tmp_path: Path) -> None:
    client = docker.from_env()
    async with DockerRuntime(_cfg(), docker_client=client) as runtime:

        async def verifier(claim: Claim, ctx: Any) -> Any:
            return ctx.ok()

        await runtime.execute(
            verifier,
            claim=_claim(),
            ctx=_make_ctx(tmp_path),
            policy=RuntimePolicy(timeout_seconds=10),
        )
        # Give Docker's auto-remove a moment to land.
        await asyncio.sleep(0.3)
        alive = client.containers.list(
            all=True,
            filters={"label": f"signoff.verifier={_meta().fully_qualified_name}"},
        )
        assert alive == []
