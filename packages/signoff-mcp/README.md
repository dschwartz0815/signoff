# signoff-mcp

MCP server for [Signoff](../../README.md) — exposes the harness over the [Model Context Protocol](https://modelcontextprotocol.io/) so any MCP client (Claude Desktop, Cursor, Cline, Zed, Continue, custom agents) can ask for verification before marking work complete.

Depth + client-wiring examples: [`docs/mcp-integration.md`](../../docs/mcp-integration.md).

## Install

```sh
pip install signoff-mcp
```

## Run

```sh
signoff-mcp                                      # stdio + ./signoff.yaml
signoff-mcp --config /etc/signoff/harness.yaml
signoff-mcp --transport http --port 8765
signoff-mcp --health --transport http            # probe a running HTTP server
```

Or as a container:

```sh
docker run -v $(pwd)/signoff.yaml:/app/signoff.yaml -p 8765:8765 signoff-mcp:dev
```

## Tools (protocol §7.3)

| Tool | Purpose |
|------|---------|
| `request_signoff` | Submit a deliverable + its claims. Returns a Verdict; if `passed=false`, the `feedback_packet` tells the agent what to fix. |
| `list_verifiers` | Return every loaded verifier plus its enabled-under-current-config status. |
| `get_verdict` | Optional per §7.3.3. This local server always returns an error; the hosted Signoff service implements persistent lookup. |

## Auth

No auth by default — Phase 0 is a developer tool. Set `SIGNOFF_MCP_AUTH_TOKEN=…` and every non-`/health`/`/version` request must carry `Authorization: Bearer <token>`.

## Recommended agent prompt

> Before marking any task complete, call `request_signoff` with your deliverable and claims. If `verdict.passed` is `false`, address each entry in `feedback_packet.blockers` and resubmit.
