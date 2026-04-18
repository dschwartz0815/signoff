"""Signoff core data models.

Every type here implements a section of ``docs/protocol.md`` §3. The
protocol document is authoritative — when code disagrees with it, the
doc wins and the code is a bug.

The module exports:

- :class:`Severity` (§3.4)
- :class:`Deliverable` (§3.2)
- :class:`Claim` (§3.3)
- :class:`VerifierResult` (§3.5)
- :class:`Verdict` (§3.6)
- :class:`FeedbackPacket`, :class:`BlockerEntry`, :class:`WarningEntry` (§3.7)

plus constants :data:`RESERVED_CLAIM_KINDS` and
:data:`DELIVERABLE_CLAIM_ID`.
"""

from __future__ import annotations

import re
from datetime import datetime
from enum import StrEnum
from typing import Annotated, Any, Final, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    field_validator,
    model_validator,
)

# ---------------------------------------------------------------------------
# Constants and shared type aliases
# ---------------------------------------------------------------------------

#: §3.1 — identifier regex every ``id`` field on the wire must match.
ID_PATTERN: Final[str] = r"^[a-zA-Z0-9][a-zA-Z0-9_\-]{0,127}$"

#: §7.2 — fully-qualified verifier name ``<pack>.<name>`` (lowercase).
VERIFIER_NAME_PATTERN: Final[str] = r"^[a-z0-9_\-]+\.[a-z0-9_]+$"

#: §3.3.1 — claim kinds reserved by the protocol. Pack authors namespace
#: their own kinds as ``<pack_name>.<kind>`` and MUST NOT redefine these.
RESERVED_CLAIM_KINDS: Final[frozenset[str]] = frozenset(
    {
        "citation",
        "quantitative",
        "quote",
        "policy",
        "computational",
        "personalization",
    }
)

#: §4.3 — synthetic claim id for whole-deliverable verifiers. This value
#: is harness-internal; it never appears on the wire because
#: :attr:`VerifierResult.claim_id` is ``None`` for whole-deliverable
#: results (§3.5). Kept as a module-level constant so callers that build
#: the synthetic claim avoid typos.
DELIVERABLE_CLAIM_ID: Final[str] = "__deliverable__"

#: Semver-ish string accepted on the wire. Major-only, major.minor, and
#: full semver all pass; §1.4 requires only that major matches, so we
#: accept the broader form and let the harness version-check.
_PROTOCOL_VERSION_PATTERN: Final[str] = r"^\d+(\.\d+){1,2}$"


#: §3.1 constrained identifier string.
IdStr = Annotated[str, StringConstraints(pattern=ID_PATTERN)]

#: §7.2 constrained verifier-name string.
VerifierName = Annotated[str, StringConstraints(pattern=VERIFIER_NAME_PATTERN)]

#: §7.1 — ISO-8601 timestamp carried as a string on the wire so the
#: format survives round-trips untouched. Parseability is verified by
#: the ``_validate_iso8601`` validator wherever this alias is used.
Iso8601 = Annotated[str, Field(description="ISO-8601 timestamp, UTC 'Z' suffix preferred.")]


def _validate_iso8601(value: str | None) -> str | None:
    """Accept ``None`` passthrough; reject strings that do not parse as ISO-8601."""
    if value is None:
        return value
    candidate = value[:-1] + "+00:00" if value.endswith("Z") else value
    try:
        datetime.fromisoformat(candidate)
    except ValueError as exc:  # pragma: no cover - simple re-raise
        raise ValueError(f"not a valid ISO-8601 timestamp: {value!r}") from exc
    return value


# ---------------------------------------------------------------------------
# Enum (§3.4)
# ---------------------------------------------------------------------------


class Severity(StrEnum):
    """Implements ``docs/protocol.md`` §3.4 Severity.

    Serialized as a lowercase string on the wire per §7.1.
    """

    BLOCKER = "blocker"
    WARNING = "warning"
    INFO = "info"


# ---------------------------------------------------------------------------
# Shared model config
# ---------------------------------------------------------------------------


class _ProtocolModel(BaseModel):
    """Base for every wire-format model.

    ``extra='ignore'`` follows Postel's law: tolerate unknown fields so
    forward-compatible producers don't break older consumers. Types are
    validated strictly.
    """

    model_config = ConfigDict(
        extra="ignore",
        str_strip_whitespace=False,
        populate_by_name=True,
        use_enum_values=True,
        frozen=False,
    )


# ---------------------------------------------------------------------------
# Deliverable (§3.2)
# ---------------------------------------------------------------------------


class Deliverable(_ProtocolModel):
    """Implements ``docs/protocol.md`` §3.2 Deliverable."""

    id: IdStr = Field(description="Unique identifier (§3.1).")
    kind: str = Field(min_length=1, description='Deliverable kind (e.g., "research_report").')
    content: Any = Field(description="JSON-serializable payload. Shape is determined by kind.")
    metadata: dict[str, Any] = Field(
        default_factory=dict,
        description="Free-form metadata. Conventional keys listed in §3.2.",
    )
    created_at: Iso8601 | None = Field(
        default=None,
        description="When the agent produced the deliverable (ISO-8601).",
    )

    @field_validator("created_at")
    @classmethod
    def _check_created_at(cls, v: str | None) -> str | None:
        return _validate_iso8601(v)


# ---------------------------------------------------------------------------
# Claim (§3.3)
# ---------------------------------------------------------------------------


_PROVENANCE_VALUES: Final[frozenset[str]] = frozenset(
    {"agent_asserted", "extractor", "user_supplied"}
)


class Claim(_ProtocolModel):
    """Implements ``docs/protocol.md`` §3.3 Claim.

    Per §3.3.1, ``kind`` MUST be either a reserved kind or a
    pack-namespaced kind ``<pack_name>.<kind>``. Unscoped kinds not in
    :data:`RESERVED_CLAIM_KINDS` are rejected because the protocol
    reserves them for future versions.
    """

    id: IdStr = Field(description="Unique identifier (§3.1).")
    text: str = Field(description="Natural-language statement of the claim.")
    kind: str = Field(min_length=1, description="Claim kind; see §3.3.1.")
    evidence: dict[str, Any] = Field(
        default_factory=dict,
        description="References used by verifiers (URLs, source refs, computations).",
    )
    span: tuple[int, int] | None = Field(
        default=None,
        description="Character offsets [start, end] into the deliverable content.",
    )
    provenance: str | None = Field(
        default=None,
        description='Extraction provenance: "agent_asserted", "extractor", "user_supplied".',
    )

    @field_validator("kind")
    @classmethod
    def _check_kind(cls, v: str) -> str:
        if v in RESERVED_CLAIM_KINDS:
            return v
        if "." in v:
            pack, _, local = v.partition(".")
            if (
                pack
                and local
                and re.fullmatch(r"[a-z0-9_\-]+", pack)
                and re.fullmatch(r"[a-z0-9_]+", local)
            ):
                return v
            raise ValueError(
                f"pack-namespaced claim kind must match <pack>.<name> in lowercase; got {v!r}"
            )
        raise ValueError(
            f"{v!r} is not a reserved claim kind (§3.3.1) and lacks a pack namespace. "
            f"Use one of {sorted(RESERVED_CLAIM_KINDS)} or namespace as <pack>.<kind>."
        )

    @field_validator("provenance")
    @classmethod
    def _check_provenance(cls, v: str | None) -> str | None:
        if v is None or v in _PROVENANCE_VALUES:
            return v
        raise ValueError(
            f"provenance must be one of {sorted(_PROVENANCE_VALUES)} or null; got {v!r}"
        )

    @field_validator("span")
    @classmethod
    def _check_span(cls, v: tuple[int, int] | None) -> tuple[int, int] | None:
        if v is None:
            return v
        start, end = v
        if start < 0 or end < 0:
            raise ValueError(f"span offsets must be non-negative; got {v!r}")
        if end < start:
            raise ValueError(f"span end must be >= start; got {v!r}")
        return v


# ---------------------------------------------------------------------------
# VerifierResult (§3.5)
# ---------------------------------------------------------------------------


class VerifierResult(_ProtocolModel):
    """Implements ``docs/protocol.md`` §3.5 VerifierResult.

    Invariants enforced (§3.5):

    - ``passed is False`` and ``severity is BLOCKER`` require non-null ``suggestion``.
    - ``cost_usd >= 0`` and ``duration_ms >= 0``.

    The "``passed=true`` implies severity=info OR non-empty evidence"
    invariant is enforced as a soft requirement: passing results must
    either declare info severity or carry evidence documenting what was
    checked.
    """

    verifier: VerifierName = Field(description="Fully-qualified verifier name (§4.1).")
    claim_id: IdStr | None = Field(
        description="Target claim id; null for whole-deliverable verifiers."
    )
    passed: bool = Field(description="Whether the check passed.")
    severity: Severity = Field(description="Severity of this result (§3.4).")
    reason: str = Field(min_length=1, description="Human-readable explanation.")
    suggestion: str | None = Field(
        default=None,
        description="Actionable repair hint. Required when passed=false and severity=blocker.",
    )
    evidence: dict[str, Any] = Field(
        default_factory=dict,
        description="Data the verifier observed or produced.",
    )
    cost_usd: float = Field(ge=0.0, description="Estimated USD cost; negative is invalid.")
    duration_ms: int = Field(ge=0, description="Wall-clock duration in milliseconds.")
    verifier_version: str | None = Field(
        default=None,
        description="Version of the verifier implementation.",
    )
    started_at: Iso8601 | None = Field(
        default=None,
        description="When the verifier began (ISO-8601).",
    )

    @field_validator("started_at")
    @classmethod
    def _check_started_at(cls, v: str | None) -> str | None:
        return _validate_iso8601(v)

    @model_validator(mode="after")
    def _check_invariants(self) -> VerifierResult:
        if not self.passed and self.severity == Severity.BLOCKER and self.suggestion is None:
            raise ValueError(
                "§3.5 invariant: passed=false and severity=blocker requires a non-null suggestion."
            )
        if self.passed and self.severity != Severity.INFO and not self.evidence:
            raise ValueError(
                "§3.5 invariant: passed=true with non-info severity must document the check "
                "via non-empty evidence."
            )
        return self


# ---------------------------------------------------------------------------
# Feedback packet entries (§3.7)
# ---------------------------------------------------------------------------


class _PacketEntry(_ProtocolModel):
    """Shared shape for BlockerEntry and WarningEntry (§3.7)."""

    claim_id: IdStr | None = Field(description="Target claim id; null for whole-deliverable.")
    claim_text: str | None = Field(
        default=None,
        description="Echo of the claim text to aid retry without re-fetch.",
    )
    verifier: VerifierName = Field(description="Which verifier produced the entry.")
    issue: str = Field(min_length=1, description="reason from the VerifierResult.")
    suggested_repair: str = Field(
        min_length=1,
        description="suggestion from the VerifierResult (non-null by §3.5 invariant).",
    )
    evidence_excerpt: str | None = Field(
        default=None,
        description="Short, agent-relevant excerpt from evidence.",
    )


class BlockerEntry(_PacketEntry):
    """Implements ``docs/protocol.md`` §3.7 BlockerEntry."""


class WarningEntry(_PacketEntry):
    """Implements ``docs/protocol.md`` §3.7 WarningEntry."""


# ---------------------------------------------------------------------------
# FeedbackPacket (§3.7)
# ---------------------------------------------------------------------------


ProtocolVersion = Annotated[str, StringConstraints(pattern=_PROTOCOL_VERSION_PATTERN)]


class FeedbackPacket(_ProtocolModel):
    """Implements ``docs/protocol.md`` §3.7 FeedbackPacket.

    ``passed`` is always ``False`` — the packet exists only for failed
    verdicts. Present as a structural convenience so consumers can
    switch on a single field.
    """

    passed: Literal[False] = Field(
        default=False,
        description="Always false; feedback packets are emitted only for failed verdicts.",
    )
    blockers: list[BlockerEntry] = Field(
        default_factory=list,
        description="Entries for results with passed=false and severity=blocker.",
    )
    warnings: list[WarningEntry] = Field(
        default_factory=list,
        description="Entries for results with passed=false and severity=warning.",
    )
    cost_usd: float = Field(ge=0.0, description="Total cost of the run.")
    retry_budget_remaining: int | None = Field(
        default=None,
        ge=0,
        description="Remaining retry count if caller set a retry budget.",
    )
    protocol_version: ProtocolVersion = Field(description="Semver of the protocol.")


# ---------------------------------------------------------------------------
# Verdict (§3.6)
# ---------------------------------------------------------------------------


class Verdict(_ProtocolModel):
    """Implements ``docs/protocol.md`` §3.6 Verdict.

    Invariants:

    - ``feedback_packet`` MUST be non-null when ``passed is False``.
      Per §3.6 it MAY be null when ``passed is True``.
    - ``cost_usd`` MUST equal the sum of ``cost_usd`` across ``results``
      (§3.6). ``duration_ms`` is the harness-wide wall clock and is
      NOT constrained to equal the sum, since verifiers run concurrently.
    """

    id: IdStr = Field(description="Unique verdict identifier (§3.1).")
    deliverable_id: IdStr = Field(description="The deliverable that was verified.")
    passed: bool = Field(description="Whether the harness signed off on the deliverable.")
    results: list[VerifierResult] = Field(
        default_factory=list,
        description="All verifier results in stable order.",
    )
    feedback_packet: FeedbackPacket | None = Field(
        default=None,
        description="Present if passed=false; MAY be null if passed=true.",
    )
    cost_usd: float = Field(ge=0.0, description="Sum of cost_usd across results.")
    duration_ms: int = Field(ge=0, description="Total harness wall-clock duration.")
    protocol_version: ProtocolVersion = Field(
        description="Semver of the protocol this verdict conforms to.",
    )
    harness_version: str | None = Field(
        default=None,
        description="Version of the harness implementation.",
    )
    started_at: Iso8601 = Field(description="When the harness began (ISO-8601).")
    completed_at: Iso8601 = Field(description="When the harness returned (ISO-8601).")
    terminated_early: bool = Field(
        default=False,
        description="Whether the harness stopped before running all applicable verifiers.",
    )

    @field_validator("started_at", "completed_at")
    @classmethod
    def _check_timestamps(cls, v: str) -> str:
        validated = _validate_iso8601(v)
        assert validated is not None  # optional=False here
        return validated

    @model_validator(mode="after")
    def _check_invariants(self) -> Verdict:
        # §3.6: feedback_packet required when the verdict fails.
        if not self.passed and self.feedback_packet is None:
            raise ValueError("§3.6 invariant: feedback_packet MUST be non-null when passed=false.")
        # §3.6: cost_usd is the sum across results. Allow tiny float drift.
        expected = sum(r.cost_usd for r in self.results)
        if abs(self.cost_usd - expected) > 1e-9:
            raise ValueError(
                "§3.6 invariant: Verdict.cost_usd must equal the sum of result costs "
                f"({expected}); got {self.cost_usd}."
            )
        return self


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

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
]
