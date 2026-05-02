# Quickstart

Ten minutes from zero to a real verdict. By the end you'll have:

1. Signoff installed and verifying a synthetic code change locally.
2. An MCP client (Claude Code, Cursor, Cline, Zed, Continue) wired
   up so your agent can call `request_signoff` itself.
3. A working sense of what passing and failing verdicts look like.

Every command block in this guide is copy-pasteable against a clean
machine with the prerequisites below.

---

## 1. Prerequisites

| You need | Notes |
|----------|-------|
| **Python ≥ 3.11** | `python --version`. Python 3.12 is what CI builds against. |
| **Docker ≥ 24** | A reachable daemon at the default socket. The sandbox image is a small overlay on `python:3.12-slim-bookworm`. |
| **An LLM API key** (recommended) | `SIGNOFF_JUDGE_API_KEY=sk-ant-...` or `ANTHROPIC_API_KEY=sk-ant-...` enables the `semantic_diff` verifier. Omit to skip that one check — the other four run without. |
| `pip` or `uv` | Either is fine; examples below use `pip`. |

Optional:

- **`cosign`** — optional. The default is `SIGNOFF_DOCKER_VERIFY_SIGNATURES=auto`: `DockerRuntime` checks for `cosign` on `PATH` at startup and verifies the sandbox image signature when it's there, or logs a WARNING and proceeds without verification when it isn't. The quickstart works either way. Set `SIGNOFF_DOCKER_VERIFY_SIGNATURES=true` to require verification (and fail loudly when cosign is missing) — the right setting for production. See [`docs/runtimes.md`](./runtimes.md) § DockerRuntime for the full mode matrix.
- **`git`** — to clone the repo if you want to run against the shipped fixtures.

---

## 2. Install

```sh
pip install signoff-core signoff-mcp signoff-http signoff-judge \
            signoff-runtime-docker signoff-code
```

Pull the sandbox image (used by `DockerRuntime` to execute the
verifiers):

```sh
docker pull ghcr.io/dschwartz0815/signoff/code-sandbox:latest
docker tag  ghcr.io/dschwartz0815/signoff/code-sandbox:latest signoff/code-sandbox:dev
```

The `:dev` re-tag matches the tag the shipped example config uses
by default; you can change either end.

Set your judge key:

```sh
export SIGNOFF_JUDGE_API_KEY=sk-ant-...    # or: export ANTHROPIC_API_KEY=sk-ant-...
```

That's the whole install.

---

## 3. First verification

Save the starter config to `signoff.yaml`:

```sh
curl -fsSL https://raw.githubusercontent.com/dschwartz0815/signoff/main/examples/code-change.yaml \
     -o signoff.yaml
```

> Why `-fsSL` and not `-sL`: the `-f` flag makes curl exit non-zero on
> 4xx/5xx instead of silently saving the error-page HTML as your
> "config file"; `-S` keeps the error message visible after `-s`
> hushes progress. If the URL ever 404s, the failure is loud rather
> than producing a baffling pydantic error five commands later.

Then run this Python script — it builds a `CodeChangeDeliverable`
out of two tiny files, verifies it, and prints the verdict:

```python
# quickstart.py
import asyncio
from pathlib import Path

from signoff import Deliverable, Harness


async def main() -> None:
    # Write the source files to the current directory so the
    # ``local_path`` base reference below points at real content.
    # signoff-code copies (never symlinks) this tree into a temp
    # workspace, so the on-disk files are seed material — verifier
    # mutations can't leak back.
    Path("calculator.py").write_text(
        "def add(a: int, b: int) -> int:\n"
        "    return a + b\n"
    )
    Path("test_calculator.py").write_text(
        "from calculator import add\n"
        "def test_add() -> None:\n"
        "    assert add(2, 3) == 5\n"
    )

    # ``Deliverable.content`` is the protocol-level payload — a plain
    # dict that the registered ``code_change`` preparer validates into
    # a ``CodeChangeDeliverable``. Pass the dict directly; don't wrap
    # it in the model class (the harness does that for you, and a
    # double-wrap trips the preparer's "unexpected content type"
    # warning and skips workspace materialisation).
    deliverable = Deliverable(
        id="dlv_quickstart",
        kind="code_change",
        content={
            "intent": "Add a small calculator module with passing tests.",
            # ``local_path`` says "treat <value> as the pre-change
            # snapshot." For the quickstart that's the cwd we just
            # wrote the two files into. ``files`` then overlays the
            # final state — here it's identical to base, which is the
            # normal pattern when you've already written the change.
            "base": {"kind": "local_path", "value": str(Path.cwd())},
            "files": {
                "calculator.py": Path("calculator.py").read_text(),
                "test_calculator.py": Path("test_calculator.py").read_text(),
            },
        },
    )
    async with await Harness.from_config_path("signoff.yaml") as harness:
        verdict = await harness.verify(deliverable, claims=[])

    print(f"verdict id:   {verdict.id}")
    print(f"passed:       {verdict.passed}")
    print(f"duration_ms:  {verdict.duration_ms}")
    print(f"results:      {len(verdict.results)} verifiers ran")
    for r in verdict.results:
        flag = "✓" if r.passed else "✗"
        print(f"  {flag} {r.verifier:40}  {r.severity}")
    if verdict.feedback_packet:
        print("feedback:")
        # ``BlockerEntry.issue`` holds the verifier's failure reason;
        # ``suggested_repair`` is the actionable next step. Both are
        # populated for every blocker — that's the contract the
        # feedback-packet builder enforces.
        for b in verdict.feedback_packet.blockers:
            print(f"  BLOCKER {b.verifier}: {b.issue}")
            if b.suggested_repair:
                print(f"          suggest: {b.suggested_repair}")


asyncio.run(main())
```

Run it:

```sh
python quickstart.py
```

Expected output (elided for readability):

```
verdict id:   vrd_7Q2X3…
passed:       True
duration_ms:  4321
results:      5 verifiers ran
  ✓ signoff-code.smoke_imports             info
  ✓ signoff-code.tests_pass                info
  ✓ signoff-code.types_check               info
  ✓ signoff-code.lint_clean                info
  ✓ signoff-code.semantic_diff             info
```

Every ✓ means one verifier (all five, in this case) ran end-to-end:
pytest executed your `test_calculator.py`, mypy type-checked
`calculator.py`, ruff ran clean, `python -c 'import calculator'`
succeeded, and the judge agreed the diff matches the stated intent.

If a verifier line reads `skipped=True` in its evidence (e.g.
`semantic_diff` when you didn't set an API key) that's expected —
the pack's default config leaves those as WARNINGs anyway.

### If that didn't work

- **`signoff-code.semantic_diff` failed with "judge call failed"**.
  You don't have a valid API key. Unset `provider: anthropic` in
  your `signoff.yaml` (swap to `fake`) to run the quickstart
  without a judge.
- **`DockerRuntime infrastructure error`**. The sandbox image isn't
  present. Re-run the `docker pull` + `docker tag` step.
- **"Workspace not mountable"**. The harness can't reach the path
  it's trying to bind. On macOS, make sure `$TMPDIR` (which the
  runtime uses by default) is inside a Docker-shared directory
  under Settings → Resources → File sharing.

---

## 4. Wire it to your agent

The most interesting thing is not running the script above — it's
letting your agent call `request_signoff` itself before saying
"done". Start the MCP server in one terminal:

```sh
signoff-mcp --transport http --host 127.0.0.1 --port 8765 \
            --config ./signoff.yaml
```

Verify it's up:

```sh
curl -fsS http://127.0.0.1:8765/health
# {"status":"ok","harness":"ready","verifier_count":5}

curl -fsS http://127.0.0.1:8765/version
# {"protocol_version":"0.1","harness_version":"0.0.1","mcp_server_version":"0.0.1"}
```

### Claude Code

Add to `~/.claude/claude_desktop_config.json`:

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

Restart Claude. In the chat, type `/mcp` — you should see a
`signoff` server with tools `list_verifiers` and `request_signoff`.

### Cursor / Cline / Zed / Continue

Same three-line MCP entry — the shape differs per client but the
contents are identical. See [`docs/mcp-integration.md`](./mcp-integration.md)
for client-by-client JSON.

### The system-prompt addition

The agent needs to know *when* to call `request_signoff`. Add this
to your agent's system prompt (or `CLAUDE.md`, `.cursor/rules`,
etc. depending on the client):

```
Before claiming any code change is complete, you MUST call the
`signoff.request_signoff` tool with a `code_change` deliverable
describing your change. If the verdict is not `passed=true`, read
the feedback packet, address each blocker, and try again.
```

Ready-made system-prompt language for multiple clients lives in
[`docs/dogfooding.md`](./dogfooding.md).

---

## 5. Verify a real change

In a throwaway repo — e.g., a small Python project you have on
disk — ask your agent to do something concrete:

> "Add input validation to the `parse_config` function so it
> rejects non-dict inputs with a clear error. Call `request_signoff`
> when you think you're done."

What you should see:

- The agent writes the change.
- The agent calls `request_signoff` with a `code_change` deliverable
  whose `intent` paraphrases your ask and whose `files` map holds
  the new version of the touched file(s).
- The MCP server responds with a `Verdict` JSON object.
- If `passed=true`, the agent tells you it's done.
- If `passed=false`, the feedback packet lists each failing
  verifier with a `reason` + `suggested_repair`. A good agent will
  iterate on the change and call `request_signoff` again.

A **passing verdict** has no feedback packet (`feedback_packet: null`) and every `results[i].passed` is true:

```json
{
  "id": "vrd_3E1M1333PDWGZV9AYL58",
  "passed": true,
  "results": [
    { "verifier": "signoff-code.smoke_imports", "passed": true, ... },
    { "verifier": "signoff-code.tests_pass",    "passed": true, ... },
    ...
  ],
  "feedback_packet": null
}
```

A **failing verdict** for, say, a test-breaking change looks like:

```json
{
  "id": "vrd_...",
  "passed": false,
  "results": [
    { "verifier": "signoff-code.tests_pass", "passed": false, "severity": "blocker",
      "reason": "pytest failed (exit 1)",
      "suggestion": "First failing test: tests/test_config.py::test_none. Inspect stdout and fix.",
      "evidence": { "first_failing_node": "tests/test_config.py::test_none", ... }
    },
    { "verifier": "signoff-code.types_check", "passed": true, "severity": "info", ... },
    ...
  ],
  "feedback_packet": {
    "blockers": [
      { "verifier": "signoff-code.tests_pass",
        "claim_text": "",
        "reason": "pytest failed (exit 1)",
        "evidence_excerpt": "FAILED tests/test_config.py::test_none — AssertionError",
        "suggested_repair": "First failing test: tests/test_config.py::test_none. Inspect stdout and fix." }
    ],
    "warnings": [],
    "retry_budget_remaining": 2
  }
}
```

The feedback packet is the piece designed for the agent to read
back — it's the reason the full verdict lives in `evidence` but the
actionable summary lives at the top level.

---

## 6. Next steps

- **Drill into a specific verifier**: [`docs/packs/signoff-code.md`](./packs/signoff-code.md).
- **Write your own check**: [`docs/writing-a-verifier.md`](./writing-a-verifier.md).
- **Ship a new pack**: [`docs/writing-a-pack.md`](./writing-a-pack.md).
- **Full config reference**: [`docs/configuration.md`](./configuration.md).
- **How the pieces fit together**: [`docs/architecture.md`](./architecture.md).
- **Run it under DockerRuntime in production**: [`docs/deployment.md`](./deployment.md).

If the quickstart above got you stuck in a place this doc didn't
call out, that's a documentation bug — open an issue with
`area:quickstart` and we'll patch it.
