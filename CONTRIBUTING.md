# Contributing to Signoff

Thanks for your interest. This doc is the short version of the
rules of the road; [`CLAUDE.md`](./CLAUDE.md) is the long version
(and is the source of truth when the two disagree).

---

## The protocol is the spec

[`docs/protocol.md`](./docs/protocol.md) is normative. Any code
that disagrees with the protocol is a code bug, not a protocol
bug. PRs that change a MUST in the protocol update the protocol
doc *first* and then the Python + TypeScript implementations in
the same PR.

Corollary: issues framed as "the code does X, the protocol says Y,
which is right?" should always answer "the protocol" unless the
protocol doc has a bug of its own.

---

## How to propose a change

1. **Open an issue first** for anything bigger than a typo fix or
   a one-file bug fix. We'd rather talk about the design than
   re-do the PR.
2. **Fork and branch.** Branch names follow the Phase 0 / Phase 1
   convention: `feat/<concise-topic>`, `fix/<concise-topic>`,
   `docs/<concise-topic>`, `chore/<concise-topic>`.
3. **Keep PRs small and reviewable.** One concern per PR. Most of
   the PRs in the git history are 6–10 commits of <500 lines each
   — that's the target cadence.
4. **Tests alongside code.** A change without a test is
   incomplete. New verifier? Add the fixture. New runtime? Add
   the integration test. New protocol field? Add the parity test.
5. **Conventional commits, scoped by package.**
   `feat(core): …`, `fix(mcp): …`, `chore(runtime-docker): …`,
   `docs(quickstart): …`.

---

## Running the tests

```sh
# Prereqs: uv + pnpm + Docker.
just setup
just test                    # Python + TS + cross-language parity
just typecheck               # mypy --strict + tsc --strict
just lint                    # ruff + biome
```

Live-network and Docker-daemon tests are opt-in:

```sh
# Requires signoff/code-sandbox:dev built locally:
docker build -t signoff/code-sandbox:dev packages/signoff-code

uv run pytest -m docker       # Docker-daemon integration tests
uv run pytest -m live         # Provider API calls (needs keys)
```

The default `pytest` run deselects both via
`addopts = -m "not live and not docker"`.

---

## Coding conventions

Short-form (see [`CLAUDE.md`](./CLAUDE.md) §7 for the long form):

- **`signoff-core` stays transport-agnostic.** No imports of
  `mcp`, `fastapi`, `httpx`, `docker`, or framework SDKs.
- **Async everywhere in execution paths.** Verifiers are
  `async def`. Blocking I/O inside a verifier is a bug.
- **Evidence is non-negotiable.** Every `VerifierResult` carries
  `evidence`. This is what makes the audit log useful.
- **Cost-aware by default.** Every verifier declares a
  `cost_tier`; every judge call records its cost.
- **Protocol consistency across languages.** Python and TS types
  MUST be structurally equivalent per `docs/protocol.md`.
- **`mypy --strict` and `tsc --strict` pass.** Type ignores need
  a comment and a tracking issue.
- **Verifiers use `ctx.exec`, not `subprocess.run`.** Runtimes
  can't sandbox what they can't see.

Commit messages:

- Lead with a one-line summary in conventional-commit form.
- Follow with a paragraph explaining *why*. Shipped PRs in the
  git log are templates.
- Include a verification block when the change touches code:
  "Verification: uv run pytest → N passed, uv run mypy → clean."
- Don't include secrets or placeholder-looking tokens. `.env.example`
  files only.

---

## Pack authorship

Shipping a pack is a first-class use case. The pattern:

1. `packages/signoff-your-pack/` — `pyproject.toml` + `Dockerfile`
   (if your verifiers need sandboxing) + `src/signoff_your_pack/`
   + `tests/`.
2. Verifiers defined with the `@verifier(...)` decorator, one
   per module, registered via
   `[project.entry-points."signoff.verifiers"]`.
3. Opinionated defaults in
   `src/signoff_your_pack/default_config.yaml`, registered via
   `[project.entry-points."signoff.pack_defaults"]`.
4. A regression suite in `tests/regression/` — at least 20
   realistic claim + ground-truth pairs per verifier. Target:
   no release with a regression.

Full walk-through: [`docs/writing-a-pack.md`](./docs/writing-a-pack.md).

---

## Maintainer one-time setup

### Making published images public

Images pushed to GHCR by the `publish-sandbox-images` workflow are
created **private by default** the first time GitHub sees them. The
workflow doesn't have permission to flip them to public; that's a
manual step in the package settings UI:

1. Visit `https://github.com/<owner>?tab=packages` (e.g.
   `https://github.com/dschwartz0815?tab=packages`).
2. Click each newly-published `signoff/*-sandbox` package.
3. **Package settings** → **Change visibility** → **Public**.

Repeat once per package per repo owner. After the toggle, the
`docker pull` commands in the user-facing docs will work without
authentication.

If you skip this step, the published `:latest` tags exist but a
fresh `docker pull` returns `denied: requested access to the
resource is denied` instead of the image. That's the visibility
gate, not a workflow bug.

## Reporting bugs

Good bug reports include:

- What you did.
- What you expected.
- What actually happened (stdout / stderr / verdict JSON when
  relevant).
- Your environment: `just --version`, `docker version`,
  `python --version`, `uv --version`.
- Whether you're running against `LocalRuntime` or
  `DockerRuntime`, and which sandbox image tag.

Security issues: see [`SECURITY.md`](./SECURITY.md).

---

## Reporting prompt-engineering issues

`signoff-judge`'s prompt files are checked in and versioned. A
verdict you disagreed with — especially a `semantic_diff`
`contradicted` or `not_addressed` label that felt wrong — is a
useful signal. File an issue tagged `area:prompts` with:

- The `intent` you gave and a lightly-redacted diff.
- The verdict's `evidence.label`, `.explanation`, `.excerpt`,
  `.model`, and `.prompt_version`.
- Why you think it was wrong.

We use those to tune prompts and to decide when a dedicated
`semantic_diff.md` needs to replace the reused `entailment.md`.

---

## Code of conduct

By participating in this project you agree to abide by the
[Contributor Covenant](./CODE_OF_CONDUCT.md).
