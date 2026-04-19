"""Unit tests for :func:`signoff_code.verifiers.smoke_imports.smoke_imports`."""

from __future__ import annotations

import pytest
from signoff import Severity
from signoff_code import CodeChangeDeliverable
from signoff_code.verifiers.smoke_imports import (
    module_name_for_path,
    smoke_imports,
)

from .conftest import exec_result

# ---------------------------------------------------------------------------
# module_name_for_path
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("path", "expected"),
    [
        ("foo.py", "foo"),
        ("pkg/foo.py", "pkg.foo"),
        ("pkg/sub/foo.py", "pkg.sub.foo"),
        ("src/pkg/foo.py", "pkg.foo"),
        ("pkg/__init__.py", "pkg"),
        ("pkg/sub/__init__.py", "pkg.sub"),
        ("README.md", None),
        ("__init__.py", None),
    ],
)
def test_module_name_for_path(path: str, expected: str | None) -> None:
    assert module_name_for_path(path) == expected


# ---------------------------------------------------------------------------
# smoke_imports verifier
# ---------------------------------------------------------------------------


async def test_all_imports_pass(make_ctx, meta_for, synthetic_claim) -> None:
    change = CodeChangeDeliverable(
        intent="x",
        files={"a.py": "X = 1\n", "b.py": "Y = 2\n"},
    )
    ctx = make_ctx(
        change,
        meta_for("smoke_imports"),
        responses=[exec_result(exit_code=0), exec_result(exit_code=0)],
    )
    result = await smoke_imports(synthetic_claim, ctx)
    assert result.passed is True
    assert result.evidence["imported"] == ["a", "b"]
    # Both modules attempted.
    assert len(ctx.calls) == 2


async def test_import_failure_becomes_blocker(make_ctx, meta_for, synthetic_claim) -> None:
    change = CodeChangeDeliverable(
        intent="break",
        files={"a.py": "X = 1\n", "b.py": "raise ImportError('boom')\n"},
    )
    responses = [
        exec_result(exit_code=0),
        exec_result(
            exit_code=1,
            stderr="Traceback ...\nImportError: boom\n",
        ),
    ]
    ctx = make_ctx(change, meta_for("smoke_imports"), responses=responses)
    result = await smoke_imports(synthetic_claim, ctx)
    assert result.passed is False
    assert result.severity == Severity.BLOCKER
    assert result.evidence["failed_module"] == "b"
    assert "ImportError" in result.evidence["traceback"]


async def test_non_python_changes_skipped_silently(make_ctx, meta_for, synthetic_claim) -> None:
    change = CodeChangeDeliverable(intent="docs", files={"README.md": "hi"})
    ctx = make_ctx(change, meta_for("smoke_imports"), responses=[])
    result = await smoke_imports(synthetic_claim, ctx)
    assert result.passed is True
    assert result.evidence["skipped"] is True
    assert len(ctx.calls) == 0
