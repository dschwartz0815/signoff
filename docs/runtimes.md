# Runtimes

A **Runtime** is the abstraction for *where and how* a verifier's work executes. The harness resolves the set of applicable verifiers for a deliverable, and for each one it calls the configured Runtime's `execute()` method. The Runtime decides whether the verifier runs in-process, inside a sandbox container, on a remote worker, inside a Wasm VM, or anywhere else — the harness and the verifier don't care.

Separating "what to check" (the verifier) from "where and how it runs" (the runtime) is what lets `signoff-code` safely execute untrusted test suites later without polluting the core engine with Docker knowledge.

Normative contract: [`CLAUDE.md`](../CLAUDE.md) §8. Protocol-level context (§4.3) and error handling (§4.4) are in [`protocol.md`](./protocol.md).

---

## The `Runtime` protocol

```python
from signoff import Runtime, RuntimePolicy, VerifierMeta

class Runtime(Protocol):
    runtime_id: str

    async def prepare(self, verifier_meta: VerifierMeta) -> None: ...
    async def execute(self, fn, *, claim, ctx, policy: RuntimePolicy) -> VerifierResult: ...
    async def teardown(self) -> None: ...
```

Every conforming implementation MUST:

- Expose a stable `runtime_id` for config matching and result attribution (`"local"`, `"docker"`, etc.).
- Translate infrastructure failure (timeouts, resource caps, sandbox launch failures) into a synthetic `VerifierResult` with `severity=info`, `passed=false`, per protocol §4.4. These never escape to the harness as exceptions.
- Re-raise `asyncio.CancelledError` unchanged so the harness can honour protocol §5.6 cooperative cancellation.
- Be idempotent in `prepare` and `teardown`.

Runtime-internal failures use the `SignoffRuntimeError` hierarchy defined in [`signoff.runtime.base`](../packages/signoff-core/src/signoff/runtime/base.py): `RuntimeTimeoutError`, `RuntimeResourceLimitError`, `RuntimePolicyViolationError`, `RuntimeInfrastructureError`. These are implementation aids — callers of `Runtime.execute()` never see them.

---

## `LocalRuntime` — the default

`LocalRuntime` runs verifiers in the harness's own process. Zero dependencies, zero isolation. It is the right choice for:

- Unit and integration tests.
- Single-user local development on trusted code.
- Controlled CI on trusted code.

**What LocalRuntime enforces**

- `policy.timeout_seconds` via `asyncio.wait_for`. A timeout becomes a synthetic `severity=info` failure (§4.4 category 2).
- §4.4 error handling: an uncaught exception in the verifier becomes a synthetic `severity=info` failure with `exception_type` and a truncated traceback in `evidence`.
- Cooperative cancellation: `CancelledError` from the harness propagates unchanged.

**What LocalRuntime explicitly does NOT enforce**

- `policy.cpu_limit` / `policy.memory_limit_bytes` — the verifier shares the harness's process; caps aren't enforceable without OS-level isolation.
- `policy.network` — verifiers can call `ctx.http` freely.
- Filesystem sandboxing.

If `LocalRuntime` receives a policy with resource or network restrictions, it logs a one-shot `WARNING` so operators notice they've handed a policy to a runtime that can't honour it. The execution proceeds anyway — it's the caller's responsibility to choose a runtime that matches the policy.

---

## Writing a runtime-portable verifier

Verifiers don't know which runtime they're running in. They interact with their environment exclusively through `ctx`:

| Need | Do | Don't |
|------|----|-------|
| Execute a subcommand | `await ctx.exec(["pytest", ...])` | `subprocess.run(...)` |
| Make an HTTP call | `await ctx.http.get(url)` or `await ctx.fetch(url)` | `requests.get(url)` or `httpx.AsyncClient()` |
| Ask an LLM judge | `await ctx.judge.check_entailment(...)` | Bespoke Anthropic / OpenAI client |
| Read a file in the workspace | `ctx.workspace / "path"` | Absolute paths |
| Construct a result | `ctx.ok(...)` / `ctx.fail(reason, suggestion=...)` | `VerifierResult(verifier=..., claim_id=..., ...)` by hand |

This discipline is what lets `signoff-code`'s test-runner verifier work identically under `LocalRuntime` (local `pytest`) and under `DockerRuntime` (`docker exec` inside a pre-built sandbox image) in Phase 1. The verifier implementation doesn't change.

---

## Choosing a runtime

| Situation | Recommended runtime |
|-----------|---------------------|
| Running `signoff-research` on agent-produced prose + citations | `LocalRuntime` |
| Running `signoff-code` on agent-produced code | `DockerRuntime` (Phase 1) |
| Single-user dev on trusted code, fast iteration | `LocalRuntime` |
| Multi-tenant hosted service verifying untrusted deliverables | sandboxed runtime (`DockerRuntime`, later `FirecrackerRuntime`) |
| CI on your own repos | `LocalRuntime` is fine |
| CI on PRs from third parties | sandboxed runtime |

A verifier can declare `runtime_required="docker"` on its `@verifier` decoration. When that verifier is scheduled against a `LocalRuntime`, the harness emits a warning but runs it anyway — trusted overrides (unit tests, single-user dev) are a legitimate reason to ignore the declaration. Production deployments should set the config's `runtime.default` to a sandboxed runtime.

---

## `DockerRuntime` — sandbox for untrusted execution

Ships in [`signoff-runtime-docker`](../packages/signoff-runtime-docker). Spawns an ephemeral container per verifier invocation, bind-mounts the workspace read-only, and enforces CPU / memory / PID / network limits via the Docker daemon. The verifier's Python body still runs in the harness process — **what's sandboxed is the subprocess invocations the verifier makes via `ctx.exec`**. That's the attack surface when running tests or linters on AI-generated code.

### Safe-by-default posture

On every container `DockerRuntime` creates:

- `cap_drop=[ALL]`, `cap_add=[]` — no Linux capabilities.
- `security_opt=["no-new-privileges"]` — setuid binaries can't escalate.
- `read_only=True` — rootfs is read-only; writable tmpfs mounted at `/tmp`.
- `user=10001:10001` — runs as the non-root `signoff` user.
- `network_mode="none"` by default — no outbound network. `RuntimePolicy.network="allowlist"` is a Phase-1 placeholder that currently downgrades to `"bridge"` with a one-shot WARNING (the DNS-filter work is tracked separately).
- `pids_limit=256`, `mem_limit`, `nano_cpus` — runaway processes are capped.
- Workspace bind-mounted at `/workspace` in read-only mode (`workspace_mount_mode`).
- Labels `signoff.harness=true`, `signoff.verifier=<fqn>`, `signoff.claim_id=<id>`, `signoff.run_id=<uuid>` so operators can track containers back to verdicts.

### Image trust

Images are verified with `cosign verify` before first use. The harness requires a certificate identity regex and OIDC issuer in config:

```yaml
# SIGNOFF_DOCKER_VERIFY_SIGNATURES=auto (the default)
# SIGNOFF_DOCKER_SIGNATURE_CERT_IDENTITY_REGEXP=^https://github\\.com/signoff/
# SIGNOFF_DOCKER_SIGNATURE_CERT_OIDC_ISSUER=https://token.actions.githubusercontent.com
```

`SIGNOFF_DOCKER_VERIFY_SIGNATURES` has three modes:

| Value     | Behavior                                                                                                       | Use when                                                                  |
|-----------|----------------------------------------------------------------------------------------------------------------|---------------------------------------------------------------------------|
| `auto`    | At construction, probe `cosign` on `PATH`. Present → verify every image. Absent → log a WARNING and proceed.   | Local dev, quickstarts, dogfooding. The default.                          |
| `true`    | Hard contract. Cosign missing at verify time raises `ImageVerificationNotConfiguredError` and the harness halts. | Production. Pins the strict invariant that unsigned images never run.     |
| `false`   | Skip verification entirely. Constructor logs a WARNING naming the opt-out so it surfaces in audit logs.        | Locally-built images only.                                                |

The `auto` default is what makes the [quickstart](./quickstart.md) work on a fresh machine that doesn't yet have `cosign` installed. Production deployments should set `SIGNOFF_DOCKER_VERIFY_SIGNATURES=true` explicitly so a missing cosign binary is a loud failure rather than a silent relaxation. The startup log line tells you which path the harness took:

- `auto and cosign is on PATH — image signatures WILL be verified.` (INFO)
- `auto and cosign is NOT on PATH — proceeding WITHOUT signature verification.` (WARNING)
- `verify_signatures=False — images will be used without cosign verification.` (WARNING)
- (no log on `verify_signatures=True`; missing cosign surfaces at the first verify call.)

### Routing through `ctx.exec`

Inside `execute()` the runtime hands the verifier a `DockerVerifierContext`. Every attribute (`deliverable`, `http`, `judge`, `workspace`, `policy`, `logger`) forwards to the real `VerifierContext` unchanged — only `ctx.exec` is rewritten to route through `docker exec` into the ephemeral container:

- `cwd` is translated from the host path into a `/workspace`-rooted container path. `cwd` outside the workspace raises `ExecCwdOutsideWorkspaceError`.
- `timeout` kills the exec'd process (not the container) via `docker exec kill -9`. The container itself stays alive for subsequent `ctx.exec` calls from the same verifier.
- stdout / stderr are streamed and truncated at per-stream byte caps (`SIGNOFF_DOCKER_EXEC_STDOUT_MAX_BYTES`, default 10 MiB). Truncation is marked in the returned `ExecResult`.

Network and LLM-judge calls from a verifier still go through the host — those are trusted library calls, not subprocess invocations of untrusted content. That's deliberate: the sandbox covers the attack surface without imposing a DNS-level filter on everything the verifier does.

### Selecting DockerRuntime

`Harness.from_config_path` auto-includes `DockerRuntime` alongside `LocalRuntime` when:

1. The config references `docker` (either `runtime.default: docker` or any per-verifier override), and
2. `signoff-runtime-docker` is importable.

When condition 1 is met but 2 isn't, the harness logs a WARNING and falls back to `LocalRuntime` — insecure for untrusted deliverables, but the harness still runs so operators notice the warning rather than hitting ImportError at startup.

### Running inside a container

If the harness itself runs in a container (the published MCP image does), `DockerRuntime` needs access to the host's Docker daemon to spawn sibling containers. See [`docs/deployment.md`](./deployment.md) § "Running with DockerRuntime" for the socket-mount pattern and its security tradeoffs.

---

## Future runtimes

The following live in separate packages (not `signoff-core`) because they introduce heavy dependencies or platform assumptions:

- **Firecracker** (later) — microVM isolation for multi-tenant hosted workloads.
- **Wasm** (later) — for verifiers that can be compiled to WASI; near-zero startup overhead.
- **Kubernetes Jobs** (later) — for distributed execution across a cluster.

Each plugs in by implementing the same `Runtime` protocol. The harness and verifier code stay unchanged.

---

## Cross-language note

The TypeScript SDK (`@signoff/sdk`) is a client of the hosted Signoff API, not a verifier host. It has no `Runtime` equivalent, and that's deliberate — verifier execution is a server-side concern. TS applications that embed Signoff call the hosted API (or the MCP server) rather than running verifiers in-browser or in-Node.

---

## See also

- [`signoff.runtime.base`](../packages/signoff-core/src/signoff/runtime/base.py) — `Runtime` protocol, `RuntimePolicy`, `VerifierMeta`, `SignoffRuntimeError` hierarchy.
- [`signoff.runtime.local`](../packages/signoff-core/src/signoff/runtime/local.py) — `LocalRuntime`.
- [`signoff.context`](../packages/signoff-core/src/signoff/context.py) — `VerifierContext` and the `HttpClient` / `JudgeClient` protocols.
- [`signoff.testing`](../packages/signoff-core/src/signoff/testing.py) — `FakeHttpClient` and `FakeJudge` for unit tests.
