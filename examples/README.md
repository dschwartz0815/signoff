# Signoff example configs

Two configs you can copy to `signoff.yaml` and use as starting
points. Each is self-contained — none reference anything outside
`examples/`.

## [`minimal.yaml`](./minimal.yaml)

**What it demonstrates.** The plumbing works. No verifier packs
installed, every `request_signoff` call returns a trivially-passing
verdict. `FakeHttpClient` and `FakeJudge` keep the stack hermetic —
zero API keys, zero network, zero Docker socket exposure.

**When to use it.** First-time verification that the MCP server,
the harness, and your agent client are actually wired together.
`just dev` uses this file by default.

**How to run it.** Three terminal windows, one command each:

```sh
# Start the MCP server:
signoff-mcp --transport http --host 127.0.0.1 --port 8765 \
            --config examples/minimal.yaml

# Probe it:
curl -fsS http://127.0.0.1:8765/health
# {"status":"ok","harness":"ready","verifier_count":0}

# Point your MCP client at it (Claude Code / Cursor / …).
# Ask the agent to call `list_verifiers`; expect an empty list.
```

Time to first verdict on a clean machine with Python + Docker
already installed: **under 30 seconds**.

## [`code-change.yaml`](./code-change.yaml)

**What it demonstrates.** The full `signoff-code` pack running
against real code changes inside a Docker sandbox. Five verifiers
(pytest, mypy, ruff, smoke imports, semantic diff), Anthropic
judge for the semantic check, cosign verification turned off for
local dev.

**When to use it.** Any time you want verdicts that actually
reflect code quality. This is the config the [quickstart
walkthrough](../docs/quickstart.md) uses.

**How to run it.** Four commands:

```sh
# Pull the published sandbox image (once per machine — the example
# config's runtime_policy.docker.image points at this same tag):
docker pull ghcr.io/dschwartz0815/signoff/code-sandbox:latest

# Export an API key (for semantic_diff):
export SIGNOFF_JUDGE_API_KEY=sk-ant-...

# Start the server:
signoff-mcp --transport http --host 127.0.0.1 --port 8765 \
            --config examples/code-change.yaml

# (Optional) smoke-check from Python:
python - <<'PY'
import asyncio
from pathlib import Path

from signoff import Deliverable, Harness


async def main():
    Path("x.py").write_text("x = 1\n")
    d = Deliverable(
        id="dlv_smoke",
        kind="code_change",
        content={
            "intent": "Add trivial calculator.",
            "base": {"kind": "local_path", "value": str(Path.cwd())},
            "files": {"x.py": "x = 1\n"},
        },
    )
    async with await Harness.from_config_path("examples/code-change.yaml") as h:
        v = await h.verify(d, claims=[])
    print(v.id, "passed" if v.passed else "blocked")

asyncio.run(main())
PY
```

Time to first verdict on a clean machine with Python + Docker +
the sandbox image: **under 60 seconds** (the first run pays an
image-pull cost of roughly 20 seconds; subsequent runs are
cache-warm).

## Adding your own

Every field in these configs is documented in
[`docs/configuration.md`](../docs/configuration.md). Packs ship
default configs via the `signoff.pack_defaults` entry point, so
your own config only needs to override what you actually want
different — for most teams that's `runtime_policy.docker.image`
(point at your own published sandbox) and a stricter
`severity_override` on `lint_clean` or `semantic_diff`.
