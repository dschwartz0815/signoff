# Signoff

**Agents make claims. Signoff makes them prove it.**

Signoff is a verification layer for AI agents. It sits between an agent and its "done" claim, runs pluggable verifiers against the deliverable, and returns either a pass or a structured feedback packet the agent can retry against — before any human sees the output.

- [Proposal](./PROPOSAL.md) — the why.
- [Protocol specification](./docs/protocol.md) — the normative contract.
- [Contributor guide](./CLAUDE.md) — repo layout, conventions, phases.

## Status

**Phase 0 — Foundation.** Scaffolding only. Public API is intentionally empty; data models, harness, runtime protocol, and verifier logic arrive in follow-up PRs per [`CLAUDE.md`](./CLAUDE.md) §14.

## Packages

| Path | Language | Purpose |
|------|----------|---------|
| [`packages/signoff-core`](./packages/signoff-core) | Python | Core engine: models, harness, runtime protocol. |
| [`packages/signoff-mcp`](./packages/signoff-mcp) | Python | MCP server adapter. |
| [`packages/signoff-code`](./packages/signoff-code) | Python | First verifier pack (coding wedge, Phase 1). |
| [`packages/signoff-sdk-ts`](./packages/signoff-sdk-ts) | TypeScript | Client SDK for the hosted API. |
| [`cloud/`](./cloud) | mixed | Hosted service (Phase 2+; see [§6](./CLAUDE.md#6-cloud-directory-and-split-policy)). |

## Development

Prerequisites: one of (a) `uv` + `pnpm` + Docker, or (b) Docker + `just` (all commands route through containers).

```sh
just setup         # Install Python and TS workspace dependencies.
just test          # Run all test suites.
just lint          # Lint Python (ruff) and TS (biome).
just typecheck     # mypy --strict + tsc --noEmit.
just build-images  # Build every Dockerfile in the OSS stack.
just dev           # docker compose up — local OSS stack.
just ci            # Full CI simulation.
```

Run `just --list` for the full recipe list.

## License

Apache 2.0. See [`LICENSE`](./LICENSE).
