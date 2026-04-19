"""MCP server exposing the Signoff harness.

Implements ``docs/protocol.md`` §7.3. The server owns one
:class:`signoff.Harness` for its process lifetime and translates MCP
tool calls into ``Harness.verify()`` calls.

Transports:

- stdio (default — Claude Desktop, Cursor, Cline, Zed, Continue).
- HTTP+SSE — for remote MCP clients and Docker deployments. Adds
  ``/health`` and ``/version`` endpoints and supports an optional
  Bearer token via ``SIGNOFF_MCP_AUTH_TOKEN``.
"""

from __future__ import annotations

import json
import logging
import os
import sys
import time
from pathlib import Path
from typing import Any

import mcp.types as mcp_types
from mcp.server import NotificationOptions, Server
from mcp.server.sse import SseServerTransport
from mcp.server.stdio import stdio_server
from pydantic import ValidationError
from signoff import Claim, Deliverable, Harness, HarnessConfig, setup_logging

from signoff_mcp import __version__ as _MCP_VERSION
from signoff_mcp._tools import (
    TOOL_GET_VERDICT,
    TOOL_LIST_VERIFIERS,
    TOOL_REQUEST_SIGNOFF,
    get_verdict_message,
)

__all__ = ["SignoffMCPServer"]


_logger = logging.getLogger("signoff.mcp")

_PROTOCOL_VERSION = "0.1"


class _ToolError(Exception):
    """Raised from a tool handler to produce an MCP error result.

    The message is returned to the client. Tracebacks are logged at
    ERROR but never leaked to the wire.
    """


class SignoffMCPServer:
    """MCP server exposing the Harness over the Model Context Protocol.

    Implements protocol §7.3 — the ``request_signoff``, ``list_verifiers``,
    and ``get_verdict`` tools.
    """

    def __init__(self, harness: Harness) -> None:
        self.harness = harness

    # ------------------------------------------------------------------
    # Factories
    # ------------------------------------------------------------------

    @classmethod
    async def from_config_path(cls, path: Path | str, **harness_kwargs: Any) -> SignoffMCPServer:
        """Build the harness from ``path`` then wrap it. ``**harness_kwargs``
        are forwarded to :meth:`Harness.from_config_path`."""
        harness = await Harness.from_config_path(path, **harness_kwargs)
        await harness.prepare()
        return cls(harness)

    # ------------------------------------------------------------------
    # App wiring
    # ------------------------------------------------------------------

    def build_app(self) -> Server[Any, Any]:
        """Construct the underlying :class:`mcp.server.Server` with the
        three Signoff tools registered. Separated from the transport
        runners for testability.
        """
        server: Server[Any, Any] = Server(
            name="signoff-mcp",
            version=_MCP_VERSION,
            instructions=(
                "Signoff verifies agent deliverables against a registry of "
                "claim-level checks. Call request_signoff with your claims "
                "before declaring a task complete; if the verdict is not "
                "passed, address each entry in feedback_packet.blockers and "
                "resubmit."
            ),
        )

        @server.list_tools()  # type: ignore[no-untyped-call,untyped-decorator]
        async def _list_tools() -> list[mcp_types.Tool]:
            return [
                TOOL_REQUEST_SIGNOFF,
                TOOL_LIST_VERIFIERS,
                TOOL_GET_VERDICT,
            ]

        @server.call_tool()  # type: ignore[untyped-decorator]
        async def _call_tool(
            name: str, arguments: dict[str, Any] | None
        ) -> list[mcp_types.TextContent]:
            return await self._dispatch_tool(name, arguments or {})

        return server

    # ------------------------------------------------------------------
    # Tool dispatch
    # ------------------------------------------------------------------

    async def _dispatch_tool(
        self, name: str, arguments: dict[str, Any]
    ) -> list[mcp_types.TextContent]:
        if name == "request_signoff":
            return _ok_content(await self._handle_request_signoff(arguments))
        if name == "list_verifiers":
            return _ok_content(await self._handle_list_verifiers())
        if name == "get_verdict":
            return _ok_content(await self._handle_get_verdict(arguments))
        raise _ToolError(f"Unknown tool: {name!r}")

    async def _handle_request_signoff(self, arguments: dict[str, Any]) -> dict[str, Any]:
        started = time.perf_counter()

        # Log the invocation before validation so every attempt shows up
        # in the audit stream, even ones that get rejected downstream.
        deliverable_id = (
            arguments.get("deliverable", {}).get("id")
            if isinstance(arguments, dict) and isinstance(arguments.get("deliverable"), dict)
            else None
        )
        raw_claims = arguments.get("claims") if isinstance(arguments, dict) else None
        claim_count = len(raw_claims) if isinstance(raw_claims, list) else 0
        _logger.info(
            "request_signoff invoked: deliverable_id=%r claims=%d",
            deliverable_id,
            claim_count,
        )

        if not isinstance(arguments, dict):
            _logger.info(
                "request_signoff validation failed: arguments must be a JSON object (got %s)",
                type(arguments).__name__,
            )
            raise _ToolError(
                f"request_signoff expects a JSON object; got {type(arguments).__name__}"
            )
        raw_deliverable = arguments.get("deliverable")
        if raw_deliverable is None:
            _logger.info("request_signoff validation failed: 'deliverable' is required")
            raise _ToolError("request_signoff: 'deliverable' is required (see protocol §7.3.1).")
        try:
            deliverable = Deliverable.model_validate(raw_deliverable)
            claims = [Claim.model_validate(c) for c in arguments.get("claims", [])]
        except ValidationError as exc:
            msg = _format_validation_error(exc)
            _logger.info("request_signoff validation failed: %s", msg)
            raise _ToolError(f"request_signoff: input validation failed: {msg}") from None

        config_override = arguments.get("config_override")
        retry_budget = arguments.get("retry_budget")
        if config_override is not None and not isinstance(config_override, dict):
            _logger.info("request_signoff validation failed: config_override must be an object")
            raise _ToolError("request_signoff: 'config_override' must be an object if provided.")
        if retry_budget is not None and (not isinstance(retry_budget, int) or retry_budget < 0):
            _logger.info(
                "request_signoff validation failed: retry_budget must be a "
                "non-negative integer (got %r)",
                retry_budget,
            )
            raise _ToolError("request_signoff: 'retry_budget' must be a non-negative integer.")

        try:
            verdict = await self.harness.verify(
                deliverable,
                claims,
                config_override=config_override,
                retry_budget=retry_budget,
            )
        except Exception as exc:
            _logger.error(
                "request_signoff: harness.verify raised %s",
                type(exc).__name__,
                exc_info=True,
            )
            raise _ToolError(f"verification failed: {type(exc).__name__}: {exc}") from None

        duration_ms = int((time.perf_counter() - started) * 1000)
        _logger.info(
            "request_signoff completed: verdict_id=%s passed=%s duration_ms=%d",
            verdict.id,
            verdict.passed,
            duration_ms,
        )
        return json.loads(verdict.model_dump_json())  # type: ignore[no-any-return]

    async def _handle_list_verifiers(self) -> dict[str, Any]:
        started = time.perf_counter()
        _logger.info("list_verifiers invoked")
        entries: list[dict[str, Any]] = []
        cfg = self.harness.config
        for meta in self.harness.registry.list_all():
            entries.append(
                {
                    "name": meta.name,
                    "pack": meta.pack,
                    "claim_kinds": list(meta.claim_kinds),
                    "cost_tier": meta.cost_tier,
                    "version": meta.version,
                    "enabled": _is_enabled(cfg, meta.fully_qualified_name),
                }
            )
        duration_ms = int((time.perf_counter() - started) * 1000)
        _logger.info(
            "list_verifiers completed: verifier_count=%d duration_ms=%d",
            len(entries),
            duration_ms,
        )
        return {
            "protocol_version": _PROTOCOL_VERSION,
            "verifiers": entries,
        }

    async def _handle_get_verdict(self, arguments: dict[str, Any]) -> dict[str, Any]:
        verdict_id = arguments.get("verdict_id") if isinstance(arguments, dict) else None
        _logger.info("get_verdict invoked: verdict_id=%r", verdict_id)
        # Local servers don't persist verdicts. Always reject — but log
        # so the audit stream still records the attempt.
        _logger.info("get_verdict rejected: local server does not persist verdicts")
        raise _ToolError(get_verdict_message())

    # ------------------------------------------------------------------
    # Transports
    # ------------------------------------------------------------------

    async def serve_stdio(self) -> None:
        """Run the server over stdio. Routes ``signoff`` loggers to
        stderr so stdout stays reserved for MCP protocol messages."""
        setup_logging(stream=sys.stderr)
        app = self.build_app()
        init_options = app.create_initialization_options(NotificationOptions())
        async with stdio_server() as (read, write):
            await app.run(read, write, init_options)

    async def serve_http(
        self,
        *,
        host: str = "127.0.0.1",
        port: int = 8765,
    ) -> None:
        """Run the server over HTTP+SSE on ``host:port``. Adds ``/health``
        and ``/version`` endpoints. Honors ``SIGNOFF_MCP_AUTH_TOKEN`` for
        an optional Bearer-token auth check.

        Logging
        -------
        Uvicorn's default ``log_config`` runs a ``dictConfig`` with
        ``disable_existing_loggers=True``, which silently disables every
        ``signoff.*`` logger we configured through
        :func:`signoff.setup_logging`. We pass ``log_config=None`` to
        prevent that, and ``access_log=True`` so per-request lines are
        restored — with Uvicorn's own loggers routed through the Signoff
        handler so app and access logs share one format.
        """
        import uvicorn

        setup_logging(stream=sys.stderr)
        _route_external_loggers_through_signoff()
        app = self.build_app()
        starlette_app = _build_http_app(self, app)
        config = uvicorn.Config(
            starlette_app,
            host=host,
            port=port,
            log_config=None,  # preserve setup_logging() / _route_uvicorn_…
            log_level="info",
            access_log=True,
        )
        await uvicorn.Server(config).serve()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _ok_content(payload: dict[str, Any]) -> list[mcp_types.TextContent]:
    """Wrap a JSON-serialisable payload as a ``TextContent`` list.

    MCP tool results are always a list of content blocks; for our
    purposes a single JSON text block is sufficient. Clients that
    understand ``structuredContent`` get the dict directly via the
    call-tool response envelope assembled by the SDK.
    """
    return [mcp_types.TextContent(type="text", text=json.dumps(payload, ensure_ascii=False))]


def _is_enabled(cfg: HarnessConfig, fqn: str) -> bool:
    """A verifier is considered disabled if **every** deliverable-kind
    block that mentions it sets ``enabled=false``. Otherwise enabled is
    assumed. A verifier never mentioned in any block reads as
    ``enabled=True`` by default (harness resolution adds it when its
    pack is active).
    """
    any_mention = False
    any_enabled = False
    for block in cfg.deliverables.values():
        v_cfg = block.verifiers.get(fqn)
        if v_cfg is None:
            continue
        any_mention = True
        if v_cfg.enabled:
            any_enabled = True
            break
    return (not any_mention) or any_enabled


def _route_external_loggers_through_signoff() -> None:
    """Route external-library loggers through the same handler
    :func:`signoff.setup_logging` installed on the ``signoff`` logger.

    Three categories of external log emitter need attention in the
    HTTP-transport MCP server process:

    - ``uvicorn`` — ``uvicorn.error`` + ``uvicorn.access``. With our
      ``log_config=None`` (see :meth:`SignoffMCPServer.serve_http`),
      Uvicorn's dictConfig doesn't run, so these loggers have no
      handlers. Route them through our handler so startup banners
      and request access lines share the Signoff format.
    - ``mcp`` — the MCP SDK's named loggers.
    - The root logger — catches dependencies that call
      :func:`logging.warning` directly (without first resolving a
      named logger). The MCP SDK does this at
      ``mcp/shared/session.py:logging.warning("Failed to validate
      request: …")`` for the protocol-level handshake warning that
      fires on SSE reconnects. Without this, those records fall
      through to Python's lastResort / basicConfig bare
      ``"LEVEL:name:message"`` format — which no other line in our
      stream uses.

    Root-handler attachment is scoped to this function (only called
    from :meth:`SignoffMCPServer.serve_http`). In library-embedded
    use — where ``setup_logging`` is opt-in and this helper is never
    invoked — root stays untouched.
    """
    signoff_logger = logging.getLogger("signoff")
    handlers = list(signoff_logger.handlers)
    if not handlers:
        return
    for name in ("uvicorn", "uvicorn.error", "uvicorn.access", "mcp"):
        logger = logging.getLogger(name)
        logger.handlers = handlers
        logger.setLevel(logging.INFO)
        # Don't also propagate to root — the root-handler attachment
        # below would otherwise duplicate the record.
        logger.propagate = False
    root = logging.getLogger()
    root.handlers = handlers
    if root.level == logging.NOTSET or root.level > logging.INFO:
        root.setLevel(logging.INFO)


def _format_validation_error(exc: ValidationError) -> str:
    errors = exc.errors()
    if not errors:
        return str(exc)
    first = errors[0]
    loc = ".".join(str(p) for p in first.get("loc", ()))
    msg = first.get("msg", "")
    return f"{loc}: {msg}" if loc else msg


# ---------------------------------------------------------------------------
# HTTP transport
# ---------------------------------------------------------------------------


def _build_http_app(signoff_server: SignoffMCPServer, mcp_app: Server[Any, Any]) -> Any:
    """Build the Starlette application that hosts the MCP SSE transport
    plus the ancillary ``/health`` and ``/version`` endpoints."""
    from starlette.applications import Starlette
    from starlette.middleware import Middleware
    from starlette.middleware.cors import CORSMiddleware
    from starlette.requests import Request
    from starlette.responses import JSONResponse, Response
    from starlette.routing import Mount, Route

    sse = SseServerTransport("/messages/")

    async def handle_sse(request: Request) -> Response:
        async with sse.connect_sse(request.scope, request.receive, request._send) as (
            read,
            write,
        ):
            init_options = mcp_app.create_initialization_options(NotificationOptions())
            await mcp_app.run(read, write, init_options)
        return Response(status_code=200)

    async def health(_request: Request) -> Response:
        try:
            verifier_count = len(signoff_server.harness.registry)
        except Exception:
            return JSONResponse({"status": "error", "harness": "unready"}, status_code=503)
        return JSONResponse(
            {
                "status": "ok",
                "harness": "ready",
                "verifier_count": verifier_count,
            }
        )

    async def version(_request: Request) -> Response:
        from signoff import __version__ as core_version

        return JSONResponse(
            {
                "protocol_version": _PROTOCOL_VERSION,
                "harness_version": core_version,
                "mcp_server_version": _MCP_VERSION,
            }
        )

    middleware = [
        Middleware(
            CORSMiddleware,
            allow_origins=["*"],  # Phase 0: developer tool. Lock down in production.
            allow_methods=["GET", "POST", "OPTIONS"],
            allow_headers=["*"],
        ),
        Middleware(_BearerAuthMiddleware),
    ]

    routes = [
        Route("/health", endpoint=health, methods=["GET"]),
        Route("/version", endpoint=version, methods=["GET"]),
        Route("/sse", endpoint=handle_sse, methods=["GET"]),
        Mount("/messages/", app=sse.handle_post_message),
    ]
    return Starlette(routes=routes, middleware=middleware)


class _BearerAuthMiddleware:
    """Enforce ``SIGNOFF_MCP_AUTH_TOKEN`` if set. No-op otherwise.

    Phase 0 deliberately ships without auth by default; this middleware
    exists so operators who expose the server over a network can opt
    into a token-based guard without standing up a full identity stack.
    """

    _UNAUTH_PATHS = frozenset({"/health", "/version"})

    def __init__(self, app: Any) -> None:
        self.app = app

    async def __call__(self, scope: Any, receive: Any, send: Any) -> None:
        token = os.environ.get("SIGNOFF_MCP_AUTH_TOKEN")
        if scope.get("type") != "http" or not token:
            await self.app(scope, receive, send)
            return
        path = scope.get("path", "")
        if path in self._UNAUTH_PATHS:
            await self.app(scope, receive, send)
            return
        headers = {k.decode().lower(): v.decode() for k, v in scope.get("headers", [])}
        auth = headers.get("authorization", "")
        if auth == f"Bearer {token}":
            await self.app(scope, receive, send)
            return
        from starlette.responses import JSONResponse

        response = JSONResponse({"error": "unauthorized"}, status_code=401)
        await response(scope, receive, send)


# ---------------------------------------------------------------------------
# Module-level convenience
# ---------------------------------------------------------------------------


async def serve(
    *,
    config_path: Path | str = "signoff.yaml",
    transport: str = "stdio",
    host: str = "127.0.0.1",
    port: int = 8765,
) -> None:
    """Start a Signoff MCP server. Used by ``python -m signoff_mcp``.

    Logging is configured eagerly via :func:`signoff.setup_logging` so
    the INFO lines emitted during :meth:`Harness.from_config_path`
    (e.g. "Using FakeHttpClient") land in stderr instead of being
    dropped. The per-transport methods call ``setup_logging`` again;
    it's idempotent.
    """
    setup_logging(stream=sys.stderr)
    server = await SignoffMCPServer.from_config_path(config_path)
    if transport == "stdio":
        await server.serve_stdio()
    elif transport == "http":
        await server.serve_http(host=host, port=port)
    else:
        raise ValueError(f"unknown transport {transport!r}; use 'stdio' or 'http'")
