"""Signoff core engine.

Phase 0 → Phase 1 bridge: the data models defined in
:mod:`signoff.models` per ``docs/protocol.md`` §3 are public; the
harness, runtime protocol, and plugin registry arrive in follow-up PRs.
"""

from __future__ import annotations

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

__version__ = "0.0.1"

__all__ = [
    "DELIVERABLE_CLAIM_ID",
    "ID_PATTERN",
    "RESERVED_CLAIM_KINDS",
    "VERIFIER_NAME_PATTERN",
    "BlockerEntry",
    "Claim",
    "Deliverable",
    "FeedbackPacket",
    "IdStr",
    "Iso8601",
    "ProtocolVersion",
    "Severity",
    "Verdict",
    "VerifierName",
    "VerifierResult",
    "WarningEntry",
    "__version__",
]
