# Signoff

**Agents make claims. Signoff makes them prove it.**

[![Demo: passing verdict](https://asciinema.org/a/UPyJk0xbUX56Ojlk.svg)](https://asciinema.org/a/UPyJk0xbUX56Ojlk) · [![Demo: catching a broken claim](https://asciinema.org/a/dJdZuIAmOpkrEb91.svg)](https://asciinema.org/a/dJdZuIAmOpkrEb91)

AI coding agents generate code faster than any process can review. Post-hoc observability tells you something broke; tests catch what you remembered to write. Signoff sits between an agent and its "done" claim, runs pluggable verifiers against the deliverable, and returns either a pass or a structured feedback packet the agent can retry against — before any human sees the output.

## Thirty-second example

Build a code-change deliverable, hand it to the harness, inspect the verdict.

```python
import asyncio
from signoff import Deliverable, Harness
from signoff_code import CodeChangeDeliverable

async def main() -> None:
    deliverable = Deliverable(
        id="dlv_1",
        kind="code_change",
        content=CodeChangeDeliverable(
            intent="Add null check to parse_config",
            files={
                "config.py": "def parse_config(x):\n    if x is None:\n        return {}\n    return x\n",
                "test_config.py": "from config import parse_config\ndef test_none():\n    assert parse_config(None) == {}\n",
            },
        ),
    )
    async with await Harness.from_config_path("examples/code-change.yaml") as h:
        verdict = await h.verify(deliverable, claims=[])
    print("passed" if verdict.passed else "blocked", "—", verdict.id)

asyncio.run(main())
```

The harness runs `pytest`, `mypy`, `ruff`, a changed-module import smoke, and an LLM judge (asking whether the diff matches the stated intent) — every verifier inside a read-only, no-network, non-root Docker sandbox. Failures come back as a [`FeedbackPacket`](./docs/protocol.md#35-feedbackpacket) the agent can read and fix against.

## Wire it to your agent

Any [MCP](https://modelcontextprotocol.io/) client — Claude Code, Cursor, Cline, Zed, Continue — registers the server in three lines:

```json
{
  "mcpServers": {
    "signoff": { "command": "signoff-mcp", "args": ["--config", "./signoff.yaml"] }
  }
}
```

The agent gets two tools: `list_verifiers` (so it knows what will check it) and `request_signoff` (so it asks before declaring done). Concrete client-by-client setup in [`docs/mcp-integration.md`](./docs/mcp-integration.md). A ready-made agent system-prompt addition lives in [`docs/dogfooding.md`](./docs/dogfooding.md).

## What it catches

Real failure modes from the `signoff-code` pack's fixture suite:

- **The diff doesn't do what the agent said.** Agent: *"Added null check to parse_config."* The diff actually catches the AttributeError and swallows it — `semantic_diff`'s LLM judge flags that the excerpt in the diff doesn't support the stated claim.
- **Tests pass locally, import breaks in prod.** Agent adds a helper module referenced only behind a feature flag; `pytest` never imports it. `smoke_imports` runs `python -c 'import <module>'` against each changed file and catches the missing dependency before an agent ever ships.
- **Refactor subtly changes a return type.** `mypy` on the changed surface alone catches the `dict[str, Any]` → `Mapping[str, object]` shift that the tests didn't exercise. The feedback packet names the file + line so the agent fixes without a round-trip through a human.
- **"Fixed the test"** by deleting it. `tests_pass` flags the pytest "collected 0 items" case as a WARNING, so an operator can notice a change that quietly dropped coverage.

Walk-throughs with real inputs and verdicts in [`docs/gallery.md`](./docs/gallery.md).

## Installation

```sh
pip install signoff-core signoff-mcp signoff-http signoff-judge \
            signoff-runtime-docker signoff-code
docker pull ghcr.io/dschwartz0815/signoff/code-sandbox:latest
export SIGNOFF_JUDGE_API_KEY=sk-ant-...      # or OPENAI_API_KEY
```

A working config takes one `cp examples/code-change.yaml signoff.yaml`. Full walk-through: [`docs/quickstart.md`](./docs/quickstart.md) — under ten minutes, Docker + one API key required.

> **PyPI status.** The packages listed above are published as of this release; the Docker image is built, signed with cosign, and scanned with trivy by [`.github/workflows/publish-sandbox-images.yml`](./.github/workflows/publish-sandbox-images.yml) on every push to `main`. Install-from-source is always available (`git clone && uv sync`).

## Status

**Phase 1, first verifier pack shipping.**

| Works today | Coming |
|-------------|--------|
| Verification of Python code changes (`signoff-code`) | Research-report / citation pack (`signoff-research`) |
| Local library + MCP server + Docker runtime | TypeScript verifier pack |
| Anthropic + OpenAI judge providers | Hosted service with audit log (Phase 2) |
| Cosign-signed sandbox images, trivy-gated releases | Firecracker / Kubernetes-Job runtimes |

The protocol (`docs/protocol.md`) is the source of truth. Implementations of the protocol are versioned independently and follow semver; the protocol itself is at `0.1` and is expected to stay there through the Phase 2 hosted alpha.

## How it works

The harness is one short pipeline:

```
Deliverable + Claims ──▶ Resolve (config + registry)
                           └─▶ Schedule verifier runs (budget + concurrency)
                                 └─▶ Execute (LocalRuntime / DockerRuntime)
                                       └─▶ Aggregate VerifierResults
                                             └─▶ Verdict (+ FeedbackPacket if failing)
```

Each verifier is an `async` function declared with a `@verifier(name=..., claim_kinds=..., cost_tier=..., runtime_required=...)` decorator, registered via a Python entry point, and given an execution context (`ctx.exec`, `ctx.http`, `ctx.judge`, `ctx.workspace`). Runtimes decide where that execution happens — in-process for trusted checks, inside a fresh cap-dropped container for untrusted ones. Packs are pip-installable bundles of verifiers + their default config. Architecture deep-dive: [`docs/architecture.md`](./docs/architecture.md).

## Why?

**LLMs generate code faster than ever; verification is the bottleneck.** Every additional unsupervised agent output is a liability that grows with usage. Post-hoc observability (logs, traces, eval suites) tells you something broke after the fact; it's the wrong place in the loop. The right place is before the agent declares "done."

Signoff is that gate. It's an MCP server — so any agent can call it — backed by a library with a typed protocol, a pluggable runtime, and a prompt-injection-resistant LLM-judge layer for checks that aren't purely mechanical. Each verdict comes with structured evidence: an audit log that ties every pass or fail back to a specific verifier run, a specific model + prompt version, and a specific sandbox container.

## Packages

| Path | Purpose |
|------|---------|
| [`packages/signoff-core`](./packages/signoff-core) | Engine: models, harness, runtime protocol, registry. |
| [`packages/signoff-mcp`](./packages/signoff-mcp) | MCP server adapter. |
| [`packages/signoff-http`](./packages/signoff-http) | Real `httpx`-backed HTTP client ([docs](./docs/http-client.md)). |
| [`packages/signoff-judge`](./packages/signoff-judge) | Anthropic + OpenAI LLM judges ([docs](./docs/judge-client.md)). |
| [`packages/signoff-runtime-docker`](./packages/signoff-runtime-docker) | Docker sandbox runtime ([docs](./docs/runtimes.md)). |
| [`packages/signoff-code`](./packages/signoff-code) | Python code-change verifier pack — five verifiers ([docs](./docs/packs/signoff-code.md)). |
| [`packages/signoff-sdk-ts`](./packages/signoff-sdk-ts) | TypeScript client for the hosted API (Phase 2). |

## Docs

- [**Quickstart**](./docs/quickstart.md) — ten-minute walkthrough.
- [**Architecture**](./docs/architecture.md) — how the pieces fit.
- [**Protocol specification**](./docs/protocol.md) — the normative contract.
- [**What it catches**](./docs/gallery.md) — real examples.
- [**Dogfooding**](./docs/dogfooding.md) — system-prompt addition + troubleshooting.
- [**Writing a verifier**](./docs/writing-a-verifier.md) — author your own check.
- [**Writing a pack**](./docs/writing-a-pack.md) — ship verifiers as a package.
- [**Configuration**](./docs/configuration.md) — YAML schema + `SIGNOFF_*` env vars.
- [**Deployment**](./docs/deployment.md) — Docker socket patterns and tradeoffs.

## Contributing

See [`CONTRIBUTING.md`](./CONTRIBUTING.md). The short version: [`docs/protocol.md`](./docs/protocol.md) is the source of truth for design decisions; code disagreeing with the protocol is a code bug. Security issues: [`SECURITY.md`](./SECURITY.md). Conduct: [`CODE_OF_CONDUCT.md`](./CODE_OF_CONDUCT.md).

## License

Apache 2.0. See [`LICENSE`](./LICENSE).
