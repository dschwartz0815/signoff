"""Tests for the failing_test fixture — one deliberately fails."""

from __future__ import annotations

from calculator import add


def test_add_basic() -> None:
    assert add(2, 3) == 5  # fails because calculator.add is off-by-one.
