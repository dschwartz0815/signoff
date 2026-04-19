# signoff-code

Coding-verifier pack for [Signoff](../../README.md). Five verifiers
that run against a proposed change to a Python codebase before the
agent declares "done":

- `tests_pass` — runs `pytest`.
- `types_check` — runs `mypy` on the change surface.
- `lint_clean` — runs `ruff check` on the change surface.
- `smoke_imports` — `import`s every changed module in isolation.
- `semantic_diff` — asks an LLM judge whether the diff matches the
  agent's stated intent.

All verifiers declare `runtime_required="docker"`. Install
[`signoff-runtime-docker`](../signoff-runtime-docker) for the
sandbox; `LocalRuntime` works for dev but surfaces a WARNING.

## Install

```sh
pip install signoff-code signoff-runtime-docker signoff-judge signoff-http

# Build the sandbox image locally (a published :latest tag will be
# available once the sandbox-images CI workflow fires on merge):
docker build -t signoff/code-sandbox:dev packages/signoff-code
```

## Quickstart

Copy [`examples/code-change.yaml`](../../examples/code-change.yaml)
to `signoff.yaml`, adjust the `runtime_policy.docker.image` tag,
and point your MCP client at the harness. See
[`docs/packs/signoff-code.md`](../../docs/packs/signoff-code.md)
for per-verifier semantics and known limitations.
