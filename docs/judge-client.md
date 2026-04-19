# LLM Judge Client (`signoff-judge`)

The `signoff-judge` package provides real LLM-backed implementations of
the [`signoff.JudgeClient`](./protocol.md#43-judgeclient) protocol.
It ships two providers, feature-complete with each other:

- **`AnthropicJudge`** — Anthropic SDK, uses tool-use for structured
  output.
- **`OpenAIJudge`** — OpenAI SDK, uses the strict `json_schema`
  `response_format` for structured output.

Both subclass a shared `BaseJudge` that owns retry policy, schema
validation, cost accounting, and error mapping — so the two providers
can't drift on safety behaviour.

---

## Selecting a provider

The harness picks the judge from the top-level `judge:` block in your
harness YAML:

```yaml
judge:
  provider: anthropic    # default; "openai" and "fake" also accepted
  model: claude-haiku-4-5
```

If `signoff-judge` isn't installed, the harness logs a WARNING and
falls back to `FakeJudge`. Install the package (or switch to
`provider: fake` explicitly) to silence the warning.

Fine-grained tuning (API keys, timeouts, retries, prompt overrides)
lives in the `SIGNOFF_JUDGE_*` environment namespace, not the YAML.

---

## The JudgeClient surface

Verifiers use three methods on `ctx.judge`:

```python
async def check_entailment(
    *, claim: str, passage: str, context: str | None = None
) -> JudgeResult: ...

async def check_policy_compliance(
    *, output: str, policy: str,
    examples_of_violations: list[str] | None = None,
) -> JudgeResult: ...

async def classify(
    *, text: str, labels: list[str], rubric: str | None = None,
) -> JudgeResult: ...
```

Each returns a `JudgeResult` whose `label` is method-specific:

| Method | Legal `label` values |
|--------|----------------------|
| `check_entailment` | `supported` / `contradicted` / `not_addressed` |
| `check_policy_compliance` | `compliant` / `violation` |
| `classify` | one of the caller-supplied `labels` list |

`JudgeResult` also carries `confidence`, `cost_usd`, `model`,
`prompt_version`, and `raw_response` — the audit fields required for
every verdict to be re-traceable.

---

## `SIGNOFF_JUDGE_*` environment variables

| Env var | Maps to |
|---------|---------|
| `SIGNOFF_JUDGE_PROVIDER=anthropic` | `provider` (alt: `openai`, `fake`) |
| `SIGNOFF_JUDGE_MODEL=claude-haiku-4-5` | `model` |
| `SIGNOFF_JUDGE_API_KEY=sk-...` | API key. Preferred over provider-native vars. |
| `SIGNOFF_JUDGE_MAX_TOKENS=1024` | generation cap |
| `SIGNOFF_JUDGE_TEMPERATURE=0.0` | 0.0 = deterministic; default |
| `SIGNOFF_JUDGE_TIMEOUT_SECONDS=60` | per-call timeout |
| `SIGNOFF_JUDGE_MAX_RETRIES=2` | retry budget on 429 / 5xx / timeout |
| `SIGNOFF_JUDGE_RETRY_BACKOFF_BASE=0.5` | exponential base (s) |
| `SIGNOFF_JUDGE_RETRY_BACKOFF_FACTOR=2.0` | exponential factor |
| `SIGNOFF_JUDGE_RETRY_MAX_BACKOFF=30.0` | retry-delay cap (s) |
| `SIGNOFF_JUDGE_PROMPT_ROOT=/path/to/prompts` | user prompt override root |

**API-key precedence:** `SIGNOFF_JUDGE_API_KEY` wins when set. When
it is not, the judge falls back to the provider-native variable
(`ANTHROPIC_API_KEY` for `provider=anthropic`, `OPENAI_API_KEY` for
`provider=openai`) so users who already have those set don't have to
re-export.

---

## Retry & error semantics

Retries happen on:

- `RateLimitError` (429) — `Retry-After` header is honoured when
  present, clamped to `retry_max_backoff`.
- `InternalServerError` / any 5xx.
- `APIConnectionError`, `APITimeoutError`, and the per-call
  `asyncio.wait_for` timeout.

Retries DO NOT happen on:

- Auth / permission errors (401 / 403).
- Bad-request (400) — the request itself is malformed, so retrying
  won't help.
- Content-policy refusals.

When retries exhaust, `JudgeInfrastructureError` is raised. Per
[`docs/protocol.md`](./protocol.md) §4.4, verifier authors should
catch this umbrella class and return
`ctx.fail(..., severity=INFO, ...)` — transient provider failure
MUST NOT fail the deliverable, only the check.

```python
from signoff_judge import JudgeError

async def my_verifier(claim, ctx):
    try:
        result = await ctx.judge.check_entailment(claim=claim.text, passage=ev)
    except JudgeError as exc:
        return ctx.fail(
            reason=f"Judge failed: {exc}",
            severity=Severity.INFO,
        )
    if result.label == "contradicted":
        return ctx.fail(
            reason="Source contradicts claim.",
            suggestion="Revise the claim to match the source.",
            evidence={"excerpt": result.excerpt, "explanation": result.explanation},
        )
    return ctx.ok(evidence={"excerpt": result.excerpt, "cost_usd": result.cost_usd})
```

---

## Cost accounting

`JudgeResult.cost_usd` is computed from actual provider-reported token
counts against the rate table in
[`signoff_judge.cost.RATES`](../packages/signoff-judge/src/signoff_judge/cost.py).
Unknown models log a WARNING and surface cost as `0.0` rather than
breaking the verdict. When provider prices change, exactly one file
changes — `cost.py` — and `effective_date` + `source` on every entry
record the pricing-page snapshot that informed the numbers.

---

## Prompt injection posture

Every built-in prompt wraps user-supplied content in a named tag
(`<source>`, `<output>`, `<text>`) and instructs the model to treat
instructions inside as data, not commands. This is a minimal
mitigation; see [`docs/prompts.md`](./prompts.md) for the prompt
contract, and the `signoff-judge` unit tests for structural checks
that the mitigation can't be bypassed by bad calling code.

---

## Writing a verifier that uses `ctx.judge`

Cheap deterministic pre-checks run first. Only call `ctx.judge` after
you've exhausted what string-matching or regex can do — LLM calls are
hundreds of milliseconds and real dollars, and they're the biggest
source of flakiness in a verifier's regression suite.

When you do call the judge:

1. Pass user content via the structured kwargs (`claim=`, `passage=`,
   `output=`, etc.) — never concatenate into a prompt string.
2. Always check `result.confidence` before acting on a borderline
   verdict.
3. Record `result.excerpt` + `result.explanation` in
   `VerifierResult.evidence` so the feedback packet is
   human-actionable.
4. Catch `JudgeError` and treat transient failures as `INFO`, not
   blockers.
