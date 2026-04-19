"""Unit tests for :func:`signoff_code.verifiers.types_check.types_check`."""

from __future__ import annotations

from signoff import Severity
from signoff_code import CodeChangeDeliverable
from signoff_code.verifiers.types_check import types_check

from .conftest import exec_result


async def test_mypy_exit_zero_passes(make_ctx, meta_for, synthetic_claim) -> None:
    change = CodeChangeDeliverable(intent="x", files={"a.py": "x: int = 1\n"})
    ctx = make_ctx(change, meta_for("types_check"), responses=[exec_result(exit_code=0)])
    result = await types_check(synthetic_claim, ctx)
    assert result.passed is True


async def test_mypy_errors_parsed_into_blocker(make_ctx, meta_for, synthetic_claim) -> None:
    stdout = (
        "a.py:1: error: Incompatible types in assignment "
        '(expression has type "str", variable has type "int")  [assignment]\n'
        'a.py:5: error: Name "undefined" is not defined  [name-defined]\n'
    )
    change = CodeChangeDeliverable(intent="x", files={"a.py": "x: int = 'nope'\n"})
    ctx = make_ctx(
        change,
        meta_for("types_check"),
        responses=[exec_result(exit_code=1, stdout=stdout)],
    )
    result = await types_check(synthetic_claim, ctx)
    assert result.passed is False
    assert result.severity == Severity.BLOCKER
    assert result.evidence["error_count"] == 2
    errors = result.evidence["errors"]
    assert isinstance(errors, list)
    assert errors[0]["line"] == 1
    assert "Incompatible types" in errors[0]["message"]


async def test_mypy_scope_limited_to_changed_py_files(make_ctx, meta_for, synthetic_claim) -> None:
    change = CodeChangeDeliverable(
        intent="x",
        files={"a.py": "x = 1\n", "README.md": "hi"},
    )
    ctx = make_ctx(change, meta_for("types_check"), responses=[exec_result(exit_code=0)])
    await types_check(synthetic_claim, ctx)
    cmd = ctx.calls[0].cmd
    assert cmd[:3] == ["python", "-m", "mypy"]
    # Only the .py path made it into the cmd; README.md did not.
    assert "a.py" in cmd
    assert "README.md" not in cmd


async def test_no_py_files_skips_gracefully(make_ctx, meta_for, synthetic_claim) -> None:
    change = CodeChangeDeliverable(intent="docs", files={"README.md": "hi"})
    ctx = make_ctx(change, meta_for("types_check"), responses=[])
    result = await types_check(synthetic_claim, ctx)
    assert result.passed is True
    assert result.evidence["skipped"] is True
    assert len(ctx.calls) == 0


async def test_mypy_unknown_exit_is_info(make_ctx, meta_for, synthetic_claim) -> None:
    change = CodeChangeDeliverable(intent="x", files={"a.py": "x = 1\n"})
    ctx = make_ctx(
        change,
        meta_for("types_check"),
        responses=[exec_result(exit_code=2, stdout="", stderr="usage error")],
    )
    result = await types_check(synthetic_claim, ctx)
    assert result.passed is False
    assert result.severity == Severity.INFO
