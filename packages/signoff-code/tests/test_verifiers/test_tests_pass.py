"""Unit tests for :func:`signoff_code.verifiers.tests_pass.tests_pass`."""

from __future__ import annotations

from signoff import Severity
from signoff_code import CodeChangeDeliverable
from signoff_code.verifiers.tests_pass import tests_pass

from .conftest import exec_result


async def test_pytest_exit_zero_passes(make_ctx, meta_for, synthetic_claim) -> None:
    change = CodeChangeDeliverable(
        intent="add coverage", files={"test_x.py": "def test_x():\n    assert 1\n"}
    )
    ctx = make_ctx(
        change,
        meta_for("tests_pass"),
        responses=[exec_result(exit_code=0, stdout="======== 1 passed in 0.01s ========")],
    )
    result = await tests_pass(synthetic_claim, ctx)
    assert result.passed is True
    assert result.evidence["tool"] == "pytest"
    assert result.evidence["passed"] == 1


async def test_pytest_exit_one_is_blocker_with_first_failing_test(
    make_ctx, meta_for, synthetic_claim
) -> None:
    stdout = (
        "FAILED tests/test_a.py::test_one - AssertionError\n"
        "FAILED tests/test_a.py::test_two - AssertionError\n"
        "======== 2 failed, 3 passed in 0.05s ========"
    )
    change = CodeChangeDeliverable(intent="add", files={"a.py": "X = 1\n"})
    ctx = make_ctx(
        change,
        meta_for("tests_pass"),
        responses=[exec_result(exit_code=1, stdout=stdout)],
    )
    result = await tests_pass(synthetic_claim, ctx)
    assert result.passed is False
    assert result.severity == Severity.BLOCKER
    assert "tests/test_a.py::test_one" in (result.suggestion or "")
    assert result.evidence["first_failing_node"] == "tests/test_a.py::test_one"


async def test_pytest_exit_five_is_warning(make_ctx, meta_for, synthetic_claim) -> None:
    change = CodeChangeDeliverable(intent="docs only", files={"README.md": "hi"})
    ctx = make_ctx(
        change,
        meta_for("tests_pass"),
        responses=[exec_result(exit_code=5, stdout="no tests ran")],
    )
    result = await tests_pass(synthetic_claim, ctx)
    assert result.passed is False
    assert result.severity == Severity.WARNING
    assert "collected no tests" in result.reason


async def test_pytest_unexpected_exit_is_info(make_ctx, meta_for, synthetic_claim) -> None:
    change = CodeChangeDeliverable(intent="x", files={"a.py": "X = 1"})
    ctx = make_ctx(
        change,
        meta_for("tests_pass"),
        responses=[exec_result(exit_code=137, stdout="killed")],
    )
    result = await tests_pass(synthetic_claim, ctx)
    assert result.passed is False
    assert result.severity == Severity.INFO


async def test_pytest_runs_with_quiet_and_tb_short(make_ctx, meta_for, synthetic_claim) -> None:
    change = CodeChangeDeliverable(intent="x", files={"a.py": "X = 1"})
    ctx = make_ctx(
        change,
        meta_for("tests_pass"),
        responses=[exec_result(exit_code=0)],
    )
    await tests_pass(synthetic_claim, ctx)
    call = ctx.calls[0]
    assert call.cmd[:3] == ["python", "-m", "pytest"]
    assert "-q" in call.cmd
    assert "--tb=short" in call.cmd
