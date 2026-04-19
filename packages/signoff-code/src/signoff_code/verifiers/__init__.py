"""Verifier entry points for :mod:`signoff_code`.

Each verifier lives in its own module for focus. The
``[project.entry-points."signoff.verifiers"]`` table in
``pyproject.toml`` references the callables here.
"""

from __future__ import annotations

from signoff_code.verifiers.lint_clean import lint_clean
from signoff_code.verifiers.semantic_diff import semantic_diff
from signoff_code.verifiers.smoke_imports import smoke_imports
from signoff_code.verifiers.tests_pass import tests_pass
from signoff_code.verifiers.types_check import types_check

__all__ = [
    "lint_clean",
    "semantic_diff",
    "smoke_imports",
    "tests_pass",
    "types_check",
]
