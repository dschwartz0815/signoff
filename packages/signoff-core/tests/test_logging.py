"""Tests for :func:`signoff.setup_logging`."""

from __future__ import annotations

import io
import logging

import pytest
from signoff import setup_logging


@pytest.fixture(autouse=True)
def _reset_signoff_logger() -> None:
    """Tear down any handlers our tests attach so each test starts
    from a clean ``signoff`` logger."""
    logger = logging.getLogger("signoff")
    saved_handlers = logger.handlers[:]
    saved_level = logger.level
    saved_propagate = logger.propagate
    logger.handlers = []
    logger.setLevel(logging.NOTSET)
    logger.propagate = True
    yield
    logger.handlers = saved_handlers
    logger.setLevel(saved_level)
    logger.propagate = saved_propagate


def test_default_does_not_configure_logging() -> None:
    """By default — without a setup_logging() call — the signoff logger
    has no handlers, so library users who haven't opted in get whatever
    their application configured."""
    logger = logging.getLogger("signoff")
    assert logger.handlers == []


def test_setup_logging_attaches_handler_only_to_signoff_namespace() -> None:
    before_root = list(logging.getLogger().handlers)
    setup_logging(stream=io.StringIO())
    logger = logging.getLogger("signoff")
    assert len(logger.handlers) == 1
    # Root logger untouched — embedded callers keep their config.
    assert logging.getLogger().handlers == before_root


def test_setup_logging_captures_records_from_child_loggers() -> None:
    buf = io.StringIO()
    setup_logging(level=logging.DEBUG, stream=buf)
    logging.getLogger("signoff.harness").info("hello from harness")
    logging.getLogger("signoff.mcp").debug("hello from mcp")
    output = buf.getvalue()
    assert "hello from harness" in output
    assert "hello from mcp" in output
    assert "signoff.harness" in output
    assert "signoff.mcp" in output


def test_setup_logging_is_idempotent() -> None:
    buf = io.StringIO()
    setup_logging(level=logging.INFO, stream=buf)
    setup_logging(level=logging.DEBUG, stream=buf)
    setup_logging(level=logging.DEBUG, stream=buf)
    logger = logging.getLogger("signoff")
    assert len(logger.handlers) == 1
    assert logger.level == logging.DEBUG


def test_setup_logging_redirects_stream_on_reconfigure() -> None:
    first = io.StringIO()
    second = io.StringIO()
    setup_logging(stream=first)
    logging.getLogger("signoff.harness").info("to first")
    setup_logging(stream=second)
    logging.getLogger("signoff.harness").info("to second")
    assert "to first" in first.getvalue()
    assert "to second" not in first.getvalue()
    assert "to second" in second.getvalue()


def test_setup_logging_accepts_string_level() -> None:
    setup_logging(level="WARNING", stream=io.StringIO())
    assert logging.getLogger("signoff").level == logging.WARNING


def test_setup_logging_disables_propagation() -> None:
    """After opt-in, records should not also propagate to root — that
    would duplicate output for callers who have their own root handler."""
    setup_logging(stream=io.StringIO())
    assert logging.getLogger("signoff").propagate is False
