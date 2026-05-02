# Dogfooding Signoff

You've got [the quickstart](./quickstart.md) working. This doc is
the next step: how to wire your agent up so `request_signoff` is a
routine part of its "I'm done" flow, and how to debug the places
where it doesn't Just Work on day one.

---

## The system-prompt addition

The single most important configuration detail. Without it your
agent sees `request_signoff` in the tool list and shrugs.

### Minimal form (copy-paste)

Add to your agent's project-level system prompt — `CLAUDE.md`,
`.cursor/rules`, `.continue/config.json`'s `systemMessage`,
`.zed/settings.json`'s agent config, or whatever the client calls
it:

```
Before claiming any code-change task is complete, you MUST call
the `signoff.request_signoff` tool with a `code_change` deliverable
that describes the change you just made.

The deliverable's `content` must be a CodeChangeDeliverable JSON
object with:
- `intent`: a one-sentence description of what the change does,
  in your own words.
- `files`: a map of relative project paths to their full new
  contents (overwrites whatever was there).

If the returned Verdict's `passed` is not `true`, read
`feedback_packet.blockers`. Each blocker has a `reason` and a
`suggested_repair`. Apply the repairs, then call `request_signoff`
again. Do not tell the user the task is done until `passed=true`.
```

### Longer form (more guidance, less drift)

Some agents benefit from explicit framing of *when* to bundle
things together vs. per-change:

```
Signoff is your pre-submit check. Before any of:
- Creating a pull request
- Marking a TODO as complete
- Telling the user "I'm done"

you MUST call `signoff.request_signoff`. Bundle related edits
into a single deliverable — one logical change is one
request_signoff call. If the change touches three files that all
serve one feature, that's one deliverable with three files; if
it's three independent bug fixes, that's three deliverables.

When the verdict is blocked:
- Prefer the `suggested_repair` text over your own guesses — it
  comes from the verifier that caught the problem.
- If the same blocker fires twice in a row with the same
  `suggested_repair`, stop and ask the user; you're probably
  misreading the failure.
- `semantic_diff` blockers mean the diff doesn't match the
  intent you stated. Either the change is wrong, or the intent
  was — check both before fixing.
```

### Why this matters

Signoff is a gate the agent *opts into*. No client-side
enforcement exists — the MCP server can't tell whether
`request_signoff` was called before the user heard "done." The
system-prompt text above is what makes the gate real for a
given agent.

---

## MCP client setup by client

All clients use the same three-line entry; the shape differs.

### Claude Code (CLI) + Claude Desktop

`~/.claude/claude_desktop_config.json` (or `~/.config/claude/claude_desktop_config.json` on Linux):

```json
{
  "mcpServers": {
    "signoff": {
      "transport": "http",
      "url": "http://127.0.0.1:8765"
    }
  }
}
```

Restart Claude. In the chat, `/mcp` lists connected servers —
you should see `signoff` with tools `list_verifiers` and
`request_signoff`.

For stdio transport (useful when running Claude Code against a
local MCP server process with no HTTP surface):

```json
{
  "mcpServers": {
    "signoff": {
      "command": "signoff-mcp",
      "args": ["--transport", "stdio", "--config", "/path/to/signoff.yaml"]
    }
  }
}
```

### Cursor

`~/.cursor/mcp.json`:

```json
{
  "mcpServers": {
    "signoff": {
      "transport": "http",
      "url": "http://127.0.0.1:8765"
    }
  }
}
```

### Cline (VS Code extension)

Edit `cline.mcpServers` in VS Code settings JSON:

```json
"cline.mcpServers": {
  "signoff": {
    "transport": "http",
    "url": "http://127.0.0.1:8765"
  }
}
```

### Zed

`~/.config/zed/settings.json`:

```json
{
  "assistant": {
    "mcp_servers": {
      "signoff": { "transport": "http", "url": "http://127.0.0.1:8765" }
    }
  }
}
```

### Continue

`~/.continue/config.json`:

```json
{
  "mcpServers": [
    { "name": "signoff", "transport": "http", "url": "http://127.0.0.1:8765" }
  ]
}
```

### Custom MCP client

The MCP protocol spec (https://modelcontextprotocol.io/) defines
the wire format. Point any client at the running server and it
will discover `list_verifiers` + `request_signoff` via the
standard `tools/list` call.

---

## Troubleshooting

### "Claude shows connected but never calls the tool"

Two usual causes, in order:

1. **The system prompt is missing or wrong.** Without the
   instruction to *call* `request_signoff`, the agent treats the
   tool as optional metadata. Paste the system-prompt block above
   into whichever file your client actually reads.
2. **The tool name in the system prompt doesn't match.** Some
   clients namespace tools as `<server>.<tool>`, others as
   `<server>/<tool>`, others as just `<tool>`. Run `/mcp` (Claude
   Code) or the equivalent and copy the name verbatim.

If both are right, check that the deliverable `kind` is the string
the config expects (`code_change`, not `codeChange`). The harness
will produce a result with no enabled verifiers for an unknown kind
rather than error — harmless but silent.

### `request_signoff` fails with "workspace not found" or similar

The pack materialises a temp directory under `ctx.workspace`. Two
paths are most commonly missing:

- **The workspace dir itself doesn't exist**. If you launched
  `signoff-mcp` from one directory and the deliverable references
  paths the harness tries to resolve against another, the bind
  mount fails. Use absolute paths in `BaseReference(kind="local_path")`.
- **macOS Docker file-sharing**. The sandbox image can't bind-mount
  paths that aren't shared with the daemon. Docker Desktop →
  Settings → Resources → File sharing; add your workspace path
  (or `$TMPDIR`, which is where the pack's temp trees land by
  default).

### A verifier times out

`tests_pass` has a 300s default. If it trips:

- Raise the per-verifier timeout in your `signoff.yaml`:

  ```yaml
  deliverables:
    code_change:
      verifiers:
        signoff-code.tests_pass:
          timeout_seconds_override: 900
  ```

- Or raise the whole harness budget:

  ```yaml
  budget:
    max_duration_seconds: 1800
  ```

- The sandbox container's wall-clock limit is separate
  (`runtime_policy.docker.timeout_seconds`). If the container
  itself is getting killed, raise that too.

### Judge returns an unexpected label

The `semantic_diff` verifier carries everything you need to debug
in `evidence`:

- `evidence.label` — what the judge returned.
- `evidence.explanation` — one-sentence reason from the judge.
- `evidence.excerpt` — the verbatim quote from the diff the judge
  anchored its verdict on, if any.
- `evidence.model` — e.g. `claude-haiku-4-5`. Useful when bumping
  model versions changes behaviour.
- `evidence.prompt_version` — e.g. `1.0.0`. Tied to the prompt
  registry; diffing two verdicts across a prompt version bump
  tells you whether the prompt change or the model change caused
  the difference.

See the [judge client docs](./judge-client.md) for the retry /
error semantics and the [prompts doc](./prompts.md) for how to
vendor a locally-modified prompt when a built-in produces
unfavourable labels.

---

## Debugging tips

### Read the feedback packet

It's the short version of the verdict designed for agents. Every
blocker includes a `suggested_repair` string — if your agent
isn't using it, check that your system prompt tells it to.

```python
verdict = await harness.verify(deliverable, claims=[])
if verdict.feedback_packet:
    for b in verdict.feedback_packet.blockers:
        print(f"[{b.verifier}] {b.issue}\n  repair: {b.suggested_repair}\n")
    for w in verdict.feedback_packet.warnings:
        print(f"[{w.verifier}] {w.issue}")
```

### Find the logs

The harness logs under the `signoff.*` logger tree; the MCP server
adds `signoff.mcp` on top of that. Every log line carries the
verdict id / verifier id once those are known.

- **HTTP transport** (`signoff-mcp --transport http`): logs go to
  stderr in the canonical Signoff format (timestamp + logger + level
  + message). Tail them with:

  ```sh
  signoff-mcp --transport http ... 2>&1 | tee signoff.log
  ```

- **stdio transport**: stderr is what the client reads; Claude Code
  stores it at
  `~/Library/Logs/Claude/mcp.log` (macOS) or
  `~/.config/claude/mcp.log` (Linux).
- **Inside Docker** (harness containerised): `docker logs` on the
  signoff-mcp container — all the server's log lines plus any
  structured log lines the harness emitted.

### Increase verbosity

```sh
SIGNOFF_CORE_LOG_LEVEL=DEBUG signoff-mcp ...
```

or per-namespace:

```sh
SIGNOFF_HTTP_VERIFY_TLS=true \
SIGNOFF_JUDGE_MAX_RETRIES=0 \
signoff-mcp --transport http ...
```

The second form is useful when debugging judge flakiness — retries
are silent by default, `MAX_RETRIES=0` surfaces every failure
immediately.

### Run a single verifier in isolation

The harness lets you enable exactly one verifier via config:

```yaml
deliverables:
  code_change:
    verifiers:
      signoff-code.tests_pass: { enabled: true }
      signoff-code.types_check: { enabled: false }
      signoff-code.lint_clean: { enabled: false }
      signoff-code.smoke_imports: { enabled: false }
      signoff-code.semantic_diff: { enabled: false }
```

Or import the verifier directly and call it against a constructed
`VerifierContext` — see `packages/signoff-code/tests/test_verifiers/`
for examples of the pattern.

### Inspect a container after the verifier ran

`DockerRuntime` destroys the sandbox container on verifier
completion by default. To keep it around for postmortem:

```sh
SIGNOFF_DOCKER_KEEP_ON_FAILURE=true signoff-mcp ...
```

Then `docker ps -a --filter label=signoff.harness=true` lists
every container the runtime spawned; `docker logs <id>` and
`docker exec -it <id> sh` give you a shell that's still the
non-root `signoff` user in a read-only rootfs — exactly the
environment the verifier saw.

---

## What to flag upstream

Good issues for the repo, in priority order:

1. The system-prompt text above caused your agent to misbehave in
   a way the doc didn't warn about.
2. A failure mode you hit that the troubleshooting section didn't
   name.
3. A verifier returned a verdict you disagreed with (especially
   `semantic_diff` — those judgements are exactly the thing we
   tune by looking at real cases).

Each of these is worth an issue tagged `area:dogfooding` even if
the fix is just a doc update.
