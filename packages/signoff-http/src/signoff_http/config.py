"""``HttpxClientConfig`` — loaded from ``SIGNOFF_HTTP_*`` env vars.

The ``SIGNOFF_HTTP_`` namespace is reserved in
``docs/configuration.md`` for this package; nothing outside
``signoff-http`` consumes it. Nesting uses double underscores
(pydantic-settings default), though most fields here are flat.
"""

from __future__ import annotations

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

__all__ = ["SIGNOFF_USER_AGENT", "HttpxClientConfig"]


#: The canonical Signoff HTTP User-Agent. Sites that log user-agents
#: will start trusting this exact string — do not change it casually.
#: TODO(phase2): point the URL at a real bot-info page once
#: https://sign-off.dev exists.
SIGNOFF_USER_AGENT = "Signoff/0.0 (+https://sign-off.dev/bot)"


class HttpxClientConfig(BaseSettings):
    """Configuration for :class:`~signoff_http.HttpxClient`.

    Loaded from ``SIGNOFF_HTTP_*`` env vars by default; pass an
    explicit instance to :class:`HttpxClient.__init__` to override
    (tests construct directly rather than relying on env).
    """

    model_config = SettingsConfigDict(
        env_prefix="SIGNOFF_HTTP_",
        env_nested_delimiter="__",
        extra="ignore",
        case_sensitive=False,
    )

    # ------------------------------ Timeouts ----------------------------------

    connect_timeout: float = Field(default=5.0, gt=0.0)
    read_timeout: float = Field(default=15.0, gt=0.0)
    #: Upper bound on the full request (connect + read + retries). A
    #: caller's per-request timeout is clamped to this at the
    #: :meth:`HttpxClient.get` / ``.head`` boundary.
    total_timeout: float = Field(default=30.0, gt=0.0)

    # ------------------------------ Connection pooling ------------------------

    max_connections: int = Field(default=100, ge=1)
    max_keepalive_connections: int = Field(default=20, ge=0)
    keepalive_expiry: float = Field(default=30.0, ge=0.0)

    # ------------------------------ Retry policy ------------------------------

    #: Applies only to transient failures — see :mod:`signoff_http.retry`.
    max_retries: int = Field(default=2, ge=0)
    retry_backoff_base: float = Field(default=0.5, ge=0.0)
    retry_backoff_factor: float = Field(default=2.0, ge=1.0)
    retry_max_backoff: float = Field(default=10.0, ge=0.0)

    # ------------------------------ Request discipline ------------------------

    user_agent: str = SIGNOFF_USER_AGENT
    follow_redirects: bool = True
    max_redirects: int = Field(default=10, ge=0)

    # ------------------------------ Content limits ----------------------------

    #: Maximum response body size for a GET. Protects against memory
    #: bombs from servers returning multi-GB payloads.
    max_response_bytes: int = Field(default=10 * 1024 * 1024, ge=1)
    #: Smaller cap for HEAD-style metadata checks.
    max_response_bytes_head: int = Field(default=16 * 1024, ge=1)

    # ------------------------------ Safety posture ----------------------------

    respect_robots_txt: bool = True
    robots_txt_cache_seconds: int = Field(default=3600, ge=0)
    #: NEVER default to False. Setting this False at runtime logs a
    #: WARNING on :class:`HttpxClient` startup so the change is visible
    #: in operator logs.
    verify_tls: bool = True

    # ------------------------------ Optional response cache -------------------

    cache_enabled: bool = False
    cache_ttl_seconds: int = Field(default=300, ge=0)
    cache_max_entries: int = Field(default=1000, ge=1)
