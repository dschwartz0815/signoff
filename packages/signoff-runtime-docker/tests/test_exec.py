"""Unit tests for :class:`DockerExec` and :class:`DockerVerifierContext`.

The Docker client's ``api.exec_create`` / ``api.exec_start`` /
``api.exec_inspect`` are mocked so the bridge logic (cwd translation,
byte-cap truncation, timeout handling, header-or-marker formatting)
is exercised without a daemon.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest
from signoff.context import ExecResult, VerifierContext
from signoff.models import Deliverable
from signoff.testing import FakeHttpClient, FakeJudge
from signoff_runtime_docker import (
    DockerExec,
    DockerVerifierContext,
    ExecCwdOutsideWorkspaceError,
)
from signoff_runtime_docker.context import wrap_context
from signoff_runtime_docker.exec import CONTAINER_WORKSPACE


def _client(
    *,
    chunks: list[tuple[bytes, bytes]],
    exit_code: int = 0,
    exec_id: str = "exec-id",
) -> Any:
    client = MagicMock()
    client.api = MagicMock()
    client.api.exec_create.return_value = {"Id": exec_id}
    # `demux=True` chunks: iterable of (stdout, stderr) tuples.
    client.api.exec_start.return_value = iter(chunks)
    client.api.exec_inspect.return_value = {"ExitCode": exit_code, "Pid": 0}
    return client


def _dexec(
    client: Any, *, workspace: Path, stdout_cap: int = 1024, stderr_cap: int = 1024
) -> DockerExec:
    return DockerExec(
        client=client,
        container_id="c" * 64,
        workspace_host=workspace,
        stdout_max_bytes=stdout_cap,
        stderr_max_bytes=stderr_cap,
    )


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


async def test_run_returns_exec_result(tmp_path: Path) -> None:
    client = _client(chunks=[(b"hello\n", b""), (b"", b"warn\n")])
    dexec = _dexec(client, workspace=tmp_path)
    result = await dexec.run(["echo", "hello"])
    assert isinstance(result, ExecResult)
    assert result.exit_code == 0
    assert "hello" in result.stdout
    assert "warn" in result.stderr
    assert result.duration_ms >= 0


async def test_run_empty_cmd_raises(tmp_path: Path) -> None:
    client = _client(chunks=[])
    dexec = _dexec(client, workspace=tmp_path)
    with pytest.raises(ValueError, match="empty cmd"):
        await dexec.run([])


async def test_env_merged_into_exec_create(tmp_path: Path) -> None:
    client = _client(chunks=[(b"", b"")])
    dexec = _dexec(client, workspace=tmp_path)
    await dexec.run(["echo", "x"], env={"FOO": "bar", "BAZ": "qux"})
    kwargs = client.api.exec_create.call_args.kwargs
    assert "FOO=bar" in kwargs["environment"]
    assert "BAZ=qux" in kwargs["environment"]


# ---------------------------------------------------------------------------
# cwd translation
# ---------------------------------------------------------------------------


async def test_cwd_none_uses_container_workspace(tmp_path: Path) -> None:
    client = _client(chunks=[(b"", b"")])
    dexec = _dexec(client, workspace=tmp_path)
    await dexec.run(["ls"])
    assert client.api.exec_create.call_args.kwargs["workdir"] == str(CONTAINER_WORKSPACE)


async def test_cwd_inside_workspace_is_translated(tmp_path: Path) -> None:
    sub = tmp_path / "sub" / "dir"
    sub.mkdir(parents=True)
    client = _client(chunks=[(b"", b"")])
    dexec = _dexec(client, workspace=tmp_path)
    await dexec.run(["ls"], cwd=sub)
    expected = str(CONTAINER_WORKSPACE / "sub" / "dir")
    assert client.api.exec_create.call_args.kwargs["workdir"] == expected


async def test_cwd_outside_workspace_raises(tmp_path: Path) -> None:
    outside = tmp_path.parent / "else"
    outside.mkdir(exist_ok=True)
    client = _client(chunks=[])
    dexec = _dexec(client, workspace=tmp_path)
    with pytest.raises(ExecCwdOutsideWorkspaceError, match="outside"):
        await dexec.run(["ls"], cwd=outside)


# ---------------------------------------------------------------------------
# Byte-cap truncation
# ---------------------------------------------------------------------------


async def test_stdout_truncated_at_cap(tmp_path: Path) -> None:
    big = b"A" * 4096
    client = _client(chunks=[(big, b"")])
    dexec = _dexec(client, workspace=tmp_path, stdout_cap=1024)
    result = await dexec.run(["cat", "big"])
    # Body capped at 1024 plus the truncation marker line.
    assert result.stdout.startswith("A" * 1024)
    assert "signoff.truncated" in result.stdout


async def test_stderr_truncated_at_cap(tmp_path: Path) -> None:
    big = b"E" * 4096
    client = _client(chunks=[(b"", big)])
    dexec = _dexec(client, workspace=tmp_path, stderr_cap=1024)
    result = await dexec.run(["cat", "bige"])
    assert result.stderr.startswith("E" * 1024)
    assert "signoff.truncated" in result.stderr


# ---------------------------------------------------------------------------
# Timeout
# ---------------------------------------------------------------------------


async def test_timeout_kills_exec_and_returns_synthetic_result(
    tmp_path: Path,
) -> None:
    client = MagicMock()
    client.api = MagicMock()
    client.api.exec_create.return_value = {"Id": "exec-slow"}

    # exec_start takes 3 seconds to return a single chunk; a 1-second
    # timeout should fire well before it finishes, but the stream
    # itself is finite so pytest teardown doesn't leak a thread.
    def _slow_stream(*_args: Any, **_kwargs: Any) -> Any:
        import time

        time.sleep(3)
        yield (b"late", b"")

    client.api.exec_start.side_effect = _slow_stream
    client.api.exec_inspect.return_value = {"ExitCode": 0, "Pid": 9999}

    dexec = _dexec(client, workspace=tmp_path)
    result = await dexec.run(["sleep", "60"], timeout=1)
    assert result.exit_code == -1
    assert "signoff.timeout" in result.stderr
    # Best-effort kill via exec_create with a `kill -9 <pid>` shell.
    create_calls = client.api.exec_create.call_args_list
    kill_call = next(
        (c for c in create_calls if "kill -9" in " ".join(c.kwargs.get("cmd", []))),
        None,
    )
    assert kill_call is not None


# ---------------------------------------------------------------------------
# DockerVerifierContext wrap_context
# ---------------------------------------------------------------------------


async def test_wrap_context_delegates_exec_to_docker_exec(tmp_path: Path) -> None:
    client = _client(chunks=[(b"wrapped\n", b"")])
    dexec = _dexec(client, workspace=tmp_path)
    base = VerifierContext(
        deliverable=Deliverable(id="dlv_1", kind="research_report", content=None),
        http=FakeHttpClient(),
        judge=FakeJudge(),
        policy=__import__("signoff.runtime.base", fromlist=["RuntimePolicy"]).RuntimePolicy(
            timeout_seconds=30
        ),
        workspace=tmp_path,
    )
    wrapped = wrap_context(base, dexec)
    assert isinstance(wrapped, DockerVerifierContext)
    result = await wrapped.exec(["echo", "wrapped"])
    assert "wrapped" in result.stdout
    # The original ctx retains its own exec (untouched).
    assert wrapped is not base


async def test_wrap_context_without_docker_exec_falls_back(tmp_path: Path) -> None:
    base = VerifierContext(
        deliverable=Deliverable(id="dlv_1", kind="research_report", content=None),
        http=FakeHttpClient(),
        judge=FakeJudge(),
        policy=__import__("signoff.runtime.base", fromlist=["RuntimePolicy"]).RuntimePolicy(
            timeout_seconds=30
        ),
        workspace=tmp_path,
    )
    # Construct via the new composition API: wrap + None means
    # exec falls through to ``base.exec`` (the local subprocess).
    wrapped = DockerVerifierContext(wrapped=base, docker_exec=None)
    import sys

    # Regression: previous inheritance-based version raised
    # ``TypeError: super(type, obj): obj must be an instance or
    # subtype of type`` here because @dataclass(slots=True)
    # subclassing broke the bare super() call. Composition makes
    # the fallback a plain method call on the wrapped context.
    result = await wrapped.exec([sys.executable, "-c", "print('local')"])
    assert "local" in result.stdout


async def test_wrap_context_delegates_attribute_access(tmp_path: Path) -> None:
    """Reads of every VerifierContext attribute go through to the
    wrapped instance. Verifiers see no behavioural difference from
    the in-process ctx."""
    base = VerifierContext(
        deliverable=Deliverable(id="dlv_attr", kind="research_report", content=None),
        http=FakeHttpClient(),
        judge=FakeJudge(),
        policy=__import__("signoff.runtime.base", fromlist=["RuntimePolicy"]).RuntimePolicy(
            timeout_seconds=42
        ),
        workspace=tmp_path,
        budget_remaining_usd=1.50,
    )
    wrapped = DockerVerifierContext(wrapped=base, docker_exec=None)

    # Every passthrough attribute reads from the wrapped instance.
    assert wrapped.deliverable is base.deliverable
    assert wrapped.http is base.http
    assert wrapped.judge is base.judge
    assert wrapped.policy is base.policy
    assert wrapped.workspace == tmp_path
    assert wrapped.budget_remaining_usd == 1.50


async def test_wrap_context_setattr_writes_through(tmp_path: Path) -> None:
    """The harness assigns ``current_verifier_meta`` and
    ``current_claim`` on the ctx after construction; those writes
    must land on the wrapped instance so verifier-side reads of
    ``ctx.current_*`` see what the harness stamped."""
    from signoff import VerifierMeta
    from signoff.models import Claim

    base = VerifierContext(
        deliverable=Deliverable(id="dlv_setattr", kind="research_report", content=None),
        http=FakeHttpClient(),
        judge=FakeJudge(),
        policy=__import__("signoff.runtime.base", fromlist=["RuntimePolicy"]).RuntimePolicy(
            timeout_seconds=10
        ),
        workspace=tmp_path,
    )
    wrapped = DockerVerifierContext(wrapped=base, docker_exec=None)

    meta = VerifierMeta(
        name="x",
        pack="signoff-research",
        claim_kinds=("citation",),
        cost_tier="cheap",
        concurrency=1,
    )
    claim = Claim.model_construct(id="clm_x", text="t", kind="citation", evidence={})
    wrapped.current_verifier_meta = meta
    wrapped.current_claim = claim
    assert base.current_verifier_meta is meta
    assert base.current_claim is claim
