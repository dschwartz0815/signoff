"""Real ``httpx``-backed HTTP client for Signoff verifiers.

Implements the :class:`~signoff.HttpClient` protocol with retry,
robots.txt compliance, optional response caching, and conservative
safety defaults (bounded response size, identifiable user agent,
TLS verification on). Drop-in replacement for
:class:`signoff.testing.FakeHttpClient` in production harness wiring.

Import path naming follows the repo convention (pip: ``signoff-http``,
module: ``signoff_http`` — same as ``signoff-mcp`` / ``signoff_mcp``)
rather than attempting to extend the ``signoff`` namespace.
"""

from __future__ import annotations

__version__ = "0.0.1"

from signoff_http.cache import ResponseCache
from signoff_http.client import HttpxClient
from signoff_http.config import HttpxClientConfig
from signoff_http.robots import RobotsChecker

__all__ = [
    "HttpxClient",
    "HttpxClientConfig",
    "ResponseCache",
    "RobotsChecker",
    "__version__",
]
