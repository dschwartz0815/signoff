# Harness Configuration

This document covers the YAML configuration file the harness consumes, the merge order the loader applies, and the environment-variable override pattern.

Normative schema: [`docs/protocol.md`](./protocol.md) §6. Code: [`signoff.config`](../packages/signoff-core/src/signoff/config.py).

---

## Schema at a glance

```yaml
protocol_version: "0.1"            # REQUIRED for compatibility gating.

packs:
  - signoff-research
  - signoff-code

deliverables:                       # Per-kind config. Keys are Deliverable.kind.
  research_report:
    # Optional pack override for this kind only:
    # packs: [signoff-research]
    verifiers:
      signoff-research.citation_existence:
        enabled: true
        severity_override: blocker  # Coerce the verifier's default severity.
        sample_rate: 1.0            # 0.0–1.0. Probability this verifier runs.
        timeout_seconds_override: 20
      signoff-research.citation_entailment:
        enabled: true

budget:                             # §5.3.
  max_cost_usd: 0.50
  max_duration_seconds: 120
  global_concurrency: 16
  early_termination: false

runtime:                            # §8.3.
  default: local
  per_verifier:
    signoff-code.tests: docker
    signoff-code.types: local

runtime_policy:                     # Per-runtime policy blocks.
  local:
    timeout_seconds: 30
  # docker: ...                     # Appears when signoff-runtime-docker is installed.

judge:
  provider: anthropic
  model: claude-haiku-4-5
  max_tokens: 1024

retries:
  default_budget: 3
```

The full Pydantic model definitions live in [`signoff.config`](../packages/signoff-core/src/signoff/config.py): `HarnessConfig`, `DeliverableConfig`, `VerifierConfig`, `BudgetConfig`, `RuntimeConfig`, `RuntimePolicyConfig`, `JudgeConfig`, `RetryConfig`.

---

## Resolution order (protocol §6.2)

`load_config()` merges five layers, later wins:

| # | Layer | Source |
|---|-------|--------|
| 1 | Built-in defaults | Pydantic model defaults in `signoff.config` |
| 2 | Pack-declared defaults | Entry-point group `signoff.pack_defaults` — normative per protocol §6.2. Each pack's entry point target resolves to either a `() -> Mapping` callable or a module-level mapping. |
| 3 | User-supplied YAML | The `path=` argument to `load_config()` |
| 4 | Environment variables | `SIGNOFF_*` — see below |
| 5 | Per-request overrides | The `request_overrides=` argument, deep-merged on top |

### A pack publishing defaults

In a pack's `pyproject.toml`:

```toml
[project.entry-points."signoff.pack_defaults"]
signoff-research = "signoff_research.defaults:DEFAULTS"
```

…and in `signoff_research/defaults.py`:

```python
DEFAULTS = {
    "deliverables": {
        "research_report": {
            "verifiers": {
                "signoff-research.citation_existence": {"enabled": True},
                "signoff-research.citation_entailment": {"enabled": True},
            },
        },
    },
}
```

`load_config()` picks this up automatically (unless called with `pack_defaults=False`). Users override any of it in their own YAML.

### Worked example

```
Built-in:      budget.max_cost_usd = 0.50
Pack default:  budget.max_cost_usd = 0.25   (signoff-research sets this)
User YAML:     budget.max_cost_usd = 1.00
Env:           SIGNOFF_BUDGET__MAX_COST_USD=2.00
Request:       request_overrides={"budget": {"max_cost_usd": 5.00}}

Result:        5.00
```

### Deep-merge rules

- **Dicts** merge recursively, key by key.
- **Lists** replace, they do not concatenate. User YAML `packs: [signoff-research]` completely overrides the pack-default list. This avoids surprising accumulation.
- **Scalars** replace.
- **`None`** in a later layer explicitly unsets the earlier value. Use this to drop a field you no longer want.

---

## Environment variable overrides

`SIGNOFF_` is the prefix. Nesting uses double underscores, matching the pydantic-settings convention.

| Env var | Maps to |
|---------|---------|
| `SIGNOFF_BUDGET__MAX_COST_USD=1.00` | `budget.max_cost_usd` |
| `SIGNOFF_BUDGET__GLOBAL_CONCURRENCY=8` | `budget.global_concurrency` |
| `SIGNOFF_RUNTIME__DEFAULT=docker` | `runtime.default` |
| `SIGNOFF_JUDGE__PROVIDER=fake` | `judge.provider` |
| `SIGNOFF_RETRIES__DEFAULT_BUDGET=0` | `retries.default_budget` |

Values are strings on the env boundary; Pydantic coerces them to the declared types during validation.

Env vars whose nested path collides with an already-set scalar are ignored and logged at WARNING.

---

## Common patterns

### Disable a single verifier

```yaml
deliverables:
  research_report:
    verifiers:
      signoff-research.citation_entailment:
        enabled: false
```

### Downgrade a blocker to a warning for a specific deliverable kind

```yaml
deliverables:
  draft_report:
    verifiers:
      signoff-research.citation_existence:
        severity_override: warning
```

Useful when a specific deliverable kind (e.g., drafts) should surface issues but not block.

### Sample a verifier

Run an expensive verifier on only 10% of invocations:

```yaml
deliverables:
  research_report:
    verifiers:
      signoff-research.citation_entailment:
        sample_rate: 0.1
```

### Pin verifier execution to a sandboxed runtime

```yaml
runtime:
  default: local
  per_verifier:
    signoff-code.tests: docker
    signoff-code.smoke: docker
```

---

## Validation

`validate_config(config, registry)` checks that:

- `protocol_version` major matches the implementation (currently `0.x`).
- Every verifier referenced in `deliverables.<kind>.verifiers` is known to `registry`.
- Every pack listed (top-level or per-deliverable) is represented by at least one registered verifier.

It does **not** reject:

- Unknown deliverable kinds (they stay idle — harmless).
- Extra keys inside `runtime_policy` (`docker:`, future `wasm:`, etc.) — `extra="allow"` there.
- Unknown environment variables.

Invalid YAML, missing files, and Pydantic validation failures raise `ConfigurationError` with the offending file path in the message.
