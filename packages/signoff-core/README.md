# signoff-core

Core engine for [Signoff](../../README.md) — a verification layer for AI agents.

**Source of truth for the types exported here:** [`docs/protocol.md`](../../docs/protocol.md) §3 and [`CLAUDE.md`](../../CLAUDE.md) §8. When this code disagrees with the protocol doc, the doc wins and the code is a bug.

## Public API

### Data models (`signoff.models`)

| Type | Protocol section |
|------|------------------|
| `Deliverable` | §3.2 |
| `Claim` | §3.3 (reserved kinds in §3.3.1) |
| `VerifierResult` | §3.5 |
| `Verdict` | §3.6 |
| `FeedbackPacket`, `BlockerEntry`, `WarningEntry` | §3.7 |
| `Severity` (`StrEnum`) | §3.4 |

Also exported: regex constants (`ID_PATTERN`, `VERIFIER_NAME_PATTERN`), the `RESERVED_CLAIM_KINDS` frozenset, and the harness-internal `DELIVERABLE_CLAIM_ID` constant (§4.3).

### Runtime abstraction (`signoff.runtime`)

Per [`CLAUDE.md`](../../CLAUDE.md) §8 — see [`docs/runtimes.md`](../../docs/runtimes.md) for the full guide.

| Symbol | Purpose |
|--------|---------|
| `Runtime` (Protocol) | Contract for "where and how a verifier executes" |
| `RuntimePolicy` | Per-execution ceiling: timeout, CPU/memory caps, network posture |
| `VerifierMeta` | §4.1 registration metadata the harness passes to the runtime |
| `LocalRuntime` | In-process default; no isolation; enforces timeout + §4.4 error handling |
| `SignoffRuntimeError` + `RuntimeTimeoutError`, `RuntimeResourceLimitError`, `RuntimePolicyViolationError`, `RuntimeInfrastructureError` | Runtime-internal error hierarchy |

### Verifier context (`signoff.context`)

Implements protocol §4.3.

| Symbol | Purpose |
|--------|---------|
| `VerifierContext` | What a verifier sees: deliverable, workspace, HTTP/judge clients, logger, budget, `ok()` / `fail()` result constructors, `exec()`, `fetch()` |
| `ExecResult`, `FetchResult`, `JudgeResult` | Structured return types for context I/O |
| `HttpClient`, `JudgeClient` (Protocols) | Pluggable client contracts; real impls arrive in follow-ups |
| `make_context(...)` | Convenience factory for tests and ad-hoc callers |

### Verifier decorator + plugin registry (`signoff.verifier`, `signoff.registry`)

Implements protocol §4.1 / §4.2. See [`docs/writing-a-verifier.md`](../../docs/writing-a-verifier.md).

| Symbol | Purpose |
|--------|---------|
| `@verifier(...)` | Declares a verifier; attaches `VerifierMeta` to the function at import time |
| `RegisteredVerifier` (Protocol) | Structural type for "callable with `.signoff_meta`" |
| `Registry` | Indexed store of registered verifiers; supports `discover`, `register`, `get`, `list_all`, `for_claim_kind`, `whole_deliverable` |
| `default_registry` | Module-level singleton convenience |
| `ENTRY_POINT_GROUP` | `"signoff.verifiers"` — what packs publish into |

### Configuration (`signoff.config`)

Implements protocol §6. See [`docs/configuration.md`](../../docs/configuration.md).

| Symbol | Purpose |
|--------|---------|
| `HarnessConfig` | Top-level YAML shape |
| `DeliverableConfig`, `VerifierConfig`, `BudgetConfig`, `RuntimeConfig`, `RuntimePolicyConfig`, `JudgeConfig`, `RetryConfig` | Nested sections |
| `load_config(path=..., pack_defaults=..., env_overrides=..., request_overrides=...)` | Full resolution-order loader |
| `validate_config(config, registry)` | Cross-check against a populated `Registry` |
| `deep_merge(base, override)` | Deep-merge primitive (dicts recurse, lists replace, `None` unsets) |
| `ConfigurationError` | Raised on YAML / validation failures |
| `PACK_DEFAULTS_ENTRY_POINT_GROUP` | `"signoff.pack_defaults"` — packs publish defaults here |

### Test helpers (`signoff.testing`)

Opt-in — not re-exported from `signoff`.

- `FakeHttpClient` — deterministic HTTP stand-in; pre-register responses by URL.
- `FakeJudge` — deterministic judge stand-in; queue then fall back to a default.

## JSON schemas

Every model is serialised to JSON Schema under [`src/signoff/schemas/`](./src/signoff/schemas/) and kept in sync via `scripts/export_schemas.py`. Regenerate with:

```sh
just schemas         # rewrite schemas from current models
just schemas-check   # fail if committed schemas drift from models (CI gate)
```

The TypeScript SDK copies these schemas at build time and asserts agreement against its Zod definitions, so a model change in this package must also re-export schemas and usually pairs with a parallel change in `@signoff/sdk`.

## Coming in follow-up PRs

- `Harness` (orchestration, concurrency, budgeting, verdict construction).
- Real `HttpClient` (httpx) and `JudgeClient` (Anthropic / OpenAI) implementations.
- `DockerRuntime` (separate package, `signoff-runtime-docker`).

See [`CLAUDE.md`](../../CLAUDE.md) §14 for the phase plan.
