# Writing a Verifier

A **verifier** is an async function that checks a claim and returns a structured result. Signoff verifiers live inside a **pack** (a pip-installable Python package named `signoff-<domain>`) and are registered via entry points so the harness finds them without explicit wiring.

This guide covers the `@verifier` decorator, authoring conventions, and the required test cases for any new verifier.

Normative contract: [`docs/protocol.md`](./protocol.md) §4. Runtime semantics: [`docs/runtimes.md`](./runtimes.md). Repo conventions: [`CLAUDE.md`](../CLAUDE.md) §10, §11.

---

## The shape of a verifier

```python
from signoff import Claim, Severity, VerifierContext, VerifierResult, verifier


@verifier(
    name="citation_existence",
    claim_kinds=["citation"],
    cost_tier="cheap",
    concurrency=20,
    timeout_seconds=10,
    version="0.1.0",
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
            evidence={"status": resp.status_code, "url": url},
        )
    return ctx.ok(evidence={"status": resp.status_code, "url": url})
```

Register it in `pyproject.toml`:

```toml
[project.entry-points."signoff.verifiers"]
citation_existence = "signoff_research.verifiers.citation_existence:citation_existence"
```

`Registry.discover()` loads every entry point in the `signoff.verifiers` group at harness startup. Nothing else to do.

---

## `@verifier` parameters

| Parameter | Required | Notes |
|-----------|----------|-------|
| `name` | yes | Local name. Must match `^[a-z][a-z0-9_]*$`, ≤ 64 chars. The fully-qualified name on the wire is `<pack>.<name>`. |
| `claim_kinds` | yes | Either `"*"` (whole-deliverable) or a list of specific kinds. Specific kinds must be reserved (§3.3.1: `citation`, `quantitative`, `quote`, `policy`, `computational`, `personalization`) or pack-namespaced (`<pack>.<kind>`). Mixing `"*"` with specific kinds is rejected. |
| `cost_tier` | yes | `"cheap"`, `"medium"`, or `"expensive"`. The harness schedules cheap verifiers first (§5.3). |
| `concurrency` | default `1` | Max simultaneous invocations of this verifier across a single run. Verifiers that hit external APIs set this higher. |
| `timeout_seconds` | default `30` | Hard timeout per invocation. The runtime turns timeouts into synthetic `severity=info` failures (§4.4 category 2). |
| `version` | optional | Implementation version, echoed into `VerifierResult.verifier_version`. Bump on semantic changes. |
| `requires` | default `()` | Fully-qualified names of other verifiers that MUST pass before this one runs (§4.1). |
| `runtime_required` | optional | `"local"` or `"docker"`. A `"docker"`-required verifier scheduled against `LocalRuntime` logs a warning but runs. Production deployments that verify untrusted code should configure a sandboxed default runtime. |

Validation happens at import time, so authoring mistakes surface immediately.

---

## Pack name detection

The decorator infers the pack name from `fn.__module__`:

- `signoff_research.verifiers.citation_existence` → pack `signoff-research`
- `signoff_code.verifiers.tests` → pack `signoff-code`

Underscores are converted to hyphens so Python module names line up with pip package names. If the module isn't under a `signoff_*` top-level package, the decorator raises with examples.

**Tests** can use the `_testing_pack` context manager to decorate locally-scoped functions outside a `signoff_*` package:

```python
from signoff.verifier import _testing_pack, verifier

with _testing_pack("signoff-research"):
    @verifier(name="x", claim_kinds=["citation"], cost_tier="cheap")
    async def x(claim, ctx): ...
```

The helper is explicitly underscore-prefixed and test-only. Production verifier code should never touch it.

---

## The `ctx` object

Verifiers interact with the outside world **exclusively** through `ctx`. This is what makes them runtime-portable — see [`docs/runtimes.md`](./runtimes.md#writing-a-runtime-portable-verifier).

| Need | Use | Do not use |
|------|-----|------------|
| HTTP GET/HEAD | `ctx.http.get(url)`, `ctx.fetch(url)` | `requests`, `httpx.AsyncClient()` |
| LLM judge | `ctx.judge.check_entailment(...)` | Bespoke Anthropic/OpenAI clients |
| Subcommand | `ctx.exec(["pytest", ...])` | `subprocess.run(...)` |
| Files in workspace | `ctx.workspace / "path"` | Absolute paths |
| Result | `ctx.ok(...)`, `ctx.fail(...)` | `VerifierResult(...)` by hand |

`ctx.ok(**overrides)` and `ctx.fail(reason, suggestion=..., severity=...)` fill in `verifier` and `claim_id` automatically from `ctx.current_verifier_meta` and `ctx.current_claim`, which the runtime stamps before dispatch. They also enforce §3.5: `ctx.fail(severity=BLOCKER)` without a non-null `suggestion` raises `ValueError` at construction time — authoring mistakes are caught immediately.

---

## Error handling (§4.4)

Three categories of problem, three handlings:

| Category | Who handles | Result |
|----------|-------------|--------|
| Claim is unverifiable given evidence | **You** — return `ctx.fail(...)` | `passed=false`, `severity=blocker` (normal failure, not an error) |
| Transient infra failure (DNS, rate limit, judge timeout) | **You** — return `ctx.fail(..., severity=Severity.INFO)` with `suggestion=None` | `passed=false`, `severity=info` — harness records, doesn't block |
| Bug in the verifier (unexpected exception) | **Runtime** — don't catch it, let it raise | Runtime produces a synthetic `severity=info` result with the exception type and a bounded traceback in `evidence` |

Under no circumstances return `passed=true` for a claim you could not actually verify.

---

## Required tests

Every verifier ships with at minimum these four tests, using `signoff.testing.FakeHttpClient` / `FakeJudge`:

1. **Pass case** — at least one claim that the verifier should sign off on.
2. **Each failure case** — one test per distinct blocker.
3. **"No evidence" case** — claim lacks the field the verifier expects (e.g., no `url` on a `citation`).
4. **Timeout case** — construct a policy with a short timeout, verify the runtime produces a synthetic INFO result.

Packs that declare `runtime_required="docker"` additionally need an integration test that runs the verifier inside the pack's sandbox image. See [`CLAUDE.md`](../CLAUDE.md) §12 for the testing-layer taxonomy.

---

## Worked example: end-to-end

```python
# packages/signoff-research/src/signoff_research/verifiers/citation_existence.py
from signoff import Claim, VerifierContext, VerifierResult, verifier


@verifier(name="citation_existence", claim_kinds=["citation"], cost_tier="cheap")
async def citation_existence(claim: Claim, ctx: VerifierContext) -> VerifierResult:
    url = claim.evidence.get("url")
    if not isinstance(url, str):
        return ctx.fail(
            reason="Citation claim has no URL in evidence.",
            suggestion="Attach a source URL to the claim.",
        )
    resp = await ctx.http.head(url, follow_redirects=True, timeout=10)
    if resp.status_code >= 400:
        return ctx.fail(
            reason=f"Source URL returned HTTP {resp.status_code}.",
            suggestion="Replace with a working source or remove the claim.",
            evidence={"status": resp.status_code, "url": url},
        )
    return ctx.ok(evidence={"status": resp.status_code, "url": url})
```

Tests that exercise this verifier use `LocalRuntime().execute(...)` with `FakeHttpClient` / `FakeJudge`; see [`packages/signoff-core/tests/runtime/test_local_runtime.py`](../packages/signoff-core/tests/runtime/test_local_runtime.py) for the pattern.
