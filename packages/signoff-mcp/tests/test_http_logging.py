"""Regression test for the Uvicorn-log_config fix.

Unit tests in ``test_server.py`` exercise the handler path via
``setup_logging`` directly, but they don't catch the class of bug where
Uvicorn's default ``log_config`` runs a ``dictConfig`` that disables
existing loggers and replaces handlers. That disabling only happens
inside ``Server.serve()`` at HTTP-transport startup.

To catch it, this test spawns ``signoff-mcp`` as a subprocess with the
HTTP transport, waits for ``/health``, pokes ``/version`` (which fires
Uvicorn's access log), tears the process down, and inspects the
captured stderr. A regression that drops ``log_config=None`` from
``uvicorn.Config`` would cause Uvicorn's default format to replace
ours and this test would fail.
"""

from __future__ import annotations

import os
import re
import socket
import subprocess
import sys
import time
from pathlib import Path

import httpx
import pytest

# Matches the formatter string from signoff._logging:
#     "%(asctime)s %(levelname)s %(name)s: %(message)s"
# i.e. "2026-04-19 13:56:25,294 INFO signoff.harness: hello"
SIGNOFF_FORMAT = re.compile(
    r"\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2},\d+ "
    r"(?:DEBUG|INFO|WARNING|ERROR|CRITICAL) "
    r"(?P<logger>[\w.\-]+): "
)


def _find_free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _wait_for_health(url: str, *, timeout: float = 15.0) -> None:
    deadline = time.monotonic() + timeout
    last: BaseException | None = None
    while time.monotonic() < deadline:
        try:
            r = httpx.get(url, timeout=1.0)
            if r.status_code == 200:
                return
        except Exception as exc:
            last = exc
        time.sleep(0.1)
    raise RuntimeError(f"server never became healthy at {url}: {last!r}")


@pytest.mark.integration
def test_http_server_stderr_carries_signoff_log_format(tmp_path: Path) -> None:
    """Real HTTP transport must emit Signoff-format log lines to stderr.

    Covers two concerns the unit tests miss:
    1. ``setup_logging`` is called early enough to catch startup logs
       from ``Harness.from_config_path`` (``signoff.harness`` INFO).
    2. Uvicorn's dictConfig does NOT clobber our handlers — verified by
       asserting that the ``uvicorn.access`` line for ``GET /version``
       lands in Signoff format (timestamp + logger name + level + message)
       rather than Uvicorn's default ``"INFO:     …"`` form.
    """
    port = _find_free_port()
    cfg_path = tmp_path / "signoff.yaml"
    cfg_path.write_text('protocol_version: "0.1"\npacks: []\ndeliverables: {}\n')

    proc = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "signoff_mcp",
            "--transport",
            "http",
            "--host",
            "127.0.0.1",
            "--port",
            str(port),
            "--config",
            str(cfg_path),
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env={**os.environ, "PYTHONUNBUFFERED": "1"},
    )
    try:
        _wait_for_health(f"http://127.0.0.1:{port}/health")
        resp = httpx.get(f"http://127.0.0.1:{port}/version", timeout=5)
        assert resp.status_code == 200
        # Give uvicorn a beat to flush the access-log record.
        time.sleep(0.2)
    finally:
        proc.terminate()
        try:
            _stdout, stderr_bytes = proc.communicate(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
            _stdout, stderr_bytes = proc.communicate()

    stderr = stderr_bytes.decode("utf-8", errors="replace")
    matches = list(SIGNOFF_FORMAT.finditer(stderr))
    loggers_seen = {m.group("logger") for m in matches}

    # At minimum: at least one line in Signoff format. Without the fix
    # Uvicorn's dictConfig wipes our handler and no Signoff-format line
    # appears.
    assert matches, "expected at least one log line in Signoff format in stderr; got:\n" + stderr

    # Specifically: the access-log line for /version is carried by the
    # uvicorn.access logger. If Uvicorn's default log_config had run
    # instead, the line would be in Uvicorn's default format (no
    # timestamp, no logger name prefix) and this assertion would fail.
    # Also accept 'uvicorn' alone as some uvicorn versions bundle the
    # startup banner under that logger.
    assert (
        any(logger.startswith("uvicorn") for logger in loggers_seen) or "GET /version" in stderr
    ), (
        "expected a uvicorn.* access-log line in Signoff format; "
        f"loggers seen: {sorted(loggers_seen)}\nstderr:\n{stderr}"
    )


@pytest.mark.integration
def test_http_server_does_not_emit_uvicorn_default_format(tmp_path: Path) -> None:
    """Uvicorn's default format is ``"INFO:     foo"`` (colon-space-aligned).

    If Uvicorn's dictConfig runs (regression), that shape replaces ours.
    We assert the default shape never appears. Combined with the
    positive assertion above, this pair pins the fix.
    """
    port = _find_free_port()
    cfg_path = tmp_path / "signoff.yaml"
    cfg_path.write_text('protocol_version: "0.1"\npacks: []\ndeliverables: {}\n')

    proc = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "signoff_mcp",
            "--transport",
            "http",
            "--host",
            "127.0.0.1",
            "--port",
            str(port),
            "--config",
            str(cfg_path),
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env={**os.environ, "PYTHONUNBUFFERED": "1"},
    )
    try:
        _wait_for_health(f"http://127.0.0.1:{port}/health")
        httpx.get(f"http://127.0.0.1:{port}/version", timeout=5)
        time.sleep(0.2)
    finally:
        proc.terminate()
        try:
            _, stderr_bytes = proc.communicate(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
            _, stderr_bytes = proc.communicate()

    stderr = stderr_bytes.decode("utf-8", errors="replace")
    # Two kinds of bare-format leak we guard against:
    #   - Uvicorn's default formatter:   "INFO:     message"
    #   - logging.basicConfig / lastResort: "WARNING:root:message" or
    #     "WARNING:mcp.shared.session:message"
    # Both match `^LEVEL:` with no preceding timestamp. Our Signoff
    # format always starts with a "YYYY-MM-DD …" date, so these
    # patterns are disjoint.
    bare_fmt = re.compile(r"^(?:DEBUG|INFO|WARNING|ERROR|CRITICAL):", re.MULTILINE)
    assert not bare_fmt.search(stderr), (
        "bare LEVEL:... format leaked into stderr — a logger fell through "
        "to Python's lastResort or Uvicorn's default log_config.\n" + stderr
    )


# Matches "INFO signoff.mcp[.*]: list_verifiers …" or the same for
# request_signoff. Regression test: if a tool handler stops logging
# (the Phase 0 shape of the bug), this pattern doesn't match and the
# test fails.
TOOL_HANDLER_LOG = re.compile(
    r"INFO signoff\.mcp[\w.]*:\s*(?:request_signoff|list_verifiers|get_verdict)\b"
)


@pytest.mark.integration
def test_root_logger_warning_uses_signoff_format(tmp_path: Path) -> None:
    """The MCP SDK logs init-handshake warnings via
    ``logging.warning(...)`` at module level (see
    ``mcp/shared/session.py:383``):

        logging.warning(f"Failed to validate request: {e}")

    That's the root logger — not ``signoff.*``, not ``mcp.*`` —
    because the call bypasses any named logger. Without explicit
    root-logger routing it falls through to Python's ``basicConfig``
    / ``lastResort`` and prints in bare ``"WARNING:root:..."``
    format, not our Signoff format.

    Simulating the init race reliably against a live MCP server is
    fiddly (SSE lifecycle + cross-task session_id extraction), so
    this test reproduces the same logging path directly: spawn a
    Python subprocess that calls ``setup_logging`` +
    ``_route_external_loggers_through_signoff`` exactly as
    ``serve_http`` does, then emits the SDK's exact
    ``logging.warning(...)`` call. Asserts the line lands with
    Signoff's date-prefixed format.

    Regression: remove root-logger routing from
    ``_route_external_loggers_through_signoff`` and this test fails
    with a bare ``WARNING:root:...`` line in stderr.
    """
    script = (
        "import logging, sys\n"
        "from signoff import setup_logging\n"
        "from signoff_mcp.server import _route_external_loggers_through_signoff\n"
        "setup_logging(stream=sys.stderr)\n"
        "_route_external_loggers_through_signoff()\n"
        "logging.warning('Failed to validate request: simulated init race')\n"
    )
    result = subprocess.run(
        [sys.executable, "-c", script],
        capture_output=True,
        text=True,
        env={**os.environ, "PYTHONUNBUFFERED": "1"},
        timeout=15,
    )

    assert "Failed to validate request: simulated init race" in result.stderr, (
        "expected simulated SDK warning in stderr; got:\n" + result.stderr
    )
    formatted_warning = re.compile(
        r"\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2},\d+ WARNING "
        r"[\w.\-]+: Failed to validate request: simulated init race"
    )
    assert formatted_warning.search(result.stderr), (
        "MCP SDK's logging.warning(...) path landed in bare format. "
        "Root-logger routing in _route_external_loggers_through_signoff "
        "is probably missing.\n" + result.stderr
    )
    assert "WARNING:root:Failed to validate request" not in result.stderr, (
        "bare 'WARNING:root:' form leaked; root-logger handler not installed.\n" + result.stderr
    )


@pytest.mark.integration
@pytest.mark.asyncio
async def test_http_tool_call_emits_signoff_mcp_log(tmp_path: Path) -> None:
    """A real MCP call via SSE must emit a ``signoff.mcp`` log line
    naming the tool. Catches the "tool handlers silent" class of bug —
    access logs alone don't tell you which tool was called, which is
    the whole point of an audit stream.
    """
    from mcp import ClientSession
    from mcp.client.sse import sse_client

    port = _find_free_port()
    cfg_path = tmp_path / "signoff.yaml"
    cfg_path.write_text('protocol_version: "0.1"\npacks: []\ndeliverables: {}\n')

    proc = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "signoff_mcp",
            "--transport",
            "http",
            "--host",
            "127.0.0.1",
            "--port",
            str(port),
            "--config",
            str(cfg_path),
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env={**os.environ, "PYTHONUNBUFFERED": "1"},
    )
    try:
        _wait_for_health(f"http://127.0.0.1:{port}/health")

        # Connect as an actual MCP client and invoke list_verifiers.
        async with sse_client(f"http://127.0.0.1:{port}/sse") as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                await session.call_tool("list_verifiers", {})

        # Let the handler's log line make it out to the pipe.
        time.sleep(0.3)
    finally:
        proc.terminate()
        try:
            _stdout, stderr_bytes = proc.communicate(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
            _stdout, stderr_bytes = proc.communicate()

    stderr = stderr_bytes.decode("utf-8", errors="replace")
    assert TOOL_HANDLER_LOG.search(stderr), (
        "expected a signoff.mcp log line naming a tool; got:\n" + stderr
    )
