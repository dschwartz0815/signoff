# CLAUDE.md

This file gives Claude Code the context it needs to work on this repository productively. Read it fully before making changes. The companion documents are `PROPOSAL.md` (the why) and `docs/protocol.md` (the normative contract). When code disagrees with `docs/protocol.md`, the protocol doc wins.

---

## 1. What this project is

**Signoff** is a verification layer for AI agents. It sits between an agent and its "done" claim, runs pluggable verifiers against the deliverable, and returns either a pass or a structured feedback packet the agent can retry against.

The product ships along multiple axes from one core engine:

- **Library (Python)** — `pip install signoff`, embed in any agent.
- **Library (TypeScript)** — `npm install @signoff/sdk`, for TS/JS agent stacks and for clients of the hosted API.
- **MCP server** — `signoff serve --mcp`, usable by any MCP client.
- **Hosted service** — managed cloud for teams that need scale, audit, and compliance.
- **Docker images** — official images for the MCP server and every supporting service.

---

## 2. The four core primitives

Every change in this codebase composes these four concepts. Get these right and the rest follows.

- **Deliverable** — what the agent submitted. Has an `id`, a `kind` (e.g. `"research_report"`, `"pr_diff"`), opaque `content`, and `metadata`.
- **Claim** — an asserted fact, citation, computation, or policy statement embedded in a deliverable.
- **Verifier** — a pluggable async function that checks claims (or whole deliverables) and returns a `VerifierResult`.
- **Pack** — a versioned pip-installable bundle of verifiers, prompts, and default config for a domain.

The orchestrator is the **Harness**. Verifiers run inside a **Runtime** (see §8). Full normative definitions live in `docs/protocol.md`.

---

## 3. Repository layout

This is a polyglot monorepo with Python and TypeScript packages side-by-side, plus the hosted service and infrastructure.

```
signoff/
├── PROPOSAL.md                  Product proposal.
├── CLAUDE.md                    This file.
├── README.md
├── LICENSE                      Apache 2.0.
├── justfile                     Cross-toolchain command runner.
├── docker-compose.yml           Local dev stack (OSS only).
├── docker-compose.cloud.yml     Cloud services for local dev (Postgres, Redis, API, workers, dashboard).
├── .github/workflows/           Matrix CI (Python, TypeScript, Docker).
├── docs/
│   ├── protocol.md              Normative protocol spec.
│   ├── writing-a-verifier.md
│   ├── writing-a-pack.md
│   ├── runtimes.md              Runtime abstraction and Docker guide.
│   ├── mcp-integration.md
│   └── deployment.md            Docker and self-hosting guide.
├── packages/
│   ├── signoff-core/            Core engine. No transport knowledge. No Docker knowledge.
│   │   ├── pyproject.toml
│   │   ├── Dockerfile           For CI verification and downstream consumers.
│   │   └── src/signoff/
│   │       ├── __init__.py
│   │       ├── models.py        Deliverable, Claim, VerifierResult, Verdict.
│   │       ├── verifier.py      @verifier decorator, Verifier protocol.
│   │       ├── harness.py       Orchestration, concurrency, budgeting.
│   │       ├── feedback.py      Feedback packet builder.
│   │       ├── config.py        YAML config loader.
│   │       ├── registry.py      Entry-point plugin discovery.
│   │       ├── context.py       Verifier execution context.
│   │       ├── runtime/         Runtime abstraction.
│   │       │   ├── base.py        Runtime protocol + RuntimePolicy.
│   │       │   └── local.py       LocalRuntime (default, in-process).
│   │       ├── schemas/         JSON schemas for protocol types.
│   │       └── exceptions.py
│   │   └── tests/
│   ├── signoff-mcp/             MCP server adapter. ~300 LOC.
│   │   ├── pyproject.toml
│   │   ├── Dockerfile           Published as ghcr.io/signoff/mcp.
│   │   └── src/signoff_mcp/
│   │       ├── __init__.py
│   │       ├── server.py        Exposes harness as MCP tools.
│   │       └── __main__.py
│   │   └── tests/
│   ├── signoff-runtime-docker/  Docker runtime for sandboxed execution.
│   │   ├── pyproject.toml
│   │   └── src/signoff/runtime_docker/
│   │       ├── __init__.py
│   │       ├── runtime.py       DockerRuntime implementation.
│   │       ├── images.py        Image management, caching, cosign verification.
│   │       └── policy.py        Resource limit enforcement.
│   │   └── tests/
│   ├── signoff-code/            First verifier pack. Coding wedge.
│   │   ├── pyproject.toml
│   │   ├── Dockerfile           Image used by DockerRuntime for isolated test execution.
│   │   └── src/signoff_code/
│   │       ├── __init__.py
│   │       ├── verifiers/
│   │       │   ├── tests.py
│   │       │   ├── types.py
│   │       │   ├── lint.py
│   │       │   ├── smoke.py
│   │       │   └── semantic_diff.py
│   │       └── prompts/
│   │   └── tests/
│   └── signoff-sdk-ts/          TypeScript client for the hosted API.
│       ├── package.json
│       ├── tsconfig.json
│       ├── vitest.config.ts
│       └── src/
│       │   ├── index.ts
│       │   ├── client.ts          HTTP client for signoff-cloud API.
│       │   ├── models.ts          Mirrors protocol types from docs/protocol.md.
│       │   ├── mcp.ts             Optional MCP client helper.
│       │   └── schemas/           JSON schemas shared with Python core.
│       └── tests/
├── cloud/                       Hosted service (see §6 for split policy).
│   ├── api/                       FastAPI app.
│   │   └── Dockerfile
│   ├── workers/                   Distributed verifier execution.
│   │   └── Dockerfile
│   ├── dashboard/                 Next.js team UI.
│   │   └── Dockerfile
│   ├── billing/                   Stripe integration.
│   ├── audit/                     Tamper-evident log.
│   └── infra/                     Terraform / Pulumi.
├── scripts/                     Dev scripts (dogfood runners, release, etc.).
└── examples/                    Example configs and agent integrations.
```

The TypeScript SDK ships in this repo because protocol-level changes benefit from being a single atomic PR across both language implementations.

Docker assets are a first-class concern and appear throughout the repo — see §8 and §9.

---

## 4. Tech stack and why

### Python packages

| Area | Choice | Reason |
|------|--------|--------|
| Language | Python 3.11+ | Widest agent/ML ecosystem, strong typing in 3.11+. |
| Package manager | `uv` | Fast, workspace-aware, modern. Replaces Poetry. |
| Data models | Pydantic v2 | Typed validation, fast, JSON serialization. |
| Concurrency | `asyncio` throughout | Verifiers run concurrently; I/O-bound. |
| Plugin discovery | `importlib.metadata` entry points | Standard Python, no special tooling. |
| Config | YAML via `pydantic-settings` + `pyyaml` | Familiar, typed loading. |
| MCP SDK | `mcp` (official Python SDK) | First-party protocol support. |
| Docker SDK | `docker` (Python SDK) | Only in `signoff-runtime-docker`. NOT in core. |
| Testing | `pytest`, `pytest-asyncio`, `pytest-cov` | Standard. |
| Lint/format | `ruff` | Fast, combines linting and formatting. |
| Type checking | `mypy --strict` | We are library authors; strict typing is the standard. |

### TypeScript package

| Area | Choice | Reason |
|------|--------|--------|
| Language | TypeScript 5.4+ with ESM | Modern, tree-shakeable. |
| Package manager | `pnpm` | Fast, efficient disk usage, workspace support. |
| Build | `tsup` | Simple, fast, dual ESM/CJS output. |
| Testing | `vitest` | Fast, first-class TS, matches pytest ergonomics. |
| Lint/format | `biome` | Single tool replacing eslint + prettier. |
| HTTP client | Native `fetch` with `ofetch` wrapper | Minimal dependency surface. |
| Validation | `zod` | Schemas mirror Python Pydantic models. |

### Docker

| Area | Choice | Reason |
|------|--------|--------|
| Base image (Python) | `python:3.12-slim-bookworm` | Small, current, well-supported. |
| Base image (Node) | `node:22-slim` | LTS, slim. |
| Multi-stage builds | Always | Separate build dependencies from runtime. |
| Registry | GitHub Container Registry (`ghcr.io/signoff/*`) | Free for public OSS, good Actions integration. |
| Compose | Docker Compose v2 | Standard tool. |
| Signing | `cosign` on release | Supply-chain integrity. |
| Scanning | `trivy` in CI | CRITICAL vulnerabilities block release. |

### Cross-toolchain orchestration

`just` (https://github.com/casey/just) runs commands across Python and TypeScript. One `justfile` at repo root. Never assume a contributor has both toolchains installed — Docker Compose is the universal fallback for running the full stack.

---

## 5. Development setup

Every PR must pass: `just ci`. This runs all language-specific checks plus image builds.

### Prerequisites

Contributors need at minimum one of:
- **Full local:** `uv`, `pnpm`, Docker
- **Docker-only:** Docker + `just` (all commands route through containers)

### Common commands

```bash
# One-time setup (local toolchains)
just setup                  # Installs uv and pnpm dependencies.
just setup-docker           # Pulls/builds required images.

# Test everything
just test                   # Python + TS + integration + parity.

# Test one toolchain
just test-py                # uv run pytest across all Python packages.
just test-ts                # pnpm -r test across all TS packages.

# Test one package
just test-py-core           # uv run pytest packages/signoff-core.
just test-ts-sdk            # pnpm --filter @signoff/sdk test.

# Type check
just typecheck              # mypy --strict + tsc --noEmit.

# Lint and format
just lint                   # ruff check + biome check.
just fmt                    # ruff format + biome format.

# Run the MCP server locally (Python, no Docker)
just mcp                    # uv run python -m signoff_mcp.

# Run the MCP server via Docker
just mcp-docker             # docker run ghcr.io/signoff/mcp:dev.

# Spin up the full local dev stack
just dev                    # docker compose up with hot reload.

# Run cloud services locally
just cloud-dev              # API + workers + Postgres + Redis + dashboard.

# Build all Docker images locally
just build-images

# Full CI simulation
just ci                     # Everything CI runs: tests, types, lint, image builds, scans.
```

### docker-compose.yml

The root `docker-compose.yml` spins up a minimal Signoff stack for OSS users:

- `signoff-mcp` — the MCP server on port 8765
- `signoff-code-sandbox` — a pre-pulled image used by `DockerRuntime` for isolated verifier execution

The optional `docker-compose.cloud.yml` adds cloud services:
- `cloud-api` — FastAPI service
- `cloud-worker` — Celery/Dramatiq workers
- `cloud-dashboard` — Next.js frontend
- `postgres` — Audit log storage
- `redis` — Rate limiting, caching

Contributors run `docker compose up` for OSS-only work, or `docker compose -f docker-compose.yml -f docker-compose.cloud.yml up` for cloud work. `just cloud-dev` wraps the latter.

---

## 6. Cloud directory and split policy

`cloud/` lives in this repo during Phase 0 through Phase 2. This is pragmatic, not permanent.

**Why it's here now:** Solo-dev monorepo overhead is real. Cross-cutting protocol changes that touch core + cloud are one PR instead of coordinated releases. Claude Code can reason about the whole system in one context.

**When it splits:** When any of these happen — (a) first paying customer, (b) first non-founder contributor needing cloud access, (c) secrets that genuinely cannot live in a public repo — `cloud/` gets extracted to a private `signoff/cloud` repo via `git filter-repo --path cloud/`. At that point the cloud repo depends on the OSS packages via published artifacts (PyPI for Python, npm for `@signoff/sdk`).

**Discipline today so the split is painless tomorrow:**

- Treat the boundary between `packages/` and `cloud/` as if it were already two repos.
- `cloud/*` MAY import from `packages/signoff-core` and friends, but only via their public API. No reaching into private modules.
- `packages/*` MUST NOT import anything from `cloud/`. Ever. The OSS packages must be usable without the cloud directory existing.
- Every `cloud/` PR should be reviewable as if it were going into the private repo — no inline references to internal infra from `packages/`, no OSS-breaking changes that "we'll fix on the cloud side."
- Secrets and credentials NEVER land in this repo, even under `cloud/`. Use `.env.example` files with placeholder values; real secrets live in GitHub Environments, AWS Secrets Manager, or equivalent.

This discipline is what makes the eventual split a weekend of `git filter-repo` work instead of a months-long refactor.

---

## 7. Design conventions

These are non-negotiable unless the user explicitly overrides them:

**Core stays transport-agnostic.** `signoff-core` imports nothing from `mcp`, `fastapi`, `httpx` (for transport), `docker`, or any particular agent framework. It exposes plain Python types and an async API. Transport and runtime adapters are separate packages.

**Core stays runtime-agnostic.** `signoff-core` defines the `Runtime` protocol and ships `LocalRuntime`. It does not depend on Docker, Firecracker, or any isolation technology. Users opt into `signoff-runtime-docker` when they need sandboxing.

**Verifiers are pure and declarative.** A verifier declares what claim kinds it handles, its cost tier, and concurrency limit. It receives a `Claim` and a `VerifierContext` and returns a `VerifierResult`. No global state, no side effects outside the result.

**Feedback packets are for agents, not humans.** When a verdict fails, the feedback packet is structured JSON designed to be dropped back into the agent loop as a tool result. Pretty-printing for humans is a separate rendering concern.

**Every verifier result carries evidence.** Whatever the verifier looked at goes into `VerifierResult.evidence`. Non-negotiable; this is what makes the audit log valuable.

**Async everywhere in execution paths.** Verifiers are `async def`. Blocking I/O inside a verifier is a bug.

**Cost-aware by default.** Every verifier declares a `cost_tier`. The harness respects a configured budget.

**Protocol consistency across languages.** Python and TypeScript types MUST be structurally equivalent per `docs/protocol.md`. When a protocol change lands, both language implementations ship in the same PR.

**Strict typing.** `mypy --strict` passes on all Python code. `tsc --strict` passes on TS code.

**Docker images are minimal, multi-stage, non-root.** See §9.

---

## 8. Runtimes (how verifiers actually execute)

A `Runtime` is the abstraction for where and how a verifier's work runs. Core defines the protocol; runtime packages implement it. This is critical because verifiers for `signoff-code` (and similar packs) execute untrusted content — running tests, linters, arbitrary commands — and cannot safely do that in the harness's own process.

### 8.1 The Runtime protocol

```python
# packages/signoff-core/src/signoff/runtime/base.py
from typing import Protocol, runtime_checkable, Callable, Awaitable

@runtime_checkable
class Runtime(Protocol):
    """Defines where and how a verifier's work executes."""

    async def execute(
        self,
        fn: Callable[..., Awaitable[VerifierResult]],
        *,
        claim: Claim,
        ctx: VerifierContext,
        policy: RuntimePolicy,
    ) -> VerifierResult:
        """Run `fn(claim, ctx)` under the runtime's isolation model."""
        ...

    async def prepare(self, verifier_meta: VerifierMeta) -> None:
        """Called once per verifier registration (pull images, warm pools, etc.)."""
        ...

    async def teardown(self) -> None:
        """Called on harness shutdown."""
        ...
```

A `RuntimePolicy` declares resource limits (CPU, memory, disk, network, timeout); the runtime enforces them.

### 8.2 Shipped runtimes

- **`LocalRuntime`** (in `signoff-core`) — runs the verifier in the harness's own process. No isolation. Default for trusted contexts. Zero dependencies.
- **`DockerRuntime`** (in `signoff-runtime-docker`) — spawns an ephemeral container per verifier execution, mounts the deliverable's workspace read-only, enforces `RuntimePolicy`. Recommended for `signoff-code` and any pack that executes untrusted content.

Future runtimes (Firecracker, Wasm, Kubernetes Jobs) slot into the same protocol as separate packages.

### 8.3 Runtime selection

Users pick a runtime in config:

```yaml
runtime:
  default: local                # Or: docker.
  per_verifier:
    signoff-code.tests:    docker    # Always sandbox test execution.
    signoff-code.types:    local     # Type-checking an AST is safe enough locally.
    signoff-research.*:    local     # Research verifiers just make HTTP calls.

runtime_policy:
  docker:
    cpu_limit: "2.0"
    memory_limit: "1g"
    network: none               # Or: allowlist with explicit domains.
    timeout_seconds: 60
    image: "ghcr.io/signoff/code-sandbox:latest"
    verify_signature: true      # cosign verification before use.
```

When a verifier declares `runtime_required="docker"` but runs in `LocalRuntime`, the harness MUST emit a warning — but not fail. Trusted contexts (unit tests, controlled CI, single-user local dev) are a valid reason to override.

### 8.4 Writing a verifier that plays well with runtimes

Verifiers don't know which runtime they're in. They interact with their execution environment only through `ctx`:

- `ctx.workspace` — the working directory (real filesystem or bind-mounted into the container).
- `ctx.exec(cmd, ...)` — execute a subcommand. The runtime decides whether this shells out locally or runs in the container.
- `ctx.http`, `ctx.judge` — network access, routed through the runtime's network policy.

Verifiers MUST NOT call `subprocess.run` or `docker.run` directly. They use `ctx.exec`. This keeps them portable across runtimes.

### 8.5 Docker image conventions for verifier sandboxes

Packs that need sandboxed execution ship a Dockerfile for their sandbox image. Conventions:

- Base image is `python:3.12-slim-bookworm` or a language-specific slim image (Go, Node, Rust).
- Preinstall the test runners and tools the pack needs (`pytest`, `ruff`, `mypy` for `signoff-code`).
- Run as a non-root user (`signoff:signoff`, UID 10001).
- No build tools in the final stage unless required.
- Published to `ghcr.io/signoff/<pack-sandbox>:<version>`.
- Image tags track the pack version.
- Signed with `cosign` on release.

See `docs/runtimes.md` for the full convention document.

---

## 9. Docker strategy across the project

Docker is an ambient capability used at three layers:

### 9.1 Verifier sandboxes (via `signoff-runtime-docker`)

Covered in §8. Per-verifier ephemeral containers for isolation.

### 9.2 Service distribution

The MCP server, cloud API, workers, and dashboard all ship as official Docker images:

- `ghcr.io/signoff/mcp:<version>` — the MCP server.
- `ghcr.io/signoff/cloud-api:<version>` — hosted service API.
- `ghcr.io/signoff/cloud-worker:<version>` — hosted service workers.
- `ghcr.io/signoff/cloud-dashboard:<version>` — hosted service UI.

All images are:
- Multi-stage builds (build deps separated from runtime).
- Non-root (`signoff:signoff`, UID 10001).
- Signed with `cosign`.
- Published on tag match via GitHub Actions.
- Scanned with `trivy` as a CI gate; CRITICAL vulnerabilities block release.

### 9.3 Local development

The root `docker-compose.yml` gives contributors a working stack in one command. `just dev` wraps it. This is the recommended path for new contributors because it bypasses the "do I have the right Python / Node / Postgres version?" onboarding tax.

Hot reload is required for developer productivity: Python services mount their source directory and use `uvicorn --reload`; the Next.js dashboard uses its native dev server; the MCP server restarts on file change.

### 9.4 Dockerfile conventions

Every Dockerfile in this repo follows these rules:

1. **Multi-stage.** A `builder` stage installs build-time deps; the final stage copies only what's needed to run.
2. **Pinned base images.** Digest pins (`python:3.12-slim-bookworm@sha256:...`) in published images. Tag-only refs are fine for local dev.
3. **Explicit `WORKDIR`.** Never rely on `/`.
4. **Non-root runtime.** Create and switch to an unprivileged user before the final `CMD`.
5. **Explicit `USER`, `EXPOSE`, `HEALTHCHECK`.** No defaults; no implicit networking.
6. **Layer ordering for cache.** Dependency install before source copy.
7. **No secrets in layers.** `.env` files, API keys, certificates NEVER in `COPY` lines.
8. **`.dockerignore` alongside every `Dockerfile`.** Exclude `.git`, `__pycache__`, `node_modules`, local test artifacts.
9. **Document the `CMD`.** A comment above `CMD` explains what's running and why.

Reference pattern for a Python service:

```dockerfile
# syntax=docker/dockerfile:1.7

# ---- builder ----
FROM python:3.12-slim-bookworm AS builder
WORKDIR /build
RUN pip install --no-cache-dir uv==0.5.*
COPY pyproject.toml uv.lock ./
COPY packages/ ./packages/
RUN uv sync --frozen --package signoff-mcp

# ---- runtime ----
FROM python:3.12-slim-bookworm
RUN groupadd --system --gid 10001 signoff \
 && useradd --system --uid 10001 --gid signoff --no-create-home signoff
WORKDIR /app
COPY --from=builder /build/.venv /app/.venv
ENV PATH="/app/.venv/bin:$PATH"
USER signoff
EXPOSE 8765
HEALTHCHECK --interval=30s --timeout=3s CMD python -m signoff_mcp --health || exit 1
# Default to stdio MCP transport; override via env for HTTP.
CMD ["python", "-m", "signoff_mcp"]
```

Reference pattern for a Node service:

```dockerfile
# syntax=docker/dockerfile:1.7

# ---- builder ----
FROM node:22-slim AS builder
WORKDIR /build
RUN corepack enable
COPY package.json pnpm-lock.yaml pnpm-workspace.yaml ./
COPY packages/signoff-sdk-ts ./packages/signoff-sdk-ts
COPY cloud/dashboard ./cloud/dashboard
RUN pnpm install --frozen-lockfile --filter "@signoff/dashboard..."
RUN pnpm --filter @signoff/dashboard build

# ---- runtime ----
FROM node:22-slim
RUN groupadd --system --gid 10001 signoff \
 && useradd --system --uid 10001 --gid signoff --no-create-home signoff
WORKDIR /app
COPY --from=builder --chown=signoff:signoff /build/cloud/dashboard/.next ./.next
COPY --from=builder --chown=signoff:signoff /build/node_modules ./node_modules
USER signoff
EXPOSE 3000
HEALTHCHECK --interval=30s --timeout=3s CMD node healthcheck.js || exit 1
CMD ["node", "server.js"]
```

---

## 10. How to add a verifier

Verifiers live inside packs. Example for `signoff-research`:

```python
# packages/signoff-research/src/signoff_research/verifiers/citation_existence.py
from signoff import verifier, Claim, VerifierContext, VerifierResult, Severity

@verifier(
    name="citation_existence",
    claim_kinds=["citation"],
    cost_tier="cheap",
    concurrency=20,
    runtime_required="local",  # or "docker" if the verifier executes untrusted code.
)
async def citation_existence(claim: Claim, ctx: VerifierContext) -> VerifierResult:
    url = claim.evidence.get("url")
    if not url:
        return ctx.fail(
            reason="Claim marked as citation but no URL in evidence.",
            suggestion="Attach a source URL to the claim.",
            severity=Severity.BLOCKER,
        )
    resp = await ctx.http.head(url, follow_redirects=True, timeout=10)
    if resp.status_code >= 400:
        return ctx.fail(
            reason=f"Source URL returned HTTP {resp.status_code}.",
            suggestion="Replace with a working source or remove the claim.",
            severity=Severity.BLOCKER,
            evidence={"status": resp.status_code, "url": url},
        )
    return ctx.ok(evidence={"status": resp.status_code, "url": url})
```

Register it via entry points in `pyproject.toml`:

```toml
[project.entry-points."signoff.verifiers"]
citation_existence = "signoff_research.verifiers.citation_existence:citation_existence"
```

Every verifier requires tests for: the pass case, each failure case, the "no evidence" case, and a timeout case. Verifiers that declare `runtime_required="docker"` additionally require an integration test that executes inside the pack's sandbox image.

Read `docs/writing-a-verifier.md` for the full checklist.

---

## 11. How to add a pack

A pack is a sibling package under `packages/`. It depends on `signoff-core`, declares its verifiers via entry points, and ships a default config.

```
packages/signoff-newpack/
├── pyproject.toml
├── Dockerfile                   If the pack's verifiers need sandboxing.
├── .dockerignore
└── src/signoff_newpack/
    ├── __init__.py
    ├── default_config.yaml
    ├── verifiers/
    └── prompts/
```

Every pack ships with:

1. A `default_config.yaml` users can import as a starting point.
2. A `README.md` documenting every verifier with an example claim.
3. A regression test suite in `tests/regression/` — at least 20 realistic claim + ground-truth pairs per verifier.
4. If sandboxed: a `Dockerfile` for the pack's sandbox image, published to `ghcr.io/signoff/<pack-name>-sandbox`.

Packs follow semver and release independently on PyPI.

---

## 12. Testing strategy

Four layers, enforced in CI:

**Unit** — each verifier tests its own logic with mocked `VerifierContext`. Fast (<1s per test). `pytest.mark.unit` / vitest default.

**Integration** — the harness runs real verifier packs against fixture deliverables with mocked external services. Exercises plugin discovery, budgeting, concurrency, runtime selection. `pytest.mark.integration`.

**Cross-language parity** — a shared fixture set exercises the Python core and TypeScript SDK against the same wire format, asserting identical serialization and identical decoded values. Lives in `tests/parity/`. Runs against both toolchains.

**Regression** — each pack has a regression suite of realistic claims with ground-truth labels. We track pass rate over time; a release that regresses doesn't ship. `pytest.mark.regression`. Opt-in `SIGNOFF_REGRESSION_USE_REAL_JUDGE=1` for periodic runs against real LLM judges.

LLM-judge verifiers are tested with `FakeJudge` in unit/integration tests. Dockerized verifiers are tested with `FakeDockerRuntime` in unit tests and against real `DockerRuntime` in integration tests.

Target coverage: 90% for `signoff-core`, 85% for packs and `signoff-sdk-ts`.

---

## 13. MCP server contract

See `docs/protocol.md` §7.3 for the normative tool schema. The MCP server exposes:

- `request_signoff` — submit a deliverable and its claims; receive a verdict.
- `list_verifiers` — discover available verifiers and their claim kinds.
- `get_verdict` — fetch a past verdict by id (hosted only; no-op locally).

Changes to this surface are protocol changes and require a version bump.

---

## 14. Current phase and priorities

We are in **Phase 0 — Foundation** (Weeks 1–4). The only deliverables that matter right now:

1. ✅ Workspace scaffold: `pyproject.toml`, `uv` workspace, `pnpm` workspace, `justfile`, CI matrix (Python + TS + Docker).
2. 🔨 `signoff-core` data models matching `docs/protocol.md` §3, with Pydantic v2 and exported JSON schemas.
3. 🔨 `signoff-core` Runtime protocol and `LocalRuntime`.
4. 🔨 `signoff-core` harness skeleton (resolution, concurrency, budgeting, verdict determination) per `docs/protocol.md` §5.
5. 🔨 Plugin discovery via entry points.
6. 🔨 YAML config loader per `docs/protocol.md` §6.
7. 🔨 `signoff-mcp` minimal server that echoes a hardcoded verdict (proves end-to-end MCP plumbing).
8. 🔨 `signoff-sdk-ts` v0.0.1 skeleton with model types mirroring the Python models and passing the cross-language parity tests.
9. 🔨 Root `Dockerfile`, `docker-compose.yml`, `docker-compose.cloud.yml` so `just dev` and `just cloud-dev` spin up working stacks.
10. 🔨 Documentation scaffolding: `docs/protocol.md` (done), `docs/writing-a-verifier.md`, `docs/runtimes.md`.
11. ⏳ No verifiers yet. Skeleton only. `signoff-runtime-docker` comes in Phase 1, not Phase 0.
12. ⏳ `cloud/` is empty scaffolding only. No code beyond placeholder Dockerfiles until Phase 2.

Phase 1 adds `signoff-code` and `signoff-runtime-docker`. Resist building verifiers before the core and runtime abstractions are stable.

Claude Code: work in small, reviewable PRs. One concern per PR. Tests alongside code.

---

## 15. Things to avoid

- **Don't conflate surfaces.** HTTP, MCP, Docker SDK, and core orchestration live in different packages. If you're importing `mcp` or `docker` inside `signoff-core`, back out.
- **Don't conflate runtimes.** The harness should not know what `DockerRuntime` is. It only knows the `Runtime` protocol.
- **Don't bake framework specifics into core.** LangChain, LangGraph, CrewAI, AutoGen are not dependencies of core.
- **Don't let `cloud/` leak into `packages/`.** Packages must be usable without the cloud directory existing. Imports from `cloud/` into `packages/` are forbidden.
- **Don't skip cross-language parity.** A protocol change that doesn't update the TS SDK is incomplete.
- **Don't let verifiers silently ignore errors.** Follow `docs/protocol.md` §4.4 precisely.
- **Don't write verifiers that only call an LLM.** Cheap deterministic pre-checks run first.
- **Don't shell out from verifiers.** Use `ctx.exec` so the runtime can route execution correctly.
- **Don't commit secrets.** Not even placeholder-looking ones. `.env.example` only.
- **Don't reproduce long copyrighted content in tests.** Synthetic or public-domain fixtures.
- **Don't add top-level dependencies without justification.** Supply-chain risk is real.
- **Don't skip `mypy --strict` or `tsc --strict`.** Type ignores need a comment and a tracking issue.
- **Don't publish Docker images with CRITICAL vulnerabilities.** `trivy` blocks CI.
- **Don't run Docker containers as root in published images.** Non-root is mandatory.

---

## 16. Release process

Each package releases independently:

- **Python packages** (`signoff-core`, `signoff-mcp`, `signoff-runtime-docker`, each pack) publish to PyPI on tag `<package>-v<version>`.
- **TypeScript package** (`@signoff/sdk`) publishes to npm on tag `sdk-ts-v<version>`.
- **Docker images** publish to `ghcr.io/signoff/*` on the same tags. Images are signed with `cosign` in the publish workflow; signatures are required on `:latest` and `:v*` tags.

Protocol-breaking changes require a coordinated release: core + MCP + SDK + affected packs all tagged together, with migration notes in each CHANGELOG.

The `cloud/` directory does not have external releases while it's in this repo; it deploys continuously from `main` via GitHub Actions once Phase 2 work begins.

---

## 17. Working inside this repo

When making changes, bias toward:

- **Small, reviewable PRs.** One concern per PR.
- **Type-first design.** Write the types, then the implementation.
- **Tests alongside code.** A change without a test is incomplete.
- **Protocol-doc-first for spec changes.** If you're changing a MUST in `docs/protocol.md`, the PR updates the protocol doc *first*, then the implementations across Python and TypeScript.
- **Documentation alongside features.** New public API means updated docs.
- **Conventional Commits, scoped by package.** `feat(core): ...`, `fix(mcp): ...`, `feat(sdk-ts): ...`, `chore(cloud): ...`, `feat(runtime-docker): ...`.

If a requested change would violate a design convention in §7, surface the conflict in the PR description rather than silently working around it.

---

## 18. One final principle

Signoff's job is to make agents honest about what they've done. The codebase should embody the same principle: no skipped tests pretending to pass, no `Any` types papering over design gaps, no verifiers that return `ok` when they couldn't actually check, no Docker images with vulnerabilities "we'll fix next sprint." If Signoff wouldn't sign off on our own code, we haven't earned the right to sign off on anyone else's.
