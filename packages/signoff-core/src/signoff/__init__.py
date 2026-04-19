"""Signoff core engine.

Public API:

- Data models (see :mod:`signoff.models` / ``docs/protocol.md`` §3).
- :class:`VerifierContext` and supporting result types
  (:mod:`signoff.context`).
- The :class:`Runtime` protocol, :class:`RuntimePolicy`,
  :class:`VerifierMeta`, and :class:`LocalRuntime`
  (:mod:`signoff.runtime`).
- The :func:`verifier` decorator and the :class:`Registry`
  (:mod:`signoff.verifier`, :mod:`signoff.registry`).

The harness orchestrator and YAML config loader arrive in follow-up
PRs.
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
from signoff.registry import ENTRY_POINT_GROUP, Registry, default_registry
from signoff.runtime import (
    LocalRuntime,
    Runtime,
    RuntimePolicy,
    SignoffRuntimeError,
    VerifierMeta,
)
from signoff.verifier import RegisteredVerifier, verifier

__version__ = "0.0.1"

__all__ = [
    "DELIVERABLE_CLAIM_ID",
    "ENTRY_POINT_GROUP",
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
    "RegisteredVerifier",
    "Registry",
    "Runtime",
    "RuntimePolicy",
    "Severity",
    "SignoffRuntimeError",
    "Verdict",
    "VerifierContext",
    "VerifierMeta",
    "VerifierName",
    "VerifierResult",
    "WarningEntry",
    "__version__",
    "default_registry",
    "make_context",
    "verifier",
]
