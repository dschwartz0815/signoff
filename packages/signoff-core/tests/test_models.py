"""Unit tests for signoff.models — one test per §3 requirement.

Test names point at the protocol section under test to make the review
trail explicit.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError
from signoff import (
    DELIVERABLE_CLAIM_ID,
    RESERVED_CLAIM_KINDS,
    BlockerEntry,
    Claim,
    Deliverable,
    FeedbackPacket,
    Severity,
    Verdict,
    VerifierResult,
    WarningEntry,
)

# ---------------------------------------------------------------------------
# §3.1 identifier regex
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "good_id",
    ["a", "0", "dlv_01HXYZ", "Z-x-9", "x" * 128],
)
def test_id_regex_accepts_valid_examples(good_id: str) -> None:
    Deliverable(id=good_id, kind="k", content=None)


@pytest.mark.parametrize(
    "bad_id",
    ["", "_leading_underscore", "-leading-dash", "has space", "a" * 129, "emoji-🚫"],
)
def test_id_regex_rejects_invalid_examples(bad_id: str) -> None:
    with pytest.raises(ValidationError):
        Deliverable(id=bad_id, kind="k", content=None)


def test_synthetic_deliverable_id_is_module_constant() -> None:
    # §4.3 — harness-internal; never on the wire (see §3.5 claim_id=null).
    assert DELIVERABLE_CLAIM_ID == "__deliverable__"
    # And it deliberately does NOT match the §3.1 wire-format regex:
    with pytest.raises(ValidationError):
        Claim(id=DELIVERABLE_CLAIM_ID, text="", kind="citation")


# ---------------------------------------------------------------------------
# §3.2 Deliverable
# ---------------------------------------------------------------------------


def test_deliverable_minimal_requires_id_kind_content() -> None:
    d = Deliverable(id="dlv_1", kind="research_report", content={"body": "..."})
    assert d.metadata == {}
    assert d.created_at is None


def test_deliverable_accepts_any_json_value_for_content() -> None:
    for payload in ({"a": 1}, [1, 2], "text", 3, 3.14, True, None):
        Deliverable(id="dlv_1", kind="k", content=payload)


def test_deliverable_created_at_must_be_iso8601() -> None:
    Deliverable(id="dlv_1", kind="k", content=None, created_at="2026-04-18T14:22:10Z")
    with pytest.raises(ValidationError):
        Deliverable(id="dlv_1", kind="k", content=None, created_at="not a date")


def test_deliverable_metadata_accepts_conventional_keys() -> None:
    d = Deliverable(
        id="dlv_1",
        kind="k",
        content=None,
        metadata={
            "agent_id": "agent-42",
            "session_id": "sess-1",
            "task_description": "check stuff",
            "parent_deliverable_id": "dlv_0",
            "retry_count": 1,
        },
    )
    assert d.metadata["retry_count"] == 1


# ---------------------------------------------------------------------------
# §3.3 Claim + §3.3.1 reserved kinds
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("kind", sorted(RESERVED_CLAIM_KINDS))
def test_reserved_claim_kinds_accepted(kind: str) -> None:
    Claim(id="clm_1", text="t", kind=kind)


def test_namespaced_claim_kind_accepted() -> None:
    Claim(id="clm_1", text="t", kind="legal.clause_reference")


@pytest.mark.parametrize(
    "kind",
    [
        "",
        "unscoped_unknown",
        "Legal.Foo",  # must be lowercase
        "legal.",
        ".clause",
        "has space.x",
    ],
)
def test_invalid_claim_kinds_rejected(kind: str) -> None:
    with pytest.raises(ValidationError):
        Claim(id="clm_1", text="t", kind=kind)


@pytest.mark.parametrize("provenance", ["agent_asserted", "extractor", "user_supplied", None])
def test_claim_provenance_accepts_reserved_values(provenance: str | None) -> None:
    Claim(id="clm_1", text="t", kind="citation", provenance=provenance)


def test_claim_provenance_rejects_unknown_values() -> None:
    with pytest.raises(ValidationError):
        Claim(id="clm_1", text="t", kind="citation", provenance="guessed")


def test_claim_span_requires_non_negative_ordered_pair() -> None:
    Claim(id="clm_1", text="t", kind="citation", span=(0, 10))
    Claim(id="clm_1", text="t", kind="citation", span=(5, 5))
    with pytest.raises(ValidationError):
        Claim(id="clm_1", text="t", kind="citation", span=(-1, 5))
    with pytest.raises(ValidationError):
        Claim(id="clm_1", text="t", kind="citation", span=(10, 5))


def test_claim_text_accepts_unicode() -> None:
    Claim(id="clm_1", text="ça 日本語 🤖", kind="citation")


# ---------------------------------------------------------------------------
# §3.4 Severity
# ---------------------------------------------------------------------------


def test_severity_values() -> None:
    assert Severity.BLOCKER == "blocker"
    assert Severity.WARNING == "warning"
    assert Severity.INFO == "info"


def test_severity_serializes_lowercase() -> None:
    r = VerifierResult(
        verifier="pack.name",
        claim_id=None,
        passed=True,
        severity=Severity.INFO,
        reason="ok",
        cost_usd=0.0,
        duration_ms=1,
    )
    assert '"severity":"info"' in r.model_dump_json()


# ---------------------------------------------------------------------------
# §3.5 VerifierResult + invariants
# ---------------------------------------------------------------------------


def _passing_result(**overrides: object) -> VerifierResult:
    base: dict[str, object] = {
        "verifier": "pack.name",
        "claim_id": "clm_1",
        "passed": True,
        "severity": Severity.INFO,
        "reason": "ok",
        "cost_usd": 0.0,
        "duration_ms": 5,
    }
    base.update(overrides)
    return VerifierResult.model_validate(base)


def test_verifier_name_pattern_enforced() -> None:
    _passing_result(verifier="pack.name")
    with pytest.raises(ValidationError):
        _passing_result(verifier="Pack.Name")  # uppercase
    with pytest.raises(ValidationError):
        _passing_result(verifier="no_dot")
    with pytest.raises(ValidationError):
        _passing_result(verifier="pack.name.extra")


def test_claim_id_null_for_whole_deliverable_result() -> None:
    _passing_result(claim_id=None)


def test_blocker_failure_requires_non_null_suggestion() -> None:
    with pytest.raises(ValidationError, match=r"§3\.5 invariant"):
        VerifierResult(
            verifier="pack.name",
            claim_id="clm_1",
            passed=False,
            severity=Severity.BLOCKER,
            reason="bad",
            suggestion=None,
            cost_usd=0.0,
            duration_ms=1,
        )
    VerifierResult(
        verifier="pack.name",
        claim_id="clm_1",
        passed=False,
        severity=Severity.BLOCKER,
        reason="bad",
        suggestion="fix it",
        cost_usd=0.0,
        duration_ms=1,
    )


def test_passed_non_info_requires_evidence() -> None:
    # passed=true, severity=warning, no evidence -> invalid per §3.5
    with pytest.raises(ValidationError, match=r"§3\.5 invariant"):
        VerifierResult(
            verifier="pack.name",
            claim_id="clm_1",
            passed=True,
            severity=Severity.WARNING,
            reason="ok",
            cost_usd=0.0,
            duration_ms=1,
        )
    # With evidence, it's allowed.
    VerifierResult(
        verifier="pack.name",
        claim_id="clm_1",
        passed=True,
        severity=Severity.WARNING,
        reason="ok",
        evidence={"note": "x"},
        cost_usd=0.0,
        duration_ms=1,
    )


@pytest.mark.parametrize("bad_cost", [-0.01, -1.0])
def test_cost_usd_non_negative(bad_cost: float) -> None:
    with pytest.raises(ValidationError):
        _passing_result(cost_usd=bad_cost)


def test_duration_ms_non_negative() -> None:
    with pytest.raises(ValidationError):
        _passing_result(duration_ms=-1)


def test_started_at_validated() -> None:
    _passing_result(started_at="2026-04-18T14:22:10Z")
    with pytest.raises(ValidationError):
        _passing_result(started_at="yesterday")


# ---------------------------------------------------------------------------
# §3.7 FeedbackPacket / BlockerEntry / WarningEntry
# ---------------------------------------------------------------------------


def _blocker_entry() -> BlockerEntry:
    return BlockerEntry(
        claim_id="clm_1",
        claim_text="A claim.",
        verifier="pack.name",
        issue="Source URL returned HTTP 404.",
        suggested_repair="Replace or remove the claim.",
    )


def test_feedback_packet_passed_is_always_false() -> None:
    p = FeedbackPacket(blockers=[_blocker_entry()], cost_usd=0.0, protocol_version="0.1")
    assert p.passed is False
    # You cannot construct a packet with passed=True.
    with pytest.raises(ValidationError):
        FeedbackPacket.model_validate(
            {"passed": True, "blockers": [], "cost_usd": 0.0, "protocol_version": "0.1"}
        )


def test_feedback_packet_protocol_version_semver() -> None:
    FeedbackPacket(blockers=[_blocker_entry()], cost_usd=0.0, protocol_version="0.1")
    FeedbackPacket(blockers=[_blocker_entry()], cost_usd=0.0, protocol_version="1.2.3")
    with pytest.raises(ValidationError):
        FeedbackPacket(blockers=[_blocker_entry()], cost_usd=0.0, protocol_version="unstable")


def test_packet_entries_require_non_empty_issue_and_repair() -> None:
    with pytest.raises(ValidationError):
        BlockerEntry(
            claim_id="clm_1",
            verifier="pack.name",
            issue="",
            suggested_repair="fix",
        )
    with pytest.raises(ValidationError):
        WarningEntry(
            claim_id=None,
            verifier="pack.name",
            issue="issue",
            suggested_repair="",
        )


def test_retry_budget_remaining_non_negative() -> None:
    FeedbackPacket(
        blockers=[_blocker_entry()],
        cost_usd=0.0,
        retry_budget_remaining=0,
        protocol_version="0.1",
    )
    with pytest.raises(ValidationError):
        FeedbackPacket(
            blockers=[_blocker_entry()],
            cost_usd=0.0,
            retry_budget_remaining=-1,
            protocol_version="0.1",
        )


# ---------------------------------------------------------------------------
# §3.6 Verdict + invariants
# ---------------------------------------------------------------------------


def _passing_verdict(**overrides: object) -> Verdict:
    base: dict[str, object] = {
        "id": "vrd_1",
        "deliverable_id": "dlv_1",
        "passed": True,
        "results": [],
        "feedback_packet": None,
        "cost_usd": 0.0,
        "duration_ms": 0,
        "protocol_version": "0.1",
        "started_at": "2026-04-18T14:22:10Z",
        "completed_at": "2026-04-18T14:22:10Z",
    }
    base.update(overrides)
    return Verdict.model_validate(base)


def test_verdict_minimal_passes() -> None:
    v = _passing_verdict()
    assert v.passed is True and v.feedback_packet is None
    assert v.terminated_early is False


def test_verdict_feedback_required_when_failed() -> None:
    with pytest.raises(ValidationError, match=r"§3\.6 invariant"):
        _passing_verdict(passed=False, feedback_packet=None)
    # With a packet, it's fine.
    packet = FeedbackPacket(blockers=[_blocker_entry()], cost_usd=0.0, protocol_version="0.1")
    _passing_verdict(passed=False, feedback_packet=packet)


def test_verdict_cost_usd_sums_results() -> None:
    r1 = _passing_result(cost_usd=0.25)
    r2 = _passing_result(cost_usd=0.1)
    _passing_verdict(results=[r1, r2], cost_usd=0.35)
    with pytest.raises(ValidationError, match=r"§3\.6 invariant"):
        _passing_verdict(results=[r1, r2], cost_usd=1.0)


def test_verdict_protocol_version_semver() -> None:
    with pytest.raises(ValidationError):
        _passing_verdict(protocol_version="abc")


def test_verdict_round_trip_json_preserves_fields() -> None:
    r = VerifierResult(
        verifier="pack.name",
        claim_id="clm_1",
        passed=False,
        severity=Severity.BLOCKER,
        reason="Source returned 404.",
        suggestion="Replace the URL.",
        evidence={"status": 404, "url": "https://example.com"},
        cost_usd=0.0,
        duration_ms=180,
    )
    packet = FeedbackPacket(
        blockers=[
            BlockerEntry(
                claim_id="clm_1",
                claim_text="A 2024 Gartner analysis found customer churn rises by 28%.",
                verifier="pack.name",
                issue=r.reason,
                suggested_repair=r.suggestion or "",
            )
        ],
        cost_usd=0.0,
        retry_budget_remaining=2,
        protocol_version="0.1",
    )
    v = _passing_verdict(
        passed=False,
        results=[r],
        feedback_packet=packet,
        cost_usd=0.0,
        duration_ms=180,
    )
    round_tripped = Verdict.model_validate_json(v.model_dump_json())
    assert round_tripped.model_dump(mode="json") == v.model_dump(mode="json")


# ---------------------------------------------------------------------------
# Wire format (§7.1): unknown fields are tolerated, required fields are not
# ---------------------------------------------------------------------------


def test_unknown_fields_tolerated() -> None:
    d = Deliverable.model_validate(
        {"id": "dlv_1", "kind": "k", "content": None, "future_field": "ignored"}
    )
    assert not hasattr(d, "future_field")


def test_required_field_null_is_not_missing() -> None:
    # claim_id REQUIRED per §3.5; null is a valid value but missing is not.
    with pytest.raises(ValidationError):
        VerifierResult.model_validate(
            {
                "verifier": "pack.name",
                "passed": True,
                "severity": "info",
                "reason": "ok",
                "cost_usd": 0.0,
                "duration_ms": 1,
            }
        )
