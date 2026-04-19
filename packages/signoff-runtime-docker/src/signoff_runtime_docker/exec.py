"""``docker exec`` bridge for :class:`DockerVerifierContext`.

One class, :class:`DockerExec`, whose :meth:`run` method signature
matches :meth:`signoff.VerifierContext.exec` so the wrapped context
can delegate verbatim. Translates host ``cwd`` paths into the
container's ``/workspace``-rooted view, enforces per-call timeouts by
killing the exec'd process (the container itself keeps running for
other execs), and truncates stdout/stderr at configurable byte caps.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from collections.abc import Mapping
from pathlib import Path, PurePosixPath
from typing import Any

from signoff.context import ExecResult

from signoff_runtime_docker.errors import ExecCwdOutsideWorkspaceError

__all__ = ["CONTAINER_WORKSPACE", "DockerExec"]


_logger = logging.getLogger("signoff_runtime_docker.exec")


#: Where the host workspace is bind-mounted inside the container.
#: Hardcoded because we also pre-bake it into the generic-sandbox
#: image's ``WORKDIR`` — a runtime override would require re-rolling
#: the image.
CONTAINER_WORKSPACE = PurePosixPath("/workspace")


class DockerExec:
    """Run commands inside a long-running container via ``docker exec``.

    The ``workspace_host`` → ``workspace_container`` mapping is part
    of the constructor so :meth:`run` can translate caller-supplied
    ``cwd`` from host to container paths deterministically.
    """

    def __init__(
        self,
        client: Any,
        container_id: str,
        *,
        workspace_host: Path,
        workspace_container: PurePosixPath = CONTAINER_WORKSPACE,
        stdout_max_bytes: int,
        stderr_max_bytes: int,
    ) -> None:
        self._client = client
        self._container_id = container_id
        self._workspace_host = workspace_host.resolve()
        self._workspace_container = workspace_container
        self._stdout_cap = stdout_max_bytes
        self._stderr_cap = stderr_max_bytes

    async def run(
        self,
        cmd: list[str],
        *,
        cwd: Path | None = None,
        timeout: int = 30,
        env: Mapping[str, str] | None = None,
    ) -> ExecResult:
        """Execute ``cmd`` inside the container.

        Signature compatible with :meth:`signoff.VerifierContext.exec`;
        the ``cwd`` parameter is translated from a host path into a
        container-rooted path using the workspace mapping. ``cwd``
        outside the workspace raises :class:`ExecCwdOutsideWorkspaceError`.

        Always returns a fully-formed :class:`ExecResult` — timeouts
        surface as ``exit_code=-1`` with a ``signoff.truncated`` marker
        in ``stderr`` rather than a raised exception, so verifier
        authors follow the same "never raise from exec" pattern that
        :class:`LocalRuntime` uses.
        """
        if not cmd:
            raise ValueError("DockerExec.run called with empty cmd list.")
        container_cwd = self._translate_cwd(cwd)
        merged_env: list[str] | None = None
        if env is not None:
            merged_env = [f"{k}={v}" for k, v in env.items()]

        started = time.perf_counter()

        def _exec_create() -> str:
            return str(
                self._client.api.exec_create(
                    container=self._container_id,
                    cmd=list(cmd),
                    workdir=str(container_cwd),
                    environment=merged_env,
                    stdout=True,
                    stderr=True,
                    tty=False,
                )["Id"]
            )

        exec_id = await asyncio.to_thread(_exec_create)

        def _exec_start_demux() -> tuple[bytes, bytes]:
            stdout_buf = bytearray()
            stderr_buf = bytearray()
            stdout_truncated = False
            stderr_truncated = False
            stream = self._client.api.exec_start(exec_id, stream=True, demux=True)
            for chunk in stream:
                s_out, s_err = chunk if isinstance(chunk, tuple) else (chunk, b"")
                if s_out:
                    if not stdout_truncated and len(stdout_buf) + len(s_out) > self._stdout_cap:
                        headroom = max(0, self._stdout_cap - len(stdout_buf))
                        stdout_buf.extend(s_out[:headroom])
                        stdout_truncated = True
                    elif not stdout_truncated:
                        stdout_buf.extend(s_out)
                if s_err:
                    if not stderr_truncated and len(stderr_buf) + len(s_err) > self._stderr_cap:
                        headroom = max(0, self._stderr_cap - len(stderr_buf))
                        stderr_buf.extend(s_err[:headroom])
                        stderr_truncated = True
                    elif not stderr_truncated:
                        stderr_buf.extend(s_err)
            if stdout_truncated:
                stdout_buf.extend(
                    f"\n[signoff.truncated: stdout exceeded {self._stdout_cap} bytes]".encode()
                )
            if stderr_truncated:
                stderr_buf.extend(
                    f"\n[signoff.truncated: stderr exceeded {self._stderr_cap} bytes]".encode()
                )
            return bytes(stdout_buf), bytes(stderr_buf)

        runner = asyncio.create_task(asyncio.to_thread(_exec_start_demux))
        try:
            stdout_bytes, stderr_bytes = await asyncio.wait_for(runner, timeout=timeout)
            exit_code = await self._inspect_exit_code(exec_id)
        except TimeoutError:
            runner.cancel()
            with contextlib.suppress(BaseException):
                await runner
            await self._kill_exec(exec_id)
            duration_ms = int((time.perf_counter() - started) * 1000)
            return ExecResult(
                exit_code=-1,
                stdout="",
                stderr=f"[signoff.timeout: exec exceeded {timeout}s]\n",
                duration_ms=duration_ms,
            )

        duration_ms = int((time.perf_counter() - started) * 1000)
        return ExecResult(
            exit_code=exit_code,
            stdout=stdout_bytes.decode("utf-8", errors="replace"),
            stderr=stderr_bytes.decode("utf-8", errors="replace"),
            duration_ms=duration_ms,
        )

    def _translate_cwd(self, cwd: Path | None) -> PurePosixPath:
        if cwd is None:
            return self._workspace_container
        resolved = cwd.resolve()
        try:
            relative = resolved.relative_to(self._workspace_host)
        except ValueError as exc:
            raise ExecCwdOutsideWorkspaceError(
                f"ctx.exec(cwd={cwd!s}) points outside "
                f"workspace={self._workspace_host!s}; the container "
                "cannot see this path."
            ) from exc
        return self._workspace_container.joinpath(*relative.parts)

    async def _inspect_exit_code(self, exec_id: str) -> int:
        def _inspect() -> int:
            info = self._client.api.exec_inspect(exec_id)
            code = info.get("ExitCode")
            if code is None:
                return -1
            return int(code)

        return await asyncio.to_thread(_inspect)

    async def _kill_exec(self, exec_id: str) -> None:
        """Poll ``exec_inspect`` for the PID and send SIGKILL via the
        host. Best-effort — the container itself keeps running for
        other execs.
        """

        def _pid() -> int | None:
            try:
                info = self._client.api.exec_inspect(exec_id)
            except Exception:
                return None
            pid = info.get("Pid")
            return int(pid) if pid else None

        pid = await asyncio.to_thread(_pid)
        if pid in (None, 0):
            return

        # We run the kill inside the container with ``docker exec``
        # so we don't assume the harness sees the same PID namespace.
        def _do_kill() -> None:
            try:
                self._client.api.exec_create(
                    container=self._container_id,
                    cmd=["/bin/sh", "-c", f"kill -9 {pid} 2>/dev/null || true"],
                    stdout=False,
                    stderr=False,
                    tty=False,
                )
            except Exception:
                _logger.debug("Best-effort kill for exec pid=%s failed", pid)

        await asyncio.to_thread(_do_kill)
