"""Unit tests for :func:`signoff_code.verifiers.lint_clean.lint_clean`."""

from __future__ import annotations

import json

from signoff import Severity
from signoff_code import CodeChangeDeliverable
from signoff_code.verifiers.lint_clean import lint_clean

from .conftest import exec_result


async def test_ruff_exit_zero_passes(make_ctx, meta_for, synthetic_claim) -> None:
    change = CodeChangeDeliverable(intent="x", files={"a.py": "X = 1\n"})
    ctx = make_ctx(change, meta_for("lint_clean"), responses=[exec_result(exit_code=0)])
    result = await lint_clean(synthetic_claim, ctx)
    assert result.passed is True


async def test_ruff_findings_are_warning_by_default(make_ctx, meta_for, synthetic_claim) -> None:
    findings = [
        {
            "filename": "a.py",
            "location": {"row": 3, "column": 1},
            "code": "F401",
            "message": "unused import",
        },
        {
            "filename": "a.py",
            "location": {"row": 5, "column": 1},
            "code": "E501",
            "message": "line too long",
        },
    ]
    change = CodeChangeDeliverable(intent="x", files={"a.py": "import os\n"})
    ctx = make_ctx(
        change,
        meta_for("lint_clean"),
        responses=[exec_result(exit_code=1, stdout=json.dumps(findings))],
    )
    result = await lint_clean(synthetic_claim, ctx)
    assert result.passed is False
    assert result.severity == Severity.WARNING
    assert result.evidence["finding_count"] == 2
    assert "F401" in (result.suggestion or "")


async def test_no_py_files_skips(make_ctx, meta_for, synthetic_claim) -> None:
    change = CodeChangeDeliverable(intent="docs", files={"README.md": "hi"})
    ctx = make_ctx(change, meta_for("lint_clean"), responses=[])
    result = await lint_clean(synthetic_claim, ctx)
    assert result.passed is True
    assert result.evidence["skipped"] is True


async def test_ruff_garbage_output_falls_back_to_info(make_ctx, meta_for, synthetic_claim) -> None:
    change = CodeChangeDeliverable(intent="x", files={"a.py": "X = 1\n"})
    ctx = make_ctx(
        change,
        meta_for("lint_clean"),
        responses=[exec_result(exit_code=2, stdout="not-json")],
    )
    result = await lint_clean(synthetic_claim, ctx)
    assert result.passed is False
    assert result.severity == Severity.INFO
