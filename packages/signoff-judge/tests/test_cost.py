"""Unit tests for :mod:`signoff_judge.cost`."""

from __future__ import annotations

import logging

import pytest
from signoff_judge.cost import RATES, estimate_cost


def test_known_model_cost_is_table_driven() -> None:
    rates = RATES["claude-haiku-4-5"]
    got = estimate_cost("claude-haiku-4-5", input_tokens=1_000, output_tokens=500)
    expected = (
        1_000 * rates.input_usd_per_million / 1_000_000
        + 500 * rates.output_usd_per_million / 1_000_000
    )
    assert got == pytest.approx(expected)


def test_zero_tokens_returns_zero() -> None:
    assert estimate_cost("claude-haiku-4-5", input_tokens=0, output_tokens=0) == 0.0


def test_unknown_model_returns_zero_and_warns(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.WARNING, logger="signoff_judge.cost"):
        got = estimate_cost("unknown-model-x", input_tokens=10, output_tokens=10)
    assert got == 0.0
    assert any(
        "unknown-model-x" in rec.message and "unknown model" in rec.message
        for rec in caplog.records
    )


def test_negative_tokens_raise() -> None:
    with pytest.raises(ValueError, match="non-negative"):
        estimate_cost("claude-haiku-4-5", input_tokens=-1, output_tokens=0)


def test_rate_table_entries_are_self_consistent() -> None:
    for key, rates in RATES.items():
        assert rates.model == key, f"key/model mismatch for {key!r}"
        assert rates.input_usd_per_million >= 0
        assert rates.output_usd_per_million >= 0
        # ISO-ish date format: YYYY-MM-DD.
        assert len(rates.effective_date) == 10
        assert rates.effective_date[4] == "-" and rates.effective_date[7] == "-"
        assert rates.source.startswith(("http://", "https://"))
