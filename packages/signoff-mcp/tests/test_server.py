"""Tests for :mod:`signoff_mcp.server`."""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

import pytest
from signoff import (
    Claim,
    Harness,
    LocalRuntime,
    Registry,
    VerifierContext,
    VerifierResult,
    load_config,
)
from signoff.testing import FakeHttpClient, FakeJudge
from signoff.verifier import _testing_pack, verifier
from signoff_mcp._tools import get_verdict_message
from signoff_mcp.server import SignoffMCPServer, _ToolError

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _make_pass_verifier() -> Any:
    with _testing_pack("signoff-research"):

        @verifier(name="smoke", claim_kinds=["citation"], cost_tier="cheap")
        async def smoke(_c: Claim, ctx: VerifierContext) -> VerifierResult:
            return ctx.ok(evidence={"checked": True})

    return smoke


def _make_blocker_verifier() -> Any:
    with _testing_pack("signoff-research"):

        @verifier(name="strict", claim_kinds=["citation"], cost_tier="cheap")
        async def strict(_c: Claim, ctx: VerifierContext) -> VerifierResult:
            return ctx.fail(reason="forced", suggestion="fix")

    return strict


def _empty_harness(registry: Registry | None = None) -> Harness:
    reg = registry if registry is not None else Registry()
    cfg = load_config(
        path=None,
        pack_defaults=False,
        env_overrides=False,
        request_overrides={
            "packs": sorted({m.pack for m in reg.list_all()}),
            "deliverables": {
                "research_report": {
                    "verifiers": {m.fully_qualified_name: {"enabled": True} for m in reg.list_all()}
                }
            },
        },
    )
    return Harness(
        config=cfg,
        registry=reg,
        runtimes=[LocalRuntime()],
        http=FakeHttpClient(),
        judge=FakeJudge(),
    )


# ---------------------------------------------------------------------------
# build_app()
# ---------------------------------------------------------------------------


def test_build_app_registers_three_tools() -> None:
    server = SignoffMCPServer(_empty_harness())
    app = server.build_app()
    import mcp.types as mcp_types

    assert mcp_types.ListToolsRequest in app.request_handlers
    assert mcp_types.CallToolRequest in app.request_handlers


@pytest.mark.asyncio
async def test_list_tools_returns_three_tools() -> None:
    server = SignoffMCPServer(_empty_harness())
    app = server.build_app()
    import mcp.types as mcp_types

    handler = app.request_handlers[mcp_types.ListToolsRequest]
    result = await handler(mcp_types.ListToolsRequest(method="tools/list"))
    tools = result.root.tools
    names = {t.name for t in tools}
    assert names == {"request_signoff", "list_verifiers", "get_verdict"}


# ---------------------------------------------------------------------------
# request_signoff
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_request_signoff_valid_input_returns_verdict() -> None:
    r = Registry()
    r.register(_make_pass_verifier())
    server = SignoffMCPServer(_empty_harness(r))

    result = await server._handle_request_signoff(
        {
            "deliverable": {"id": "dlv_1", "kind": "research_report", "content": None},
            "claims": [
                {
                    "id": "clm_1",
                    "text": "A claim.",
                    "kind": "citation",
                    "evidence": {"url": "https://x"},
                },
            ],
        }
    )
    assert result["passed"] is True
    assert result["deliverable_id"] == "dlv_1"
    assert result["protocol_version"] == "0.1"


@pytest.mark.asyncio
async def test_request_signoff_malformed_deliverable_raises_tool_error() -> None:
    server = SignoffMCPServer(_empty_harness())
    with pytest.raises(_ToolError, match="validation failed"):
        await server._handle_request_signoff(
            {"deliverable": {"id": "_bad", "kind": "k", "content": None}}
        )


@pytest.mark.asyncio
async def test_request_signoff_missing_deliverable_raises() -> None:
    server = SignoffMCPServer(_empty_harness())
    with pytest.raises(_ToolError, match="deliverable"):
        await server._handle_request_signoff({})


@pytest.mark.asyncio
async def test_request_signoff_harness_error_becomes_tool_error(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    server = SignoffMCPServer(_empty_harness())

    async def boom(*args: Any, **kwargs: Any) -> Any:
        raise RuntimeError("internal /secret/path/leaked")

    monkeypatch.setattr(server.harness, "verify", boom)

    with caplog.at_level(logging.ERROR, logger="signoff.mcp"):
        with pytest.raises(_ToolError) as exc_info:
            await server._handle_request_signoff(
                {"deliverable": {"id": "dlv_1", "kind": "k", "content": None}}
            )
    # The public message names the class + the exception text (short),
    # but the traceback (which might contain internal paths) is only in
    # the log.
    assert "RuntimeError" in str(exc_info.value)
    # Traceback logged at ERROR level server-side.
    assert any("harness.verify raised" in rec.getMessage() for rec in caplog.records)


@pytest.mark.asyncio
async def test_request_signoff_passes_through_retry_budget() -> None:
    r = Registry()
    r.register(_make_blocker_verifier())
    server = SignoffMCPServer(_empty_harness(r))

    result = await server._handle_request_signoff(
        {
            "deliverable": {"id": "dlv_1", "kind": "research_report", "content": None},
            "claims": [
                {"id": "clm_1", "text": "x", "kind": "citation", "evidence": {}},
            ],
            "retry_budget": 3,
        }
    )
    assert result["feedback_packet"]["retry_budget_remaining"] == 2


@pytest.mark.asyncio
async def test_request_signoff_rejects_non_integer_retry_budget() -> None:
    server = SignoffMCPServer(_empty_harness())
    with pytest.raises(_ToolError, match="retry_budget"):
        await server._handle_request_signoff(
            {
                "deliverable": {"id": "dlv_1", "kind": "k", "content": None},
                "retry_budget": -1,
            }
        )


# ---------------------------------------------------------------------------
# list_verifiers
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_verifiers_empty_registry() -> None:
    server = SignoffMCPServer(_empty_harness())
    result = await server._handle_list_verifiers()
    assert result == {"protocol_version": "0.1", "verifiers": []}


@pytest.mark.asyncio
async def test_list_verifiers_populated_registry() -> None:
    r = Registry()
    r.register(_make_pass_verifier())
    server = SignoffMCPServer(_empty_harness(r))
    result = await server._handle_list_verifiers()
    assert len(result["verifiers"]) == 1
    entry = result["verifiers"][0]
    assert entry == {
        "name": "smoke",
        "pack": "signoff-research",
        "claim_kinds": ["citation"],
        "cost_tier": "cheap",
        "version": None,
        "enabled": True,
    }


@pytest.mark.asyncio
async def test_list_verifiers_reflects_disabled_in_config() -> None:
    r = Registry()
    r.register(_make_pass_verifier())
    cfg = load_config(
        path=None,
        pack_defaults=False,
        env_overrides=False,
        request_overrides={
            "packs": ["signoff-research"],
            "deliverables": {
                "research_report": {"verifiers": {"signoff-research.smoke": {"enabled": False}}}
            },
        },
    )
    harness = Harness(
        config=cfg,
        registry=r,
        runtimes=[LocalRuntime()],
        http=FakeHttpClient(),
        judge=FakeJudge(),
    )
    server = SignoffMCPServer(harness)
    result = await server._handle_list_verifiers()
    assert result["verifiers"][0]["enabled"] is False


# ---------------------------------------------------------------------------
# get_verdict
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_verdict_always_errors() -> None:
    server = SignoffMCPServer(_empty_harness())
    with pytest.raises(_ToolError) as exc_info:
        await server._dispatch_tool("get_verdict", {"verdict_id": "vrd_x"})
    assert "hosted Signoff service" in str(exc_info.value)
    assert str(exc_info.value) == get_verdict_message()


# ---------------------------------------------------------------------------
# Dispatch + content shape
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_dispatch_unknown_tool() -> None:
    server = SignoffMCPServer(_empty_harness())
    with pytest.raises(_ToolError, match="Unknown tool"):
        await server._dispatch_tool("not_a_tool", {})


@pytest.mark.asyncio
async def test_dispatch_returns_text_content_wrapper() -> None:
    server = SignoffMCPServer(_empty_harness())
    content = await server._dispatch_tool("list_verifiers", {})
    assert len(content) == 1
    block = content[0]
    assert block.type == "text"
    payload = json.loads(block.text)
    assert payload["protocol_version"] == "0.1"


# ---------------------------------------------------------------------------
# HTTP transport — /health, /version, auth
# ---------------------------------------------------------------------------


@pytest.fixture
def http_app() -> Any:
    r = Registry()
    r.register(_make_pass_verifier())
    server = SignoffMCPServer(_empty_harness(r))
    from signoff_mcp.server import _build_http_app

    return _build_http_app(server, server.build_app())


def test_health_endpoint_returns_ready(http_app: Any) -> None:
    from starlette.testclient import TestClient

    client = TestClient(http_app)
    resp = client.get("/health")
    assert resp.status_code == 200
    payload = resp.json()
    assert payload == {"status": "ok", "harness": "ready", "verifier_count": 1}


def test_version_endpoint_returns_versions(http_app: Any) -> None:
    from starlette.testclient import TestClient

    client = TestClient(http_app)
    resp = client.get("/version")
    assert resp.status_code == 200
    payload = resp.json()
    assert payload["protocol_version"] == "0.1"
    assert "harness_version" in payload
    assert "mcp_server_version" in payload


def test_auth_middleware_allows_unauth_paths_always(
    http_app: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("SIGNOFF_MCP_AUTH_TOKEN", "hunter2")
    from starlette.testclient import TestClient

    client = TestClient(http_app)
    assert client.get("/health").status_code == 200
    assert client.get("/version").status_code == 200


def test_auth_middleware_rejects_missing_token_on_protected_path(
    http_app: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("SIGNOFF_MCP_AUTH_TOKEN", "hunter2")
    from starlette.testclient import TestClient

    client = TestClient(http_app)
    resp = client.get("/sse")
    assert resp.status_code == 401


def test_auth_middleware_accepts_matching_bearer(
    http_app: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Bearer-token match lets the request through to the handler.

    We exercise this via /health with an intentionally required token,
    since /sse is a long-lived stream. The auth middleware runs for
    every path; /health is only whitelisted when there's no token
    configured (the middleware skips auth checks entirely in that
    case). So with the token required and no header, /sse returns 401;
    with the correct header, any non-stream endpoint resolves cleanly.
    """
    monkeypatch.setenv("SIGNOFF_MCP_AUTH_TOKEN", "hunter2")
    from starlette.testclient import TestClient

    client = TestClient(http_app)
    # Rejected without header:
    assert client.post("/messages/?session_id=x").status_code == 401
    # Accepted with header (handler returns 400 because no session —
    # that's fine, we only assert auth didn't short-circuit to 401):
    resp = client.post(
        "/messages/?session_id=x",
        headers={"Authorization": "Bearer hunter2"},
    )
    assert resp.status_code != 401


# ---------------------------------------------------------------------------
# from_config_path
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_from_config_path_builds_server(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from signoff.registry import default_registry

    default_registry.clear()
    monkeypatch.setattr(
        "signoff.registry.Registry.discovered",
        classmethod(lambda cls: default_registry),
    )

    cfg_path = tmp_path / "signoff.yaml"
    cfg_path.write_text('protocol_version: "0.1"\npacks: []\ndeliverables: {}\n')
    server = await SignoffMCPServer.from_config_path(cfg_path)
    assert isinstance(server, SignoffMCPServer)
