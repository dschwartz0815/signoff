# What it catches

Five concrete failures Signoff catches on real code changes. Each
example is a `CodeChangeDeliverable` going in and a `Verdict`
coming out, pulled from fixtures in
`packages/signoff-code/tests/fixtures/`.

Every example below is reproducible from your own checkout:

```sh
# Build the sandbox image once per machine.
docker build -t signoff/code-sandbox:dev packages/signoff-code

# Run the specific integration test; uncomment as needed.
uv run pytest -m docker \
  packages/signoff-code/tests/integration/test_e2e_docker.py -v
```

If the verdicts in your run diverge from the ones shown here, open
an issue — either the doc is stale or there's a real bug.

---

## 1. Agent said "I added null checks"; diff caught and swallowed the exception

**Scenario.** The agent describes its change as "Add input
validation to `parse_config` to reject non-dict inputs." The diff
actually wraps the call site in `try/except AttributeError: pass`
and returns `{}`, hiding the bug.

**Deliverable** (abridged):

```json
{
  "kind": "code_change",
  "content": {
    "intent": "Add input validation to parse_config to reject non-dict inputs.",
    "files": {
      "config.py": "def parse_config(x):\n    try:\n        return dict(x)\n    except AttributeError:\n        return {}\n"
    }
  }
}
```

**Verdict** (elided):

```json
{
  "passed": false,
  "results": [
    {
      "verifier": "signoff-code.semantic_diff",
      "passed": false,
      "severity": "warning",
      "reason": "Judge says the diff contradicts the stated intent: the code silently swallows the AttributeError instead of validating and rejecting non-dict inputs.",
      "suggestion": "Either the intent is wrong or the change doesn't do what it claims. Review the explanation in evidence and rework one of the two.",
      "evidence": {
        "label": "contradicted",
        "excerpt": "except AttributeError:\n    return {}",
        "confidence": 0.85,
        "model": "claude-haiku-4-5",
        "prompt_version": "1.0.0"
      }
    }
  ]
}
```

**Why it matters.** No deterministic verifier catches this —
syntactically the code is fine, it type-checks, it lints clean,
imports work. The only signal that the change doesn't do what the
agent said is the semantic mismatch between the stated intent and
the actual diff. `semantic_diff`'s LLM judge is specifically built
to flag that class of drift.

---

## 2. Test-free code path passes CI; `smoke_imports` catches the broken import

**Scenario.** The agent adds `calculator.py` but with a top-level
import of a module that doesn't exist. Its `test_calculator.py`
tests a happy path but only exercises functions — the broken
import would fire at production import time, not during the test
(if tests imported the module at all, which they don't when the
tests are parametrised over fixtures).

Fixture: `packages/signoff-code/tests/fixtures/broken_import/`:

```python
# calculator.py
import this_module_does_not_exist  # top-level; import time

def add(a: int, b: int) -> int:
    return a + b
```

**Verdict** (elided):

```json
{
  "passed": false,
  "results": [
    {
      "verifier": "signoff-code.smoke_imports",
      "passed": false,
      "severity": "blocker",
      "reason": "Import failed for module 'calculator' (calculator.py).",
      "suggestion": "`python -c 'import calculator'` failed; fix the top-level error before continuing.",
      "evidence": {
        "failed_module": "calculator",
        "failed_path": "calculator.py",
        "traceback": "Traceback (most recent call last):\n  File \"<string>\", line 1, in <module>\n  File \"/workspace/calculator.py\", line 1, in <module>\n    import this_module_does_not_exist\nModuleNotFoundError: No module named 'this_module_does_not_exist'\n"
      }
    }
  ]
}
```

**Why it matters.** `pytest` can report "3 passed" on a codebase
that will `ImportError` the first time someone actually imports
the module in production. `smoke_imports` closes the gap by
running `python -c 'import <module>'` against every changed `.py`
in isolation — it forces top-level evaluation, which is exactly
when feature-flagged or lazy imports trip.

---

## 3. Refactor subtly changes a return type; `types_check` catches it

**Scenario.** The agent "simplifies" a helper by returning a
`dict` literal directly instead of building a `Mapping`. Behavior
is the same at a glance but the type narrows (or widens, depending
on the caller), and callers annotated with the original type
break elsewhere.

Fixture: `packages/signoff-code/tests/fixtures/type_error/`:

```python
# calculator.py
def add(a: int, b: int) -> int:
    # Incompatible-types error: assigning a str to an int.
    result: int = "not a number"
    return a + b + len(result)
```

**Verdict** (elided):

```json
{
  "passed": false,
  "results": [
    {
      "verifier": "signoff-code.types_check",
      "passed": false,
      "severity": "blocker",
      "reason": "mypy reported 1 error(s).",
      "suggestion": "Fix the type errors:\n- calculator.py:4: Incompatible types in assignment (expression has type \"str\", variable has type \"int\")  [assignment]",
      "evidence": {
        "error_count": 1,
        "errors": [
          {
            "path": "calculator.py",
            "line": 4,
            "message": "Incompatible types in assignment (expression has type \"str\", variable has type \"int\")  [assignment]"
          }
        ]
      }
    }
  ]
}
```

**Why it matters.** The `suggested_repair` names the specific
file, line, and mypy error code. An agent reading the feedback
packet doesn't have to re-run the type checker; it can apply the
repair and call `request_signoff` again. Signoff's feedback-first
design is specifically about closing this loop without a human.

---

## 4. Deleted test "fixed" the failure; `tests_pass` flags the missing coverage

**Scenario.** The agent's change makes a test fail. Instead of
fixing the code, it deletes the test. The test suite still passes
locally — but coverage dropped.

Fixture: any deliverable where the only `.py` changes are
non-test files.

**Verdict** (elided):

```json
{
  "passed": false,
  "results": [
    {
      "verifier": "signoff-code.tests_pass",
      "passed": false,
      "severity": "warning",
      "reason": "pytest collected no tests for this change.",
      "suggestion": "If this is a test-free change (docs, config), override this verifier's severity in config; otherwise add tests that cover the new behaviour.",
      "evidence": {
        "tool": "pytest",
        "passed": 0,
        "failed": 0,
        "errors": 0,
        "skipped": 0
      }
    }
  ]
}
```

**Why it matters.** A WARNING rather than a BLOCKER here is a
deliberate choice — docs-only changes and config tweaks
legitimately don't add tests. But the surface is visible, and the
suggestion tells a reviewer where to look. An operator who wants
this strict promotes to blocker via `severity_override: blocker` in
config for their pipeline.

---

## 5. Agent's pytest-passing fix actually broke a different test

**Scenario.** The agent fixed the failing test the user asked
about but broke another one. `tests_pass` runs the full suite, not
just the targeted test, and catches the regression.

Fixture: `packages/signoff-code/tests/fixtures/failing_test/`:

```python
# calculator.py
def add(a: int, b: int) -> int:
    # Intentionally wrong: off-by-one. test_add_basic catches this.
    return a + b + 1
```

**Verdict** (elided):

```json
{
  "passed": false,
  "results": [
    {
      "verifier": "signoff-code.tests_pass",
      "passed": false,
      "severity": "blocker",
      "reason": "pytest failed (exit 1)",
      "suggestion": "First failing test: test_calculator.py::test_add_basic. Inspect stdout and fix.",
      "evidence": {
        "first_failing_node": "test_calculator.py::test_add_basic",
        "failed": 1,
        "passed": 0,
        "stdout": "F                                                                        [100%]\n=================================== FAILURES ===================================\n_________________________________ test_add_basic _________________________________\n    def test_add_basic() -> None:\n>       assert add(2, 3) == 5  # fails because calculator.add is off-by-one.\nE       AssertionError: assert 6 == 5\n..."
      }
    }
  ]
}
```

**Why it matters.** Agents frequently make targeted fixes that
pass the one failing test they were asked about while breaking
something else in the suite. A gate that runs the *full* pytest
invocation — not just `pytest path/to/specific_test.py` — catches
those regressions before the human ever sees a "done."

---

## How these were produced

Every example above came out of the signoff-code fixture suite. To
regenerate any of them:

```sh
# Unit tests (mocked ctx.exec, illustrate parsing + severity
# mapping):
uv run pytest packages/signoff-code/tests/test_verifiers -v

# Integration tests (real Docker daemon, real sandbox image):
uv run pytest -m docker packages/signoff-code -v
```

If your verdicts diverge — different `first_failing_node`, a
different `label` from `semantic_diff`, different lint findings —
open an issue tagged `area:gallery`. Drift between this doc and
reality is exactly the thing the doc is trying to prevent.

---

## See also

- [`docs/packs/signoff-code.md`](./packs/signoff-code.md) —
  per-verifier semantics, exit-code tables, configuration.
- [`docs/judge-client.md`](./judge-client.md) — how
  `semantic_diff`'s judge layer works under the hood.
- [`docs/protocol.md`](./protocol.md) §3.5 — the shape of
  `FeedbackPacket` entries.
