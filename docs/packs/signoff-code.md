# `signoff-code` — Python code-change verifier pack

Five verifiers that run against a proposed change to a Python
codebase. Designed to be the pack an agent talks to before declaring
"I'm done editing."

- `tests_pass` — runs `pytest` against the materialised workspace.
- `types_check` — runs `mypy` against the change surface.
- `lint_clean` — runs `ruff check` against the change surface.
- `smoke_imports` — `import`s every changed module in isolation.
- `semantic_diff` — asks an LLM judge whether the diff plausibly
  does what the agent said it would.

All five declare `runtime_required="docker"` — they execute
untrusted content and need the [DockerRuntime](../runtimes.md)
sandbox in production. Running under `LocalRuntime` is supported
for dev but surfaces a WARNING.

---

## The deliverable shape

`signoff-code` consumes `Deliverable(kind="code_change",
content=<CodeChangeDeliverable>)`. The model:

```python
from signoff_code import CodeChangeDeliverable, BaseReference

CodeChangeDeliverable(
    intent="Add input validation to parse_config",
    base=BaseReference(kind="local_path", value="/repo/project"),
    diff="--- a/config.py\n+++ b/config.py\n@@ ... @@\n+...",
    # ... or: files={"config.py": "new content ..."},
)
```

Exactly one of `diff` or `files` must be set; `intent` drives the
`semantic_diff` verifier and is ignored by the deterministic ones.
Paths in `files` must be relative and may not contain `..`.

`BaseReference.kind` is one of `git_sha`, `tarball_url`, or
`local_path`. In Phase 1, only `local_path` materialises fully —
`git_sha` and `tarball_url` raise a clear placeholder error (the
follow-up tracks in [docs/http-client.md](../http-client.md) for
the bytes API that `tarball_url` needs).

---

## Verifier semantics

### `tests_pass`

Runs `python -m pytest -q --tb=short` in the workspace.

| pytest exit | result |
|-------------|--------|
| 0 | OK, evidence carries parsed pass/fail/error/skip counts |
| 1 or 2 | BLOCKER; suggestion names the first `FAILED nodeid` |
| 5 (no tests collected) | WARNING — a docs-only change shouldn't fail the deliverable |
| other | INFO (infra failure per protocol §4.4) |

### `types_check`

Runs `python -m mypy --no-error-summary --show-error-codes
--follow-imports=silent <changed .py files>`. Only the change
surface is type-checked by default so full-repo typing doesn't
blow the timeout; `--follow-imports=silent` keeps unchanged
neighbours out of the output.

| mypy exit | result |
|-----------|--------|
| 0 | OK |
| 1 with parsed errors | BLOCKER, suggestion lists the first 3, evidence lists them all |
| other | INFO |

### `lint_clean`

Runs `python -m ruff check --no-fix --output-format=json`. Default
severity is **WARNING**, not BLOCKER — a stylistic nit shouldn't
fail a deliverable. Operators promote via
`severity_override: blocker` in config.

### `smoke_imports`

For each changed `.py`, runs `python -c 'import <dotted.name>'`
with `PYTHONPATH=.`. First failure is a BLOCKER with the
traceback tail in evidence. `src/pkg/x.py` maps to `pkg.x`;
`pkg/__init__.py` maps to `pkg`.

### `semantic_diff`

Asks `ctx.judge.check_entailment(claim=intent, passage=diff)`.

- `"supported"` → OK.
- `"contradicted"` → WARNING with the judge's explanation.
- `"not_addressed"` → WARNING.
- Empty / short (< 10 chars) intent → OK with `skipped=True`.
- Diff over 2000 lines → WARNING with `skipped=True`.

Uses the general-purpose `entailment` prompt from
[`signoff-judge`](../../packages/signoff-judge) — no dedicated
prompt file yet. If live tuning shows the citation-tuned prompt
underperforms on diffs, a dedicated `semantic_diff.md` is a
follow-up.

---

## Configuration

The pack ships an opinionated `default_config.yaml` via the
`signoff.pack_defaults` entry point. Installing the pack enables
every verifier on `code_change` deliverables automatically; you
only write YAML for what you want to change.

Minimal override for strict mode:

```yaml
deliverables:
  code_change:
    verifiers:
      signoff-code.lint_clean:
        severity_override: blocker     # default is warning
      signoff-code.semantic_diff:
        severity_override: blocker     # default is warning
```

A fully-wired example with `DockerRuntime` + a real judge lives at
[`examples/code-change.yaml`](../../examples/code-change.yaml).

### Scoping to the change surface

The deterministic verifiers (types / lint / smoke) only look at
`CodeChangeDeliverable.changed_paths`. This is populated
automatically from `files.keys()` or the diff headers; pass it
explicitly to override.

---

## The sandbox image

`ghcr.io/signoff/code-sandbox:latest` ships with pinned pytest,
mypy, ruff, coverage, plus git and patch. Non-root signoff user
(UID 10001), `/workspace` as the bind-mount target, `sleep
infinity` as the holder. See
[`packages/signoff-code/Dockerfile`](../../packages/signoff-code/Dockerfile)
for the recipe and regeneration instructions.

Locally-built variant for dev:

```sh
docker build -t signoff/code-sandbox:dev packages/signoff-code
```

Published variants are signed with `cosign` and scanned with
`trivy` via the [publish-sandbox-images workflow](
../../.github/workflows/publish-sandbox-images.yml); the
`DockerRuntime` cosign gate verifies the signature on every pull
in production deployments.

---

## Known limitations

- Python-only. TypeScript and other-language packs are separate
  follow-ups.
- `git_sha` / `tarball_url` bases are placeholders that raise.
  Use `local_path` with a pre-seeded checkout for now.
- No coverage verifiers yet — interesting but a separate concern.
- No per-file claim scoping — verifiers operate on the whole
  deliverable.
- Tests that need extra Python dependencies require extending the
  sandbox image: write a small Dockerfile that
  `FROM ghcr.io/signoff/code-sandbox:latest`, `RUN pip install
  your-deps`, publish to your own registry, and set
  `runtime_policy.docker.image` to that tag.

---

## See also

- [`docs/runtimes.md`](../runtimes.md) — Runtime protocol and
  DockerRuntime safety posture.
- [`docs/judge-client.md`](../judge-client.md) — Judge providers
  and API-key precedence for `semantic_diff`.
- [`docs/deployment.md`](../deployment.md) — Running the MCP server
  with `DockerRuntime` (socket mount / dind / worker nodes).
