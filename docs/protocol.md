# Signoff Protocol Specification

**Version:** 0.1.0-draft
**Status:** Draft — subject to change until v1.0.0
**Audience:** Implementers of `signoff-core`, verifier packs, adapters (MCP, HTTP, SDKs), and agent integrations.

---

## 1. Introduction

### 1.1 Purpose

This document is the normative specification for the Signoff protocol. It defines the data types, verifier contract, harness behavior, feedback format, and wire representations that any conforming implementation MUST honor.

Where implementation code in this repository disagrees with this document, this document is authoritative and the code is a bug.

### 1.2 Scope

This specification covers:

- Core data types and their invariants.
- The verifier registration, invocation, and result contract.
- Harness orchestration semantics (resolution, concurrency, budgeting, termination).
- The structure of the feedback packet returned to agents.
- Configuration file schema.
- JSON wire format for transport adapters (MCP, HTTP, cross-process).
- Versioning rules and compatibility guarantees.
- Conformance criteria for implementations.

This specification does NOT cover:

- Specific verifier logic (that belongs to each pack's documentation).
- Transport-specific authentication, rate limiting, or billing (those are adapter concerns).
- User-facing rendering of verdicts (that is a separate rendering concern).

### 1.3 Requirement Keywords

The keywords **MUST**, **MUST NOT**, **REQUIRED**, **SHALL**, **SHALL NOT**, **SHOULD**, **SHOULD NOT**, **RECOMMENDED**, **MAY**, and **OPTIONAL** in this document are to be interpreted as described in [RFC 2119](https://www.rfc-editor.org/rfc/rfc2119) and [RFC 8174](https://www.rfc-editor.org/rfc/rfc8174).

### 1.4 Protocol Versioning

The protocol follows Semantic Versioning. Within the `0.x` series, breaking changes MAY occur between minor versions and will be called out in the changelog. From `1.0.0` onward:

- A **major** version bump signals a breaking change to any MUST in this document.
- A **minor** version bump adds new MUSTs to optional paths or new OPTIONAL fields.
- A **patch** version bump clarifies language without changing behavior.

Every protocol message on the wire MUST include a `protocol_version` field. Implementations MUST reject messages whose major version does not match their supported major version.

---

## 2. Terminology

- **Agent** — an LLM-based system that produces Deliverables.
- **Deliverable** — the unit of work an agent submits for verification.
- **Claim** — an asserted fact, computation, citation, or policy-bound statement embedded in a Deliverable.
- **Verifier** — a function that checks one or more Claims (or a whole Deliverable) and returns a VerifierResult.
- **Pack** — a versioned, independently-installable bundle of Verifiers targeting a domain.
- **Harness** — the orchestrator that resolves applicable Verifiers, runs them, and emits a Verdict.
- **Verdict** — the harness's final decision: pass or fail plus all VerifierResults.
- **Feedback Packet** — a machine-consumable subset of a Verdict designed to be returned to an agent for retry.
- **Registry** — the runtime index of Verifiers discovered via Python entry points.

---

## 3. Core Data Types

All types are defined semantically here and MUST be implemented with equivalent structural guarantees. Wire-format JSON schemas are in §8.

### 3.1 Identifiers

All `id` fields in this protocol MUST be strings matching the regex `^[a-zA-Z0-9][a-zA-Z0-9_\-]{0,127}$`. Implementations SHOULD use URL-safe UUIDs or short prefixed IDs (e.g., `dlv_01HXYZ...`, `clm_01HXYZ...`).

IDs MUST be unique within the scope of a single verification request.

### 3.2 Deliverable

A Deliverable represents what an agent submitted for verification.

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `id` | string | MUST | Unique identifier. |
| `kind` | string | MUST | Deliverable kind (e.g., `"research_report"`, `"pr_diff"`, `"support_reply"`). |
| `content` | any (JSON-serializable) | MUST | The deliverable's payload. Format is determined by `kind`. |
| `metadata` | object | SHOULD | Free-form metadata. Conventional keys below. |
| `created_at` | string (ISO-8601) | SHOULD | When the agent produced the deliverable. |

Conventional metadata keys (not required, but implementations SHOULD use these names when applicable):

- `agent_id` — identifier of the agent that produced the deliverable.
- `session_id` — identifier of the agent session.
- `task_description` — the task the agent was asked to perform.
- `parent_deliverable_id` — if this is a retry, the prior deliverable's ID.
- `retry_count` — zero-indexed integer; 0 for first attempt.

### 3.3 Claim

A Claim represents an asserted statement within a Deliverable that SHOULD be independently verifiable.

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `id` | string | MUST | Unique identifier. |
| `text` | string | MUST | Natural-language statement of the claim. |
| `kind` | string | MUST | Claim kind (see §3.3.1). |
| `evidence` | object | SHOULD | References used by verifiers (URLs, source refs, computations). |
| `span` | array of two integers or null | MAY | Character offsets `[start, end]` into the deliverable content where the claim originates. |
| `provenance` | string or null | MAY | How the claim was extracted: `"agent_asserted"`, `"extractor"`, `"user_supplied"`. |

#### 3.3.1 Reserved Claim Kinds

The following claim kinds are reserved by this protocol. Pack authors MUST NOT redefine them.

- `citation` — a claim referencing an external source. `evidence` SHOULD contain `url`, `doi`, `isbn`, `arxiv_id`, or `source_ref`.
- `quantitative` — a numeric claim. `evidence` SHOULD contain `value`, `unit`, and optionally `source_ref`.
- `quote` — a verbatim quotation. `evidence` SHOULD contain `source_ref` and MAY contain `expected_exact_text`.
- `policy` — an assertion governed by a policy document. `evidence` SHOULD contain `policy_ref`.
- `computational` — a derived result. `evidence` SHOULD contain `inputs` and `expression` or `query`.
- `personalization` — a personalized statement about a person or entity. `evidence` SHOULD contain `entity_id` and the source fact IDs.

Packs MAY define additional claim kinds using a pack-scoped namespace: `<pack_name>.<kind>` (e.g., `legal.clause_reference`). Unscoped kinds not listed above are RESERVED for future protocol versions and MUST NOT be used by packs.

### 3.4 Severity

Severity is an enum with three values:

- `"blocker"` — the verifier considers this failure sufficient grounds to fail the verdict.
- `"warning"` — the verifier flags a concern but does not fail the verdict.
- `"info"` — telemetry only; affects neither the verdict nor the feedback packet.

### 3.5 VerifierResult

A VerifierResult is the structured output of running one verifier against one claim (or the whole deliverable).

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `verifier` | string | MUST | Fully-qualified verifier name (see §4.1). |
| `claim_id` | string or null | MUST | The claim this result pertains to; `null` for whole-deliverable verifiers. |
| `passed` | boolean | MUST | Whether the check passed. |
| `severity` | string | MUST | One of the values in §3.4. |
| `reason` | string | MUST | Human-readable explanation (short, single sentence preferred). |
| `suggestion` | string or null | SHOULD | Actionable repair hint for the agent. REQUIRED when `passed` is false and `severity` is `blocker`. |
| `evidence` | object | SHOULD | Data the verifier observed or produced (URLs, excerpts, exit codes, LLM judgments). |
| `cost_usd` | number | MUST | Estimated USD cost of running this verifier. Zero is valid; negative is not. |
| `duration_ms` | integer | MUST | Wall-clock duration in milliseconds. Non-negative. |
| `verifier_version` | string | SHOULD | Version of the verifier implementation; aids reproducibility. |
| `started_at` | string (ISO-8601) | MAY | When the verifier began. |

Invariants:

- If `passed` is `true`, `severity` MUST be `info` OR the result MUST document the passed check via `evidence`.
- If `passed` is `false` and `severity` is `blocker`, `suggestion` MUST be non-null.
- `cost_usd` MUST be `0` for any verifier that makes no paid API calls.

### 3.6 Verdict

A Verdict is the harness's final output for one verification request.

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `id` | string | MUST | Unique verdict identifier. |
| `deliverable_id` | string | MUST | The Deliverable that was verified. |
| `passed` | boolean | MUST | Whether the harness signs off on the deliverable (see §5.4). |
| `results` | array of VerifierResult | MUST | All verifier results, in stable order. |
| `feedback_packet` | FeedbackPacket or null | MUST | Present if `passed` is false; MAY be null if `passed` is true. |
| `cost_usd` | number | MUST | Sum of `cost_usd` across all results. |
| `duration_ms` | integer | MUST | Total wall-clock duration of the harness run. |
| `protocol_version` | string | MUST | Semver string of the protocol this verdict conforms to. |
| `harness_version` | string | SHOULD | Version of the harness implementation. |
| `started_at` | string (ISO-8601) | MUST | When the harness began. |
| `completed_at` | string (ISO-8601) | MUST | When the harness returned. |
| `terminated_early` | boolean | SHOULD | Whether the harness stopped before running all applicable verifiers (see §5.5). |

### 3.7 FeedbackPacket

A FeedbackPacket is a machine-consumable summary of a failed Verdict designed to be dropped back into an agent as a tool result. It MUST NOT contain prose intended for human consumption.

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `passed` | boolean | MUST | Always `false`. Present for structural convenience. |
| `blockers` | array of BlockerEntry | MUST | Results with `passed=false` and `severity=blocker`. |
| `warnings` | array of WarningEntry | SHOULD | Results with `passed=false` and `severity=warning`. |
| `cost_usd` | number | MUST | Total cost of the run. |
| `retry_budget_remaining` | integer or null | MAY | If the caller set a retry budget, the remaining count. |
| `protocol_version` | string | MUST | Semver of the protocol. |

BlockerEntry and WarningEntry have the same shape:

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `claim_id` | string or null | MUST | The claim this entry pertains to. |
| `claim_text` | string or null | SHOULD | Echo of the claim text to aid retry without re-fetch. |
| `verifier` | string | MUST | Which verifier produced the entry. |
| `issue` | string | MUST | `reason` from the VerifierResult. |
| `suggested_repair` | string | MUST | `suggestion` from the VerifierResult. Non-null by §3.5 invariant. |
| `evidence_excerpt` | string or null | MAY | A short, agent-relevant excerpt from `evidence`. |

---

## 4. Verifier Protocol

### 4.1 Verifier Registration

Every verifier MUST declare the following metadata at registration time:

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `name` | string | MUST | Local name, unique within its pack. |
| `pack` | string | MUST | The pack's package name (e.g., `signoff-research`). |
| `claim_kinds` | array of string or `"*"` | MUST | Claim kinds this verifier handles, or `"*"` for whole-deliverable verifiers. |
| `cost_tier` | string | MUST | One of `"cheap"`, `"medium"`, `"expensive"`. |
| `concurrency` | integer | MUST | Maximum simultaneous invocations of this verifier. Must be ≥ 1. |
| `timeout_seconds` | integer | SHOULD | Hard timeout for a single invocation. Default 30. |
| `version` | string | SHOULD | Implementation version. |
| `requires` | array of string | MAY | Names of other verifiers that MUST have passed before this one runs. |

A verifier's **fully-qualified name** is `<pack>.<name>` (e.g., `signoff-research.citation_entailment`). All references in VerifierResults, configs, and feedback MUST use the fully-qualified form.

### 4.2 Verifier Registration via Entry Points

Python implementations MUST register verifiers via the `signoff.verifiers` entry point group:

```toml
[project.entry-points."signoff.verifiers"]
citation_entailment = "signoff_research.verifiers.citation_entailment:citation_entailment"
```

The entry point target MUST be a callable decorated with `@verifier` (the decorator's contract is defined below).

### 4.3 Verifier Invocation Contract

A verifier MUST be an async callable with the signature:

```
async def verifier(claim: Claim, ctx: VerifierContext) -> VerifierResult
```

Whole-deliverable verifiers (those with `claim_kinds = ["*"]`) receive a synthetic Claim with `id="__deliverable__"`, `text=""`, and the full deliverable content exposed via `ctx.deliverable`.

The `VerifierContext` MUST expose at minimum:

- `ctx.deliverable: Deliverable` — the deliverable under verification.
- `ctx.http: AsyncHttpClient` — a rate-limited async HTTP client.
- `ctx.judge: JudgeClient` — an LLM judge client configured with the harness's judge model.
- `ctx.fetch(url: str) -> FetchResult` — convenience method that uses `ctx.http` with default caching and timeout.
- `ctx.ok(**kwargs) -> VerifierResult` — constructs a passing result; fills `verifier`, `claim_id`, `severity=info`, timing.
- `ctx.fail(reason, suggestion, severity=BLOCKER, **kwargs) -> VerifierResult` — constructs a failing result.
- `ctx.logger` — a structured logger scoped to this verifier.
- `ctx.budget_remaining_usd: float` — how much of the verification budget remains.

A verifier MUST NOT:

- Mutate shared state outside its returned VerifierResult.
- Call `exit()`, `os._exit()`, or otherwise terminate the process.
- Swallow exceptions silently (see §4.4).
- Make network calls outside `ctx.http` or `ctx.judge` (this is RECOMMENDED, not required, to enable harness-level telemetry).

### 4.4 Verifier Error Handling

Verifiers encounter three categories of problem. Each has a prescribed handling:

1. **Claim is unverifiable given available evidence.** Return `VerifierResult(passed=False, severity=BLOCKER, reason="…", suggestion="…")`. This is a normal failure, not an error.

2. **Transient infrastructure failure** (DNS, timeout, rate limit on an LLM judge). The verifier MUST return `VerifierResult(passed=False, severity=INFO, reason="Verifier could not complete: <detail>", suggestion=None)`. The harness records this but does NOT treat it as a blocker. Callers MAY retry the verdict.

3. **Bug in the verifier** (unexpected exception). The verifier SHOULD NOT attempt to catch these; the harness MUST wrap the call in a try/except and produce a synthetic `VerifierResult(passed=False, severity=INFO, reason="Verifier raised <exception_class>: <message>", suggestion=None, evidence={"traceback": "…"})`. Implementations SHOULD surface these in logs at `WARNING` level.

Under no circumstances MAY a verifier return `passed=True` when it could not actually verify the claim.

---

## 5. Harness Behavior

### 5.1 Verification Request Inputs

A verification request MUST include:

- A `Deliverable`.
- A list of `Claim` objects (MAY be empty if only whole-deliverable verifiers are configured).
- An effective configuration (merged from pack defaults, user config, and any per-request overrides).

### 5.2 Verifier Resolution

For each verification request, the harness MUST compute the set of applicable verifiers as follows:

1. From the configured packs, load all registered verifiers.
2. Filter by the deliverable's `kind` via the configuration (see §6).
3. For each Claim, select verifiers whose `claim_kinds` includes the claim's `kind` or `"*"`.
4. Select whole-deliverable verifiers (those with `claim_kinds = ["*"]`) once per request.
5. Apply per-verifier overrides (disabled, severity adjusted, sample_rate).

A verifier disabled in config MUST NOT run. A verifier with `sample_rate` less than 1.0 MUST run with probability equal to `sample_rate`; excluded-by-sampling verifiers MUST NOT appear in the results.

### 5.3 Concurrency and Budgeting

The harness MUST enforce:

- A global concurrency limit (default: 16, configurable).
- A per-verifier concurrency limit as declared in §4.1.
- A total cost budget in USD (default: 0.50, configurable).
- A total time budget in seconds (default: 120, configurable).

Verifiers MUST run in cost-tier order: all `cheap` verifiers SHOULD start before any `medium`, and all `medium` before any `expensive`, subject to the `requires` dependency graph in §4.1. This allows the harness to short-circuit when a cheap verifier already fails the verdict.

The harness MUST track cumulative cost. Before starting an `expensive` verifier, the harness MUST check that `budget_remaining_usd > verifier.estimated_cost_usd`; if not, the verifier is skipped with a synthetic result of `severity=INFO, reason="Skipped: budget exceeded"`.

### 5.4 Verdict Determination

The verdict's `passed` field is `true` if and only if:

- No VerifierResult has `passed=false` AND `severity=blocker`, AND
- No applicable verifier was unable to run due to a configuration or registry error (distinct from §4.4 error handling).

Warnings and info-level failures MUST NOT cause `passed=false`.

### 5.5 Early Termination

The harness MAY terminate verification early if:

- A blocker-severity failure has occurred AND the configuration sets `early_termination: true` (default: false; when false, the harness runs all applicable verifiers to completion to populate a complete audit record).
- The time budget has been exceeded.
- The caller cancels the request (see §5.6).

When the harness terminates early, it MUST set `terminated_early: true` on the Verdict and MUST include synthetic results for skipped verifiers with `severity=INFO, reason="Skipped: …"`.

### 5.6 Cancellation

Implementations MUST support cooperative cancellation of an in-flight verification. When cancelled, the harness MUST:

1. Signal all in-flight verifiers to stop (via `asyncio.CancelledError` in Python).
2. Await their completion with a 2-second grace period.
3. Return a Verdict with `passed=false`, `terminated_early=true`, and whatever results were collected.

### 5.7 Retry Semantics

The protocol does NOT dictate whether a failed verdict triggers an agent retry. That is the agent's (or host application's) decision.

When the caller provides a `retry_budget` parameter (non-negative integer), the harness MUST echo it back in `feedback_packet.retry_budget_remaining` decremented by 1. When the budget reaches 0, the FeedbackPacket MUST include `retry_budget_remaining: 0` and callers SHOULD stop retrying.

---

## 6. Configuration

### 6.1 Schema

The harness configuration is a YAML document with the following top-level keys:

```yaml
protocol_version: "0.1"        # REQUIRED. Major.minor only.

packs:                          # REQUIRED. List of installed packs to load.
  - signoff-code
  - signoff-research

deliverables:                   # REQUIRED. Per-kind configuration.
  research_report:
    verifiers:
      signoff-research.citation_existence:
        enabled: true
        severity_override: blocker
        sample_rate: 1.0
      signoff-research.citation_entailment:
        enabled: true

budget:                         # OPTIONAL. Defaults per §5.3.
  max_cost_usd: 0.50
  max_duration_seconds: 120
  global_concurrency: 16
  early_termination: false

judge:                          # OPTIONAL. LLM judge configuration.
  provider: anthropic
  model: claude-haiku-4-5
  max_tokens: 1024

retries:
  default_budget: 3
```

### 6.2 Config Resolution Order

Effective config MUST be computed by deep-merging in this order (later values win):

1. Built-in defaults.
2. Pack-declared defaults (each pack MAY ship a `default_config.yaml`).
3. User-supplied config file.
4. Environment variable overrides (prefix `SIGNOFF_`).
5. Per-request overrides passed to the harness.

### 6.3 Validation

The harness MUST validate the merged configuration before running any verifier. Invalid configuration (unknown verifier name, invalid severity, etc.) MUST cause the harness to raise a configuration error rather than silently skipping verifiers.

---

## 7. Wire Format

Transport adapters (MCP, HTTP, cross-process RPC) MUST serialize the protocol types as JSON per this section.

### 7.1 General Rules

- Dates and times MUST be ISO 8601 strings with UTC timezone (`Z` suffix).
- Enums MUST be serialized as lowercase strings.
- `null` is the only valid serialization for optional-null fields; `undefined` or missing keys are NOT equivalent to `null` for REQUIRED fields.
- Numbers MUST use JSON numbers (not strings).

### 7.2 JSON Schema for VerifierResult

```json
{
  "$schema": "https://json-schema.org/draft/2020-12/schema",
  "type": "object",
  "required": ["verifier", "claim_id", "passed", "severity", "reason",
               "cost_usd", "duration_ms"],
  "properties": {
    "verifier": { "type": "string", "pattern": "^[a-z0-9_\\-]+\\.[a-z0-9_]+$" },
    "claim_id": { "type": ["string", "null"] },
    "passed": { "type": "boolean" },
    "severity": { "enum": ["blocker", "warning", "info"] },
    "reason": { "type": "string" },
    "suggestion": { "type": ["string", "null"] },
    "evidence": { "type": "object" },
    "cost_usd": { "type": "number", "minimum": 0 },
    "duration_ms": { "type": "integer", "minimum": 0 },
    "verifier_version": { "type": "string" },
    "started_at": { "type": "string", "format": "date-time" }
  }
}
```

Schemas for Deliverable, Claim, Verdict, and FeedbackPacket follow the same pattern and SHALL be maintained in `packages/signoff-core/src/signoff/schemas/`.

### 7.3 MCP Tool Surface

The MCP server MUST expose the following tools. Input and output schemas MUST match this document's type definitions.

#### 7.3.1 `request_signoff`

**Description:** Submit a deliverable and its claims for verification.

**Input schema:**

```json
{
  "type": "object",
  "required": ["deliverable"],
  "properties": {
    "deliverable": { "$ref": "#/definitions/Deliverable" },
    "claims": {
      "type": "array",
      "items": { "$ref": "#/definitions/Claim" },
      "default": []
    },
    "config_override": { "type": "object" },
    "retry_budget": { "type": "integer", "minimum": 0 }
  }
}
```

**Output schema:** A Verdict (see §3.6).

#### 7.3.2 `list_verifiers`

**Description:** Return the set of registered verifiers for this harness instance.

**Input:** `{}`

**Output:**

```json
{
  "type": "object",
  "required": ["verifiers", "protocol_version"],
  "properties": {
    "protocol_version": { "type": "string" },
    "verifiers": {
      "type": "array",
      "items": {
        "type": "object",
        "required": ["name", "pack", "claim_kinds", "cost_tier"],
        "properties": {
          "name": { "type": "string" },
          "pack": { "type": "string" },
          "claim_kinds": {
            "type": "array",
            "items": { "type": "string" }
          },
          "cost_tier": { "enum": ["cheap", "medium", "expensive"] },
          "version": { "type": "string" },
          "enabled": { "type": "boolean" }
        }
      }
    }
  }
}
```

#### 7.3.3 `get_verdict` (Optional)

Implementations MAY expose `get_verdict(verdict_id: string) -> Verdict` for retrieval of past verdicts. Local-only implementations MAY return a not-implemented error.

---

## 8. Conformance

### 8.1 Conformance Levels

An implementation conforms at one of three levels:

- **Core conformance** — implements §3 (data types), §4 (verifier protocol), and §5 (harness behavior).
- **Transport conformance** — also implements §7 (wire format) for at least one transport.
- **Full conformance** — implements all of the above plus configuration (§6).

### 8.2 Test Vectors

A conforming implementation MUST pass the test vectors published in `tests/conformance/`. These cover:

- Minimal valid Deliverable, Claim, VerifierResult, Verdict round-tripping.
- Correct handling of each severity combination for verdict determination.
- Correct cost-tier ordering in harness execution.
- Correct handling of each error category in §4.4.
- Correct config resolution and override precedence.
- Feedback packet construction from mixed-severity results.

### 8.3 Documenting Deviations

An implementation MAY deliberately diverge from a SHOULD-level requirement. When it does, the implementation's documentation MUST list the deviation and its rationale. Divergence from a MUST-level requirement means the implementation is non-conforming.

---

## 9. Security Considerations

### 9.1 Untrusted Deliverables

Deliverables are submitted by agents and MUST be treated as untrusted input. Implementations MUST NOT:

- Execute code from deliverable `content` outside of a sandbox.
- Follow arbitrary URLs from `evidence` without configurable allow-listing or rate limiting.
- Deserialize deliverable `content` with unsafe deserializers (`pickle`, `yaml.load` without `SafeLoader`, etc.).

### 9.2 Verifier Isolation

In the hosted deployment, verifiers that execute untrusted code (e.g., `signoff-code` running a test suite) MUST run in isolated sandboxes with:

- No network access except to allow-listed destinations.
- Bounded CPU, memory, and disk.
- Per-invocation filesystem isolation.

The library-mode deployment delegates this responsibility to the caller; the documentation MUST make this delegation explicit.

### 9.3 LLM Judge Prompt Injection

Verifiers that pass untrusted text to an LLM judge (e.g., quotations from fetched sources) MUST use structured prompt patterns that delimit the untrusted content and instruct the judge to treat any instructions embedded therein as data, not commands.

### 9.4 Audit Integrity

For deployments that rely on the audit log for compliance evidence, the log MUST be append-only. The hosted service SHOULD use a cryptographic chain (hash of previous entry included in each new entry) to make tampering detectable.

---

## 10. Changelog

- **0.1.0-draft** (current) — Initial draft. Covers §§1–9.

Future changes will be recorded here and linked to the RFC-style proposal document that introduced them.

---

## Appendix A: Example Verdicts

### A.1 Passing Verdict

```json
{
  "id": "vrd_01HZ3...",
  "deliverable_id": "dlv_01HZ2...",
  "passed": true,
  "results": [
    {
      "verifier": "signoff-research.citation_existence",
      "claim_id": "clm_01",
      "passed": true,
      "severity": "info",
      "reason": "Source URL returned HTTP 200.",
      "suggestion": null,
      "evidence": { "status": 200, "url": "https://ftc.gov/..." },
      "cost_usd": 0.0,
      "duration_ms": 240
    }
  ],
  "feedback_packet": null,
  "cost_usd": 0.0,
  "duration_ms": 240,
  "protocol_version": "0.1",
  "started_at": "2026-04-18T14:22:10Z",
  "completed_at": "2026-04-18T14:22:10Z",
  "terminated_early": false
}
```

### A.2 Failing Verdict with Feedback Packet

```json
{
  "id": "vrd_01HZ4...",
  "deliverable_id": "dlv_01HZ3...",
  "passed": false,
  "results": [
    {
      "verifier": "signoff-research.citation_existence",
      "claim_id": "clm_01",
      "passed": false,
      "severity": "blocker",
      "reason": "Source URL returned HTTP 404.",
      "suggestion": "Replace with a working source URL or remove the claim.",
      "evidence": { "status": 404, "url": "https://gartner.com/report-xyz" },
      "cost_usd": 0.0,
      "duration_ms": 180
    }
  ],
  "feedback_packet": {
    "passed": false,
    "blockers": [
      {
        "claim_id": "clm_01",
        "claim_text": "A 2024 Gartner analysis found customer churn rises by 28%.",
        "verifier": "signoff-research.citation_existence",
        "issue": "Source URL returned HTTP 404.",
        "suggested_repair": "Replace with a working source URL or remove the claim.",
        "evidence_excerpt": null
      }
    ],
    "warnings": [],
    "cost_usd": 0.0,
    "retry_budget_remaining": 2,
    "protocol_version": "0.1"
  },
  "cost_usd": 0.0,
  "duration_ms": 180,
  "protocol_version": "0.1",
  "started_at": "2026-04-18T14:24:01Z",
  "completed_at": "2026-04-18T14:24:01Z",
  "terminated_early": false
}
```

---

## Appendix B: Glossary of Reserved Names

The following names are reserved by this protocol and MUST NOT be used by pack authors for their own purposes:

- Claim kinds: `citation`, `quantitative`, `quote`, `policy`, `computational`, `personalization`.
- Severity values: `blocker`, `warning`, `info`.
- Cost tiers: `cheap`, `medium`, `expensive`.
- Synthetic claim IDs: `__deliverable__`.
- Entry point groups: `signoff.verifiers`, `signoff.packs`, `signoff.adapters`.
- Environment variable prefix: `SIGNOFF_`.

Pack authors MUST use their pack's namespace (e.g., `signoff-legal.clause_reference`) for additions.
