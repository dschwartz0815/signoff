# Signoff

**Agents make claims. Signoff makes them prove it.**

Signoff is a verification layer for AI agents. It sits between an agent and its "done" claim, runs pluggable verifiers against the deliverable, and returns either a pass or a structured feedback packet the agent can retry against — before any human sees the output.

- [Proposal](./PROPOSAL.md) — the why.
- [Protocol specification](./docs/protocol.md) — the normative contract.
- [Contributor guide](./CLAUDE.md) — repo layout, conventions, phases.

## Quick start

Bring up the MCP server in a container, probe it, and make a real `request_signoff` call:

```sh
just dev                                                   # builds and runs signoff-mcp:dev on :8765

curl -s http://localhost:8765/health
# {"status":"ok","harness":"ready","verifier_count":0}

curl -s http://localhost:8765/version
# {"protocol_version":"0.1","harness_version":"0.0.1","mcp_server_version":"0.0.1"}
```

…then wire up any MCP client (Claude Desktop, Cursor, Cline, Zed, Continue, or a custom agent). Concrete config snippets in [`docs/mcp-integration.md`](./docs/mcp-integration.md).

With the empty default config ([`examples/minimal.yaml`](./examples/minimal.yaml)) every request trivially passes — that's the "plumbing works" gate. Install a verifier pack (Phase 1 ships `signoff-code`) and the same agent flow starts doing real verification.

## Packages

| Path | Language | Purpose |
|------|----------|---------|
| [`packages/signoff-core`](./packages/signoff-core) | Python | Core engine: models, harness, runtime protocol. |
| [`packages/signoff-mcp`](./packages/signoff-mcp) | Python | MCP server adapter. |
| [`packages/signoff-http`](./packages/signoff-http) | Python | Real `httpx`-backed HTTP client ([docs](./docs/http-client.md)). |
| [`packages/signoff-judge`](./packages/signoff-judge) | Python | Real LLM judge — `AnthropicJudge`, `OpenAIJudge` ([docs](./docs/judge-client.md)). |
| [`packages/signoff-code`](./packages/signoff-code) | Python | First verifier pack (coding wedge, Phase 1). |
| [`packages/signoff-sdk-ts`](./packages/signoff-sdk-ts) | TypeScript | Client SDK for the hosted API. |
| [`cloud/`](./cloud) | mixed | Hosted service (Phase 2+; see [§6](./CLAUDE.md#6-cloud-directory-and-split-policy)). |

## Status

**Phase 0 — Foundation.** Data models, harness, runtime protocol, config loader, registry, MCP server, real HTTP client (`signoff-http`), and real LLM judge clients (`signoff-judge` — Anthropic and OpenAI) are all live. Verifier packs land in Phase 1. See [`CLAUDE.md`](./CLAUDE.md) §14 for the phase plan.

## Development

Prerequisites: one of (a) `uv` + `pnpm` + Docker, or (b) Docker + `just` (all commands route through containers).

```sh
just setup         # Install Python and TS workspace dependencies.
just test          # Run all test suites.
just lint          # Lint Python (ruff) and TS (biome).
just typecheck     # mypy --strict + tsc --noEmit.
just build-images  # Build every Dockerfile in the OSS stack.
just dev           # docker compose up — local MCP server on :8765.
just ci            # Full CI simulation.
```

Run `just --list` for the full recipe list.

## License

Apache 2.0. See [`LICENSE`](./LICENSE).
