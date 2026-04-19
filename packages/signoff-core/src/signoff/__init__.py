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

__version__ = "0.0.1"

from signoff.config import (
    PACK_DEFAULTS_ENTRY_POINT_GROUP,
    BudgetConfig,
    ConfigurationError,
    DeliverableConfig,
    HarnessConfig,
    JudgeConfig,
    RetryConfig,
    RuntimeConfig,
    RuntimePolicyConfig,
    VerifierConfig,
    deep_merge,
    load_config,
    validate_config,
)
from signoff.context import (
    ExecResult,
    FetchResult,
    HttpClient,
    JudgeClient,
    JudgeResult,
    VerifierContext,
    make_context,
)
from signoff.harness import Harness
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

__all__ = [
    "DELIVERABLE_CLAIM_ID",
    "ENTRY_POINT_GROUP",
    "ID_PATTERN",
    "PACK_DEFAULTS_ENTRY_POINT_GROUP",
    "RESERVED_CLAIM_KINDS",
    "VERIFIER_NAME_PATTERN",
    "BlockerEntry",
    "BudgetConfig",
    "Claim",
    "ConfigurationError",
    "Deliverable",
    "DeliverableConfig",
    "ExecResult",
    "FeedbackPacket",
    "FetchResult",
    "Harness",
    "HarnessConfig",
    "HttpClient",
    "IdStr",
    "Iso8601",
    "JudgeClient",
    "JudgeConfig",
    "JudgeResult",
    "LocalRuntime",
    "ProtocolVersion",
    "RegisteredVerifier",
    "Registry",
    "RetryConfig",
    "Runtime",
    "RuntimeConfig",
    "RuntimePolicy",
    "RuntimePolicyConfig",
    "Severity",
    "SignoffRuntimeError",
    "Verdict",
    "VerifierConfig",
    "VerifierContext",
    "VerifierMeta",
    "VerifierName",
    "VerifierResult",
    "WarningEntry",
    "__version__",
    "deep_merge",
    "default_registry",
    "load_config",
    "make_context",
    "validate_config",
    "verifier",
]
