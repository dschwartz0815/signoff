"""Tests for the passing_change fixture — all pass."""

from __future__ import annotations

from calculator import add, multiply


def test_add() -> None:
    assert add(2, 3) == 5


def test_multiply() -> None:
    assert multiply(4, 5) == 20
