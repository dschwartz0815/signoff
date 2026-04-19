"""Signoff core engine.

Public API:

- Data models (see :mod:`signoff.models` / ``docs/protocol.md`` §3).
- :class:`VerifierContext` and its supporting result types
  (:mod:`signoff.context`).
- The :class:`Runtime` protocol, :class:`RuntimePolicy`,
  :class:`VerifierMeta`, and :class:`LocalRuntime`
  (:mod:`signoff.runtime`).

The harness orchestrator, ``@verifier`` decorator, plugin registry,
and config loader arrive in follow-up PRs.
"""

from __future__ import annotations

from signoff.context import (
    ExecResult,
    FetchResult,
    HttpClient,
    JudgeClient,
    JudgeResult,
    VerifierContext,
    make_context,
)
from signoff.models import (
    DELIVERABLE_CLAIM_ID,
    ID_PATTERN,
    RESERVED_CLAIM_KINDS,
    VERIFIER_NAME_PATTERN,
    BlockerEntry,
    Claim,
    Deliverable,
    FeedbackPacket,
    IdStr,
    Iso8601,
    ProtocolVersion,
    Severity,
    Verdict,
    VerifierName,
    VerifierResult,
    WarningEntry,
)
from signoff.runtime import (
    LocalRuntime,
    Runtime,
    SignoffRuntimeError,
    RuntimePolicy,
    VerifierMeta,
)

__version__ = "0.0.1"

__all__ = [
    "DELIVERABLE_CLAIM_ID",
    "ID_PATTERN",
    "RESERVED_CLAIM_KINDS",
    "VERIFIER_NAME_PATTERN",
    "BlockerEntry",
    "Claim",
    "Deliverable",
    "ExecResult",
    "FeedbackPacket",
    "FetchResult",
    "HttpClient",
    "IdStr",
    "Iso8601",
    "JudgeClient",
    "JudgeResult",
    "LocalRuntime",
    "ProtocolVersion",
    "Runtime",
    "SignoffRuntimeError",
    "RuntimePolicy",
    "Severity",
    "Verdict",
    "VerifierContext",
    "VerifierMeta",
    "VerifierName",
    "VerifierResult",
    "WarningEntry",
    "__version__",
    "make_context",
]
