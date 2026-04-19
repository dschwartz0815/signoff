"""Unit tests for :class:`DockerRuntime` with a mocked Docker client."""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

from signoff import (
    Claim,
    Deliverable,
    Severity,
    VerifierMeta,
    make_context,
)
from signoff.runtime.base import RuntimePolicy
from signoff.testing import FakeHttpClient, FakeJudge
from signoff_runtime_docker import DockerRuntime, DockerRuntimeConfig, ImageManager


class _StubImageManager:
    def __init__(self) -> None:
        self.ensured: list[str] = []

    async def ensure(self, image: str) -> None:
        self.ensured.append(image)

    def invalidate(self, image: str | None = None) -> None:
        pass


def _cfg(**overrides: Any) -> DockerRuntimeConfig:
    base: dict[str, Any] = {
        "verify_signatures": False,
        "auto_remove": False,
        "default_image": "signoff/test-sandbox:dev",
    }
    base.update(overrides)
    return DockerRuntimeConfig(**base)


def _mock_client() -> Any:
    client = MagicMock()
    client.api = MagicMock()
    client.api.create_container.return_value = {"Id": "c0ffee" + "0" * 58}
    client.api.create_host_config.return_value = {"HostConfig": {}}
    client.api.start.return_value = None
    client.api.stop.return_value = None
    client.api.remove_container.return_value = None
    # exec_create / exec_start / exec_inspect aren't exercised here — no
    # verifier calls ctx.exec in these tests.
    return client


def _meta(name: str = "unit_test") -> VerifierMeta:
    return VerifierMeta(
        name=name,
        pack="signoff-test",
        claim_kinds=("citation",),
        cost_tier="cheap",
        concurrency=1,
        runtime_required="docker",
    )


def _claim() -> Claim:
    return Claim(id="clm_1", text="c", kind="citation")


def _deliverable() -> Deliverable:
    return Deliverable(id="dlv_1", kind="research_report", content=None)


# ---------------------------------------------------------------------------
# prepare()
# ---------------------------------------------------------------------------


async def test_prepare_calls_image_manager_once_per_image(tmp_path: Path) -> None:
    manager = _StubImageManager()
    client = _mock_client()
    runtime = DockerRuntime(_cfg(), docker_client=client, image_manager=manager)  # type: ignore[arg-type]
    await runtime.prepare(_meta())
    await runtime.prepare(_meta())  # second call short-circuits
    assert manager.ensured == ["signoff/test-sandbox:dev"]
    del tmp_path


# ---------------------------------------------------------------------------
# execute() happy path
# ---------------------------------------------------------------------------


async def test_execute_runs_verifier_and_annotates_container_id(tmp_path: Path) -> None:
    manager = _StubImageManager()
    client = _mock_client()
    runtime = DockerRuntime(_cfg(), docker_client=client, image_manager=manager)  # type: ignore[arg-type]

    async def verifier(claim: Claim, ctx: Any) -> Any:
        # Verifier body runs in-process; we don't call ctx.exec.
        return ctx.ok(evidence={"note": "no exec"})

    ctx = make_context(
        deliverable=_deliverable(),
        http=FakeHttpClient(),
        judge=FakeJudge(),
        workspace=tmp_path,
    )
    ctx.current_verifier_meta = _meta()
    result = await runtime.execute(
        verifier, claim=_claim(), ctx=ctx, policy=RuntimePolicy(timeout_seconds=5)
    )
    assert result.passed is True
    assert result.evidence["runtime"] == "docker"
    # Container ID is first 12 chars of the mock's "c0ffee..." id.
    assert result.evidence["container_id"] == "c0ffee000000"
    assert result.evidence["image"] == "signoff/test-sandbox:dev"
    # Create + start + stop were all called on the mock.
    assert client.api.create_container.called
    assert client.api.start.called
    assert client.api.stop.called


async def test_execute_passes_policy_to_host_config(tmp_path: Path) -> None:
    manager = _StubImageManager()
    client = _mock_client()
    runtime = DockerRuntime(_cfg(), docker_client=client, image_manager=manager)  # type: ignore[arg-type]

    async def verifier(claim: Claim, ctx: Any) -> Any:
        return ctx.ok()

    ctx = make_context(
        deliverable=_deliverable(),
        http=FakeHttpClient(),
        judge=FakeJudge(),
        workspace=tmp_path,
    )
    ctx.current_verifier_meta = _meta()
    policy = RuntimePolicy(
        timeout_seconds=5, cpu_limit=1.5, memory_limit_bytes=512 * 1024 * 1024, network="none"
    )
    await runtime.execute(verifier, claim=_claim(), ctx=ctx, policy=policy)
    host_config_kwargs = client.api.create_host_config.call_args.kwargs
    assert host_config_kwargs["mem_limit"] == f"{512 * 1024 * 1024}b"
    assert host_config_kwargs["nano_cpus"] == int(1.5 * 1e9)
    assert host_config_kwargs["network_mode"] == "none"
    assert host_config_kwargs["cap_drop"] == ["ALL"]
    assert "no-new-privileges" in host_config_kwargs["security_opt"]
    assert host_config_kwargs["read_only"] is True


# ---------------------------------------------------------------------------
# Error paths
# ---------------------------------------------------------------------------


async def test_execute_synthetic_info_when_container_start_fails(
    tmp_path: Path,
) -> None:
    manager = _StubImageManager()
    client = _mock_client()
    client.api.start.side_effect = RuntimeError("daemon hiccup")
    runtime = DockerRuntime(_cfg(), docker_client=client, image_manager=manager)  # type: ignore[arg-type]

    async def verifier(claim: Claim, ctx: Any) -> Any:
        return ctx.ok()

    ctx = make_context(
        deliverable=_deliverable(),
        http=FakeHttpClient(),
        judge=FakeJudge(),
        workspace=tmp_path,
    )
    ctx.current_verifier_meta = _meta()
    result = await runtime.execute(
        verifier, claim=_claim(), ctx=ctx, policy=RuntimePolicy(timeout_seconds=5)
    )
    assert result.passed is False
    assert result.severity == Severity.INFO
    assert "ContainerStartError" in result.reason


async def test_execute_synthetic_info_when_verifier_raises(tmp_path: Path) -> None:
    manager = _StubImageManager()
    client = _mock_client()
    runtime = DockerRuntime(_cfg(), docker_client=client, image_manager=manager)  # type: ignore[arg-type]

    async def verifier(claim: Claim, ctx: Any) -> Any:
        raise ValueError("boom")

    ctx = make_context(
        deliverable=_deliverable(),
        http=FakeHttpClient(),
        judge=FakeJudge(),
        workspace=tmp_path,
    )
    ctx.current_verifier_meta = _meta()
    result = await runtime.execute(
        verifier, claim=_claim(), ctx=ctx, policy=RuntimePolicy(timeout_seconds=5)
    )
    assert result.passed is False
    assert result.severity == Severity.INFO
    assert "ValueError" in result.reason
    # Container still stopped even though verifier raised.
    assert client.api.stop.called


async def test_execute_synthetic_info_on_verifier_timeout(tmp_path: Path) -> None:
    import asyncio

    manager = _StubImageManager()
    client = _mock_client()
    runtime = DockerRuntime(_cfg(), docker_client=client, image_manager=manager)  # type: ignore[arg-type]

    async def verifier(claim: Claim, ctx: Any) -> Any:
        await asyncio.sleep(10)
        return ctx.ok()

    ctx = make_context(
        deliverable=_deliverable(),
        http=FakeHttpClient(),
        judge=FakeJudge(),
        workspace=tmp_path,
    )
    ctx.current_verifier_meta = _meta()
    result = await runtime.execute(
        verifier, claim=_claim(), ctx=ctx, policy=RuntimePolicy(timeout_seconds=1)
    )
    assert result.passed is False
    assert result.severity == Severity.INFO
    assert "timed out" in result.reason


async def test_execute_errors_when_workspace_missing(tmp_path: Path) -> None:
    manager = _StubImageManager()
    client = _mock_client()
    runtime = DockerRuntime(_cfg(), docker_client=client, image_manager=manager)  # type: ignore[arg-type]

    async def verifier(claim: Claim, ctx: Any) -> Any:
        return ctx.ok()

    missing = tmp_path / "does-not-exist"
    ctx = make_context(
        deliverable=_deliverable(),
        http=FakeHttpClient(),
        judge=FakeJudge(),
        workspace=missing,
    )
    ctx.current_verifier_meta = _meta()
    result = await runtime.execute(
        verifier, claim=_claim(), ctx=ctx, policy=RuntimePolicy(timeout_seconds=5)
    )
    assert result.passed is False
    assert "WorkspaceNotMountableError" in result.reason


# ---------------------------------------------------------------------------
# Concurrency
# ---------------------------------------------------------------------------


async def test_max_concurrent_containers_respected(tmp_path: Path) -> None:
    import asyncio

    manager = _StubImageManager()
    client = _mock_client()
    runtime = DockerRuntime(
        _cfg(max_concurrent_containers=2),
        docker_client=client,
        image_manager=manager,  # type: ignore[arg-type]
    )
    in_flight = 0
    peak = 0
    lock = asyncio.Lock()

    async def verifier(claim: Claim, ctx: Any) -> Any:
        nonlocal in_flight, peak
        async with lock:
            in_flight += 1
            peak = max(peak, in_flight)
        await asyncio.sleep(0.05)
        async with lock:
            in_flight -= 1
        return ctx.ok()

    async def run_one() -> None:
        ctx = make_context(
            deliverable=_deliverable(),
            http=FakeHttpClient(),
            judge=FakeJudge(),
            workspace=tmp_path,
        )
        ctx.current_verifier_meta = _meta()
        await runtime.execute(
            verifier, claim=_claim(), ctx=ctx, policy=RuntimePolicy(timeout_seconds=5)
        )

    await asyncio.gather(*[run_one() for _ in range(5)])
    assert peak <= 2


# ---------------------------------------------------------------------------
# teardown
# ---------------------------------------------------------------------------


async def test_teardown_is_idempotent(tmp_path: Path) -> None:
    client = _mock_client()
    runtime = DockerRuntime(
        _cfg(),
        docker_client=client,
        image_manager=_StubImageManager(),  # type: ignore[arg-type]
    )
    await runtime.teardown()
    await runtime.teardown()
    del tmp_path


# ---------------------------------------------------------------------------
# ImageManager construction
# ---------------------------------------------------------------------------


def test_default_image_manager_is_constructed_lazily() -> None:
    client = _mock_client()
    runtime = DockerRuntime(_cfg(), docker_client=client)
    assert runtime._image_manager is None  # type: ignore[attr-defined]
    manager = runtime._get_image_manager(client)  # type: ignore[attr-defined]
    assert isinstance(manager, ImageManager)
    # Second call returns the same instance.
    assert runtime._get_image_manager(client) is manager  # type: ignore[attr-defined]


def test_structural_conformance_with_runtime_protocol() -> None:
    from signoff.runtime.base import Runtime

    client = _mock_client()
    runtime = DockerRuntime(_cfg(), docker_client=client)
    assert isinstance(runtime, Runtime)
