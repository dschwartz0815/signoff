"""Tests for :mod:`signoff_mcp.config`."""

from __future__ import annotations

import logging

import pytest
from signoff_mcp.config import MCPServerConfig


def test_defaults_without_env() -> None:
    cfg = MCPServerConfig()
    assert cfg.log_level == "INFO"
    assert cfg.auth_token is None


def test_log_level_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SIGNOFF_MCP_LOG_LEVEL", "DEBUG")
    cfg = MCPServerConfig()
    assert cfg.log_level == "DEBUG"


def test_auth_token_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SIGNOFF_MCP_AUTH_TOKEN", "hunter2")
    cfg = MCPServerConfig()
    assert cfg.auth_token == "hunter2"


def test_log_level_rejects_unknown_value(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SIGNOFF_MCP_LOG_LEVEL", "trace")  # not a valid level
    with pytest.raises(Exception):  # pydantic ValidationError
        MCPServerConfig()


def test_sibling_namespace_not_consumed(monkeypatch: pytest.MonkeyPatch) -> None:
    """``SIGNOFF_CORE_*`` vars belong to signoff-core and must not
    leak into MCP settings (``extra="ignore"``)."""
    monkeypatch.setenv("SIGNOFF_CORE_BUDGET__MAX_COST_USD", "1.0")
    cfg = MCPServerConfig()
    assert "budget" not in cfg.model_dump()
    assert "max_cost_usd" not in cfg.model_dump()


def test_setup_logging_honors_mcp_log_level(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Setting ``SIGNOFF_MCP_LOG_LEVEL=DEBUG`` makes
    :func:`signoff.setup_logging` install a handler that lets DEBUG
    records through. This is the end-to-end wiring the server uses
    in ``serve_stdio`` / ``serve_http``.
    """
    import io

    from signoff import setup_logging

    monkeypatch.setenv("SIGNOFF_MCP_LOG_LEVEL", "DEBUG")
    cfg = MCPServerConfig()
    buf = io.StringIO()
    # Save/restore the signoff logger state so this test doesn't
    # leak handlers into sibling tests.
    signoff_logger = logging.getLogger("signoff")
    saved_handlers = signoff_logger.handlers[:]
    saved_level = signoff_logger.level
    saved_propagate = signoff_logger.propagate
    signoff_logger.handlers = []
    try:
        setup_logging(level=cfg.log_level, stream=buf)
        assert logging.getLogger("signoff").level == logging.DEBUG
        logging.getLogger("signoff.mcp").debug("debug visible")
        assert "debug visible" in buf.getvalue()
    finally:
        signoff_logger.handlers = saved_handlers
        signoff_logger.setLevel(saved_level)
        signoff_logger.propagate = saved_propagate
