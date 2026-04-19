# MCP Integration

Signoff exposes the harness over the [Model Context Protocol](https://modelcontextprotocol.io/) via the `signoff-mcp` package. Any MCP client — Claude Desktop, Cursor, Cline, Zed, Continue, or a custom agent — points at the server, discovers the three Signoff tools, and can call `request_signoff` before declaring any task complete.

Normative tool surface: [`docs/protocol.md`](./protocol.md) §7.3. Harness internals: [`docs/harness.md`](./harness.md). Docker + deployment: [`CLAUDE.md`](../CLAUDE.md) §9.

---

## Architecture

```
┌─────────────────────────────────────────────────────────────┐
│ signoff-mcp process                                         │
│                                                             │
│  ┌────────────┐     ┌──────────────┐     ┌──────────────┐  │
│  │ MCP server │────▶│   Adapter    │────▶│   Harness    │  │
│  │ (stdio /   │     │  (validates, │     │  (singleton) │  │
│  │  HTTP+SSE) │     │   converts)  │     │              │  │
│  └────────────┘     └──────────────┘     └──────────────┘  │
└─────────────────────────────────────────────────────────────┘
```

The adapter is stateless per request. A single `Harness` is constructed at process start via `Harness.from_config_path()` and handles every `request_signoff` call for the process lifetime.

---

## Tools

### `request_signoff` (§7.3.1)

**Input**

```json
{
  "deliverable": {"id": "dlv_1", "kind": "research_report", "content": "..."},
  "claims": [
    {"id": "clm_1", "text": "…", "kind": "citation", "evidence": {"url": "…"}}
  ],
  "config_override": {"budget": {"max_cost_usd": 1.0}},   // optional
  "retry_budget": 3                                         // optional
}
```

**Output**: a [Verdict](./protocol.md#36-verdict). When `verdict.passed` is `false`, `verdict.feedback_packet.blockers` lists every agent-actionable repair.

**Errors**: validation failures on the `Deliverable` or `Claim` objects surface as MCP errors with the first-field path. Internal harness errors surface the class name + short message; tracebacks are logged on the server at ERROR but never leak to the wire.

### `list_verifiers` (§7.3.2)

No arguments. Returns `{"protocol_version": "0.1", "verifiers": [...]}` where each entry has `{name, pack, claim_kinds, cost_tier, version, enabled}`. `enabled=false` means the verifier is disabled in every deliverable-kind block that mentions it; `true` is the default when any block leaves it enabled.

### `get_verdict` (§7.3.3)

Always returns an MCP error from local servers — persistence is the [hosted Signoff service](../CLAUDE.md#9-docker-strategy-across-the-project)'s job. Ship a cloud server in Phase 2 and this tool resolves real verdicts by id.

---

## Wiring a client

### Claude Desktop

`~/Library/Application Support/Claude/claude_desktop_config.json` (macOS) / `%APPDATA%\Claude\claude_desktop_config.json` (Windows):

```json
{
  "mcpServers": {
    "signoff": {
      "command": "signoff-mcp",
      "args": ["--config", "/absolute/path/to/signoff.yaml"]
    }
  }
}
```

Restart Claude Desktop. The tools appear in the tools panel.

### Cursor

`~/.cursor/mcp.json`:

```json
{
  "mcpServers": {
    "signoff": {
      "command": "signoff-mcp",
      "args": ["--config", "/absolute/path/to/signoff.yaml"]
    }
  }
}
```

### Cline / Continue (VS Code extensions)

In the extension settings, add:

```json
"mcpServers": {
  "signoff": {
    "command": "signoff-mcp",
    "args": ["--config", "${workspaceFolder}/signoff.yaml"]
  }
}
```

### HTTP (remote)

Point any MCP client that supports SSE transport at `http://<host>:8765/sse`. When `SIGNOFF_MCP_AUTH_TOKEN` is set on the server, include `Authorization: Bearer <token>` on every request.

---

## Recommended agent system prompt addition

> Before marking any task complete, call `request_signoff` with the deliverable and the claims you made. If the verdict's `passed` field is `false`, address every entry in `feedback_packet.blockers` and resubmit. Only declare the task complete when the verdict passes.

This is enough to drive an agent through a retry loop without any additional scaffolding.

---

## Auth

Phase 0 ships without auth by default — it's a developer tool. Two options:

- **Ambient trust** (single-user, localhost, Docker network). No config. Don't expose the port to the public internet.
- **Bearer token**. Set `SIGNOFF_MCP_AUTH_TOKEN` on the server and send `Authorization: Bearer <token>` from every client. `/health` and `/version` stay unauthenticated so orchestrators can probe without credentials.

Cloud-grade auth (OAuth, SSO, RBAC, per-key quotas) is a Phase 2 concern and lives in the hosted service, not in the library.

---

## Transports

| Transport | Use | CLI |
|-----------|-----|-----|
| stdio | Desktop IDEs, Claude Desktop, Cursor, Cline — they spawn the process and speak over pipes | `signoff-mcp` (default) |
| HTTP + SSE | Containers, remote agents, headless deployments | `signoff-mcp --transport http --port 8765` |

Docker containers default to HTTP because there's no parent process to own a stdio pipe.

---

## Troubleshooting

| Symptom | Likely cause |
|---------|--------------|
| Client reports "server failed to start" | Config path wrong. Use `signoff-mcp --config /absolute/path` and confirm the YAML is valid with `python -c "import yaml, sys; yaml.safe_load(open('/path/to/signoff.yaml'))"`. |
| Tools don't appear in the client | Client didn't restart after config edit. Also confirm the `command` is on PATH — `which signoff-mcp`. |
| Verdicts always `passed=true`, empty `results` | Expected with an empty `packs: []` config. Install a verifier pack (e.g., `pip install signoff-research`) and add it to `packs:`. |
| HTTP 401 from server | `SIGNOFF_MCP_AUTH_TOKEN` is set but the request didn't carry a matching Bearer. |
| Server crashes on start over HTTP | Port already in use. `--port <free>` or stop the conflicting service. |
| `request_signoff` returns a validation error | Run the same payload against `Deliverable.model_validate(...)` locally to see the pydantic error trail; the MCP adapter surfaces the first-field detail. |

---

## What's next

PRs 7 and 8 swap the Phase 0 `FakeHttpClient` and `FakeJudge` for real httpx-backed + Anthropic/OpenAI-backed implementations; no MCP-surface changes. After those land, the same client wiring drives real verifier packs through production-grade HTTP and LLM judge calls.
