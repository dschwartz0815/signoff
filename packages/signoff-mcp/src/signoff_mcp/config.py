"""MCP-server-specific settings.

Settings live in the ``SIGNOFF_MCP_*`` env namespace, parallel to the
``SIGNOFF_CORE_*`` harness namespace owned by :mod:`signoff.config`.
Each package owns its own namespace so the three concerns can evolve
independently without the PR 4 collision
(``SIGNOFF_LOG_LEVEL`` tripping ``HarnessConfig(extra="forbid")``)
recurring.
"""

from __future__ import annotations

from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

__all__ = ["MCPServerConfig"]


class MCPServerConfig(BaseSettings):
    """Settings loaded from ``SIGNOFF_MCP_*`` env vars.

    All fields have safe defaults so the server runs cleanly without
    any env configuration. Defined as :class:`BaseSettings` so
    pydantic-settings handles env resolution, type coercion, and
    validation. Instantiate directly — :class:`SignoffMCPServer`
    builds one on construction.
    """

    model_config = SettingsConfigDict(
        env_prefix="SIGNOFF_MCP_",
        extra="ignore",
        case_sensitive=False,
    )

    #: Log level for the ``signoff`` namespace handler installed by
    #: :func:`signoff.setup_logging`. Accepts ``"DEBUG"``, ``"INFO"``,
    #: ``"WARNING"``, ``"ERROR"``, or ``"CRITICAL"``.
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] = Field(
        default="INFO",
        description="Log level for the signoff/uvicorn/mcp logger tree.",
    )

    #: Optional Bearer token. When set, every non-``/health`` and
    #: non-``/version`` HTTP request must carry
    #: ``Authorization: Bearer <token>`` or receive 401.
    auth_token: str | None = Field(
        default=None,
        description=(
            "Bearer token required on protected HTTP endpoints. "
            "Unset (default) = no auth enforcement."
        ),
    )
