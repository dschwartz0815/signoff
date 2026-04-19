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
from pathlib import Path
from typing import Any

import mcp.types as mcp_types
from mcp.server import NotificationOptions, Server
from mcp.server.sse import SseServerTransport
from mcp.server.stdio import stdio_server
from pydantic import ValidationError
from signoff import Claim, Deliverable, Harness, HarnessConfig

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
            raise _ToolError(get_verdict_message())
        raise _ToolError(f"Unknown tool: {name!r}")

    async def _handle_request_signoff(self, arguments: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(arguments, dict):
            raise _ToolError(
                f"request_signoff expects a JSON object; got {type(arguments).__name__}"
            )
        raw_deliverable = arguments.get("deliverable")
        if raw_deliverable is None:
            raise _ToolError("request_signoff: 'deliverable' is required (see protocol §7.3.1).")
        try:
            deliverable = Deliverable.model_validate(raw_deliverable)
            claims = [Claim.model_validate(c) for c in arguments.get("claims", [])]
        except ValidationError as exc:
            # Surface the first validation error clearly for the client.
            msg = _format_validation_error(exc)
            raise _ToolError(f"request_signoff: input validation failed: {msg}") from None

        config_override = arguments.get("config_override")
        retry_budget = arguments.get("retry_budget")
        if config_override is not None and not isinstance(config_override, dict):
            raise _ToolError("request_signoff: 'config_override' must be an object if provided.")
        if retry_budget is not None and (not isinstance(retry_budget, int) or retry_budget < 0):
            raise _ToolError("request_signoff: 'retry_budget' must be a non-negative integer.")

        try:
            verdict = await self.harness.verify(
                deliverable,
                claims,
                config_override=config_override,
                retry_budget=retry_budget,
            )
        except Exception as exc:
            _logger.exception("request_signoff: harness.verify failed")
            raise _ToolError(f"verification failed: {type(exc).__name__}: {exc}") from None

        return json.loads(verdict.model_dump_json())  # type: ignore[no-any-return]

    async def _handle_list_verifiers(self) -> dict[str, Any]:
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
        return {
            "protocol_version": _PROTOCOL_VERSION,
            "verifiers": entries,
        }

    # ------------------------------------------------------------------
    # Transports
    # ------------------------------------------------------------------

    async def serve_stdio(self) -> None:
        """Run the server over stdio. Configured logger on stderr — stdout
        is reserved for MCP protocol messages."""
        _configure_stderr_logging()
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
        an optional Bearer-token auth check."""
        import uvicorn

        app = self.build_app()
        starlette_app = _build_http_app(self, app)
        config = uvicorn.Config(
            starlette_app,
            host=host,
            port=port,
            log_level="info",
            access_log=False,
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


def _format_validation_error(exc: ValidationError) -> str:
    errors = exc.errors()
    if not errors:
        return str(exc)
    first = errors[0]
    loc = ".".join(str(p) for p in first.get("loc", ()))
    msg = first.get("msg", "")
    return f"{loc}: {msg}" if loc else msg


def _configure_stderr_logging() -> None:
    """Route logging to stderr. stdio transport requires stdout to carry
    only protocol messages."""
    root = logging.getLogger()
    if any(isinstance(h, logging.StreamHandler) and h.stream is sys.stderr for h in root.handlers):
        return
    handler = logging.StreamHandler(stream=sys.stderr)
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    root.addHandler(handler)
    if root.level == logging.NOTSET:
        root.setLevel(logging.INFO)


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
    """Start a Signoff MCP server. Used by ``python -m signoff_mcp``."""
    server = await SignoffMCPServer.from_config_path(config_path)
    if transport == "stdio":
        await server.serve_stdio()
    elif transport == "http":
        await server.serve_http(host=host, port=port)
    else:
        raise ValueError(f"unknown transport {transport!r}; use 'stdio' or 'http'")
