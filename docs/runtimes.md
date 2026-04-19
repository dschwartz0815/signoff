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

## Future runtimes

The following live in separate packages (not `signoff-core`) because they introduce heavy dependencies or platform assumptions:

- **`signoff-runtime-docker`** (Phase 1) — `DockerRuntime` spawns an ephemeral container per execution, bind-mounts the workspace read-only, and enforces CPU / memory / network limits via the Docker daemon. Images are signed with `cosign`; `trivy` scans block publication on CRITICAL CVEs. See [`CLAUDE.md`](../CLAUDE.md) §8.5 and §9.1 for the image conventions.
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
