# signoff-judge

Real LLM-judge client for Signoff verifiers. Ships two providers:

- `AnthropicJudge` — uses the official `anthropic` SDK. Default model
  is `claude-haiku-4-5` (cheap and fast for judge use).
- `OpenAIJudge` — uses the official `openai` SDK with structured
  outputs for provider parity.

Drop-in replacement for `signoff.testing.FakeJudge` in production
wiring.

```python
from signoff_judge import AnthropicJudge, JudgeClientConfig

async with AnthropicJudge(JudgeClientConfig(provider="anthropic")) as judge:
    result = await judge.check_entailment(
        claim="Paris is the capital of France.",
        passage="France's capital city is Paris.",
    )
    print(result.label, result.confidence, result.model, result.prompt_version)
```

Configuration (API keys, model, timeouts, retries) is loaded from
`SIGNOFF_JUDGE_*` environment variables; see
[`docs/judge-client.md`](../../docs/judge-client.md) for the full
reference. Prompts live as versioned Markdown files under
`signoff_judge.prompts`; see
[`docs/prompts.md`](../../docs/prompts.md) for the authoring and
override conventions.

Install: `pip install signoff-judge` (pulls `anthropic` and `openai`
as required deps so the provider surface is always available).
