"""Calculator with a type error mypy catches."""

from __future__ import annotations


def add(a: int, b: int) -> int:
    # Incompatible-types error: assigning a str to an int.
    result: int = "not a number"
    return a + b + len(result)
