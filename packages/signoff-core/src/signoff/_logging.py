"""Opt-in logging setup for the Signoff package family.

Signoff's modules all log through the ``signoff`` logger namespace
(``signoff.harness``, ``signoff.config``, ``signoff.runtime.local``,
``signoff.registry``, ``signoff.context``, ``signoff.mcp``, …) — but
by default no handlers are attached. Python's root logger also has no
handler by default, so every ``logger.info(...)`` call silently
evaporates unless *somebody* configures handlers.

For embedded-library callers, that default is correct: they probably
have their own logging config and Signoff should not overwrite it.
For entry-point-style callers (the MCP server, a CLI, a script), the
default is a bug waiting to happen — the audit/debug story documented
in ``docs/harness.md`` and ``docs/mcp-integration.md`` doesn't actually
fire.

:func:`setup_logging` resolves this without surprising embedded users:
it touches only the ``signoff`` logger (not root), and it is idempotent
so a server that calls it on every restart doesn't stack handlers.
"""

from __future__ import annotations

import logging
import sys
from typing import IO

__all__ = ["setup_logging"]


_LOGGER_NAMESPACE = "signoff"
_SENTINEL = "_signoff_setup_logging_done"


def setup_logging(
    level: int | str = logging.INFO,
    stream: IO[str] | None = None,
) -> logging.Logger:
    """Attach a stream handler to the ``signoff`` logger.

    Args:
        level: Log level threshold for the ``signoff`` namespace.
            Passed through to :meth:`logging.Logger.setLevel` so both
            numeric levels and name strings (``"DEBUG"``) work.
        stream: Destination stream. ``None`` means :data:`sys.stderr`,
            which is the right default for any entry-point caller —
            stdio-transport MCP servers must keep stdout clean for the
            protocol.

    The call only touches the ``signoff`` logger — root stays as the
    caller left it. If :func:`setup_logging` is invoked more than once,
    subsequent calls update the level + stream on the existing handler
    rather than adding duplicates.

    Embedded-library callers who want Signoff logs to flow into their
    own root-logger configuration should NOT call this — the default
    propagation behaviour carries records up the logger tree.
    """
    logger = logging.getLogger(_LOGGER_NAMESPACE)
    logger.setLevel(level)

    destination = stream if stream is not None else sys.stderr
    existing_handler = _existing_signoff_handler(logger)
    if existing_handler is not None:
        existing_handler.setStream(destination)  # type: ignore[attr-defined]
        existing_handler.setLevel(level)
        return logger

    handler = logging.StreamHandler(stream=destination)
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    handler.setLevel(level)
    setattr(handler, _SENTINEL, True)
    logger.addHandler(handler)
    # Ensure records don't ALSO propagate to root (which would duplicate
    # output when an embedded caller configured root separately). The
    # caller opted in to Signoff's handler; that's the one they get.
    logger.propagate = False
    return logger


def _existing_signoff_handler(logger: logging.Logger) -> logging.Handler | None:
    for handler in logger.handlers:
        if getattr(handler, _SENTINEL, False):
            return handler
    return None
