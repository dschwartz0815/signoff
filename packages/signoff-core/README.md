# signoff-core

Core engine for [Signoff](../../README.md) — a verification layer for AI agents.

**Source of truth for the types exported here:** [`docs/protocol.md`](../../docs/protocol.md) §3. When this code disagrees with the protocol doc, the doc wins and the code is a bug.

## Phase 0 surface

Data models only — `signoff.models` (via `from signoff import …`):

| Type | Protocol section |
|------|------------------|
| `Deliverable` | §3.2 |
| `Claim` | §3.3 (reserved kinds in §3.3.1) |
| `VerifierResult` | §3.5 |
| `Verdict` | §3.6 |
| `FeedbackPacket`, `BlockerEntry`, `WarningEntry` | §3.7 |
| `Severity` (`StrEnum`) | §3.4 |

Also exported: regex constants (`ID_PATTERN`, `VERIFIER_NAME_PATTERN`), the `RESERVED_CLAIM_KINDS` frozenset, and the harness-internal `DELIVERABLE_CLAIM_ID` constant (§4.3).

## JSON schemas

Every model is serialised to JSON Schema under [`src/signoff/schemas/`](./src/signoff/schemas/) and kept in sync via `scripts/export_schemas.py`. Regenerate with:

```sh
just schemas         # rewrite schemas from current models
just schemas-check   # fail if committed schemas drift from models (CI gate)
```

The TypeScript SDK copies these schemas at build time and asserts agreement against its Zod definitions, so a model change in this package must also re-export schemas and usually pairs with a parallel change in `@signoff/sdk`.

## Coming in follow-up PRs

- `@verifier` decorator and `VerifierResult` builders.
- `Harness` (orchestration, concurrency, budgeting).
- `Runtime` protocol + `LocalRuntime`.
- YAML config loader.
- Plugin registry (entry points).

See [`CLAUDE.md`](../../CLAUDE.md) §14 for the phase plan.
