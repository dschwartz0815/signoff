# Writing a Pack

A **pack** is a pip-installable Python package that ships a coherent set of verifiers for a domain. Phase 1 ships `signoff-code` (test execution, type checking, lint). This guide covers what every pack ships, how it registers its verifiers and defaults, and how it hooks into the harness.

Authoring a single verifier is covered in [`docs/writing-a-verifier.md`](./writing-a-verifier.md). This doc is one level up — pack-level concerns.

Repo conventions: [`CLAUDE.md`](../CLAUDE.md) §11. Protocol: [`docs/protocol.md`](./protocol.md) §4.

---

## Pack layout

```
packages/signoff-<domain>/
├── pyproject.toml
├── Dockerfile                       # If verifiers need sandboxing.
├── .dockerignore
├── README.md                        # Public-facing: every verifier documented
└── src/signoff_<domain>/
    ├── __init__.py                  # Pack version
    ├── default_config.py            # Returns the pack's default config dict
    ├── verifiers/
    │   ├── __init__.py
    │   ├── foo.py                   # One verifier per file
    │   └── bar.py
    └── prompts/                     # LLM-judge prompts live alongside verifiers
        └── foo.md
tests/
├── verifiers/
│   ├── test_foo.py                  # Unit + integration per verifier
│   └── test_bar.py
└── regression/
    └── fixtures/                    # Claim + ground-truth pairs for the suite
```

Pack name convention: pip-name is `signoff-<domain>` (hyphens); Python module is `signoff_<domain>` (underscores). The `@verifier` decorator infers the pip-name from the module automatically, so you don't have to repeat it.

---

## `pyproject.toml` — two entry-point groups

```toml
[project]
name = "signoff-research"
version = "0.1.0"
dependencies = [
  "signoff-core>=0.0.1",
]

[project.entry-points."signoff.verifiers"]
citation_existence = "signoff_research.verifiers.citation_existence:citation_existence"
citation_entailment = "signoff_research.verifiers.citation_entailment:citation_entailment"

[project.entry-points."signoff.pack_defaults"]
signoff-research = "signoff_research.default_config:defaults"
```

- `signoff.verifiers` — one entry per verifier. Each target is a `@verifier`-decorated async function. `Registry.discovered()` (used by `Harness.from_config_path`) loads every target at harness startup.
- `signoff.pack_defaults` — at most one entry per pack, targeting either a `() -> Mapping` callable or a module-level `Mapping`. The loader deep-merges these in at layer 2 of the resolution order (see [`docs/configuration.md`](./configuration.md) and protocol §6.2).

---

## `default_config.py` — what a pack contributes

```python
# packages/signoff-research/src/signoff_research/default_config.py
from typing import Any

def defaults() -> dict[str, Any]:
    """Default config contributed by this pack. Merged under user YAML."""
    return {
        "deliverables": {
            "research_report": {
                "verifiers": {
                    "signoff-research.citation_existence": {"enabled": True},
                    "signoff-research.citation_entailment": {
                        "enabled": True,
                        "sample_rate": 0.5,
                    },
                },
            },
        },
    }
```

The shape matches the top-level [`HarnessConfig`](../packages/signoff-core/src/signoff/config.py). You can contribute any subtree — packs typically stick to `deliverables.*.verifiers` but are welcome to ship sensible budget defaults too. Users override any of it.

---

## Testing layers

Per [`CLAUDE.md`](../CLAUDE.md) §12, every pack ships four layers of tests:

| Layer | What it checks | Speed | Location |
|-------|----------------|-------|----------|
| Unit | Each verifier's logic against a mocked `VerifierContext` | <1s | `tests/verifiers/test_<name>.py` |
| Integration | The harness runs the real pack against fixture deliverables with mocked HTTP/judge | seconds | `tests/integration/test_<flow>.py` |
| Parity | Cross-language fixtures round-trip cleanly through both Python and TS SDKs | seconds | `tests/parity/` (shared with core) |
| Regression | ≥ 20 realistic claim + ground-truth pairs per verifier; pass rate tracked over time | slow, opt-in | `tests/regression/` with `pytest.mark.regression` |

Unit + integration run on every PR. Parity runs on every PR. Regression runs nightly or opt-in via `SIGNOFF_REGRESSION_USE_REAL_JUDGE=1`.

For verifiers that use an LLM judge, mock it with [`signoff.testing.FakeJudge`](../packages/signoff-core/src/signoff/testing.py). Regression tests can flip to the real judge via the env var.

---

## Sandbox image (if required)

Packs whose verifiers execute untrusted code (`signoff-code`, future `signoff-data`) declare `runtime_required="docker"` on the decorator and ship a `Dockerfile` alongside the pack. The image follows [`CLAUDE.md`](../CLAUDE.md) §9.4 conventions: multi-stage, non-root, matching `.dockerignore`, preinstalled toolchain, signed with `cosign` on release.

`signoff-runtime-docker` loads the image tagged `ghcr.io/dschwartz0815/signoff/<pack>-sandbox:<version>` — the pack version drives the tag. See [`docs/runtimes.md`](./runtimes.md) for the runtime side.

---

## Release cadence

Each pack is a separate PyPI package with independent semver. Protocol-breaking changes (a new MUST in the protocol doc) require a coordinated release: core + the affected packs tagged together, with a shared changelog note. See [`CLAUDE.md`](../CLAUDE.md) §16.

---

## Worked example references

- A minimal complete pack is the scaffold in [`packages/signoff-code`](../packages/signoff-code/). It has the shape but no verifiers yet (Phase 1 lands them).
- The first shipped verifier in any pack should track the pattern shown in [`docs/writing-a-verifier.md`](./writing-a-verifier.md) — worked example with tests.
- For the harness lifecycle around a pack, see [`docs/harness.md`](./harness.md).
