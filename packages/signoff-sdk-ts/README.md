# @signoff/sdk

TypeScript client for [Signoff](../../README.md).

**Source of truth for the types exported here:** [`docs/protocol.md`](../../docs/protocol.md) §3 (and the Python `signoff-core` package, which ships the JSON schemas this SDK copies at build time). Wire format is snake_case; this SDK does not camel-case at the protocol layer.

## Phase 0 surface

Zod schemas and inferred TypeScript types for every §3 wire-format model:

| Schema | Type | Protocol section |
|--------|------|------------------|
| `DeliverableSchema` | `Deliverable` | §3.2 |
| `ClaimSchema` | `Claim` | §3.3 |
| `VerifierResultSchema` | `VerifierResult` | §3.5 |
| `VerdictSchema` | `Verdict` | §3.6 |
| `FeedbackPacketSchema`, `BlockerEntrySchema`, `WarningEntrySchema` | `FeedbackPacket`, `BlockerEntry`, `WarningEntry` | §3.7 |
| `SeveritySchema` | `Severity` | §3.4 |

Also exported: `ID_PATTERN`, `VERIFIER_NAME_PATTERN`, `RESERVED_CLAIM_KINDS`, and `DELIVERABLE_CLAIM_ID`.

## Schema parity

JSON Schemas live in [`packages/signoff-core/src/signoff/schemas/`](../signoff-core/src/signoff/schemas/). This SDK copies them into `src/schemas/` at build/test time via [`scripts/copy-schemas.mjs`](./scripts/copy-schemas.mjs). The copy is *not* symlinked — symlinks break on Windows contributors.

Two tests guard parity:

- [`tests/schema-parity.test.ts`](./tests/schema-parity.test.ts) — asserts every Zod schema agrees with its JSON Schema counterpart on field names and required status.
- [`tests/parity.test.ts`](./tests/parity.test.ts) + [`../../tests/parity/test_cross_language.py`](../../tests/parity/test_cross_language.py) — round-trip the same fixtures through Zod and Pydantic and compare canonical outputs.

## Coming in follow-up PRs

- HTTP client for the hosted Signoff API.
- MCP client helper for agents talking to the MCP server.

See [`CLAUDE.md`](../../CLAUDE.md) §14 for the phase plan.
