"""Tool descriptors for the Signoff MCP server (protocol §7.3).

Kept in a dedicated module so the server module stays focused on
transport + dispatch. Schemas here are hand-written subsets of the
Pydantic model schemas exported by ``signoff-core``; kept deliberately
minimal so the MCP tool descriptor documentation is readable.
"""

from __future__ import annotations

import mcp.types as mcp_types

__all__ = [
    "GET_VERDICT_SCHEMA",
    "LIST_VERIFIERS_SCHEMA",
    "REQUEST_SIGNOFF_SCHEMA",
    "TOOL_GET_VERDICT",
    "TOOL_LIST_VERIFIERS",
    "TOOL_REQUEST_SIGNOFF",
    "get_verdict_message",
]


# ---------------------------------------------------------------------------
# Input schemas (protocol §7.3.*)
# ---------------------------------------------------------------------------

REQUEST_SIGNOFF_SCHEMA: dict[str, object] = {
    "type": "object",
    "required": ["deliverable"],
    "additionalProperties": False,
    "properties": {
        "deliverable": {
            "type": "object",
            "description": (
                "The deliverable to verify. Must conform to docs/protocol.md §3.2 "
                "(Deliverable). At minimum: id, kind, content."
            ),
            "required": ["id", "kind", "content"],
        },
        "claims": {
            "type": "array",
            "description": (
                "Claims embedded in the deliverable. Each must conform to "
                "docs/protocol.md §3.3 (Claim)."
            ),
            "items": {"type": "object"},
            "default": [],
        },
        "config_override": {
            "type": "object",
            "description": (
                "Per-request config override deep-merged on top of the server's "
                "loaded config. See docs/protocol.md §6.2."
            ),
        },
        "retry_budget": {
            "type": "integer",
            "minimum": 0,
            "description": (
                "If provided, the harness echoes retry_budget - 1 back in "
                "FeedbackPacket.retry_budget_remaining (§5.7)."
            ),
        },
    },
}


LIST_VERIFIERS_SCHEMA: dict[str, object] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {},
}


GET_VERDICT_SCHEMA: dict[str, object] = {
    "type": "object",
    "required": ["verdict_id"],
    "additionalProperties": False,
    "properties": {
        "verdict_id": {
            "type": "string",
            "description": "Previously-returned verdict id.",
        }
    },
}


# ---------------------------------------------------------------------------
# Tool descriptors
# ---------------------------------------------------------------------------

TOOL_REQUEST_SIGNOFF = mcp_types.Tool(
    name="request_signoff",
    title="Request signoff on a deliverable",
    description=(
        "Submit a deliverable and its embedded claims for verification. "
        "The harness runs every applicable verifier and returns a Verdict. "
        "If passed=false, address each entry in feedback_packet.blockers "
        "and resubmit. See docs/protocol.md §7.3.1."
    ),
    inputSchema=REQUEST_SIGNOFF_SCHEMA,
)


TOOL_LIST_VERIFIERS = mcp_types.Tool(
    name="list_verifiers",
    title="List registered verifiers",
    description=(
        "Return the set of verifiers this server has loaded plus their "
        "enabled status under the active configuration. "
        "See docs/protocol.md §7.3.2."
    ),
    inputSchema=LIST_VERIFIERS_SCHEMA,
)


TOOL_GET_VERDICT = mcp_types.Tool(
    name="get_verdict",
    title="Fetch a past verdict by id",
    description=(
        "Retrieve a previously-issued verdict. OPTIONAL per protocol §7.3.3. "
        "This local server does not persist verdicts; the hosted Signoff "
        "service implements this tool. Always returns an error here."
    ),
    inputSchema=GET_VERDICT_SCHEMA,
)


def get_verdict_message() -> str:
    """Error message returned for ``get_verdict`` calls against the local
    server. Kept in one place so the test can assert against it without
    duplicating the string."""
    return (
        "get_verdict is only supported by the hosted Signoff service; "
        "this is a local server. See https://signoff.dev (forthcoming) for "
        "the managed offering."
    )
