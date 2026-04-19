"""Unit tests for :mod:`signoff_judge.prompts`."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from signoff_judge.prompts import (
    PromptNotFoundError,
    PromptRegistry,
)

# ---------------------------------------------------------------------------
# Built-in prompt sanity
# ---------------------------------------------------------------------------


def test_builtin_entailment_loads() -> None:
    reg = PromptRegistry()
    t = reg.get("entailment")
    assert t.name == "entailment"
    assert t.version == "1.0.0"
    assert "fact-checker" in t.system
    assert "{{ claim }}" in t.user_template
    assert t.output_schema["properties"]["label"]["enum"] == [
        "supported",
        "contradicted",
        "not_addressed",
    ]


def test_builtin_list_available_includes_three_prompts() -> None:
    reg = PromptRegistry()
    available = dict(reg.list_available())
    assert set(available) == {"entailment", "policy_compliance", "classify"}


def test_get_caches_by_key() -> None:
    reg = PromptRegistry()
    a = reg.get("entailment")
    b = reg.get("entailment")
    assert a is b


def test_get_explicit_version_matches() -> None:
    reg = PromptRegistry()
    t = reg.get("entailment", version="1.0.0")
    assert t.version == "1.0.0"


def test_get_wrong_version_raises() -> None:
    reg = PromptRegistry()
    with pytest.raises(PromptNotFoundError):
        reg.get("entailment", version="9.9.9")


def test_get_unknown_name_raises() -> None:
    reg = PromptRegistry()
    with pytest.raises(PromptNotFoundError):
        reg.get("no-such-prompt")


# ---------------------------------------------------------------------------
# Template rendering
# ---------------------------------------------------------------------------


def test_render_entailment_fills_claim_and_passage() -> None:
    reg = PromptRegistry()
    system, user = reg.get("entailment").render(
        claim="Paris is the capital of France.",
        passage="Paris is the capital city of France.",
    )
    assert "fact-checker" in system
    assert "Paris is the capital of France." in user
    assert "<source>" in user and "</source>" in user


def test_render_entailment_with_optional_context() -> None:
    reg = PromptRegistry()
    _, user = reg.get("entailment").render(
        claim="c",
        passage="p",
        context="prior discussion about geography",
    )
    assert "prior discussion about geography" in user


def test_render_missing_required_variable_raises() -> None:
    reg = PromptRegistry()
    with pytest.raises(ValueError, match=r"missing required variable\(s\).*passage"):
        reg.get("entailment").render(claim="c")


def test_render_unexpected_variable_raises() -> None:
    reg = PromptRegistry()
    with pytest.raises(ValueError, match=r"unexpected variable\(s\).*surprise"):
        reg.get("entailment").render(claim="c", passage="p", surprise="oh no")


def test_render_classify_joins_labels() -> None:
    reg = PromptRegistry()
    _, user = reg.get("classify").render(
        text="Breaking news about geopolitics.",
        labels=["news", "blog", "academic"],
    )
    assert "news, blog, academic" in user


def test_render_policy_compliance_enumerates_examples() -> None:
    reg = PromptRegistry()
    _, user = reg.get("policy_compliance").render(
        output="Here's the API key: sk-abc",
        policy="Do not disclose secrets.",
        examples_of_violations=["Leaking an API key", "Printing a password"],
    )
    assert "Leaking an API key" in user
    assert "Printing a password" in user


# ---------------------------------------------------------------------------
# Override directory
# ---------------------------------------------------------------------------


def _write_override(root: Path, schema_name: str = "entailment") -> None:
    (root / "schemas").mkdir(parents=True, exist_ok=True)
    (root / "schemas" / f"{schema_name}.schema.json").write_text(
        json.dumps(
            {
                "type": "object",
                "required": ["label"],
                "properties": {"label": {"type": "string"}},
                "additionalProperties": False,
            }
        )
    )
    (root / f"{schema_name}.md").write_text(
        "---\n"
        f"name: {schema_name}\n"
        "version: 2.0.0-local\n"
        "description: locally vendored override\n"
        f"output_schema: schemas/{schema_name}.schema.json\n"
        "required_variables:\n  - claim\n  - passage\n"
        "---\n\n"
        "# System prompt\n\n"
        "LOCAL SYSTEM\n\n"
        "# User prompt template\n\n"
        "Claim: {{ claim }}\nPassage: {{ passage }}\n"
    )


def test_user_root_override_shadows_builtin(tmp_path: Path) -> None:
    _write_override(tmp_path)
    reg = PromptRegistry(user_root=tmp_path)
    t = reg.get("entailment")
    assert t.version == "2.0.0-local"
    assert "LOCAL SYSTEM" in t.system


def test_user_root_added_to_list_available(tmp_path: Path) -> None:
    _write_override(tmp_path, schema_name="entailment")
    reg = PromptRegistry(user_root=tmp_path)
    names = {n: v for n, v in reg.list_available()}
    assert names["entailment"] == "2.0.0-local"


def test_malformed_prompt_raises(tmp_path: Path) -> None:
    (tmp_path / "broken.md").write_text("no frontmatter here\n")
    reg = PromptRegistry(user_root=tmp_path)
    with pytest.raises(ValueError, match="frontmatter"):
        reg.get("broken")
