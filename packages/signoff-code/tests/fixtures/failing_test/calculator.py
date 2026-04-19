"""Calculator with a bug that the fixture's test catches."""

from __future__ import annotations


def add(a: int, b: int) -> int:
    # Intentionally wrong: off-by-one. test_add_basic catches this.
    return a + b + 1
