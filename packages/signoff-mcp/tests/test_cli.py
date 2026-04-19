"""Tests for the signoff-mcp CLI (:mod:`signoff_mcp.__main__`)."""

from __future__ import annotations

import pytest

from signoff_mcp.__main__ import _build_parser, main


def test_parser_defaults() -> None:
    args = _build_parser().parse_args([])
    assert args.config == "signoff.yaml"
    assert args.transport == "stdio"
    assert args.host == "127.0.0.1"
    assert args.port == 8765
    assert args.health is False


def test_parser_rejects_unknown_transport() -> None:
    with pytest.raises(SystemExit):
        _build_parser().parse_args(["--transport", "grpc"])


def test_health_on_stdio_exits_one(capsys: pytest.CaptureFixture[str]) -> None:
    rc = main(["--health"])  # default transport is stdio
    assert rc == 1
    assert "requires --transport http" in capsys.readouterr().err


def test_health_probe_unreachable_port(capsys: pytest.CaptureFixture[str]) -> None:
    # Pick a port that's extremely unlikely to be bound. Exit code 1.
    rc = main(["--health", "--transport", "http", "--port", "1"])
    assert rc == 1
    assert "health probe failed" in capsys.readouterr().err
